"""Runtime / TaskRepository hot-path optimization regression tests.

覆盖 5 个用户要求的行为（不做依赖 <0.30s 的脆弱 wall-clock 断言，优先验证
调用次数 / 状态 / 同步语义）：

1. known project task lookup does not scan unrelated projects
   （load_project_task 只连接目标项目）
2. active runtime control poll does not scan unrelated project DBs
   （_apply_control 走 project-local）
3. schema ensure executes once per DB lifecycle, not every connection
   （同一 path 多次 _connect(create=True) 只执行一次重路径；DROP 后自愈）
4. heartbeat updates owned active task without scanning unrelated projects
   （projects 参数只连指定项目）
5. pending task claim still wakes promptly
   （claim_next_pending 无 signal 行为不变 + signal 门控在 notify 后能发现）

注意：``_SCHEMA_CACHE`` 是 per-process 全局缓存，用例间用
``TaskRepository.reset_schema_cache()`` 隔离（fixture 前后各一次）。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import pytest

from lib import project_manager as pm
from repositories.project_repo import ProjectRepository
from repositories.task_repo import (
    RuntimePendingSignal,
    TaskRecord,
    TaskRepository,
)
from services.production_runtime import ProductionRuntime
from services.synthesis import SynthesisState

SCRIPT = {
    "meta": {"title": "Hotpath"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def hotpath_roots(tmp_path, monkeypatch):
    """重定向项目根 + 清空 per-process schema 缓存（隔离）。"""
    TaskRepository.reset_schema_cache()
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
    yield
    TaskRepository.reset_schema_cache()


class TestLoadProjectTask:
    def test_known_project_lookup_does_not_scan_unrelated_projects(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """load_project_task 只连接目标项目；未知 id 也只连接目标项目。"""
        ProjectRepository.create_project_from_data("alpha", SCRIPT)
        ProjectRepository.create_project_from_data("beta", SCRIPT)
        TaskRepository.save_task(
            TaskRecord("task-alpha", "synthesis", "alpha", "running")
        )
        TaskRepository.save_task(
            TaskRecord("task-beta", "synthesis", "beta", "running")
        )

        calls: list[tuple[str, bool]] = []
        original_connect = TaskRepository._connect

        def counting_connect(project, *, create=True):
            calls.append((str(project), bool(create)))
            return original_connect(project, create=create)

        monkeypatch.setattr(
            TaskRepository, "_connect", staticmethod(counting_connect)
        )

        loaded = TaskRepository.load_project_task("alpha", "task-alpha")
        assert loaded is not None
        assert loaded.task_id == "task-alpha"
        # 只连了一次 alpha（create=False），从未触碰 beta。
        assert calls == [("alpha", False)]

        calls.clear()
        assert TaskRepository.load_project_task("alpha", "missing") is None
        assert calls == [("alpha", False)]

    def test_load_project_task_missing_project_returns_none(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """不存在的项目 / DB 直接返回 None，不产生副作用（不建库）。"""
        calls: list[tuple[str, bool]] = []
        original_connect = TaskRepository._connect

        def counting_connect(project, *, create=True):
            calls.append((str(project), bool(create)))
            return original_connect(project, create=create)

        monkeypatch.setattr(
            TaskRepository, "_connect", staticmethod(counting_connect)
        )
        assert TaskRepository.load_project_task("no_such_project", "x") is None
        assert calls == [("no_such_project", False)]


class TestApplyControlProjectLocal:
    def test_active_runtime_control_poll_does_not_scan_unrelated_dbs(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """_apply_control 走 load_project_task(state.project, task_id)，
        不再调用全库扫描的 load_task。"""
        ProjectRepository.create_project_from_data("ctl_book", SCRIPT)
        TaskRepository.save_task(TaskRecord(
            task_id="ctl-task",
            task_type="synthesis",
            project="ctl_book",
            status="running",
            owner_id="owner-ctl",
            created_at="2026-08-09T00:00:00Z",
            updated_at="2026-08-09T00:00:00Z",
        ))

        runtime = ProductionRuntime(
            lock_path=str(tmp_path / "runtime.lock"),
            poll_interval=0.02,
        )
        runtime.owner_id = "owner-ctl"
        state = SynthesisState(
            task_id="ctl-task",
            project="ctl_book",
            status="running",
        )

        calls: list[tuple] = []
        original_local = TaskRepository.load_project_task
        original_global = TaskRepository.load_task

        def counting_local(project, task_id):
            calls.append(("project", str(project), str(task_id)))
            return original_local(project, task_id)

        def counting_global(task_id):
            calls.append(("global", str(task_id)))
            return original_global(task_id)

        monkeypatch.setattr(
            TaskRepository, "load_project_task", staticmethod(counting_local)
        )
        monkeypatch.setattr(
            TaskRepository, "load_task", staticmethod(counting_global)
        )

        runtime._apply_control(state)
        # 只走 project-local 一次；全局 load_task 完全不被调用。
        assert calls == [("project", "ctl_book", "ctl-task")]
        runtime.stop(timeout=0.0)


class TestSchemaOnce:
    def test_schema_ensure_executes_once_per_db_lifecycle(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """同一 path 多次 _connect(create=True) 只执行一次重路径；
        DROP TABLE 后探针失败 → 下次连接自愈重建。"""
        ProjectRepository.create_project_from_data("schema_book", SCRIPT)

        heavy_runs: list[int] = []
        original_ensure = TaskRepository._ensure_schema

        def counting_ensure(connection):
            heavy_runs.append(1)
            return original_ensure(connection)

        monkeypatch.setattr(
            TaskRepository, "_ensure_schema", staticmethod(counting_ensure)
        )

        # 5 次连接只应触发 1 次重路径。
        for _ in range(5):
            conn = TaskRepository._connect("schema_book", create=True)
            conn.close()
        assert len(heavy_runs) == 1

        # schema 版本标记已写入。
        conn = TaskRepository._connect("schema_book", create=True)
        try:
            row = conn.execute(
                "SELECT value FROM repository_meta WHERE key='schema_version'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row["value"] == "2"

        # 外部 DROP 表（模拟迁移测试 / restore 场景）→ 探针失败 → 重路径自愈。
        db_path = TaskRepository.get_database_path("schema_book", create=False)
        assert db_path and os.path.isfile(db_path)
        raw = sqlite3.connect(db_path)
        raw.execute("DROP TABLE production_tasks")
        raw.commit()
        raw.close()

        conn = TaskRepository._connect("schema_book", create=True)
        try:
            columns = {
                str(item["name"])
                for item in conn.execute("PRAGMA table_info(production_tasks)")
            }
        finally:
            conn.close()
        assert "startup_json" in columns
        assert len(heavy_runs) == 2

    def test_reset_schema_cache_forces_heavy_path_again(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """reset_schema_cache() 后同一 path 会重新执行一次重路径（测试钩子）。"""
        ProjectRepository.create_project_from_data("cache_book", SCRIPT)

        heavy_runs: list[int] = []
        original_ensure = TaskRepository._ensure_schema

        def counting_ensure(connection):
            heavy_runs.append(1)
            return original_ensure(connection)

        monkeypatch.setattr(
            TaskRepository, "_ensure_schema", staticmethod(counting_ensure)
        )

        conn = TaskRepository._connect("cache_book", create=True)
        conn.close()
        assert len(heavy_runs) == 1

        TaskRepository.reset_schema_cache()
        conn = TaskRepository._connect("cache_book", create=True)
        conn.close()
        assert len(heavy_runs) == 2


class TestHeartbeatProjects:
    def test_heartbeat_projects_updates_owned_without_scanning_unrelated(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """update_runtime_heartbeat(projects=[...]) 只连接指定项目；未指定项目
        不被触碰；非 owner 行不被更新。"""
        ProjectRepository.create_project_from_data("hb_a", SCRIPT)
        ProjectRepository.create_project_from_data("hb_b", SCRIPT)
        ProjectRepository.create_project_from_data("hb_c", SCRIPT)
        TaskRepository.save_task(TaskRecord(
            "task-a", "synthesis", "hb_a", "running", owner_id="owner-hb"
        ))
        TaskRepository.save_task(TaskRecord(
            "task-b", "synthesis", "hb_b", "running", owner_id="owner-hb"
        ))
        TaskRepository.save_task(TaskRecord(
            "task-c", "synthesis", "hb_c", "running", owner_id="other-owner"
        ))

        calls: list[str] = []
        original_connect = TaskRepository._connect

        def counting_connect(project, *, create=True):
            calls.append(str(project))
            return original_connect(project, create=create)

        monkeypatch.setattr(
            TaskRepository, "_connect", staticmethod(counting_connect)
        )

        TaskRepository.update_runtime_heartbeat("owner-hb", projects=["hb_a"])
        assert calls == ["hb_a"]

        task_a = TaskRepository.load_project_task("hb_a", "task-a")
        assert task_a is not None and task_a.heartbeat_at
        # hb_b / hb_c 从未连接 → 其行未更新。
        task_b = TaskRepository.load_project_task("hb_b", "task-b")
        assert task_b is not None and not task_b.heartbeat_at
        task_c = TaskRepository.load_project_task("hb_c", "task-c")
        assert task_c is not None and not task_c.heartbeat_at

        # projects=None → 保持全扫（默认兼容路径），仍只更新 owner 行。
        calls.clear()
        TaskRepository.update_runtime_heartbeat("owner-hb")
        assert set(calls) == {"hb_a", "hb_b", "hb_c"}
        task_a = TaskRepository.load_project_task("hb_a", "task-a")
        assert task_a.heartbeat_at
        task_b = TaskRepository.load_project_task("hb_b", "task-b")
        assert task_b.heartbeat_at
        task_c = TaskRepository.load_project_task("hb_c", "task-c")
        assert not task_c.heartbeat_at


class TestClaimSignalGate:
    def test_claim_without_signal_unchanged_and_signal_gate_wakes(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """无 signal → claim 行为不变；signal 门控在 notify 后能发现新任务。"""
        ProjectRepository.create_project_from_data("signal_book", SCRIPT)
        ProjectRepository.create_project_from_data("signal_book2", SCRIPT)

        # 1) 无 signal：权威全扫，行为与现状完全一致。
        t1 = TaskRecord(
            task_id="sig-1",
            task_type="synthesis",
            project="signal_book",
            status="pending",
            created_at=_now(),
            updated_at=_now(),
        )
        outcome, _ = TaskRepository.create_production_task(t1)
        assert outcome == "created"
        claimed = TaskRepository.claim_next_pending("owner-plain", {"synthesis"})
        assert claimed is not None and claimed.task_id == "sig-1"

        # 2) signal 门控：无 fresh 标记 → 不扫描直接返回 None。
        t2 = TaskRecord(
            task_id="sig-2",
            task_type="synthesis",
            project="signal_book2",
            status="pending",
            created_at=_now(),
            updated_at=_now(),
        )
        outcome, _ = TaskRepository.create_production_task(t2)
        assert outcome == "created"

        signal_path = str(tmp_path / "runtime_pending.signal")
        signal = RuntimePendingSignal(path=signal_path)

        scanned: list[int] = []
        original_list = TaskRepository.list_tasks

        def counting_list(*args, **kwargs):
            scanned.append(1)
            return original_list(*args, **kwargs)

        monkeypatch.setattr(
            TaskRepository, "list_tasks", staticmethod(counting_list)
        )
        assert TaskRepository.claim_next_pending(
            "owner-gated", {"synthesis"}, signal=signal
        ) is None
        assert scanned == []

        # 3) notify()（原子写）→ 同一 signal 实例下一次 stat 判定 fresh → claim。
        monkeypatch.setattr(
            RuntimePendingSignal,
            "default_path",
            staticmethod(lambda: signal_path),
        )
        RuntimePendingSignal.notify()
        # claim 内部消费 fresh 标记（不再先手动 may_have_pending，避免提前消费）。
        gated = TaskRepository.claim_next_pending(
            "owner-gated", {"synthesis"}, signal=signal
        )
        assert gated is not None and gated.task_id == "sig-2"
        assert len(scanned) == 1
        # claim 后信号已消费（mtime 变化检测，不重复扫描）。
        assert signal.may_have_pending() is False

    def test_claim_signal_force_bypasses_gate(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """force=True 忽略信号门，执行权威全扫（30s 兜底路径）。"""
        ProjectRepository.create_project_from_data("force_book", SCRIPT)
        record = TaskRecord(
            task_id="force-1",
            task_type="synthesis",
            project="force_book",
            status="pending",
            created_at=_now(),
            updated_at=_now(),
        )
        TaskRepository.create_production_task(record)

        signal = RuntimePendingSignal(path=str(tmp_path / "never_written.signal"))
        claimed = TaskRepository.claim_next_pending(
            "owner-force", {"synthesis"}, signal=signal, force=True
        )
        assert claimed is not None and claimed.task_id == "force-1"


class TestPerClaimTypeStamp:
    def test_claim_pending_dedups_per_type_and_rescans_on_new_notify(
        self,
        hotpath_roots,
        tmp_path,
        monkeypatch,
    ):
        """P1 回归：合成活跃 + 他项目 pending 时，per-type stamp 去重避免每 tick 全扫；
        新 notify 后（含 retire 场景）立即全扫，无 30s 兜底延迟。"""
        signal_path = str(tmp_path / "runtime_pending.signal")
        monkeypatch.setattr(
            RuntimePendingSignal,
            "default_path",
            staticmethod(lambda: signal_path),
        )
        ProjectRepository.create_project_from_data("stamp_a", SCRIPT)
        ProjectRepository.create_project_from_data("stamp_b", SCRIPT)
        now = _now()
        TaskRepository.save_task(TaskRecord(
            "s1", "synthesis", "stamp_a", "pending", created_at=now, updated_at=now,
        ))
        TaskRepository.save_task(TaskRecord(
            "s2", "synthesis", "stamp_b", "pending", created_at=now, updated_at=now,
        ))

        runtime = ProductionRuntime(
            lock_path=str(tmp_path / "runtime.lock"),
            poll_interval=0.02,
        )
        scanned: list[int] = []
        original_claim = TaskRepository.claim_next_pending

        def counting_claim(*args, **kwargs):
            scanned.append(1)
            return original_claim(*args, **kwargs)

        monkeypatch.setattr(
            TaskRepository, "claim_next_pending", staticmethod(counting_claim)
        )

        # 第一轮：synthesis 类型 stamp 未记录 → 权威全扫一次并 claim 最早任务。
        first = runtime._claim_pending("synthesis", {"synthesis"}, force=False)
        assert first is not None and first.task_id == "s1"
        assert len(scanned) == 1
        # 同一信号戳再次调用同类型 → 去重跳过（不再全扫）。这正是“合成活跃期间
        # export claim 每 tick 全库扫描”缺陷的修复点。
        assert runtime._claim_pending("synthesis", {"synthesis"}, force=False) is None
        assert len(scanned) == 1
        # 他项目新任务 notify → 新戳 → 立即全扫 claim（retire 后及时接管）。
        RuntimePendingSignal.notify()
        second = runtime._claim_pending("synthesis", {"synthesis"}, force=False)
        assert second is not None and second.task_id == "s2"
        assert len(scanned) == 2
        # force=True 绕过戳去重（30s 兜底路径），无候选返回 None。
        assert runtime._claim_pending("synthesis", {"synthesis"}, force=True) is None
        assert len(scanned) == 3
        runtime.stop(timeout=0.0)


class TestWakeWiring:
    def test_poke_sets_wake_event(self, tmp_path):
        """poke() 置 _wake：inline 模式提交后立即唤醒 idle 轮询。"""
        runtime = ProductionRuntime(
            lock_path=str(tmp_path / "runtime.lock"),
            poll_interval=0.02,
        )
        runtime._wake.clear()
        runtime.poke()
        assert runtime._wake.is_set()
        runtime.stop(timeout=0.0)

    def test_stop_sets_wake_event(self, tmp_path):
        """stop() 置 _wake：idle 1s 等待不会拖延 stop。"""
        runtime = ProductionRuntime(
            lock_path=str(tmp_path / "runtime.lock"),
            poll_interval=0.02,
        )
        runtime._wake.clear()
        runtime.stop(timeout=0.0)
        assert runtime._wake.is_set()
