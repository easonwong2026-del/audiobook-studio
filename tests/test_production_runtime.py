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


def test_runtime_stop_retains_lock_until_export_worker_finishes(tmp_path):
    lock_path = str(tmp_path / "runtime.lock")
    runtime = ProductionRuntime(lock_path=lock_path, poll_interval=0.02)
    assert runtime.start_background() is True
    started = threading.Event()
    release = threading.Event()

    def blocked_export():
        started.set()
        release.wait(5)

    future = runtime._export_executor.submit(blocked_export)
    runtime._export_future = future
    assert started.wait(2)
    runtime.stop(timeout=0.01)

    contender = ProcessFileLock(lock_path)
    assert contender.acquire(blocking=False) is False
    release.set()
    future.result(timeout=2)
    deadline = time.time() + 2
    while time.time() < deadline and not contender.acquire(blocking=False):
        time.sleep(0.01)
    assert contender.acquired is True
    contender.release()


def test_runtime_restart_interrupts_orphaned_export(runtime_project, tmp_path):
    task = TaskRecord(
        task_id="orphaned_export",
        task_type="export",
        project="book",
        status="running",
        owner_id="old-runtime",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:00:00Z",
    )
    outcome, _ = TaskRepository.create_runtime_task(task)
    assert outcome == "created"

    runtime = ProductionRuntime(
        owner_id="new-runtime",
        lock_path=str(tmp_path / "runtime.lock"),
        poll_interval=0.02,
    )
    assert runtime.start_background() is True
    try:
        deadline = time.time() + 2
        while time.time() < deadline:
            restored = TaskRepository.load_task(task.task_id)
            if restored is not None and restored.status == "interrupted":
                break
            time.sleep(0.01)
        restored = TaskRepository.load_task(task.task_id)
        assert restored is not None
        assert restored.status == "interrupted"
        assert not restored.control_intent
    finally:
        runtime.stop()


def test_runtime_takeover_interrupts_old_synthesis_and_blocks_stale_publish(
    runtime_project,
    tmp_path,
    monkeypatch,
):
    """A dead runtime's claimed task is interrupted on takeover and the
    stale owner can never publish over the new ownership (Test G)."""
    from lib import tts_engine
    from services.synthesis import SynthesisService

    monkeypatch.setattr(tts_engine, "init_engine", lambda: None)
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)
    started: list[str] = []

    def fake_start(state, *_args, **_kwargs):
        started.append(state.task_id)
        state.status = "running"
        state.notify()
        return state.task_id

    monkeypatch.setattr(SynthesisService, "start", staticmethod(fake_start))
    now = "2026-08-09T00:00:00Z"
    lock_path = str(tmp_path / "runtime.lock")
    old_task = TaskRecord(
        task_id="old_synthesis",
        task_type="synthesis",
        project="book",
        status="pending",
        source="mcp",
        scope={"all": True, "chapter_ids": [], "segment_ids": []},
        progress={"total": 1, "completed": 0, "failed": 0},
        created_at=now,
        updated_at=now,
    )
    outcome, _ = TaskRepository.create_production_task(old_task)
    assert outcome == "created"

    old_runtime = ProductionRuntime(
        owner_id="old-runtime",
        lock_path=lock_path,
        poll_interval=0.02,
    )
    assert old_runtime.start_background() is True
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            record = TaskRepository.load_task("old_synthesis")
            if record is not None and record.status == "running":
                break
            time.sleep(0.01)
        record = TaskRepository.load_task("old_synthesis")
        assert record is not None and record.status == "running"
        assert record.owner_id == "old-runtime"
        assert started == ["old_synthesis"]
    finally:
        old_runtime.stop()

    new_runtime = ProductionRuntime(
        owner_id="new-runtime",
        lock_path=lock_path,
        poll_interval=0.02,
    )
    assert new_runtime.start_background() is True
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            record = TaskRepository.load_task("old_synthesis")
            if record is not None and record.status == "interrupted":
                break
            time.sleep(0.01)
        assert record is not None and record.status == "interrupted"

        # The stale owner can still call its in-memory callbacks, but the
        # durable repository must refuse to publish over the interruption.
        stale_state = SynthesisState(
            task_id="old_synthesis",
            project="book",
            status="running",
            completed=1,
        )
        old_runtime._on_state_update(stale_state)
        record = TaskRepository.load_task("old_synthesis")
        assert record.status == "interrupted"
        assert record.progress["completed"] == 0

        new_task = TaskRecord(
            task_id="new_synthesis",
            task_type="synthesis",
            project="book",
            status="pending",
            source="mcp",
            scope={"all": True, "chapter_ids": [], "segment_ids": []},
            progress={"total": 1, "completed": 0, "failed": 0},
            created_at=now,
            updated_at=now,
        )
        outcome, _ = TaskRepository.create_production_task(new_task)
        assert outcome == "created"
        deadline = time.time() + 3
        while time.time() < deadline:
            record = TaskRepository.load_task("new_synthesis")
            if record is not None and record.status == "running":
                break
            time.sleep(0.01)
        record = TaskRepository.load_task("new_synthesis")
        assert record is not None and record.status == "running"
        assert record.owner_id == "new-runtime"
        assert started == ["old_synthesis", "new_synthesis"]
    finally:
        new_runtime.stop()


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
