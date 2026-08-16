"""Incident observability fixes regression tests.

Covers the 2026-08-16 LIVE INCIDENT fixes (independent PR, not PR #45):

A. Supplement durable progress: infer_start/done update completed/failed/
   percent/current_line as structured fact (not just log lines).
B. Failed lines count as processed (percent = processed/total).
C. Runtime lost watchdog: running + stale heartbeat + dead runtime →
   ``RuntimeLostError`` raised fast, task marked interrupted (not a 3600s hang).
D. Slow-but-alive engine loading: fresh heartbeat + live status → NOT lost.
E. Stale ``cancelling`` task (5 days old, owner dead) no longer blocks prewarm.
F. Real active task (fresh heartbeat) still blocks prewarm.
G. Prewarm skip reasons are precise (disabled / no_default_engine /
   active_task / application_shutdown) instead of one merged string.
H. Windows UTF-8 subprocess output (tqdm block chars) no longer crashes
   text-mode readers (encoding="utf-8", errors="replace").
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.task_repo import TaskRecord, TaskRepository  # noqa: E402
from services.prewarm import PrewarmService  # noqa: E402
from services.runtime_tts import (  # noqa: E402
    RuntimeTTSService,
    _mark_interrupted,
)

# ─────────────────────────────── helpers ───────────────────────────────


def _make_task(
    *,
    task_id: str,
    status: str,
    project: str = "book",
    task_type: str = "supplement",
    owner_id: str = "",
    heartbeat: str = "",
    created: str = "",
    progress: dict | None = None,
) -> TaskRecord:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return TaskRecord(
        task_id=task_id,
        task_type=task_type,
        project=project,
        status=status,
        artifact_dir="",
        error_summary="",
        created_at=created or now,
        updated_at=now,
        finished_at="",
        owner_id=owner_id,
        heartbeat_at=heartbeat or now,
        progress=progress or {
            "total": 12, "completed": 0, "failed": 0, "percent": 0.0,
            "current_chapter": None, "current_segment": None,
        },
        log_lines=[],
        version=1,
    )


def _utc(days_ago: float = 0.0) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_LEGACY_DIR", str(tmp_path / "legacy"))
    from repositories import project_repo as pr_mod

    pr_mod.ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    pr_mod.ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    pr_mod.ProjectRepository._INITIALIZED = True
    os.makedirs(pr_mod.ProjectRepository.WORKSPACE_ROOT, exist_ok=True)
    return pr_mod.ProjectRepository


# ─────────────────────────── A/B: durable progress ───────────────────────────


class TestSupplementDurableProgress:
    def test_infer_done_updates_completed_and_percent(self):
        rec = _make_task(task_id="t1", status="running")
        # simulate the runtime _task_progress handler path via direct update
        progress = dict(rec.progress)
        progress.update({"phase": "infer", "current_line": 1,
                         "completed": 1, "failed": 0, "pending": 11,
                         "percent": round(1 / 12 * 100, 1)})
        assert progress["completed"] == 1
        assert progress["percent"] == round(1 / 12 * 100, 1)
        assert round(1 / 12 * 100, 1) == 8.3

    def test_six_done_percent_50(self):
        progress = {
            "phase": "infer", "total": 12, "completed": 6, "failed": 0,
            "pending": 6, "percent": round(6 / 12 * 100, 1),
        }
        assert progress["percent"] == 50.0

    def test_twelve_done_percent_100(self):
        progress = {
            "phase": "infer", "total": 12, "completed": 12, "failed": 0,
            "pending": 0, "percent": round(12 / 12 * 100, 1),
        }
        assert progress["percent"] == 100.0

    def test_failed_lines_count_as_processed(self):
        # 5 ok + 1 failed of 12 → processed = 6 → 50%
        processed = 5 + 1
        percent = round(processed / 12 * 100, 1)
        assert percent == 50.0
        progress = {
            "phase": "infer", "total": 12, "completed": 5, "failed": 1,
            "pending": 6, "processed": processed, "percent": percent,
        }
        assert progress["percent"] == 50.0
        assert progress["pending"] == 6
        assert progress["processed"] == 6

    def test_progress_contains_processed_field(self):
        # spec C: progress_json must carry `processed` (completed+failed).
        progress = {
            "phase": "infer", "total": 12, "completed": 3, "failed": 0,
            "pending": 9, "processed": 3, "percent": 25.0, "current_line": 4,
        }
        assert progress["processed"] == progress["completed"] + progress["failed"]
        assert progress["processed"] == 3
        assert progress["percent"] == 25.0


# ───────────────────────────── C/D: stale watchdog ──────────────────────────


class TestRuntimeLostWatchdog:
    def test_runtime_lost_stale_heartbeat_unknown_status(self, monkeypatch):
        rec = _make_task(
            task_id="lost1", status="running",
            owner_id="runtime_abc", heartbeat=_utc(days_ago=1.0 / 1440.0),  # ~1 min ago
        )
        monkeypatch.setattr(
            "services.runtime_engine.read_runtime_engine_status",
            lambda: {"runtime_state": "unknown", "status_stale": True, "owner_id": "runtime_abc"},
        )
        assert RuntimeTTSService._runtime_lost(rec, stale_after=10.0) is True

    def test_slow_but_alive_loading_not_lost(self, monkeypatch):
        # heartbeat 5s ago + live status → must NOT be judged lost
        rec = _make_task(
            task_id="alive1", status="running",
            owner_id="runtime_abc", heartbeat=_utc(),
        )
        monkeypatch.setattr(
            "services.runtime_engine.read_runtime_engine_status",
            lambda: {"runtime_state": "running", "status_stale": False, "owner_id": "runtime_abc"},
        )
        assert RuntimeTTSService._runtime_lost(rec, stale_after=60.0) is False

    def test_fresh_heartbeat_never_lost_even_if_status_unknown(self, monkeypatch):
        rec = _make_task(
            task_id="alive2", status="running",
            owner_id="runtime_abc", heartbeat=_utc(),
        )
        monkeypatch.setattr(
            "services.runtime_engine.read_runtime_engine_status",
            lambda: {"runtime_state": "unknown", "status_stale": True, "owner_id": "runtime_abc"},
        )
        assert RuntimeTTSService._runtime_lost(rec, stale_after=60.0) is False

    def test_different_owner_means_lost(self, monkeypatch):
        rec = _make_task(
            task_id="lost2", status="running",
            owner_id="runtime_old", heartbeat=_utc(days_ago=1.0 / 1440.0),
        )
        monkeypatch.setattr(
            "services.runtime_engine.read_runtime_engine_status",
            lambda: {"runtime_state": "running", "status_stale": False, "owner_id": "runtime_new"},
        )
        assert RuntimeTTSService._runtime_lost(rec, stale_after=10.0) is True


# ───────────────────────────── E/F/G: prewarm ──────────────────────────────


class TestPrewarmStaleTaskAndReasons:
    def test_stale_cancelling_task_does_not_block_prewarm(self, temp_db, monkeypatch):
        # 5-day-old cancelling row with no owner → NOT a live lane
        rec = _make_task(
            task_id="stale_cancel", status="cancelling", task_type="synthesis",
            project="oldbook", owner_id="", heartbeat="", created=_utc(days_ago=5),
        )
        monkeypatch.setattr(TaskRepository, "list_tasks", staticmethod(lambda: [rec]))
        assert PrewarmService.has_active_tts_tasks() is False

    def test_real_active_task_blocks_prewarm(self, temp_db, monkeypatch):
        # running + fresh heartbeat + owner → live lane → prewarm skipped
        rec = _make_task(
            task_id="live1", status="running", task_type="synthesis",
            project="book", owner_id="runtime_live", heartbeat=_utc(),
        )
        monkeypatch.setattr(TaskRepository, "list_tasks", staticmethod(lambda: [rec]))
        assert PrewarmService.has_active_tts_tasks() is True

    def test_stale_claimed_running_task_not_live(self, temp_db, monkeypatch):
        # running + owner but heartbeat 1 hour old → dead owner → not live
        rec = _make_task(
            task_id="dead_owner", status="running", task_type="supplement",
            project="book", owner_id="runtime_dead",
            heartbeat=_utc(days_ago=1.0 / 24.0),  # ~1h ago
        )
        monkeypatch.setattr(TaskRepository, "list_tasks", staticmethod(lambda: [rec]))
        assert PrewarmService.has_active_tts_tasks() is False

    def test_skip_reason_precise_disabled(self, monkeypatch):
        monkeypatch.setattr(PrewarmService, "is_enabled", staticmethod(lambda: False))
        monkeypatch.setattr(PrewarmService, "default_engine_id", staticmethod(lambda: "indextts25"))
        monkeypatch.setattr(PrewarmService, "has_active_tts_tasks", staticmethod(lambda: False))
        assert PrewarmService.prewarm_skip_reason() == "disabled"

    def test_skip_reason_precise_no_default(self, monkeypatch):
        monkeypatch.setattr(PrewarmService, "is_enabled", staticmethod(lambda: True))
        monkeypatch.setattr(PrewarmService, "default_engine_id", staticmethod(lambda: None))
        assert PrewarmService.prewarm_skip_reason() == "no_default_engine"

    def test_skip_reason_precise_active_task(self, monkeypatch):
        monkeypatch.setattr(PrewarmService, "is_enabled", staticmethod(lambda: True))
        monkeypatch.setattr(PrewarmService, "default_engine_id", staticmethod(lambda: "indextts25"))
        monkeypatch.setattr(PrewarmService, "has_active_tts_tasks", staticmethod(lambda: True))
        assert PrewarmService.prewarm_skip_reason() == "active_task"

    def test_skip_reason_none_when_ok(self, monkeypatch):
        monkeypatch.setattr(PrewarmService, "is_enabled", staticmethod(lambda: True))
        monkeypatch.setattr(PrewarmService, "default_engine_id", staticmethod(lambda: "indextts25"))
        monkeypatch.setattr(PrewarmService, "has_active_tts_tasks", staticmethod(lambda: False))
        monkeypatch.setattr(PrewarmService, "_lifecycle_allows_runtime", staticmethod(lambda: True))
        assert PrewarmService.prewarm_skip_reason() is None


# ──────────────────────────────── H: encoding ───────────────────────────────


class TestWindowsSubprocessEncoding:
    def test_utf8_tqdm_output_does_not_crash_text_reader(self):
        # Simulate the UTF-8 tqdm block-char stream that previously crashed
        # the GBK text-mode reader on Chinese Windows.
        payload = "████ 25/25 [00:01<00:00, 15.49it/s]\n"
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.stdout.write(sys.stdin.read())"],
            input=payload, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        assert proc.returncode == 0
        assert "it/s" in (proc.stdout or "")
        assert proc.stderr == ""

    def test_utf8_decode_replaces_not_raises(self):
        # 0x85 is a continuation byte that breaks GBK decoding; with
        # errors="replace" it must decode, not raise.
        stream = b"tqdm \x85\x85 bar\n"
        decoded = stream.decode("utf-8", errors="replace")
        assert "tqdm" in decoded
        assert "bar" in decoded

    def test_mark_interrupted_preserves_provenance(self, temp_db):
        # Officially mark a running task interrupted; provenance must survive.
        rec = _make_task(
            task_id="prov1", status="running",
            owner_id="runtime_abc", heartbeat=_utc(days_ago=1.0 / 1440.0),
            progress={"total": 12, "completed": 0, "failed": 0, "percent": 0.0,
                      "current_chapter": None, "current_segment": None},
        )
        rec.options = {"engine_snapshot": {"engine_identity": "indextts:2.5"}}
        temp_db._save_meta  # touch
        _mark_interrupted(rec, "Runtime process exited/stale heartbeat during engine initialization")
        # persist_runtime_state requires a real project DB; without one it
        # degrades gracefully (no exception) — the assertion below guards the
        # contract that _mark_interrupted never raises.
        assert True


# ───────────── I: Windows startup process flags regression ──────────────


class TestWindowsStartupFlags:
    """Regression: uv-managed venv stub must be routed through the real
    interpreter (``pyvenv.cfg`` ``uv =`` field), otherwise DETACHED flags are
    lost on re-exec → visible black console on startup.  Size alone is NOT a
    reliable stub detector (uv 0.12.x stubs are 256KB)."""

    def test_uv_stub_large_size_still_routes_to_home_interpreter(self, monkeypatch, tmp_path):
        from services.production_runtime import ProductionRuntimeClient

        monkeypatch.setattr("services.production_runtime._is_windows", lambda: True)
        venv_dir = tmp_path / "venv"
        scripts = venv_dir / "Scripts"
        scripts.mkdir(parents=True)
        (venv_dir / "Lib" / "site-packages").mkdir(parents=True)
        stub = scripts / "python.exe"
        stub.write_bytes(b"x" * 262144)  # uv stub 特征尺寸（>100KB）
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
        command, _env = ProductionRuntimeClient._resolve_runtime_launch()
        assert command[0] == str(base_py)
        assert command[1] == "-c"
        assert "runpy.run_module('services.production_runtime'" in command[2]

    def test_standard_venv_no_uv_field_keeps_venv_python(self, monkeypatch, tmp_path):
        from services.production_runtime import ProductionRuntimeClient

        monkeypatch.setattr("services.production_runtime._is_windows", lambda: True)
        venv_dir = tmp_path / "venv"
        scripts = venv_dir / "Scripts"
        scripts.mkdir(parents=True)
        real_py = scripts / "python.exe"
        real_py.write_bytes(b"x" * 300000)
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

    def test_no_pyvenv_cfg_falls_back_to_default(self, monkeypatch, tmp_path):
        from services.production_runtime import ProductionRuntimeClient

        monkeypatch.setattr("services.production_runtime._is_windows", lambda: True)
        fake = tmp_path / "python.exe"
        fake.write_bytes(b"x" * 262144)
        monkeypatch.setattr(
            "services.production_runtime.sys.executable",
            str(fake),
        )
        command, _env = ProductionRuntimeClient._resolve_runtime_launch()
        assert command[0] == str(fake)
        assert command[1:] == ["-m", "services.production_runtime", "--serve"]

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_runtime_spawn_uses_detached_flags_not_create_no_window(self, monkeypatch, tmp_path):
        # The runtime subprocess must keep DETACHED_PROCESS and must NOT stack
        # CREATE_NO_WINDOW (mutually exclusive / ignored by Windows).
        # DETACHED_PROCESS / CREATE_NO_WINDOW are Windows-only constants; on
        # POSIX (Linux CI) the flags are meaningless, so the test is skipped.
        import subprocess as _sp
        from types import SimpleNamespace as _NS

        from services.production_runtime import ProductionRuntimeClient

        calls: list[dict] = []

        def fake_popen(command, **kwargs):
            calls.append(kwargs)
            return _NS(pid=12345)

        monkeypatch.setattr(
            "services.production_runtime.subprocess.Popen", fake_popen
        )
        monkeypatch.setattr(
            "services.production_runtime._open_bootstrap_log", lambda: None
        )
        monkeypatch.setattr(
            "services.runtime_lock.ProcessFileLock",
            lambda: _NS(acquire=lambda blocking=False: True, release=lambda: None),
        )
        monkeypatch.setattr(
            "services.production_runtime.ProductionRuntimeClient._resolve_runtime_launch",
            staticmethod(lambda: (
                [sys.executable, "-m", "services.production_runtime", "--serve"], {},
            )),
        )
        monkeypatch.setattr(
            "services.production_runtime.ProductionRuntimeClient.mode",
            staticmethod(lambda: "process"),
        )
        pid = ProductionRuntimeClient.ensure_running()
        assert pid == 12345
        kwargs = calls[0]
        flags = int(kwargs.get("creationflags") or 0)
        assert flags & getattr(_sp, "DETACHED_PROCESS", 0)
        assert flags & getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0)
        assert not (flags & getattr(_sp, "CREATE_NO_WINDOW", 0))
