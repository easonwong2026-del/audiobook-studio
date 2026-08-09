"""Cross-process production ownership tests (Windows spawn compatible)."""
from __future__ import annotations

import multiprocessing
import os
import threading
import time

import pytest

from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services.production_runtime import ProductionRuntime
from services.runtime_lock import ProcessFileLock
from services.synthesis import SynthesisService, SynthesisState


SCRIPT = {
    "meta": {"title": "Runtime"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


def _configure_child(data_dir: str) -> None:
    os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = data_dir
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True


def _spawn_create(
    data_dir: str,
    key: str,
    task_id: str,
    go,
    output,
    scope=None,
    options=None,
) -> None:
    _configure_child(data_dir)
    go.wait(10)
    now = "2026-08-09T00:00:00Z"
    record = TaskRecord(
        task_id=task_id,
        task_type="synthesis",
        project="book",
        status="pending",
        source="mcp",
        scope=scope or {"all": True, "chapter_ids": [], "segment_ids": []},
        options=options or {},
        idempotency_key=key,
        created_at=now,
        updated_at=now,
    )
    try:
        outcome, durable = TaskRepository.create_production_task(record)
        output.put(("ok", outcome, durable.task_id))
    except Exception as exc:  # pragma: no cover - asserted via child payload
        output.put(("error", type(exc).__name__, str(exc)))


def _spawn_read(data_dir: str, task_id: str, output) -> None:
    _configure_child(data_dir)
    record = TaskRepository.load_task(task_id)
    output.put(record.status if record is not None else None)


def _hold_lock(path: str, ready, release) -> None:
    lock = ProcessFileLock(path)
    if not lock.acquire(blocking=False):
        ready.set()
        raise RuntimeError("holder could not acquire test lock")
    ready.set()
    try:
        release.wait(10)
    finally:
        lock.release()


@pytest.fixture
def runtime_project(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("book", SCRIPT)
    return data_dir


def _spawn_results(processes, output) -> list[tuple]:
    for process in processes:
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert process.exitcode == 0
    return [output.get(timeout=2) for _ in processes]


def test_sqlite_transaction_enforces_cross_process_idempotency(runtime_project):
    ctx = multiprocessing.get_context("spawn")
    go = ctx.Event()
    output = ctx.Queue()
    processes = [
        ctx.Process(
            target=_spawn_create,
            args=(runtime_project, "same-key", f"task_{index}", go, output),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    go.set()
    results = _spawn_results(processes, output)

    assert sorted(item[1] for item in results) == ["created", "idempotent"]
    assert len({item[2] for item in results}) == 1
    database = TaskRepository.get_database_path("book")
    assert database and os.path.isfile(database)


def test_sqlite_idempotency_conflict_is_race_safe(runtime_project):
    ctx = multiprocessing.get_context("spawn")
    go = ctx.Event()
    output = ctx.Queue()
    processes = [
        ctx.Process(
            target=_spawn_create,
            args=(
                runtime_project,
                "conflicting-key",
                "task_a",
                go,
                output,
                {"segment_ids": ["001-001"]},
                {
                    "num_beams": 2,
                    "voice_overrides": {"001-001": "08_质检记录/a.wav"},
                },
            ),
        ),
        ctx.Process(
            target=_spawn_create,
            args=(
                runtime_project,
                "conflicting-key",
                "task_b",
                go,
                output,
                {"all": True},
                {
                    "num_beams": 2,
                    "voice_overrides": {"001-001": "08_质检记录/b.wav"},
                },
            ),
        ),
    ]
    for process in processes:
        process.start()
    go.set()
    results = _spawn_results(processes, output)

    assert sorted(item[1] for item in results) == [
        "created",
        "idempotency_conflict",
    ]
    assert len(TaskRepository.list_tasks(project="book", task_type="synthesis")) == 1


def test_sqlite_transaction_enforces_one_active_task_per_project(runtime_project):
    ctx = multiprocessing.get_context("spawn")
    go = ctx.Event()
    output = ctx.Queue()
    processes = [
        ctx.Process(
            target=_spawn_create,
            args=(runtime_project, f"key-{index}", f"task_{index}", go, output),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    go.set()
    results = _spawn_results(processes, output)

    assert sorted(item[1] for item in results) == ["active", "created"]
    assert len(TaskRepository.list_tasks(project="book", task_type="synthesis")) == 1


def test_read_client_never_marks_foreign_owner_interrupted(runtime_project, tmp_path):
    record = TaskRecord(
        task_id="task_owned",
        task_type="synthesis",
        project="book",
        status="running",
        owner_id="old-runtime",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:01:00Z",
    )
    TaskRepository.save_task(record)
    ctx = multiprocessing.get_context("spawn")
    output = ctx.Queue()
    client = ctx.Process(target=_spawn_read, args=(runtime_project, record.task_id, output))
    client.start()
    client.join(10)
    assert client.exitcode == 0
    assert output.get(timeout=2) == "running"
    assert TaskRepository.load_task(record.task_id).status == "running"

    # A replacement runtime may repair the row only after it owns the OS lock.
    runtime = ProductionRuntime(lock_path=str(tmp_path / "runtime.lock"))
    assert runtime.start_background() is True
    try:
        assert TaskRepository.load_task(record.task_id).status == "interrupted"
    finally:
        runtime.stop()


def test_client_exit_does_not_release_runtime_process_lock(runtime_project, tmp_path):
    ctx = multiprocessing.get_context("spawn")
    lock_path = str(tmp_path / "runtime.lock")
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(lock_path, ready, release))
    holder.start()
    assert ready.wait(10)

    output = ctx.Queue()
    client = ctx.Process(target=_spawn_read, args=(runtime_project, "missing", output))
    client.start()
    client.join(10)
    assert client.exitcode == 0
    assert output.get(timeout=2) is None

    contender = ProcessFileLock(lock_path)
    assert contender.acquire(blocking=False) is False
    release.set()
    holder.join(10)
    assert holder.exitcode == 0
    assert contender.acquire(blocking=False) is True
    contender.release()


def test_worker_acknowledges_pause_only_at_generator_boundary(monkeypatch):
    def fake_synthesis(*_args, **_kwargs):
        yield "[0] done|0|0s"

    from services import synthesis as synthesis_module

    monkeypatch.setattr(
        synthesis_module.synth_queue,
        "synthesize_project",
        fake_synthesis,
    )
    state = SynthesisState(task_id="boundary", project="missing")
    state.paused = True
    state.status = "pausing"
    worker = threading.Thread(
        target=SynthesisService._run,
        args=(state, "missing", {}),
        kwargs={"persist_task": False},
    )
    worker.start()
    deadline = time.time() + 5
    while state.status != "paused" and time.time() < deadline:
        time.sleep(0.01)
    assert state.status == "paused"
    SynthesisService.resume(state)
    worker.join(5)
    assert not worker.is_alive()
    assert state.status == "done"
