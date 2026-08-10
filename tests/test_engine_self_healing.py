"""Failure-driven engine self-healing tests (GPU-free, fake engine).

Covers (spec section 35): D recycle resets the real adapter, E known
recoverable engine failure, F errno22 from a non-engine phase, G ordinary
segment failure, H systemic fingerprint, I recovery succeeds, J budget
exhausted -> needs_attention, K cancel during recovery, L pause during
recovery, M old-generation fencing, N readiness, O health never inits the
engine, P MCP recovering/needs_attention schema, R no mass-failure storm,
S resume preserves completed segments.
"""
from __future__ import annotations

import json
import os
import threading
import time
import wave

import pytest

from lib import project_manager as pm
from lib import queue as queue_module
from lib import tts_engine
from lib.failures import (
    PHASE_ENGINE_INFER,
    RecoveryBudget,
    RecoveryHooks,
)
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import ProductionJobService, ProjectService, VoiceCastResolver
from services.production_runtime import ProductionRuntime, ProductionRuntimeClient
from services.runtime_engine import read_runtime_engine_status
from services.synthesis import SynthesisState


def _write_wav(path: str, frames: int = 800) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * frames)
    return path


def _script(segments: int = 4) -> dict:
    return {
        "meta": {"title": "自愈测试", "author": "测试"},
        "voices": {"旁白": {}},
        "chapters": [{
            "id": "001",
            "title": "第一章",
            "segments": [
                {
                    "id": f"001-{index:03d}",
                    "role": "旁白",
                    "text": f"第{index}段",
                }
                for index in range(1, segments + 1)
            ],
        }],
    }


@pytest.fixture
def healing_project(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_RUNTIME_MODE", "inline")
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
    ProjectService.create_project_from_data("book", _script())
    project_dir = ProjectRepository.get_project_dir("book")
    voice_path = os.path.join(project_dir, "voices", "narrator.wav")
    os.makedirs(os.path.dirname(voice_path), exist_ok=True)
    with open(voice_path, "wb") as file:
        file.write(b"voice")
    bindings_path = os.path.join(project_dir, "voice_bindings.json")
    with open(bindings_path, encoding="utf-8") as file:
        bindings = json.load(file)
    bindings["bindings"]["旁白"] = voice_path
    with open(bindings_path, "w", encoding="utf-8") as file:
        json.dump(bindings, file, ensure_ascii=False)
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)
    monkeypatch.setattr(tts_engine, "init_engine", lambda: None)
    monkeypatch.setattr(tts_engine, "reset_engine", lambda: None)
    yield {
        "project": "book",
        "voice": voice_path,
        "segments_dir": os.path.join(project_dir, "segments"),
    }
    ProductionRuntimeClient.reset_inline()


def _wait_terminal(task_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        record = TaskRepository.load_task(task_id)
        if record is not None and record.status in {
            "done", "error", "cancelled", "interrupted", "needs_attention",
        }:
            return record
        time.sleep(0.02)
    raise AssertionError(
        f"task {task_id} did not reach a terminal state (last={record.status if record else None})"
    )


def _wait_status(task_id: str, status: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        record = TaskRepository.load_task(task_id)
        if record is not None and record.status == status:
            return record
        time.sleep(0.02)
    raise AssertionError(
        f"task {task_id} did not reach status {status!r} (last={record.status if record else None})"
    )


def _write_success_synthesize(monkeypatch, calls: list[str]):
    def fake(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        return _write_wav(str(kwargs["output_path"]))

    monkeypatch.setattr(tts_engine, "synthesize_segment", fake)
    return fake


def test_d_recycle_detaches_real_engine_and_increments_generation(
    healing_project,
    monkeypatch,
):
    monkeypatch.setattr(tts_engine, "reset_engine", lambda: None)
    init_calls: list[str] = []
    monkeypatch.setattr(
        tts_engine,
        "init_engine",
        lambda: init_calls.append("init"),
    )
    runtime = ProductionRuntime(
        lock_path=str(healing_project["project"]) + "-d.lock",
        poll_interval=0.05,
    )
    runtime.ensure_engine_ready()
    snapshot = runtime.engine_snapshot()
    assert snapshot["state"] == "ready"
    assert snapshot["engine_generation"] == 1
    assert init_calls == ["init"]

    generation = runtime._engine.recycle()
    assert generation == 2
    snapshot = runtime.engine_snapshot()
    assert snapshot["state"] == "ready"
    assert snapshot["engine_generation"] == 2
    assert snapshot["recovery_count"] == 1
    assert init_calls == ["init", "init"]
    runtime.stop()

    # The real reset_engine API detaches the module-level engine instance.
    monkeypatch.undo()
    tts_engine._tts = object()
    tts_engine.reset_engine()
    assert tts_engine.engine_is_initialized() is False
    assert tts_engine._tts is None


def test_e_known_recoverable_failure_recovers_same_segment(
    healing_project,
    monkeypatch,
):
    calls: list[str] = []

    def flaky(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        if len(calls) == 1:
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                "Invalid argument",
                errno=22,
                recoverable=True,
            )
        return _write_wav(str(kwargs["output_path"]))

    monkeypatch.setattr(tts_engine, "synthesize_segment", flaky)
    created = ProductionJobService.start(
        healing_project["project"], {"all": True}, source="mcp"
    )
    record = _wait_terminal(created["task_id"])

    assert record.status == "done"
    assert record.progress["completed"] == 4
    assert record.failed_segment_ids == []
    # 4 segments + 1 recovery retry of the same (first) segment.
    assert len(calls) == 5
    assert calls.count("第1段") == 2
    recovery = record.progress["recovery"]
    assert recovery["attempt"] == 1
    assert recovery["max_attempts"] == 2
    assert recovery["retry_segment"] == "001-001"
    assert recovery["reason_code"] == "TTS_ENGINE_RUNTIME_FAILURE"
    assert recovery["errno"] == 22
    assert recovery["phase"] == PHASE_ENGINE_INFER
    assert recovery["recovered"] is True
    assert record.progress["engine_generation"] == 2


def test_f_errno22_from_publish_phase_is_not_engine_failure(
    healing_project,
    monkeypatch,
):
    _write_success_synthesize(monkeypatch, [])
    failures = []
    hooks = RecoveryHooks(on_failure=failures.append)
    real_replace = os.replace
    replaced = {"count": 0}

    def selective_replace(src, dst):
        if ".part.wav" in str(src):
            replaced["count"] += 1
            if replaced["count"] == 1:
                raise OSError(22, "Invalid argument")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", selective_replace)
    gen = queue_module.synthesize_project(
        healing_project["project"],
        {"旁白": healing_project["voice"]},
        recovery=hooks,
        budget=RecoveryBudget(),
    )
    lines = list(gen)

    publish_failures = [
        failure for failure in failures
        if failure.phase == "atomic_publish"
    ]
    assert publish_failures
    assert publish_failures[0].errno == 22
    assert publish_failures[0].engine_related is False
    assert publish_failures[0].recoverable is False
    # errno=22 from os.replace must not be classified as engine_infer.
    assert not any(failure.phase == PHASE_ENGINE_INFER for failure in failures)
    # The failed segment is reported as [X]; later segments still run.
    assert any(line.startswith("[X] 001-001") for line in lines)
    assert any(line.startswith("[+] 001-002") for line in lines)
    assert sum(1 for line in lines if line.startswith("[+]")) == 3


def test_g_ordinary_segment_failure_does_not_recycle_engine(
    healing_project,
    monkeypatch,
):
    calls: list[str] = []

    def fake(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        if kwargs.get("text") == "第1段":
            raise ValueError("非法文本")
        return _write_wav(str(kwargs["output_path"]))

    monkeypatch.setattr(tts_engine, "synthesize_segment", fake)
    failures = []
    hooks = RecoveryHooks(
        recycle=lambda: 99,
        cancel_requested=lambda: False,
        pause_gate=lambda: None,
        on_recovery=lambda _event: None,
        on_failure=failures.append,
    )
    gen = queue_module.synthesize_project(
        healing_project["project"],
        {"旁白": healing_project["voice"]},
        recovery=hooks,
        budget=RecoveryBudget(),
    )
    lines = list(gen)

    # ValueError on segment A, segment B still succeeds: no engine recycle.
    assert any(line.startswith("[X] 001-001") for line in lines)
    assert any(line.startswith("[+] 001-002") for line in lines)
    assert failures[0].exception_type == "ValueError"
    assert failures[0].recoverable is False
    assert all(failure.engine_related is False for failure in failures)
    assert not any("[re] recovering" in line for line in lines)


def test_h_repeated_systemic_fingerprint_stops_new_segments(healing_project, monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", os.environ["AUDIOBOOK_STUDIO_DATA_DIR"])
    calls: list[str] = []

    def fake(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        raise tts_engine.EngineRuntimeFailure(
            PHASE_ENGINE_INFER,
            "native handle invalid",
            recoverable=False,
        )

    monkeypatch.setattr(tts_engine, "synthesize_segment", fake)
    recycle_calls: list[int] = []
    events: list[dict] = []
    hooks = RecoveryHooks(
        recycle=lambda: (recycle_calls.append(len(recycle_calls) + 1) or len(recycle_calls) + 1),
        cancel_requested=lambda: False,
        pause_gate=lambda: None,
        on_recovery=events.append,
        on_failure=lambda _failure: None,
    )
    gen = queue_module.synthesize_project(
        healing_project["project"],
        {"旁白": healing_project["voice"]},
        recovery=hooks,
        budget=RecoveryBudget(
            segment_retry_limit=1,
            engine_recycle_limit=2,
            systemic_failure_threshold=3,
        ),
    )
    lines = list(gen)

    # Three distinct segments share one fingerprint -> systemic stop.
    assert [line for line in lines if line.startswith("[X]")] == [
        "[X] 001-001 失败: TTS_ENGINE_RUNTIME_FAILURE phase=engine_infer: native handle invalid",
        "[X] 001-002 失败: TTS_ENGINE_RUNTIME_FAILURE phase=engine_infer: native handle invalid",
        "[X] 001-003 失败: TTS_ENGINE_RUNTIME_FAILURE phase=engine_infer: native handle invalid",
    ]
    assert len(calls) == 5  # 3 initial + 2 recovery retries, then stop
    assert len(recycle_calls) == 2
    assert "第4段" not in calls
    recovering_events = [e for e in events if e.get("event") == "recovering"]
    assert len(recovering_events) == 2
    exhausted = [e for e in events if e.get("event") == "exhausted"]
    assert exhausted and exhausted[0]["reason"] == "systemic_fingerprint"
    assert exhausted[0]["attempt"] == 2


def test_i_recovery_succeeds_and_completed_segments_are_not_redone(
    healing_project,
    monkeypatch,
):
    calls: list[str] = []

    def flaky(**kwargs):
        text = str(kwargs.get("text") or "")
        calls.append(text)
        if text == "第2段" and calls.count("第2段") == 1:
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                "Invalid argument",
                errno=22,
                recoverable=True,
            )
        return _write_wav(str(kwargs["output_path"]))

    monkeypatch.setattr(tts_engine, "synthesize_segment", flaky)
    created = ProductionJobService.start(
        healing_project["project"], {"all": True}, source="mcp"
    )
    record = _wait_terminal(created["task_id"])

    assert record.status == "done"
    assert record.progress["completed"] == 4
    # 1st segment once, 2nd twice (fail + retry), 3rd/4th once each.
    assert calls == ["第1段", "第2段", "第2段", "第3段", "第4段"]
    assert record.progress["recovery"]["attempt"] == 1
    assert record.progress["recovery"]["recovered"] is True
    assert record.progress["engine_generation"] == 2


def test_j_recovery_budget_exhausted_becomes_needs_attention(
    healing_project,
    monkeypatch,
):
    calls: list[str] = []

    def always_fail(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        raise tts_engine.EngineRuntimeFailure(
            PHASE_ENGINE_INFER,
            "Invalid argument",
            errno=22,
            recoverable=True,
        )

    monkeypatch.setattr(tts_engine, "synthesize_segment", always_fail)
    created = ProductionJobService.start(
        healing_project["project"], {"all": True}, source="mcp"
    )
    record = _wait_terminal(created["task_id"])

    assert record.status == "needs_attention"
    # generation 1 fail -> recycle (gen 2) -> retry fail -> recycle (gen 3)
    # -> retry fail -> budget exhausted.  Exactly 3 engine calls.
    assert len(calls) == 3
    assert record.progress["completed"] == 0
    assert record.failed_segment_ids == ["001-001"]
    recovery = record.progress["recovery"]
    assert recovery["attempt"] == 2
    assert recovery["max_attempts"] == 2
    assert recovery["recovered"] is False
    assert recovery["errno"] == 22
    assert record.progress["engine_generation"] == 3
    assert record.error_summary


def test_k_cancel_during_recovery_wins(healing_project, monkeypatch):
    calls: list[str] = []
    reset_calls: list[str] = []

    def flaky(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        raise tts_engine.EngineRuntimeFailure(
            PHASE_ENGINE_INFER,
            "Invalid argument",
            errno=22,
            recoverable=True,
        )

    def slow_reset():
        reset_calls.append("reset")
        time.sleep(0.5)

    monkeypatch.setattr(tts_engine, "synthesize_segment", flaky)
    monkeypatch.setattr(tts_engine, "reset_engine", slow_reset)
    created = ProductionJobService.start(
        healing_project["project"], {"all": True}, source="mcp"
    )
    _wait_status(created["task_id"], "recovering")
    ProductionJobService.cancel(created["task_id"])
    record = _wait_terminal(created["task_id"])

    assert record.status == "cancelled"
    # One recycle was already in flight, but no retry and no second recycle.
    assert len(calls) == 1
    assert len(reset_calls) == 1
    assert record.progress["completed"] == 0
    assert record.failed_segment_ids == []


def test_l_pause_during_recovery_is_not_bypassed(healing_project, monkeypatch):
    calls: list[str] = []
    reset_calls: list[str] = []

    def flaky(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        if len(calls) == 1:
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                "Invalid argument",
                errno=22,
                recoverable=True,
            )
        return _write_wav(str(kwargs["output_path"]))

    def slow_reset():
        reset_calls.append("reset")
        time.sleep(0.5)

    monkeypatch.setattr(tts_engine, "synthesize_segment", flaky)
    monkeypatch.setattr(tts_engine, "reset_engine", slow_reset)
    created = ProductionJobService.start(
        healing_project["project"], {"all": True}, source="mcp"
    )
    _wait_status(created["task_id"], "recovering")
    ProductionJobService.pause(created["task_id"])
    _wait_status(created["task_id"], "paused")
    ProductionJobService.resume(created["task_id"])
    record = _wait_terminal(created["task_id"])

    assert record.status == "done"
    assert record.progress["completed"] == 4
    assert len(reset_calls) == 1
    assert len(calls) == 5  # 4 segments + 1 retry after the paused recycle


def test_m_stale_generation_update_is_fenced(healing_project, tmp_path):
    runtime = ProductionRuntime(
        lock_path=str(tmp_path / "fence.lock"),
        poll_interval=0.05,
    )
    runtime.ensure_engine_ready()
    assert runtime.engine_snapshot()["engine_generation"] == 1
    assert runtime._engine.recycle() == 2

    now = "2026-08-11T00:00:00Z"
    record = TaskRecord(
        task_id="fenced_task",
        task_type="synthesis",
        project="book",
        status="running",
        owner_id=runtime.owner_id,
        source="mcp",
        progress={"total": 4, "completed": 1, "failed": 0},
        created_at=now,
        updated_at=now,
    )
    TaskRepository.save_task(record)
    runtime._current_record = record

    stale = SynthesisState(
        task_id="fenced_task",
        project="book",
        status="done",
        completed=4,
        engine_generation=1,
    )
    runtime._on_state_update(stale)
    assert TaskRepository.load_task("fenced_task").status == "running"

    fresh = SynthesisState(
        task_id="fenced_task",
        project="book",
        status="done",
        completed=4,
        engine_generation=2,
    )
    runtime._on_state_update(fresh)
    assert TaskRepository.load_task("fenced_task").status == "done"
    runtime.stop()


def test_n_readiness_unknown_engine_does_not_block_start(healing_project):
    status = VoiceCastResolver.get_voice_binding_status(healing_project["project"])
    assert status["mode"] == "legacy_manual"
    assert status["cast_ready"] is True
    assert status["production_ready"] is True
    assert status["runtime_status"] == "unknown"
    assert status["engine_ready"] is False
    # unknown/uninitialized is NOT a blocker for starting production.
    assert status["synthesis_ready"] is True
    plan = ProductionJobService.plan(healing_project["project"], {"all": True})
    assert plan["ready"] is True
    assert plan["voice_cast"]["production_ready"] is True


def test_o_runtime_health_query_never_initializes_engine(
    healing_project,
    monkeypatch,
):
    init_calls: list[str] = []
    monkeypatch.setattr(
        tts_engine,
        "init_engine",
        lambda: init_calls.append("init"),
    )
    health = ProductionJobService.get_runtime_health()
    assert health["engine_state"] == "unknown"
    assert "engine_generation" in health
    assert "pid" in health
    assert "active_task_id" in health
    assert init_calls == []
    VoiceCastResolver.get_voice_binding_status(healing_project["project"])
    assert init_calls == []
    assert read_runtime_engine_status()["state"] == "unknown"


def test_p_mcp_task_status_schema_for_recovery_states(
    healing_project,
    monkeypatch,
):
    from mcp_server.server import handle_request

    now = "2026-08-11T00:00:00Z"
    base = {
        "task_type": "synthesis",
        "project": "book",
        "source": "mcp",
        "created_at": now,
        "updated_at": now,
    }
    recovering = TaskRecord(
        task_id="task_recovering",
        status="recovering",
        owner_id="runtime-1",
        progress={
            "total": 224,
            "completed": 119,
            "failed": 0,
            "current_segment": "8-020",
            "engine_generation": 2,
            "recovery": {
                "reason_code": "TTS_ENGINE_RUNTIME_FAILURE",
                "attempt": 1,
                "max_attempts": 2,
                "engine_generation": 2,
                "retry_segment": "8-020",
                "fingerprint": "OSError|22|engine_infer|msg|infer.py:10:infer",
                "exception_type": "OSError",
                "errno": 22,
                "phase": "engine_infer",
                "recovered": False,
            },
        },
        **base,
    )
    attention = TaskRecord(
        task_id="task_attention",
        status="needs_attention",
        progress={
            "total": 224,
            "completed": 119,
            "failed": 1,
            "engine_generation": 3,
            "recovery": {
                "reason_code": "TTS_ENGINE_RUNTIME_FAILURE",
                "attempt": 2,
                "max_attempts": 2,
                "engine_generation": 3,
                "retry_segment": "8-020",
                "fingerprint": "OSError|22|engine_infer|msg|infer.py:10:infer",
                "exception_type": "OSError",
                "errno": 22,
                "phase": "engine_infer",
                "recovered": False,
            },
        },
        error_summary="TTS_ENGINE_RUNTIME_FAILURE: Invalid argument",
        **base,
    )
    for record in (
        TaskRecord(task_id="task_running", status="running", **base),
        TaskRecord(task_id="task_done", status="done", **base),
        TaskRecord(task_id="task_error", status="error", **base),
        TaskRecord(task_id="task_cancelled", status="cancelled", **base),
        recovering,
        attention,
    ):
        TaskRepository.save_task(record)

    for task_id, expected_status in (
        ("task_running", "running"),
        ("task_recovering", "recovering"),
        ("task_attention", "needs_attention"),
        ("task_done", "done"),
        ("task_error", "error"),
        ("task_cancelled", "cancelled"),
    ):
        response = handle_request({
            "jsonrpc": "2.0",
            "id": task_id,
            "method": "tools/call",
            "params": {
                "name": "get_production_task",
                "arguments": {"task_id": task_id},
            },
        })
        result = response["result"]
        assert result["isError"] is False
        payload = result["structuredContent"]
        assert isinstance(payload, dict)
        json.dumps(payload, ensure_ascii=False)  # serializable
        assert payload["status"] == expected_status

    recovering_payload = handle_request({
        "jsonrpc": "2.0", "id": "r1", "method": "tools/call",
        "params": {
            "name": "get_production_task",
            "arguments": {"task_id": "task_recovering"},
        },
    })["result"]["structuredContent"]
    assert recovering_payload["recovery"]["reason_code"] == "TTS_ENGINE_RUNTIME_FAILURE"
    assert recovering_payload["recovery"]["attempt"] == 1
    assert recovering_payload["recovery"]["engine_generation"] == 2
    assert recovering_payload["recovery"]["retry_segment"] == "8-020"
    assert recovering_payload["engine_generation"] == 2

    attention_payload = handle_request({
        "jsonrpc": "2.0", "id": "r2", "method": "tools/call",
        "params": {
            "name": "get_production_task",
            "arguments": {"task_id": "task_attention"},
        },
    })["result"]["structuredContent"]
    assert attention_payload["recovery"]["attempt"] == 2
    assert attention_payload["error"]["code"] == "TTS_ENGINE_RUNTIME_FAILURE"
    assert attention_payload["error"]["errno"] == 22
    assert attention_payload["error"]["phase"] == "engine_infer"
    assert attention_payload["next_actions"] == [
        "retry_task",
        "inspect_runtime_health",
        "cancel_task",
    ]

    health = handle_request({
        "jsonrpc": "2.0", "id": "r3", "method": "tools/call",
        "params": {"name": "get_runtime_health", "arguments": {}},
    })["result"]
    assert health["isError"] is False
    assert isinstance(health["structuredContent"], dict)
    assert "engine_state" in health["structuredContent"]
    json.dumps(health["structuredContent"], ensure_ascii=False)


def test_r_no_mass_failure_storm(healing_project, monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", os.environ["AUDIOBOOK_STUDIO_DATA_DIR"])
    calls: list[str] = []

    def always_fail(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        raise tts_engine.EngineRuntimeFailure(
            PHASE_ENGINE_INFER,
            "Invalid argument",
            errno=22,
            recoverable=True,
        )

    monkeypatch.setattr(tts_engine, "synthesize_segment", always_fail)
    # Simulate a 224-segment book through the production runtime.
    ProjectService.create_project_from_data("huge", _script(224))
    project_dir = ProjectRepository.get_project_dir("huge")
    voice_path = os.path.join(project_dir, "voices", "narrator.wav")
    os.makedirs(os.path.dirname(voice_path), exist_ok=True)
    with open(voice_path, "wb") as file:
        file.write(b"voice")
    bindings_path = os.path.join(project_dir, "voice_bindings.json")
    with open(bindings_path, encoding="utf-8") as file:
        bindings = json.load(file)
    bindings["bindings"]["旁白"] = voice_path
    with open(bindings_path, "w", encoding="utf-8") as file:
        json.dump(bindings, file, ensure_ascii=False)

    created = ProductionJobService.start("huge", {"all": True}, source="mcp")
    record = _wait_terminal(created["task_id"])

    assert record.status == "needs_attention"
    # gen1 fail -> recycle -> retry fail -> recycle -> retry fail -> stop.
    assert len(calls) == 3
    assert record.progress["completed"] == 0
    assert len(record.failed_segment_ids) == 1
    assert record.progress["recovery"]["attempt"] == 2
    assert record.progress["engine_generation"] == 3


def test_s_resume_preserves_completed_segments(healing_project, monkeypatch):
    calls: list[str] = []

    def flaky(**kwargs):
        text = str(kwargs.get("text") or "")
        calls.append(text)
        if text == "第4段" and calls.count("第4段") == 1:
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                "Invalid argument",
                errno=22,
                recoverable=True,
            )
        return _write_wav(str(kwargs["output_path"]))

    monkeypatch.setattr(tts_engine, "synthesize_segment", flaky)
    first = ProductionJobService.start(
        healing_project["project"],
        {"segment_ids": ["001-001", "001-002", "001-003"]},
        source="mcp",
    )
    first_record = _wait_terminal(first["task_id"])
    assert first_record.status == "done"
    assert first_record.progress["completed"] == 3
    assert calls == ["第1段", "第2段", "第3段"]

    second = ProductionJobService.start(
        healing_project["project"],
        {"segment_ids": ["001-004"]},
        source="mcp",
    )
    second_record = _wait_terminal(second["task_id"])
    assert second_record.status == "done"
    # Only the new segment is synthesized (fail once + retry once); the
    # completed 001-001..003 are never re-synthesized.
    assert calls == ["第1段", "第2段", "第3段", "第4段", "第4段"]
    assert second_record.progress["completed"] == 1
    assert second_record.progress["recovery"]["recovered"] is True


def test_workflow_stage_for_recovering_and_needs_attention(healing_project):
    from services import WorkflowService

    now = "2026-08-11T00:00:00Z"
    TaskRepository.save_task(TaskRecord(
        task_id="wf_recovering",
        task_type="synthesis",
        project="book",
        status="recovering",
        owner_id="runtime-1",
        progress={"total": 4, "completed": 1, "failed": 0},
        created_at=now,
        updated_at=now,
    ))
    state = WorkflowService.get_state("book")
    assert state["stage"] == "recovering"
    assert state["next_actions"][0]["action"] == "wait_for_recovery"
    assert all(
        action["tool"] != "start_production"
        for action in state["next_actions"]
    )

    TaskRepository.delete_task("wf_recovering")
    TaskRepository.save_task(TaskRecord(
        task_id="wf_attention",
        task_type="synthesis",
        project="book",
        status="needs_attention",
        progress={"total": 4, "completed": 1, "failed": 1},
        created_at=now,
        updated_at=now,
    ))
    state = WorkflowService.get_state("book")
    assert state["stage"] == "needs_attention"
    actions = {item["action"] for item in state["next_actions"]}
    assert {"retry_task", "inspect_runtime_health", "cancel_task"} <= actions
