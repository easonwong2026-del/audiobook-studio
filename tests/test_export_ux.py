"""PR B 修复 4：补录导出 UX —— 文件名清洗 / 扩展名归一 / 重名后缀 / 打开文件夹。

覆盖：
- 自定义文件名：``abc`` + MP3 → ``abc.mp3``；``abc.wav`` + MP3 → ``abc.mp3``；
- 非法字符清洗（< > : " / \\ | ? *）与尾部空格 / 尾部点；空名回退；
- 重名不静默覆盖：abc.mp3 → abc_2.mp3 → abc_3.mp3；
- 最终路径正确（保存目录 + 归一文件名 + 唯一后缀）；
- 打开所在文件夹 handler 正确（no-window）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.export_naming import (
    build_export_path,
    normalize_export_name,
    sanitize_filename,
    strip_extension,
    unique_path,
)


def _install_export_backend(monkeypatch, app, task, manifest=None, start_result=None):
    """Install a tiny persistent-state double for the Export UI callbacks."""
    state = {"task": dict(task)}

    class Backend:
        @staticmethod
        def start_export(*_args, **_kwargs):
            return dict(start_result or state["task"])

        @staticmethod
        def get_export_task(*_args, **_kwargs):
            return dict(state["task"])

        @staticmethod
        def get_delivery_manifest(*_args, **_kwargs):
            return dict(manifest) if isinstance(manifest, dict) else manifest

    monkeypatch.setattr(app, "ExportService", Backend)
    return state


def _session():
    return SimpleNamespace(project="book")


# ── 自定义文件名 / 扩展名归一 ─────────────────────────────────────────────
def test_normalize_plain_name():
    assert normalize_export_name("abc", "mp3") == "abc.mp3"
    assert normalize_export_name("abc", "MP3") == "abc.mp3"
    assert normalize_export_name("abc", ".wav") == "abc.wav"


def test_normalize_strips_known_audio_extension():
    # abc.wav + MP3 → abc.mp3（不生成 abc.wav.mp3）
    assert normalize_export_name("abc.wav", "mp3") == "abc.mp3"
    assert normalize_export_name("abc.mp3", "wav") == "abc.wav"
    assert normalize_export_name("abc.m4b", "wav") == "abc.wav"


def test_normalize_empty_falls_back():
    assert normalize_export_name("", "mp3") == "export.mp3"
    assert normalize_export_name("   ", "mp3") == "export.mp3"
    assert normalize_export_name("...", "mp3") == "export.mp3"


# ── 非法字符清洗 ─────────────────────────────────────────────────────────
def test_sanitize_illegal_chars():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_trailing_space_and_dot():
    assert sanitize_filename("abc  ") == "abc"
    assert sanitize_filename("abc.") == "abc"
    assert sanitize_filename("abc . ") == "abc"


def test_sanitize_empty_falls_back():
    assert sanitize_filename("") == "export"
    assert sanitize_filename(None) == "export"
    assert sanitize_filename("...", fallback="自定义") == "自定义"
    assert sanitize_filename("   ", fallback="自定义") == "自定义"


def test_strip_extension_only_audio():
    assert strip_extension("abc.wav") == "abc"
    assert strip_extension("abc.wav.mp3") == "abc.wav"
    assert strip_extension("my.voice") == "my.voice"


# ── 重名后缀（不静默覆盖）────────────────────────────────────────────────
def test_unique_path_no_conflict(tmp_path):
    target = str(tmp_path / "abc.mp3")
    assert unique_path(target) == target


def test_unique_path_appends_suffix(tmp_path):
    first = tmp_path / "abc.mp3"
    first.write_bytes(b"x")
    second = unique_path(str(first))
    assert second == str(tmp_path / "abc_2.mp3")
    # 第一次导出落盘后，同一名称再导出 → abc_3.mp3
    Path(second).write_bytes(b"x")
    third = unique_path(str(first))
    assert third == str(tmp_path / "abc_3.mp3")


def test_unique_path_skips_existing_suffix(tmp_path):
    first = tmp_path / "abc.mp3"
    first.write_bytes(b"x")
    (tmp_path / "abc_2.mp3").write_bytes(b"x")
    third = unique_path(str(first))
    assert third == str(tmp_path / "abc_3.mp3")


# ── 最终路径 ─────────────────────────────────────────────────────────────
def test_build_export_path_and_unique_combined(tmp_path):
    out_dir = tmp_path / "exports"
    path = build_export_path(str(out_dir), "abc.wav", "mp3")
    assert path == str(out_dir / "abc.mp3")
    final = unique_path(path)
    assert final == str(out_dir / "abc.mp3")
    # 写入后重名 → abc_2.mp3
    os.makedirs(out_dir, exist_ok=True)
    with open(final, "w", encoding="utf-8") as fh:
        fh.write("x")
    assert unique_path(path) == str(out_dir / "abc_2.mp3")


# ── durable Export task → UI status chain ──────────────────────────────────
def test_export_start_returns_durable_task_and_immediate_pending(monkeypatch):
    import app

    task = {"task_id": "export-1", "project": "book", "status": "pending"}
    _install_export_backend(monkeypatch, app, task)
    result = app.do_export("mp3", "192k", "", "require_passed", _session())

    assert result[0] is None
    assert "等待导出" in result[1]
    assert result[2] == "export-1"
    assert result[5].active is True
    assert result[6]["interactive"] is False


def test_second_export_click_keeps_existing_active_task(monkeypatch):
    import app

    task = {"task_id": "export-active", "project": "book", "status": "running"}
    _install_export_backend(monkeypatch, app, task)
    session = _session()
    app.refresh_export_status("export-active", "", session)
    start_calls = []

    def unexpected_start(*_args, **_kwargs):
        start_calls.append(True)
        raise AssertionError("a second export must not be started while one is active")

    monkeypatch.setattr(app.ExportService, "start_export", unexpected_start)
    result = app.do_export(
        "mp3",
        "192k",
        "",
        "require_passed",
        session,
    )

    assert start_calls == []
    assert result[2] == "export-active"
    assert "正在导出" in result[1]
    assert result[5].active is True
    assert result[6]["interactive"] is False


def test_export_active_race_error_keeps_backend_task(monkeypatch):
    import app

    task = {"task_id": "export-race", "project": "book", "status": "running"}
    _install_export_backend(monkeypatch, app, task)

    class ActiveExportError(RuntimeError):
        def __init__(self):
            super().__init__("export already active")
            self.plan = {
                "blockers": [{
                    "code": "EXPORT_ACTIVE",
                    "task_id": "export-race",
                    "status": "running",
                }]
            }

    def reject_second_start(*_args, **_kwargs):
        raise ActiveExportError()

    monkeypatch.setattr(app.ExportService, "start_export", reject_second_start)
    result = app.do_export("mp3", "192k", "", "require_passed", _session())

    assert result[2] == "export-race"
    assert "正在导出" in result[1]
    assert result[5].active is True
    assert result[6]["interactive"] is False


def test_export_tracking_does_not_bleed_into_new_project(monkeypatch):
    import app

    task = {"task_id": "export-old-project", "project": "book", "status": "running"}
    _install_export_backend(monkeypatch, app, task)
    session = _session()
    app.refresh_export_status("export-old-project", "", session)

    session.project = "another-book"
    result = app.refresh_export_status("export-old-project", "", session)

    assert result[2] == ""
    assert result[3] == ""
    assert "当前项目没有" in result[1]
    assert result[5].active is False


def test_export_refresh_keeps_pending_and_running_active(monkeypatch):
    import app

    task = {"task_id": "export-2", "project": "book", "status": "pending"}
    state = _install_export_backend(monkeypatch, app, task)
    pending = app.refresh_export_status("export-2", "", _session())
    assert "等待导出" in pending[1]
    assert pending[5].active is True
    assert pending[6]["interactive"] is False

    state["task"]["status"] = "running"
    running = app.refresh_export_status("export-2", "", _session())
    assert "正在导出" in running[1]
    assert running[5].active is True
    assert running[6]["interactive"] is False


def _ready_manifest(tmp_path, *, version=3, relative_path="03_导出成品/正式导出/book.mp3"):
    project_dir = tmp_path / "project"
    artifact = project_dir / Path(*relative_path.split("/"))
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"audio")
    if version >= 3:
        manifest_path = project_dir / "99_系统数据" / "配置" / "project.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text('{"storage_version": 3}', encoding="utf-8")
    return project_dir, artifact, {
        "manifest_id": "manifest-1",
        "export_id": "export-3",
        "ready": True,
        "outputs": [{"relative_path": relative_path}],
    }


def test_done_refresh_reads_ready_manifest_and_shows_final_artifact(monkeypatch, tmp_path):
    import app

    project_dir, artifact, manifest = _ready_manifest(tmp_path)
    task = {
        "task_id": "export-3", "project": "book", "status": "done",
        "manifest_id": "manifest-1",
    }
    _install_export_backend(monkeypatch, app, task, manifest)
    monkeypatch.setattr(app.ProjectService, "get_project_dir", lambda _name: str(project_dir))
    monkeypatch.setattr(app, "_safe_path_for_file_component", lambda path: path)
    result = app.refresh_export_status("export-3", "", _session())

    assert result[0] == str(artifact)
    assert "✅ 导出成功" in result[1]
    assert artifact.name in result[1]
    assert str(artifact) in result[1]
    assert result[4]["interactive"] is True
    assert result[5].active is False
    assert result[6]["interactive"] is True


def test_error_and_cancelled_are_terminal_without_success_artifact(monkeypatch):
    import app

    error_task = {
        "task_id": "export-error", "project": "book", "status": "error",
        "error": {"code": "EXPORT_ERROR", "message": "ffmpeg failed"},
    }
    _install_export_backend(monkeypatch, app, error_task)
    error = app.refresh_export_status("export-error", "", _session())
    assert error[0] is None
    assert "导出失败" in error[1]
    assert "✅ 导出成功" not in error[1]
    assert error[5].active is False
    assert error[6]["interactive"] is True

    cancelled_task = {
        "task_id": "export-cancelled", "project": "book", "status": "cancelled",
    }
    _install_export_backend(monkeypatch, app, cancelled_task)
    cancelled = app.refresh_export_status("export-cancelled", "", _session())
    assert cancelled[0] is None
    assert "导出已取消" in cancelled[1]
    assert "失败" not in cancelled[1]
    assert cancelled[5].active is False
    assert cancelled[6]["interactive"] is True


def test_stale_running_ui_is_corrected_by_durable_done_state(monkeypatch, tmp_path):
    import app

    project_dir, artifact, manifest = _ready_manifest(tmp_path)
    task = {
        "task_id": "export-stale", "project": "book", "status": "running",
        "manifest_id": "manifest-1",
    }
    manifest["export_id"] = task["task_id"]
    state = _install_export_backend(monkeypatch, app, task, manifest)
    monkeypatch.setattr(app.ProjectService, "get_project_dir", lambda _name: str(project_dir))
    monkeypatch.setattr(app, "_safe_path_for_file_component", lambda path: path)
    active = app.refresh_export_status("export-stale", "", _session())
    assert "正在导出" in active[1]

    state["task"]["status"] = "done"
    completed = app.refresh_export_status("export-stale", "", _session())
    assert completed[0] == str(artifact)
    assert "✅ 导出成功" in completed[1]
    assert "正在导出" not in completed[1]


def test_done_before_manifest_ready_never_claims_success(monkeypatch, tmp_path):
    import app

    project_dir, _artifact, manifest = _ready_manifest(tmp_path)
    manifest["ready"] = False
    task = {
        "task_id": "export-not-ready", "project": "book", "status": "done",
        "manifest_id": "manifest-1",
    }
    _install_export_backend(monkeypatch, app, task, manifest)
    monkeypatch.setattr(app.ProjectService, "get_project_dir", lambda _name: str(project_dir))
    result = app.refresh_export_status("export-not-ready", "", _session())

    assert result[0] is None
    assert "尚未就绪" in result[1]
    assert "✅ 导出成功" not in result[1]
    assert result[4]["interactive"] is False


def test_open_export_location_uses_resolved_artifact_directory(monkeypatch, tmp_path):
    import app

    project_dir, artifact, manifest = _ready_manifest(tmp_path)
    task = {
        "task_id": "export-open", "project": "book", "status": "done",
        "manifest_id": "manifest-1",
    }
    manifest["export_id"] = task["task_id"]
    _install_export_backend(monkeypatch, app, task, manifest)
    monkeypatch.setattr(app.ProjectService, "get_project_dir", lambda _name: str(project_dir))
    opened = []
    monkeypatch.setattr("lib.procutil.open_in_folder", lambda path: opened.append(path) or True)

    message = app.open_export_location("export-open", "", _session())

    assert opened == [str(artifact.parent)]
    assert str(artifact.parent) in message
    assert "已打开" in message


@pytest.mark.parametrize(
    ("version", "relative_path"),
    [
        (1, "output/book.mp3"),
        (2, "09_导出文件/exports/book.mp3"),
    ],
)
def test_export_artifact_resolver_keeps_legacy_v1_v2_paths(
    monkeypatch, tmp_path, version, relative_path
):
    import app

    project_dir = tmp_path / f"legacy-{version}"
    artifact = project_dir / Path(*relative_path.split("/"))
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"audio")
    (project_dir / "project.json").write_text(
        f'{{"storage_version": {version}}}', encoding="utf-8"
    )
    manifest = {
        "manifest_id": "manifest-legacy",
        "export_id": "export-legacy",
        "ready": True,
        "outputs": [{"relative_path": relative_path}],
    }
    _install_export_backend(
        monkeypatch,
        app,
        {"task_id": "export-legacy", "project": "book", "status": "done"},
        manifest,
    )
    monkeypatch.setattr(app.ProjectService, "get_project_dir", lambda _name: str(project_dir))

    resolved, reason = app._resolve_export_ui_artifact(
        "book", "export-legacy", {"task_id": "export-legacy"}
    )

    assert reason == ""
    assert resolved["path"] == os.path.normpath(str(artifact))


# ── 打开所在文件夹 handler（no-window）────────────────────────────────────
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_open_folder_handler_uses_no_window(monkeypatch, tmp_path):

    from lib import procutil

    monkeypatch.setattr(procutil, "_is_windows", lambda: True)
    started: list = []
    monkeypatch.setattr(os, "startfile", lambda path: started.append(path))
    ok = procutil.open_in_folder(str(tmp_path))
    assert ok is True
    # 目录 → os.startfile（本身无 console，不经过 subprocess）
    assert started == [str(tmp_path)]
