"""Runtime-owned TTS engine bootstrap tests (GPU-free).

The independent ProductionRuntime is the only process allowed to initialize
``lib.tts_engine``.  These tests stub ``init_engine`` / ``synthesize_project``
and verify the lifecycle contract without any GPU:

- A: runtime claim -> init once -> SynthesisService.start
- B: engine reuse across consecutive tasks (init stays 1)
- C: concurrent ensure_ready inits exactly once
- D: init failure fails the whole task fast (TTS_ENGINE_INIT_FAILED)
- E: cancel keeps a healthy engine reusable by the next task
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from lib import project_manager as pm
from lib import tts_engine
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRepository
from services import ProductionJobService, ProjectService
from services import synthesis as synthesis_module
from services.production_runtime import ProductionRuntime, ProductionRuntimeClient
from services.runtime_engine import read_runtime_engine_status
from services.synthesis import SynthesisService


SCRIPT = {
    "meta": {"title": "引擎引导测试", "author": "测试"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [
            {"id": "001-001", "role": "旁白", "text": "一"},
            {"id": "001-002", "role": "旁白", "text": "二"},
            {"id": "001-003", "role": "旁白", "text": "三"},
        ],
    }],
}

_TERMINAL = frozenset({"done", "error", "cancelled", "interrupted"})


@pytest.fixture
def engine_project(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_RUNTIME_MODE", "inline")
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    # Keep the legacy compat wrapper in sync: the runtime loop reaches
    # ``lib.project_manager`` through ``build_segment_states``, and the
    # wrapper mirrors its module-level roots into ProjectRepository.
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
    ProjectService.create_project_from_data("book", SCRIPT)
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
    calls = {"init": 0}

    def _fake_init() -> None:
        calls["init"] += 1

    monkeypatch.setattr(tts_engine, "init_engine", _fake_init)
    yield {"project": "book", "calls": calls}
    ProductionRuntimeClient.reset_inline()


def _wait_terminal(task_id: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        record = TaskRepository.load_task(task_id)
        if record is not None and record.status in _TERMINAL:
            return record
        time.sleep(0.02)
    raise AssertionError(
        f"task {task_id} did not reach a terminal state (last={record.status if record else None})"
    )


def _fake_start(status: str = "done"):
    started: list[str] = []

    def _start(state, _project, _bindings, **_kwargs):
        started.append(state.task_id)
        state.status = status
        state.notify()
        return state.task_id

    return _start, started


def test_a_runtime_claims_synthesis_and_inits_engine_once(engine_project, monkeypatch):
    fake, started = _fake_start("done")
    monkeypatch.setattr(SynthesisService, "start", staticmethod(fake))

    created = ProductionJobService.start(
        engine_project["project"], {"all": True}, source="mcp"
    )
    assert created["created"] is True
    record = _wait_terminal(created["task_id"])

    assert record.status == "done"
    assert engine_project["calls"]["init"] == 1
    assert started == [created["task_id"]]


def test_b_engine_is_reused_across_consecutive_tasks(engine_project, monkeypatch):
    fake, started = _fake_start("done")
    monkeypatch.setattr(SynthesisService, "start", staticmethod(fake))

    first = ProductionJobService.start(
        engine_project["project"], {"all": True}, source="mcp"
    )
    _wait_terminal(first["task_id"])
    second = ProductionJobService.start(
        engine_project["project"], {"all": True}, source="mcp"
    )
    _wait_terminal(second["task_id"])

    # The second task must reuse the already-loaded engine: exactly one init
    # across the whole runtime lifecycle, never one per book/chapter.
    assert engine_project["calls"]["init"] == 1
    assert started == [first["task_id"], second["task_id"]]


def test_c_concurrent_ensure_ready_inits_exactly_once(tmp_path, monkeypatch):
    init_calls: list[str] = []

    def slow_init() -> None:
        time.sleep(0.2)
        init_calls.append("init")

    monkeypatch.setattr(tts_engine, "init_engine", slow_init)
    runtime = ProductionRuntime(
        lock_path=str(tmp_path / "runtime.lock"),
        poll_interval=0.05,
    )
    results: list[str] = []
    barrier = threading.Barrier(2)

    def caller() -> None:
        barrier.wait(timeout=2)
        try:
            runtime.ensure_engine_ready()
            results.append("ready")
        except Exception as exc:  # pragma: no cover - asserted below
            results.append(f"error:{exc}")

    threads = [threading.Thread(target=caller) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert len(init_calls) == 1
    assert sorted(results) == ["ready", "ready"]
    assert runtime.engine_snapshot()["state"] == "ready"
    runtime.stop()


def test_d_engine_init_failure_fails_task_fast_without_segment_loop(
    engine_project,
    monkeypatch,
):
    def boom() -> None:
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(tts_engine, "init_engine", boom)
    fake, started = _fake_start("done")
    monkeypatch.setattr(SynthesisService, "start", staticmethod(fake))
    queue_calls: list[str] = []
    monkeypatch.setattr(
        synthesis_module.synth_queue,
        "synthesize_project",
        lambda *_args, **_kwargs: queue_calls.append("queue") or iter(()),
    )

    created = ProductionJobService.start(
        engine_project["project"], {"all": True}, source="mcp"
    )
    record = _wait_terminal(created["task_id"])

    assert record.status == "error"
    assert "TTS_ENGINE_INIT_FAILED" in record.error_summary
    assert record.progress["completed"] == 0
    assert record.progress["attempted"] == 0
    assert record.progress["failed"] == 0
    assert record.failed_segment_ids == []
    # The segment loop must never run: no SynthesisService.start, no queue.
    assert started == []
    assert queue_calls == []
    # The unhealthy runtime terminates instead of lingering and re-claiming.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if read_runtime_engine_status()["state"] == "unknown":
            break
        time.sleep(0.02)
    assert read_runtime_engine_status()["state"] == "unknown"


def test_e_cancel_keeps_engine_healthy_for_next_task(engine_project, monkeypatch):
    def endless(*_args, **_kwargs):
        index = 0
        while True:
            index += 1
            time.sleep(0.01)
            yield f"[+] seg_{index:04d}|ok"

    monkeypatch.setattr(synthesis_module.synth_queue, "synthesize_project", endless)

    created = ProductionJobService.start(
        engine_project["project"], {"all": True}, source="mcp"
    )
    first_id = created["task_id"]
    deadline = time.monotonic() + 8
    record = None
    while time.monotonic() < deadline:
        record = TaskRepository.load_task(first_id)
        if record is not None and record.status == "running":
            break
        time.sleep(0.02)
    assert record is not None and record.status == "running"

    ProductionJobService.cancel(first_id)
    cancelled = _wait_terminal(first_id)
    assert cancelled.status == "cancelled"
    # Cancel is a task-level control: the engine stays loaded and healthy.
    assert engine_project["calls"]["init"] == 1

    def finite(*_args, **_kwargs):
        for index in range(3):
            yield f"[+] 001-00{index + 1}|ok"

    monkeypatch.setattr(synthesis_module.synth_queue, "synthesize_project", finite)
    second = ProductionJobService.start(
        engine_project["project"], {"all": True}, source="mcp"
    )
    done = _wait_terminal(second["task_id"])
    assert done.status == "done"
    assert engine_project["calls"]["init"] == 1
