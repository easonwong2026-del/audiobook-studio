"""Failure-driven engine self-healing tests (GPU-free, fake engine).

Covers (spec section 35): D recycle resets the real adapter, E known
recoverable engine failure, F errno22 from a non-engine phase, G ordinary
segment failure, H systemic non-engine fingerprint, I recovery succeeds, J
budget exhausted -> needs_attention, K cancel during recovery, L pause during
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
    SynthesisFailure,
    is_confirmed_engine_recovery,
)
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import ProductionJobService, ProjectService, VoiceCastResolver
from services.production_runtime import ProductionRuntime, ProductionRuntimeClient
from services.runtime_engine import (
    RuntimeEngineLifecycle,
    read_runtime_engine_status,
    runtime_engine_status_path,
)
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
                original_exception=OSError(22, "Invalid argument"),
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


def test_task_recycle_budget_is_not_reset_for_each_segment(healing_project, monkeypatch):
    calls: list[str] = []
    recycle_calls: list[int] = []

    def flaky(**kwargs):
        text = str(kwargs.get("text") or "")
        calls.append(text)
        if text == "第1段" and calls.count(text) == 1:
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                "Invalid argument",
                errno=22,
                recoverable=True,
                original_exception=OSError(22, "Invalid argument"),
            )
        if text == "第2段":
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                "Invalid argument",
                errno=22,
                recoverable=True,
                original_exception=OSError(22, "Invalid argument"),
            )
        return _write_wav(str(kwargs["output_path"]))

    monkeypatch.setattr(tts_engine, "synthesize_segment", flaky)
    events: list[dict] = []
    hooks = RecoveryHooks(
        recycle=lambda: (recycle_calls.append(1) or len(recycle_calls) + 1),
        cancel_requested=lambda: False,
        pause_gate=lambda: None,
        on_recovery=events.append,
    )
    lines = list(queue_module.synthesize_project(
        healing_project["project"],
        {"旁白": healing_project["voice"]},
        recovery=hooks,
        budget=RecoveryBudget(
            segment_retry_limit=1,
            engine_recycle_limit=2,
            systemic_failure_threshold=99,
        ),
    ))

    # Segment 1 consumes one task-level recycle.  Segment 2 gets only the
    # single remaining recycle and then stops; segment 3 is never pulled.
    assert calls == ["第1段", "第1段", "第2段", "第2段"]
    assert len(recycle_calls) == 2
    recovering = [event for event in events if event.get("event") == "recovering"]
    assert [event["attempt"] for event in recovering] == [1, 2]
    exhausted = [event for event in events if event.get("event") == "exhausted"]
    assert exhausted and exhausted[0]["attempt"] == 2
    assert any(line.startswith("[re] stop") for line in lines)
    assert "第3段" not in calls


def test_unconfirmed_engine_oserror_cannot_recycle(healing_project, monkeypatch):
    calls: list[str] = []
    recycle_calls: list[int] = []
    failures = []

    def fake(**kwargs):
        text = str(kwargs.get("text") or "")
        calls.append(text)
        if text == "第1段":
            original = OSError(5, "access denied")
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                str(original),
                errno=5,
                recoverable=True,
                original_exception=original,
            ) from original
        return _write_wav(str(kwargs["output_path"]))

    monkeypatch.setattr(tts_engine, "synthesize_segment", fake)
    hooks = RecoveryHooks(
        recycle=lambda: (recycle_calls.append(1) or 2),
        cancel_requested=lambda: False,
        pause_gate=lambda: None,
        on_failure=failures.append,
    )
    lines = list(queue_module.synthesize_project(
        healing_project["project"],
        {"旁白": healing_project["voice"]},
        recovery=hooks,
        budget=RecoveryBudget(systemic_failure_threshold=99),
    ))

    assert recycle_calls == []
    assert failures[0].exception_type == "OSError"
    assert failures[0].errno == 5
    assert failures[0].recoverable is False
    assert failures[0].engine_related is True
    assert any(line.startswith("[+] 001-002") for line in lines)


def test_oom_exhaustion_is_an_explicit_confirmed_engine_fingerprint():
    wrapped = tts_engine.EngineRuntimeFailure(
        PHASE_ENGINE_INFER,
        "OOM after retries",
        recoverable=True,
        code=tts_engine.TTS_ENGINE_OOM_EXHAUSTED,
    )
    failure = SynthesisFailure.from_exception(
        segment_id="001-001",
        chapter_id="001",
        phase=PHASE_ENGINE_INFER,
        exc=wrapped,
        recoverable=wrapped.recoverable,
        engine_related=True,
        code=wrapped.code,
    )
    assert failure.recoverable is True
    assert failure.code == tts_engine.TTS_ENGINE_OOM_EXHAUSTED
    assert is_confirmed_engine_recovery(failure) is True


def test_structured_failure_keeps_original_type_errno_and_traceback_origin(
    healing_project,
    monkeypatch,
):
    failures = []

    def fake(**kwargs):
        original = OSError(22, "Invalid argument")
        try:
            raise original
        except OSError as exc:
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                str(exc),
                errno=22,
                recoverable=True,
                original_exception=exc,
            ) from exc

    monkeypatch.setattr(tts_engine, "synthesize_segment", fake)
    hooks = RecoveryHooks(
        recycle=lambda: 2,
        cancel_requested=lambda: False,
        pause_gate=lambda: None,
        on_failure=failures.append,
    )
    list(queue_module.synthesize_project(
        healing_project["project"],
        {"旁白": healing_project["voice"]},
        recovery=hooks,
        budget=RecoveryBudget(engine_recycle_limit=0),
    ))

    failure = failures[0]
    assert failure.exception_type == "OSError"
    assert failure.errno == 22
    assert failure.phase == PHASE_ENGINE_INFER
    assert failure.traceback_origin.endswith(":fake")
    assert failure.as_dict()["traceback_origin"] == failure.traceback_origin


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
        raise ValueError("native handle invalid")

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
        "[X] 001-001 失败: native handle invalid",
        "[X] 001-002 失败: native handle invalid",
        "[X] 001-003 失败: native handle invalid",
    ]
    assert len(calls) == 3  # threshold reached without recycling the engine
    assert recycle_calls == []
    assert "第4段" not in calls
    recovering_events = [e for e in events if e.get("event") == "recovering"]
    assert recovering_events == []
    exhausted = [e for e in events if e.get("event") == "exhausted"]
    assert exhausted and exhausted[0]["reason"] == "systemic_fingerprint"
    assert exhausted[0]["reason_code"] == "SYSTEMIC_FAILURE_THRESHOLD"
    assert exhausted[0]["attempt"] == 0


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
                original_exception=OSError(22, "Invalid argument"),
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
            original_exception=OSError(22, "Invalid argument"),
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


def test_recycle_failure_persists_attention_then_fresh_runtime_resumes(
    healing_project,
    monkeypatch,
):
    import services.production_runtime as production_runtime_module

    init_calls: list[int] = []

    def init_engine():
        init_calls.append(1)
        if len(init_calls) == 2:
            raise RuntimeError("recycle init failed")

    calls: list[str] = []

    def synthesize(**kwargs):
        text = str(kwargs.get("text") or "")
        calls.append(text)
        if len(calls) == 1:
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                "Invalid argument",
                errno=22,
                recoverable=True,
                original_exception=OSError(22, "Invalid argument"),
            )
        return _write_wav(str(kwargs["output_path"]))

    monkeypatch.setattr(tts_engine, "init_engine", init_engine)
    monkeypatch.setattr(tts_engine, "synthesize_segment", synthesize)
    created = ProductionJobService.start(
        healing_project["project"], {"all": True}, source="mcp"
    )
    first = _wait_terminal(created["task_id"])
    assert first.status == "needs_attention"
    assert first.progress["recovery"]["reason_code"] == "TTS_ENGINE_RECYCLE_FAILED"
    assert first.progress["recovery"]["recycle_exception_type"] == "RuntimeError"
    assert first.progress["recovery"]["recycle_message"] == "recycle init failed"

    old_runtime = production_runtime_module._INLINE_RUNTIME
    assert old_runtime is not None
    assert old_runtime.requires_fresh_runtime is True
    assert old_runtime.wait_until_stopped(timeout=5.0) is True
    assert old_runtime.is_running is False
    assert read_runtime_engine_status()["runtime_state"] == "unknown"

    resumed = ProductionJobService.resume(created["task_id"])
    new_runtime = production_runtime_module._INLINE_RUNTIME
    assert new_runtime is not None
    assert new_runtime is not old_runtime
    resumed_record = _wait_terminal(resumed["task_id"])
    assert resumed_record.status == "done"
    assert resumed_record.progress["completed"] == 4
    # initial preflight + failed recycle init + fresh-runtime preflight
    assert len(init_calls) == 3


def test_runtime_and_engine_states_are_separate_and_stale_ready_is_hidden(
    healing_project,
    monkeypatch,
):
    status_path = runtime_engine_status_path()
    lifecycle = RuntimeEngineLifecycle(owner_id="runtime-state-test", status_path=status_path)
    monkeypatch.setattr(tts_engine, "init_engine", lambda: None)
    lifecycle.set_runtime_state("running")
    lifecycle.ensure_ready()
    live = read_runtime_engine_status()
    assert live["runtime_state"] == "running"
    assert live["engine_state"] == "ready"
    assert live["state"] == "ready"
    assert live["status_stale"] is False

    with open(status_path, encoding="utf-8") as file:
        payload = json.load(file)
    payload["runtime_updated_at"] = "2000-01-01T00:00:00Z"
    payload["updated_at"] = "2000-01-01T00:00:00Z"
    with open(status_path, "w", encoding="utf-8") as file:
        json.dump(payload, file)
    stale = read_runtime_engine_status()
    assert stale["runtime_state"] == "unknown"
    assert stale["engine_state"] == "unknown"
    assert stale["state"] == "unknown"
    assert stale["status_stale"] is True
    lifecycle.mark_unknown()


def test_k_cancel_during_recovery_wins(healing_project, monkeypatch):
    calls: list[str] = []
    recycle_calls: list[int] = []
    events: list[dict] = []
    cancelled = {"flag": False}

    def flaky(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        raise tts_engine.EngineRuntimeFailure(
            PHASE_ENGINE_INFER,
            "Invalid argument",
            errno=22,
            recoverable=True,
            original_exception=OSError(22, "Invalid argument"),
        )

    def recycle():
        recycle_calls.append(1)
        # The user cancels while the engine recycle is in flight: no retry
        # and no further recycle may happen afterwards.
        cancelled["flag"] = True
        return 2

    monkeypatch.setattr(tts_engine, "synthesize_segment", flaky)
    hooks = RecoveryHooks(
        recycle=recycle,
        cancel_requested=lambda: cancelled["flag"],
        pause_gate=lambda: None,
        on_recovery=events.append,
        on_failure=lambda _failure: None,
    )
    gen = queue_module.synthesize_project(
        healing_project["project"],
        {"旁白": healing_project["voice"]},
        recovery=hooks,
        budget=RecoveryBudget(),
    )
    lines = list(gen)

    assert "[re] cancelled" in lines
    assert len(calls) == 1
    assert len(recycle_calls) == 1
    assert not any(line.startswith("[+]") for line in lines)
    assert not any(
        event.get("event") == "recovered"
        for event in events
    )


def test_l_pause_during_recovery_is_not_bypassed(healing_project, monkeypatch):
    calls: list[str] = []
    events: list[dict] = []
    failures: list = []
    recycle_calls: list[int] = []
    gate_entered = threading.Event()
    released = threading.Event()

    def flaky(**kwargs):
        calls.append(str(kwargs.get("text") or ""))
        if len(calls) == 1:
            raise tts_engine.EngineRuntimeFailure(
                PHASE_ENGINE_INFER,
                "Invalid argument",
                errno=22,
                recoverable=True,
                original_exception=OSError(22, "Invalid argument"),
            )
        return _write_wav(str(kwargs["output_path"]))

    def pause_gate():
        # Simulate a human pause during recovery: the gate blocks the
        # worker, and no new segment work may happen until resume.
        gate_entered.set()
        released.wait(10)

    monkeypatch.setattr(tts_engine, "synthesize_segment", flaky)
    hooks = RecoveryHooks(
        recycle=lambda: (recycle_calls.append(1) or 2),
        cancel_requested=lambda: False,
        pause_gate=pause_gate,
        on_recovery=events.append,
        on_failure=failures.append,
    )
    lines: list[str] = []

    def consume():
        gen = queue_module.synthesize_project(
            healing_project["project"],
            {"旁白": healing_project["voice"]},
            recovery=hooks,
            budget=RecoveryBudget(),
        )
        for line in gen:
            lines.append(line)

    worker = threading.Thread(target=consume)
    worker.start()
    assert gate_entered.wait(10)
    # While the pause gate holds, recovery must not run new engine work.
    time.sleep(0.2)
    assert len(recycle_calls) == 0
    assert calls == ["第1段"]  # only the initial failed attempt
    assert any(
        event.get("event") == "recovering"
        for event in events
    )

    # Resume: the gate releases and the same segment is retried.
    released.set()
    worker.join(15)
    assert not worker.is_alive()
    assert len(recycle_calls) == 1
    assert calls == ["第1段", "第1段", "第2段", "第3段", "第4段"]
    assert any(line.startswith("[+] 001-001") for line in lines)
    assert sum(1 for line in lines if line.startswith("[+]")) == 4
    assert any(
        event.get("event") == "recovered"
        for event in events
    )


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
            original_exception=OSError(22, "Invalid argument"),
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
                original_exception=OSError(22, "Invalid argument"),
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
