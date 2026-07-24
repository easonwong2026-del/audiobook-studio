"""集成测试：阶段三「减少重复读盘」实证（计划 §7.4）。

验证：
- 调用一次打开项目 handler（``app.open_project(name, ss)``）后，``ss.project_snapshot``
  非 None，且只真正读盘一次（``load_snapshot`` → ``lib.project_manager.open_project``）；
- 之后连续多次调用刷新函数，``lib.project_manager.open_project`` 的**磁盘读盘调用次数**
  不因刷新次数增长——即刷新走 ``ss.project_snapshot``，不重复读盘。

计数方法：用 monkeypatch 把 ``lib.project_manager.open_project`` 包成计数 wrapper。
``ProjectSnapshot.reload_if_stale``、``services.project.ProjectService.open_project``、
``lib.progress.build_preview_rows`` 等所有读盘点最终都走这个模块属性，因此计数覆盖全链路。

设计要点（吻合任务提示）：测试期间把三个关键文件 mtime 设到快照加载时刻之前，
``reload_if_stale`` 不会误触发回读——这正是要证明的「刷新走快照」。
"""
import sys
import os
import json

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.project_manager as pm  # noqa: E402
from services.session import SessionState  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402


SCRIPT = {
    "meta": {"title": "缓存实证项目"},
    "voices": {"旁白": {"description": "沉稳男中音"}},
    "chapters": [
        {
            "id": 1, "title": "第一章",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段内容"},
                {"id": "1-002", "role": "旁白", "text": "第二段内容"},
            ],
        },
        {
            "id": 2, "title": "第二章",
            "segments": [
                {"id": "2-001", "role": "旁白", "text": "第三段内容"},
            ],
        },
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """造最小项目，并把 WORKSPACE_ROOT 指到临时目录。"""
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    script_path = tmp_path / "structured_script.json"
    script_path.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    name = "cachebook"
    pm.create_project(name, str(script_path))
    return name


def _counting_open(monkeypatch):
    """把 ``ProjectRepository.load_project`` 包成计数 wrapper，返回计数状态 dict。"""
    real_load = ProjectRepository.load_project
    state = {"n": 0}

    def counting_load(name):
        state["n"] += 1
        return real_load(name)

    monkeypatch.setattr(ProjectRepository, "load_project", counting_load)
    return state


def _freeze_mtime_older_than_snapshot(name, snap):
    """测试期间把三个关键文件 mtime 设到快照加载时刻之前，避免 reload_if_stale 误回读。"""
    proj_dir = pm.get_project_dir(name)
    for fn in ("project.json", "structured_script.json", "voice_bindings.json"):
        p = os.path.join(proj_dir, fn)
        if os.path.isfile(p):
            os.utime(p, (snap.loaded_at - 10, snap.loaded_at - 10))


def test_open_project_establishes_snapshot(project, monkeypatch):
    """打开项目 handler 应建立会话态快照，且只真正读盘一次。"""
    state = _counting_open(monkeypatch)
    import app  # 延迟 import（app 顶层 import gradio，需构建 Blocks）

    ss = SessionState()
    app.open_project(project, ss)

    assert ss.project_snapshot is not None, "打开项目后 ss.project_snapshot 应为非 None"
    assert state["n"] == 1, (
        f"打开项目应仅读盘一次（load_snapshot → open_project），实际 {state['n']} 次"
    )


def test_core_refresh_functions_read_snapshot_not_disk(project, monkeypatch):
    """refresh_top_status / preview_chapters / preview_chapter_options / play_segment
    多次刷新不应重复读盘（走 ss.project_snapshot）。"""
    state = _counting_open(monkeypatch)
    import app

    ss = SessionState()
    app.open_project(project, ss)
    _freeze_mtime_older_than_snapshot(project, ss.project_snapshot)

    for _ in range(5):
        app.refresh_top_status(ss)
        app.preview_chapters(ss)
        app.preview_chapter_options(ss)
        app.play_segment("1-001", ss)

    assert state["n"] == 1, (
        f"核心刷新函数不应重复读盘：刷新后 open_project 调用 {state['n']} 次，"
        f"期望仅打开时的 1 次（走 ss.project_snapshot）"
    )


def test_render_preview_reads_snapshot_not_disk(project, monkeypatch):
    """render_preview 多次刷新不应重复读盘（走 ss.project_snapshot）。

    若失败：render_preview → synth_progress.build_preview_rows(ss.project)
    → pm.open_project 仍在重复读盘，违反计划 §7.4「页面普通刷新读会话快照」。
    """
    state = _counting_open(monkeypatch)
    import app

    ss = SessionState()
    app.open_project(project, ss)
    _freeze_mtime_older_than_snapshot(project, ss.project_snapshot)

    for _ in range(5):
        app.render_preview(ss)

    assert state["n"] == 1, (
        f"render_preview 不应重复读盘：刷新后 open_project 调用 {state['n']} 次，"
        f"期望仅打开时的 1 次（走 ss.project_snapshot）。\n"
        f"诊断：render_preview → build_preview_rows(ss.project) → pm.open_project 仍在读盘。"
    )
