"""PHASE B — 进程内 Runtime 优雅停机测试（graceful / interrupted 语义）。

覆盖用户验收清单中的：
  2. active production → interrupted → exit
  3. user cancel ≠ application shutdown（cancelled vs interrupted）
  4. repeated shutdown 幂等
  6. shutdown during engine loading
 11. crash recovery 的 mark_orphaned 仍然有效

关键断言：应用关闭后，进行中的任务绝不能以持久化的 ``running`` / ``cancelling``
状态留到下次启动 —— 必须已经落库为 ``interrupted``。
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from lib import project_manager as pm
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services.production_runtime import ProductionRuntime
from services.runtime_engine import RuntimeEngineLifecycle
from services.synthesis import SynthesisService

SCRIPT = {
    "meta": {"title": "Runtime"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


@pytest.fixture
def runtime_project(tmp_path, monkeypatch):
    """隔离数据目录并建一个最小项目（与 test_production_runtime.py 同构）。"""
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
    ProjectRepository.create_project_from_data("book", SCRIPT)
    return data_dir


def _install_fake_synthesis(monkeypatch, *, segments: int = 3, step: float = 0.05) -> list[str]:
    """替换 SynthesisService.start 为轻量 worker（不加载 TTS 引擎）。

    段边界语义与真实 ``SynthesisService._run`` 保持一致：
    cancel 优先 → 置 ``cancelled`` 终态；shutdown → 直接返回、不置终态
    （由 Runtime 落 ``interrupted``）。
    """
    started: list[str] = []

    def fake_start(state, *args, **kwargs):
        state.status = "running"
        state.total = segments
        state.completed = 0
        state.shutdown_requested = False
        state.notify()
        started.append(state.task_id)

        def worker():
            for index in range(segments):
                time.sleep(step)
                if state.cancel:
                    state.append_log("⏹ 已停止（用户取消）")
                    state.status = "cancelled"
                    state.cancel_requested = True
                    state.notify()
                    return
                if state.shutdown_requested:
                    state.append_log("⏹ 应用关闭，合成在段边界中断")
                    state.notify()
                    return
                state.completed = index + 1
                state.notify()
            state.status = "done"
            state.notify()

        state.future = SynthesisService._executor.submit(worker)
        return state.task_id

    monkeypatch.setattr(SynthesisService, "start", staticmethod(fake_start))
    return started


def _wait_for_progress(task_id: str, completed: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = TaskRepository.load_task(task_id)
        if record is not None and int(record.progress.get("completed") or 0) >= completed:
            return True
        time.sleep(0.01)
    return False


def _wait_for_status(task_id: str, status: str, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = TaskRepository.load_task(task_id)
        if record is not None and record.status == status:
            return True
        time.sleep(0.01)
    return False


def _make_task(task_id: str, total: int = 3):
    now = "2026-08-09T00:00:00Z"
    return TaskRecord(
        task_id=task_id,
        task_type="synthesis",
        project="book",
        status="pending",
        source="mcp",
        scope={"all": True, "chapter_ids": [], "segment_ids": []},
        progress={"total": total, "completed": 0, "failed": 0},
        created_at=now,
        updated_at=now,
    )


def test_idle_runtime_graceful_shutdown(runtime_project, tmp_path):
    runtime = ProductionRuntime(
        owner_id="rt", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert runtime.start_background() is True
    assert runtime.is_running
    runtime.request_shutdown(reason="test", wait=True, timeout=5.0)
    assert not runtime.is_running


def test_active_production_interrupted_on_shutdown(runtime_project, tmp_path, monkeypatch):
    """Application Shutdown 必须落库 ``interrupted``（不是 cancelled），Runtime 退出，
    且已完成的进度被保留。"""
    monkeypatch.setattr(RuntimeEngineLifecycle, "ensure_ready", lambda self, profile=None: None)
    _install_fake_synthesis(monkeypatch, segments=10, step=0.05)

    TaskRepository.create_production_task(_make_task("act1", total=10))

    runtime = ProductionRuntime(
        owner_id="rt", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert runtime.start_background() is True
    try:
        assert _wait_for_status("act1", "running")
        # 等真实产生进度后再关，才能验证「进度被保存」而非恰好为 0。
        assert _wait_for_progress("act1", 1)
        before = int(TaskRepository.load_task("act1").progress["completed"])

        runtime.request_shutdown(reason="test", wait=True, timeout=10.0)

        record = TaskRepository.load_task("act1")
        assert record.status == "interrupted", "应用关闭必须落 interrupted"
        assert record.status != "cancelled", "应用关闭不得被记为用户取消"
        assert not record.control_intent, "终态必须清空 control_intent"
        assert int(record.progress["completed"]) >= before, "已完成进度必须保留"
        assert int(record.progress["completed"]) < 10, "应在段边界中断而非跑完"
        assert not runtime.is_running, "Runtime 必须自行退出（不留孤儿）"
    finally:
        if runtime.is_running:
            runtime.stop()


def test_interrupted_task_is_not_left_active_for_next_startup(
    runtime_project, tmp_path, monkeypatch
):
    """硬约束：应用关闭后，任务绝不能以 running / cancelling 等活跃态留到下次启动。"""
    monkeypatch.setattr(RuntimeEngineLifecycle, "ensure_ready", lambda self, profile=None: None)
    _install_fake_synthesis(monkeypatch, segments=10, step=0.05)

    TaskRepository.create_production_task(_make_task("live1", total=10))

    runtime = ProductionRuntime(
        owner_id="rt", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert runtime.start_background() is True
    try:
        assert _wait_for_status("live1", "running")
        runtime.request_shutdown(reason="test", wait=True, timeout=10.0)
    finally:
        if runtime.is_running:
            runtime.stop()

    record = TaskRepository.load_task("live1")
    assert record.status not in {
        "pending", "running", "pausing", "paused", "recovering", "cancelling",
    }, f"应用关闭后仍是活跃态：{record.status}"
    assert record.status == "interrupted"


def test_user_cancel_is_not_app_shutdown(runtime_project, tmp_path, monkeypatch):
    """User Cancel → ``cancelled``，且 Runtime 必须继续存活（回到 idle）。

    这是与 Application Shutdown 的语义分界：取消一个任务不等于关闭应用。
    """
    monkeypatch.setattr(RuntimeEngineLifecycle, "ensure_ready", lambda self, profile=None: None)
    _install_fake_synthesis(monkeypatch, segments=20, step=0.05)

    TaskRepository.create_production_task(_make_task("cancel1", total=20))

    runtime = ProductionRuntime(
        owner_id="rt", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert runtime.start_background() is True
    try:
        assert _wait_for_status("cancel1", "running")

        TaskRepository.request_control("cancel1", "cancel")
        assert _wait_for_status("cancel1", "cancelled"), "用户取消必须落 cancelled"

        record = TaskRepository.load_task("cancel1")
        assert record.status == "cancelled"
        assert record.status != "interrupted", "用户取消不得被记为应用中断"
        # Runtime 未被要求关闭 → 必须仍然存活可用
        assert runtime.is_running, "用户取消不得导致 Runtime 退出"
    finally:
        runtime.stop()


def test_applied_user_cancel_survives_app_shutdown(runtime_project, tmp_path, monkeypatch):
    """已生效的用户取消在应用关闭中被保留为 ``cancelled``，且 Runtime 仍会退出。

    为避免断言一个不确定的竞态胜者，这里先等取消真正作用到合成状态
    （``state.cancel``），再触发应用关闭 —— 此时 cancel 优先是确定的。
    """
    monkeypatch.setattr(RuntimeEngineLifecycle, "ensure_ready", lambda self, profile=None: None)
    _install_fake_synthesis(monkeypatch, segments=40, step=0.05)

    TaskRepository.create_production_task(_make_task("both1", total=40))

    runtime = ProductionRuntime(
        owner_id="rt", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert runtime.start_background() is True
    try:
        assert _wait_for_status("both1", "running")
        TaskRepository.request_control("both1", "cancel")

        deadline = time.time() + 5
        while time.time() < deadline:
            state = runtime._current_state
            if state is not None and state.cancel:
                break
            time.sleep(0.01)
        state = runtime._current_state
        assert state is not None and state.cancel, "取消未作用到合成状态"

        runtime.request_shutdown(reason="test", wait=True, timeout=10.0)

        record = TaskRepository.load_task("both1")
        assert record.status == "cancelled", "已生效的用户取消不应被改写为 interrupted"
        assert not record.control_intent
        assert not runtime.is_running, "即使以 cancelled 收尾，Runtime 仍必须退出"
    finally:
        if runtime.is_running:
            runtime.stop()


def test_repeated_shutdown_idempotent(runtime_project, tmp_path):
    runtime = ProductionRuntime(
        owner_id="rt", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert runtime.start_background() is True
    try:
        assert runtime.request_shutdown(wait=True, timeout=5.0) is True
        assert runtime.request_shutdown(wait=True, timeout=5.0) is True
        assert not runtime.is_running
    finally:
        if runtime.is_running:
            runtime.stop()


def test_request_shutdown_when_not_started_is_noop(tmp_path):
    runtime = ProductionRuntime(
        owner_id="rt", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    # not started → no-op, no error, returns True
    assert runtime.request_shutdown(wait=True, timeout=1.0) is True


def test_shutdown_during_engine_loading(runtime_project, tmp_path, monkeypatch):
    """A shutdown request arriving while the engine is loading must NOT start
    synthesis; the freshly-claimed task is interrupted and the runtime exits."""
    loading_started = threading.Event()

    def fake_ensure_ready(self, profile=None):
        loading_started.set()
        time.sleep(0.2)

    monkeypatch.setattr(RuntimeEngineLifecycle, "ensure_ready", fake_ensure_ready)

    start_called = []

    def fake_start(state, *args, **kwargs):
        start_called.append(state.task_id)
        state.status = "running"
        state.notify()
        return state.task_id

    monkeypatch.setattr(SynthesisService, "start", staticmethod(fake_start))

    TaskRepository.create_production_task(_make_task("load1"))

    runtime = ProductionRuntime(
        owner_id="rt", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert runtime.start_background() is True
    try:
        assert loading_started.wait(3)
        runtime.request_shutdown(reason="test", wait=True, timeout=5.0)
        record = TaskRepository.load_task("load1")
        assert record.status == "interrupted"
        assert start_called == []  # synthesis never started
        assert not runtime.is_running
    finally:
        if runtime.is_running:
            runtime.stop()


def test_crash_recovery_mark_orphaned_still_works(runtime_project, tmp_path, monkeypatch):
    """Unchanged crash-recovery: a dead owner's active task is interrupted on
    the next runtime takeover (must survive PHASE B changes)."""
    monkeypatch.setattr(RuntimeEngineLifecycle, "ensure_ready", lambda self, profile=None: None)

    def fake_start(state, *args, **kwargs):
        state.status = "running"
        state.notify()
        return state.task_id

    monkeypatch.setattr(SynthesisService, "start", staticmethod(fake_start))

    TaskRepository.create_production_task(_make_task("crash1"))

    old = ProductionRuntime(
        owner_id="old-runtime", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert old.start_background() is True
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            record = TaskRepository.load_task("crash1")
            if record is not None and record.status == "running":
                break
            time.sleep(0.01)
        assert TaskRepository.load_task("crash1").status == "running"
    finally:
        old.stop()

    new = ProductionRuntime(
        owner_id="new-runtime", lock_path=str(tmp_path / "runtime.lock"), poll_interval=0.02
    )
    assert new.start_background() is True
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            record = TaskRepository.load_task("crash1")
            if record is not None and record.status == "interrupted":
                break
            time.sleep(0.01)
        assert TaskRepository.load_task("crash1").status == "interrupted"
    finally:
        new.stop()
