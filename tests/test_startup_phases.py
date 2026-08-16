"""Production startup phase machine tests.

覆盖：
- lib/procutil：Windows CREATE_NO_WINDOW 助手（黑框修复的原子单元）
- lib/startup：enrich 诊断（elapsed / slow / 布尔标志）
- repositories/task_repo：startup 持久化、schema 迁移、update_startup owner 守卫
- services/production_jobs：start() 写入 task_submitted → runtime_starting，snapshot 富化
- services/production_runtime：claim 阶段、engine init 失败 → engine_failed、
  ensure_running 单飞（锁占用时不重复 spawn）+ bootstrap 日志
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lib import project_manager as pm
from lib import procutil
from lib.procutil import no_window_kwargs, run_no_window
from lib.startup import enrich
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import ProductionJobService, ProjectService
from services.production_runtime import (
    ProductionRuntime,
    ProductionRuntimeClient,
    _open_bootstrap_log,
)
from services.runtime_engine import EngineInitError
from services.synthesis import SynthesisState

SCRIPT = {
    "meta": {"title": "启动阶段测试"},
    "voices": {"旁白": {}},
    "chapters": [
        {
            "id": "001",
            "title": "第一章",
            "segments": [
                {"id": "001-001", "role": "旁白", "text": "一"},
                {"id": "001-002", "role": "旁白", "text": "二"},
            ],
        },
    ],
}


@pytest.fixture
def production_project(tmp_path, monkeypatch):
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
    monkeypatch.setattr(
        TaskRepository,
        "get_task_dir",
        staticmethod(lambda: str(tmp_path / "task_records")),
    )
    ProjectService.create_project_from_data("startup", SCRIPT)
    project_dir = ProjectRepository.get_project_dir("startup")
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
    ProductionJobService.reset_runtime()
    yield "startup"
    ProductionJobService.reset_runtime()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ────────────────────────────────────────────────────────────────────────────
# lib/procutil —— 黑框修复的原子单元
# ────────────────────────────────────────────────────────────────────────────


class TestProcutil:
    # CREATE_NO_WINDOW 是 Windows 专用常量；Linux 的 subprocess 没有该属性。
    # 测试在强制模拟 Windows 时注入同值（0x08000000），保证 Linux CI 可测。
    _CNW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    def test_no_window_kwargs_adds_create_no_window_on_nt(self, monkeypatch):
        monkeypatch.setattr(procutil, "_is_windows", lambda: True)
        monkeypatch.setattr(procutil.subprocess, "CREATE_NO_WINDOW", self._CNW, raising=False)
        kw = no_window_kwargs()
        assert kw["creationflags"] & self._CNW

    def test_no_window_kwargs_merges_existing_flags_on_nt(self, monkeypatch):
        monkeypatch.setattr(procutil, "_is_windows", lambda: True)
        monkeypatch.setattr(procutil.subprocess, "CREATE_NO_WINDOW", self._CNW, raising=False)
        kw = no_window_kwargs(creationflags=0x200)
        assert kw["creationflags"] & 0x200
        assert kw["creationflags"] & self._CNW

    def test_no_window_kwargs_unchanged_on_posix(self, monkeypatch):
        monkeypatch.setattr(procutil, "_is_windows", lambda: False)
        assert no_window_kwargs() == {}
        assert no_window_kwargs(creationflags=1) == {"creationflags": 1}

    def test_run_no_window_forwards_create_no_window(self, monkeypatch):
        monkeypatch.setattr(procutil, "_is_windows", lambda: True)
        monkeypatch.setattr(procutil.subprocess, "CREATE_NO_WINDOW", self._CNW, raising=False)
        captured: dict = {}

        def fake_run(*_args, **_kwargs):
            captured.update(_kwargs)
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(procutil.subprocess, "run", fake_run)
        run_no_window(["ffmpeg", "-version"], capture_output=True, text=True)
        assert captured["capture_output"] is True
        assert captured["creationflags"] & self._CNW


# ────────────────────────────────────────────────────────────────────────────
# lib/startup —— enrich 诊断
# ────────────────────────────────────────────────────────────────────────────


class TestStartupEnrich:
    def test_elapsed_and_slow_diagnostic(self):
        started = (
            datetime.now(timezone.utc) - timedelta(seconds=130)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        out = enrich({"phase": "engine_loading", "phase_started_at": started})
        assert out["startup_phase"] == "engine_loading"
        assert out["startup_phase_started_at"] == started
        assert out["startup_phase_elapsed_seconds"] is not None
        assert out["startup_phase_elapsed_seconds"] > 120
        assert out["startup_slow"] is True

    def test_running_phase_never_slow(self):
        started = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        out = enrich({"phase": "running", "phase_started_at": started})
        assert out["startup_phase_elapsed_seconds"] > 100
        assert out["startup_slow"] is False

    def test_boolean_flags(self):
        out = enrich({
            "claimed_at": "t1",
            "first_segment_started_at": "t2",
            "first_audio_ready_at": "t3",
        })
        assert out["task_claimed"] is True
        assert out["first_segment_started"] is True
        assert out["first_audio_ready"] is True

    def test_empty_startup_defaults(self):
        out = enrich({})
        assert out["startup_phase"] == ""
        assert out["startup_slow"] is False
        assert out["task_claimed"] is False


# ────────────────────────────────────────────────────────────────────────────
# repositories/task_repo —— 持久化 + 迁移 + owner 守卫
# ────────────────────────────────────────────────────────────────────────────


class TestTaskStartupPersistence:
    def test_task_record_roundtrip_preserves_startup(self):
        record = TaskRecord(
            task_id="t1",
            task_type="synthesis",
            project="p",
            status="pending",
            startup={"phase": "engine_loading", "engine_load_started_at": "now"},
        )
        restored = TaskRecord.from_dict(record.to_dict())
        assert restored.startup["phase"] == "engine_loading"
        assert restored.startup["engine_load_started_at"] == "now"

    def test_create_persists_startup_and_update_merge(self, production_project):
        record = TaskRecord(
            task_id=f"task_{uuid.uuid4().hex[:12]}",
            task_type="synthesis",
            project=production_project,
            status="pending",
            created_at=_now(),
            updated_at=_now(),
            startup={"phase": "task_submitted", "phase_started_at": _now(), "submitted_at": _now()},
        )
        outcome, _ = TaskRepository.create_production_task(record)
        assert outcome == "created"
        # 真实 claim 路径（claim_next_pending 写 owner_id）
        claimed = TaskRepository.claim_next_pending("runtime_A")
        assert claimed is not None and claimed.task_id == record.task_id

        advanced = TaskRepository.update_startup_phase(
            record.task_id,
            "task_claimed",
            owner_id="runtime_A",
            claimed_at="t1",
            runtime_available_at="t0",
        )
        assert advanced.startup["phase"] == "task_claimed"
        assert advanced.startup["claimed_at"] == "t1"
        assert advanced.startup["runtime_available_at"] == "t0"
        assert advanced.startup["submitted_at"]  # 既有字段保留

        # 错误 owner 的写入被丢弃
        intruder = TaskRepository.update_startup(
            record.task_id, owner_id="runtime_B", phase="hacked"
        )
        assert intruder.startup["phase"] == "task_claimed"

        # claim 后 client（owner 为空）写入被丢弃
        client = TaskRepository.update_startup(
            record.task_id, owner_id="", phase="task_submitted"
        )
        assert client.startup["phase"] == "task_claimed"

        # 落库后读取一致
        assert TaskRepository.load_task(record.task_id).startup["phase"] == "task_claimed"

    def test_schema_migration_adds_startup_column(self, production_project, tmp_path, monkeypatch):
        """旧库（无 startup_json 列）在 _connect(create=True) 时自动迁移。"""
        import sqlite3

        # 先建库（_connect 触发建库 + 迁移）
        conn0 = TaskRepository._connect(production_project, create=True)
        conn0.close()
        db_path = TaskRepository.get_database_path(production_project, create=False)
        assert db_path and os.path.isfile(db_path)
        # 模拟旧 schema：删列等价重建一张旧表
        connection = sqlite3.connect(db_path)
        connection.execute("DROP TABLE production_tasks")
        connection.execute(
            """
            CREATE TABLE production_tasks (
                task_id TEXT PRIMARY KEY, task_type TEXT NOT NULL,
                project TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL,
                scope_json TEXT NOT NULL, options_json TEXT NOT NULL,
                progress_json TEXT NOT NULL, failed_segment_ids_json TEXT NOT NULL,
                attempt INTEGER NOT NULL, idempotency_key TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '', finished_at TEXT NOT NULL DEFAULT '',
                parent_task_id TEXT NOT NULL DEFAULT '', recovery_of TEXT NOT NULL DEFAULT '',
                artifact_dir TEXT NOT NULL DEFAULT '', error_summary TEXT NOT NULL DEFAULT '',
                owner_id TEXT NOT NULL DEFAULT '', heartbeat_at TEXT NOT NULL DEFAULT '',
                control_intent TEXT NOT NULL DEFAULT '', log_lines_json TEXT NOT NULL DEFAULT '[]',
                version INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.commit()
        connection.close()

        # 重新连接会触发 _ensure_schema 迁移
        conn = TaskRepository._connect(production_project, create=True)
        try:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(production_tasks)")
            }
        finally:
            conn.close()
        assert "startup_json" in columns


# ────────────────────────────────────────────────────────────────────────────
# services/production_jobs —— start() 阶段 + snapshot 富化
# ────────────────────────────────────────────────────────────────────────────


class TestJobsStartup:
    def test_start_records_submit_and_runtime_starting(self, production_project, monkeypatch):
        monkeypatch.setattr(
            ProductionRuntimeClient,
            "ensure_running",
            staticmethod(lambda: None),
        )
        result = ProductionJobService.start(production_project)
        assert result["created"] is True
        startup = result["startup"]
        assert startup["startup_phase"] in {"runtime_starting", "task_submitted"}
        assert startup["submitted_at"]
        assert startup["startup_phase_elapsed_seconds"] is not None
        assert startup["task_claimed"] is False

    def test_snapshot_includes_enriched_startup(self, production_project, monkeypatch):
        monkeypatch.setattr(
            ProductionRuntimeClient,
            "ensure_running",
            staticmethod(lambda: None),
        )
        result = ProductionJobService.start(production_project)
        snapshot = ProductionJobService.get_task_snapshot(result["task_id"])
        assert snapshot["startup"]["startup_phase"]
        assert "startup_phase_elapsed_seconds" in snapshot["startup"]
        assert "startup_diagnostics" in snapshot["startup"]


# ────────────────────────────────────────────────────────────────────────────
# services/production_runtime —— claim 阶段 / engine 失败 / 单飞 spawn
# ────────────────────────────────────────────────────────────────────────────


class TestRuntimeStartup:
    def _claimed_record(self, production_project, owner_id="runtime_A"):
        record = TaskRecord(
            task_id=f"task_{uuid.uuid4().hex[:12]}",
            task_type="synthesis",
            project=production_project,
            status="pending",
            created_at=_now(),
            updated_at=_now(),
            startup={"phase": "task_submitted", "phase_started_at": _now(), "submitted_at": _now()},
        )
        TaskRepository.create_production_task(record)
        claimed = TaskRepository.claim_next_pending(owner_id)
        assert claimed is not None and claimed.task_id == record.task_id
        return claimed

    def _runtime(self, tmp_path, owner_id="runtime_A"):
        runtime = ProductionRuntime(
            lock_path=str(tmp_path / "runtime.lock"),
            status_path=str(tmp_path / "engine_status.json"),
        )
        runtime.owner_id = owner_id
        return runtime

    def test_note_task_claim_persists_phase(self, production_project, tmp_path):
        record = self._claimed_record(production_project)
        runtime = self._runtime(tmp_path)
        runtime._note_task_claim(record)
        fresh = TaskRepository.load_task(record.task_id)
        assert fresh.startup["phase"] == "task_claimed"
        assert fresh.startup["claimed_at"]

    def test_engine_init_failure_persists_engine_failed(self, production_project, tmp_path):
        record = self._claimed_record(production_project)
        runtime = self._runtime(tmp_path)
        state = SynthesisState(
            task_id=record.task_id,
            project=production_project,
            status="pending",
            total=2,
            completed=0,
        )
        exc = EngineInitError("模型加载失败", original_exception=RuntimeError("boom"))
        runtime._fail_synthesis_engine_init(record, state, exc)
        fresh = TaskRepository.load_task(record.task_id)
        assert fresh.status == "error"
        assert fresh.startup["phase"] == "engine_failed"
        assert fresh.startup["engine_error_code"] == "TTS_ENGINE_INIT_FAILED"
        assert fresh.error_summary

    def test_ensure_running_skips_spawn_when_lock_held(self, monkeypatch):
        from services.runtime_lock import ProcessFileLock

        monkeypatch.setattr(
            ProductionRuntimeClient, "mode", staticmethod(lambda: "process")
        )
        monkeypatch.setattr(
            ProcessFileLock, "acquire", lambda self, blocking=False: False
        )
        assert ProductionRuntimeClient.ensure_running() is None

    def test_ensure_running_spawns_with_detached_and_bootstrap(self, monkeypatch):
        from services.runtime_lock import ProcessFileLock

        monkeypatch.setattr(
            ProductionRuntimeClient, "mode", staticmethod(lambda: "process")
        )
        monkeypatch.setattr(
            ProcessFileLock, "acquire", lambda self, blocking=False: True
        )
        monkeypatch.setattr(ProcessFileLock, "release", lambda self: None)
        # 固定为默认启动命令，避免 uv stub 解析干扰本断言
        monkeypatch.setattr(
            ProductionRuntimeClient,
            "_resolve_runtime_launch",
            staticmethod(lambda: (
                [sys.executable, "-m", "services.production_runtime", "--serve"], {},
            )),
        )
        calls: list[tuple] = []

        def fake_popen(command, **_kwargs):
            calls.append((command, _kwargs))
            return SimpleNamespace(pid=4242)

        monkeypatch.setattr(
            "services.production_runtime.subprocess.Popen", fake_popen
        )
        monkeypatch.setattr(
            "services.production_runtime._open_bootstrap_log", lambda: None
        )
        pid = ProductionRuntimeClient.ensure_running()
        assert pid == 4242
        command, kwargs = calls[0]
        assert command[-2:] == ["services.production_runtime", "--serve"]
        # bootstrap 日志不可用时回退 DEVNULL（无日志 ≠ 无控制台，stderr 不丢到窗口）
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["stdin"] is subprocess.DEVNULL

    def test_resolve_runtime_launch_falls_back_without_pyvenv_cfg(self, monkeypatch, tmp_path):
        monkeypatch.setattr("services.production_runtime._is_windows", lambda: True)
        fake_stub = tmp_path / "python.exe"
        fake_stub.write_bytes(b"x" * 40960)  # 45KB stub 尺寸
        monkeypatch.setattr(
            "services.production_runtime.sys.executable",
            str(fake_stub),
        )
        command, env = ProductionRuntimeClient._resolve_runtime_launch()
        assert command[0] == str(fake_stub)
        assert command[1:] == ["-m", "services.production_runtime", "--serve"]
        assert env == {}

    def test_resolve_runtime_launch_uses_real_interpreter_for_uv_stub(self, monkeypatch, tmp_path):
        # uv-managed venv stub：即使尺寸 >100KB（实测 uv 0.12.x stub 为 256KB），
        # pyvenv.cfg 存在 ``uv =`` 字段 → 判定为 uv stub → 绕行到 home 真实解释器。
        monkeypatch.setattr("services.production_runtime._is_windows", lambda: True)
        venv_dir = tmp_path / "venv"
        scripts = venv_dir / "Scripts"
        scripts.mkdir(parents=True)
        site_pkgs = venv_dir / "Lib" / "site-packages"
        site_pkgs.mkdir(parents=True)
        stub = scripts / "python.exe"
        stub.write_bytes(b"x" * 262144)  # uv stub 特征尺寸（>100KB 仍须绕行）
        base_dir = tmp_path / "base_python"
        base_dir.mkdir()
        base_py = base_dir / "python.exe"
        base_py.write_bytes(b"x" * 200000)
        (venv_dir / "pyvenv.cfg").write_text(
            "home = %s\nimplementation = CPython\nuv = 0.12.3\n" % str(base_dir),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "services.production_runtime.sys.executable",
            str(stub),
        )
        command, env = ProductionRuntimeClient._resolve_runtime_launch()
        assert command[0] == str(base_py)
        assert command[1] == "-c"
        assert "runpy.run_module('services.production_runtime'" in command[2]
        assert "site-packages" in command[2]
        assert env == {}

    def test_resolve_runtime_launch_keeps_venv_python_for_standard_venv(self, monkeypatch, tmp_path):
        # 标准 venv（python -m venv，pyvenv.cfg 无 ``uv`` 字段）→ 真实解释器副本，
        # 即使尺寸 >100KB 也直接用，DETACHED 生效，不绕行。
        monkeypatch.setattr("services.production_runtime._is_windows", lambda: True)
        venv_dir = tmp_path / "venv"
        scripts = venv_dir / "Scripts"
        scripts.mkdir(parents=True)
        real_py = scripts / "python.exe"
        real_py.write_bytes(b"x" * 200000)  # >100KB
        (venv_dir / "pyvenv.cfg").write_text(
            "home = %s\nimplementation = CPython\n" % str(tmp_path),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "services.production_runtime.sys.executable",
            str(real_py),
        )
        command, _env = ProductionRuntimeClient._resolve_runtime_launch()
        assert command[0] == str(real_py)
        assert command[1:] == ["-m", "services.production_runtime", "--serve"]

    def test_open_bootstrap_log_on_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "services.production_runtime._is_windows", lambda: True
        )
        monkeypatch.setattr(
            "lib.config.get_data_dir", lambda: str(tmp_path)
        )
        handle = _open_bootstrap_log()
        assert handle is not None
        handle.write("import-time failure\n")
        handle.close()
        log_path = tmp_path / "logs" / "production_runtime_bootstrap.log"
        assert log_path.is_file()
        assert "import-time failure" in log_path.read_text(encoding="utf-8")
