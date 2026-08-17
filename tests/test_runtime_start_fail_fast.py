"""Runtime cold-start fail-fast（RUNTIME_START_FAILED）回归测试。

背景：``RuntimeTTSService._submit`` 创建 pending 任务后调用
``ProductionRuntimeClient.ensure_running()`` spawn runtime 子进程，然后
``_wait`` 最多等 3600s。若子进程 spawn 后立即退出（bootstrap 失败）且没有
worker claim 该 pending 任务，客户端可能一直等 pending 到超时。

修复：``ProductionRuntimeClient.confirm_started`` 在 spawn 后做**有限**
startup confirmation——数秒～30s 内等待任一成功信号（runtime status live /
heartbeat 新鲜 / 任务已被 claim / 精确 spawn 的 Popen 仍 alive 且 status 正在
建立）；失败信号（精确 spawn 的 poll() 已退出 且 任务仍 pending/unclaimed）
→ 立即抛 ``RUNTIME_START_FAILED``，不等待长 timeout。

关键区分：startup confirmation 只确认「worker 活着 / 拥有 runtime」，**不**
等待 ``engine_state=ready`` —— IndexTTS 2.5 冷加载数分钟不能被误判为启动失败。
"""
from __future__ import annotations
from lib import project_paths

import json
import os
import time
import uuid
from datetime import datetime, timezone

import pytest

from lib import config as lib_config
from lib import project_manager as pm
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord
from services import production_runtime as pr
from services.production_runtime import (
    ProductionRuntimeClient,
    RuntimeStartFailedError,
)
from services.runtime_tts import RuntimeTTSService

SCRIPT = {
    "meta": {"title": "FailFast"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


@pytest.fixture
def runtime_project(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
    ProjectRepository.create_project_from_data("book", SCRIPT)
    return data_dir


class FakeProc:
    """Popen 句柄替身：poll() 模拟子进程存活/退出。"""

    def __init__(self, pid: int, exit_code=None) -> None:
        self.pid = pid
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code

    @property
    def returncode(self):
        return self._exit_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_live_status(monkeypatch, tmp_path, *, runtime_state="running",
                       engine_state="uninitialized", pid=None) -> str:
    """Publish a fresh runtime_engine_status.json the runtime would have written."""
    data_dir = str(tmp_path / "status-data")
    monkeypatch.setattr(lib_config, "get_data_dir", lambda: data_dir)
    path = os.path.join(data_dir, "logs", "runtime_engine_status.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = _now_iso()
    json.dump({
        "state": engine_state,
        "engine_state": engine_state,
        "runtime_state": runtime_state,
        "pid": pid or os.getpid(),
        "owner_id": "test-runtime",
        "updated_at": now,
        "runtime_updated_at": now,
        "error_summary": "",
        "engine_generation": 1,
        "recovery_count": 0,
        "last_error_code": "",
        "last_recovery_at": "",
        "engine_backend": "indextts",
        "engine_version": "2.5",
        "engine_identity": "indextts:2.5",
        "model_identity": "fp-25",
        "precision": "BF16",
        "device": "cuda:0",
        "cache_identity": "indextts:2.5|fp-25|BF16",
    }, open(path, "w", encoding="utf-8"))
    return data_dir


def _done_record(task_id: str) -> TaskRecord:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return TaskRecord(
        task_id=task_id, task_type="supplement", project="book", status="done",
        artifact_dir="", source="web", scope={}, options={}, progress={},
        idempotency_key="k", created_at=now, updated_at=now,
    )


# ── Case A: spawn alive + runtime startup confirmed → 正常等待 ─────────────

def test_confirm_started_returns_when_task_claimed(runtime_project, monkeypatch):
    """任务已被 claim（owner_id 非空）→ 视为启动成功。"""
    from repositories.task_repo import TaskRepository

    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", FakeProc(pid=9001))
    task_id = f"task_claimed_{uuid.uuid4().hex[:8]}"
    record = TaskRecord(
        task_id=task_id, task_type="supplement", project="book", status="pending",
        artifact_dir="", source="web", scope={}, options={"lines": ["甲"]},
        progress={"total": 1, "completed": 0, "percent": 0.0},
        idempotency_key=f"key_{os.urandom(4).hex()}",
        created_at=_now_iso(), updated_at=_now_iso(),
    )
    TaskRepository.create_runtime_task(record)
    claimed = TaskRepository.claim_next_pending("runtime_A", {"supplement"}, force=True)
    assert claimed is not None and claimed.owner_id == "runtime_A"
    # 不写 status 文件 —— 仅凭任务被 claim 就应返回
    ProductionRuntimeClient.confirm_started(task_id, spawned_pid=9001)


def test_confirm_started_returns_when_status_live(runtime_project, monkeypatch, tmp_path):
    """runtime status live（starting/running）→ 视为启动成功，不等模型 ready。"""
    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", FakeProc(pid=9002))
    _write_live_status(monkeypatch, tmp_path, runtime_state="running",
                       engine_state="uninitialized")
    task_id = f"task_live_{uuid.uuid4().hex[:8]}"
    ProductionRuntimeClient.confirm_started(task_id, spawned_pid=9002)


def test_submit_waits_normally_when_spawn_confirmed(
    runtime_project, tmp_path, monkeypatch,
):
    """集成：ensure_running 返回 pid + confirm_started 成功 → 正常走 _wait。"""
    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", FakeProc(pid=9003))
    _write_live_status(monkeypatch, tmp_path, runtime_state="starting")
    monkeypatch.setattr(
        ProductionRuntimeClient, "ensure_running", staticmethod(lambda: 9003)
    )
    submitted: dict = {}

    def _fake_wait(cls, task_id, timeout, progress_cb=None):
        submitted["task_id"] = task_id
        return _done_record(task_id)

    monkeypatch.setattr(RuntimeTTSService, "_wait", classmethod(_fake_wait))
    artifact_dir = os.path.join(project_paths.project_dir(ProjectRepository.get_project_dir("book"), "cache", create=True), "sup")
    result = RuntimeTTSService._submit(
        project_name="book", task_type="supplement", artifact_dir=artifact_dir,
        options={"lines": ["甲"]}, total=1, timeout=30,
    )
    assert result.status == "done"
    assert submitted["task_id"]


# ── Case B: spawn 后立即退出 → 快速 RUNTIME_START_FAILED ───────────────────

def test_confirm_started_fails_fast_when_child_exited(runtime_project, monkeypatch):
    """精确 spawn 的 Popen poll() 已退出 + 任务仍 pending → 立即失败。"""
    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", FakeProc(pid=9100, exit_code=1))
    task_id = f"task_dead_{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    with pytest.raises(RuntimeStartFailedError) as captured:
        ProductionRuntimeClient.confirm_started(task_id, spawned_pid=9100)
    elapsed = time.monotonic() - started
    error = captured.value
    assert error.code == "RUNTIME_START_FAILED"
    assert error.task_id == task_id
    assert error.spawned_pid == 9100
    assert "production_runtime_bootstrap.log" in error.bootstrap_log
    assert "production_runtime.log" in error.runtime_log
    assert "task_id=" in str(error)
    assert "spawned_pid=9100" in str(error)
    # 不等待长 timeout：fail-fast 应远小于 5s
    assert elapsed < 5.0


def test_submit_fails_fast_with_runtime_start_failed(
    runtime_project, monkeypatch,
):
    """集成：ensure_running spawn 后子进程立即退出 → _submit 抛 RUNTIME_START_FAILED。"""
    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", FakeProc(pid=9101, exit_code=1))
    monkeypatch.setattr(
        ProductionRuntimeClient, "ensure_running", staticmethod(lambda: 9101)
    )
    artifact_dir = os.path.join(project_paths.project_dir(ProjectRepository.get_project_dir("book"), "cache", create=True), "sup")
    started = time.monotonic()
    with pytest.raises(RuntimeStartFailedError) as captured:
        RuntimeTTSService._submit(
            project_name="book", task_type="supplement", artifact_dir=artifact_dir,
            options={"lines": ["甲"]}, total=1, timeout=3600,
        )
    assert captured.value.code == "RUNTIME_START_FAILED"
    assert time.monotonic() - started < 5.0


# ── Case C: runtime 已存在 → 不走 child-exit 判断误伤已有 runtime ──────────

def test_submit_skips_confirmation_when_runtime_already_running(
    runtime_project, monkeypatch,
):
    """ensure_running 返回 None（runtime_already_running）→ 不调用 confirm_started。"""
    calls: list[str] = []
    monkeypatch.setattr(
        ProductionRuntimeClient, "ensure_running", staticmethod(lambda: None)
    )

    def _spy_confirm(cls, task_id, spawned_pid=None, timeout=30.0):
        calls.append(task_id)
        raise AssertionError("runtime 已存在时不应做 child-exit 判断")

    monkeypatch.setattr(ProductionRuntimeClient, "confirm_started", classmethod(_spy_confirm))

    def _fake_wait(cls, task_id, timeout, progress_cb=None):
        return _done_record(task_id)

    monkeypatch.setattr(RuntimeTTSService, "_wait", classmethod(_fake_wait))
    artifact_dir = os.path.join(project_paths.project_dir(ProjectRepository.get_project_dir("book"), "cache", create=True), "sup")
    result = RuntimeTTSService._submit(
        project_name="book", task_type="supplement", artifact_dir=artifact_dir,
        options={"lines": ["甲"]}, total=1, timeout=30,
    )
    assert result.status == "done"
    assert calls == []


# ── Case D: runtime 活着但引擎正在加载（2.5 冷加载数分钟）→ 不误报 ─────────

def test_confirm_started_does_not_wait_for_engine_ready(
    runtime_project, monkeypatch, tmp_path,
):
    """engine_state=loading 且进程 alive → 不抛启动失败（模型加载 ≠ 启动失败）。"""
    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", FakeProc(pid=9200))
    _write_live_status(monkeypatch, tmp_path, runtime_state="running",
                       engine_state="loading")
    task_id = f"task_loading_{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    ProductionRuntimeClient.confirm_started(task_id, spawned_pid=9200)
    assert time.monotonic() - started < 5.0


def test_confirm_started_keeps_waiting_while_child_alive_and_status_building(
    runtime_project, monkeypatch, tmp_path,
):
    """子进程仍 alive 且 status 正在建立（starting）→ 不误报、正常返回。"""
    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", FakeProc(pid=9201))
    _write_live_status(monkeypatch, tmp_path, runtime_state="starting",
                       engine_state="uninitialized")
    task_id = f"task_building_{uuid.uuid4().hex[:8]}"
    ProductionRuntimeClient.confirm_started(task_id, spawned_pid=9201)
