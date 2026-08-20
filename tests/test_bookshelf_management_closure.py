"""Bookshelf management UX/state-safety regression coverage for the close-out PR."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import gradio as gr
import pytest

from repositories.project_repo import ProjectRepository
from repositories.project_storage_repo import ProjectStorageRepository
from services.project_catalog import ProjectCatalogService
from services.project_storage import ProjectStorageService
from services.session import SessionState
from ui import project_catalog_handlers as handlers
from ui.pages.overview_page import create_overview_page
from ui.settings_handlers import apply_data_dir
from ui.wiring.project_catalog_wiring import (
    bookshelf_management_outputs,
    cleanup_outputs,
    storage_upgrade_outputs,
)


def _script_file(tmp_path: Path, title: str, author: str = "作者") -> Path:
    path = tmp_path / f"{title}.json"
    path.write_text(
        json.dumps(
            {
                "meta": {"title": title, "author": author},
                "voices": {"旁白": {"description": "x"}},
                "chapters": [
                    {
                        "id": 1,
                        "title": "第一章",
                        "segments": [
                            {"id": "1-001", "role": "旁白", "text": "A"}
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def bookshelf_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project("alpha", str(_script_file(tmp_path, "阿尔法")))
    ProjectRepository.create_project("beta", str(_script_file(tmp_path, "贝塔")))
    return data_root


def _page_in_blocks():
    block = gr.Blocks()
    block.__enter__()
    return block, create_overview_page()


def test_initial_project_actions_are_disabled_and_transients_hidden():
    block, page = _page_in_blocks()
    try:
        for key in handlers.BOOKSHELF_ACTION_KEYS:
            assert page[key].interactive is False, key
        for key in (
            "bookshelf_cleanup_confirm",
            "bookshelf_cleanup_cancel",
            "bookshelf_storage_confirm",
            "bookshelf_storage_cancel",
            "bookshelf_integrity_repair",
        ):
            assert page[key].visible is False, key
    finally:
        block.__exit__(None, None, None)


def test_selection_controls_enable_actions_and_clear_all_transients(
    bookshelf_workspace,
):
    ss = SessionState(project="beta")
    ss.set_selected("alpha")
    updates = handlers.reconcile_bookshelf_selection(ss, "alpha")
    assert len(updates) == 19
    assert updates[0].get("value") == "beta"  # current workflow project
    for update in updates[1:10]:
        assert update.get("interactive") is True
    assert updates[10].get("value") == ""  # archive confirmation
    assert updates[11].get("value") == ""  # cleanup token
    assert updates[12].get("visible") is False
    assert updates[13].get("visible") is False
    assert updates[14].get("value") == ""  # storage token
    assert updates[15].get("visible") is False
    assert updates[16].get("visible") is False
    assert updates[17].get("visible") is False
    assert updates[18].get("value") == ""

    ss.clear_selected()
    cleared = handlers.reconcile_bookshelf_selection(ss, "alpha")
    # p_sel falls back to the opened workflow project, never to the stale
    # bookshelf mirror that was just cleared.
    assert cleared[0].get("value") == "beta"
    for update in cleared[1:10]:
        assert update.get("interactive") is False


def test_opened_project_wins_p_sel_while_bookshelf_selection_stays_independent(
    bookshelf_workspace,
):
    ss = SessionState(project="beta", script={"meta": {"title": "贝塔"}})
    ss.set_selected("alpha")

    updates = handlers.reconcile_bookshelf_selection(ss, "alpha")
    assert ss.project == "beta"
    assert ss.selected_project == "alpha"
    assert updates[0].get("value") == "beta"
    assert updates[1].get("interactive") is True

    refreshed = handlers.refresh_bookshelf_management_view("", "alpha", ss)
    assert refreshed[1].get("value") == "beta"  # project-page workflow control
    assert refreshed[5].get("value") == "alpha"  # bookshelf mirror
    assert "alpha" in refreshed[6].get("value", "")
    assert "beta" in refreshed[6].get("value", "")


def test_selected_card_explicitly_distinguishes_opened_project(bookshelf_workspace):
    ss = SessionState(project="beta", script={"meta": {}})
    ss.set_selected("alpha")
    _name, info, _mirror = handlers.select_bookshelf_row(
        {"data": [["alpha", 1, "0/1", "⚪未开始"]]}, ss, type("E", (), {"index": (0, 0)})()
    )
    assert "当前选择" in info
    assert "alpha" in info
    assert "当前工作项目" in info
    assert "beta" in info
    assert "尚未打开" in info


def test_refresh_preserves_valid_selection_and_filters(bookshelf_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    result = handlers.refresh_bookshelf_management_view("阿尔法", "alpha", ss)
    assert len(result) == 25
    assert [row[0] for row in result[0]["data"]] == ["alpha"]
    assert result[1].get("choices") == ["alpha", "beta"]
    assert result[1].get("value") == "alpha"
    assert result[5].get("value") == "alpha"
    assert "阿尔法" in result[6].get("value", "")
    assert result[7].get("interactive") is True
    assert ss.selected_project == "alpha"


def test_valid_refresh_keeps_selection_but_invalidates_archive_confirmation(
    bookshelf_workspace,
):
    ss = SessionState()
    ss.set_selected("alpha")
    _message, confirmation, *_ = handlers.archive_selected("alpha", "", ss)
    assert confirmation.get("value") == "alpha"
    revision = ss.selection_revision

    result = handlers.refresh_bookshelf_management_view("阿尔法", "alpha", ss)
    assert ss.selected_project == "alpha"
    assert ss.selection_revision == revision
    assert result[5].get("value") == "alpha"
    assert result[16].get("value") == ""  # destructive confirmation is stale


def test_state_aware_refresh_uses_one_catalog_snapshot(bookshelf_workspace, monkeypatch):
    calls = 0
    original_scan = ProjectCatalogService.scan

    def _scan_once():
        nonlocal calls
        calls += 1
        return original_scan()

    monkeypatch.setattr(ProjectCatalogService, "scan", staticmethod(_scan_once))
    result = handlers.refresh_bookshelf_management_view("阿尔法", "alpha", SessionState())

    assert len(result) == 25
    assert calls == 1


def test_selection_revision_changes_only_for_real_context_changes():
    ss = SessionState()
    initial = ss.selection_revision
    ss.set_selected("alpha")
    after_alpha = ss.selection_revision
    ss.set_selected("alpha")
    assert after_alpha > initial
    assert ss.selection_revision == after_alpha

    ss.set_selected("beta")
    after_beta = ss.selection_revision
    ss.set_selected("alpha")
    after_back = ss.selection_revision
    ss.set_selected(None)
    assert after_beta > after_alpha
    assert after_back > after_beta
    assert ss.selection_revision > after_back


def test_search_filter_clears_p_sel_mirror_and_actions(bookshelf_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    handlers.apply_project_search("贝塔", ss)
    updates = handlers.reconcile_bookshelf_selection(ss, "alpha")
    assert ss.selected_project is None
    assert updates[0].get("value") is None
    for update in updates[1:10]:
        assert update.get("interactive") is False


def test_refresh_removes_missing_selection_and_disables_actions(bookshelf_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    handlers.archive_selected("alpha", "alpha", ss)
    result = handlers.refresh_bookshelf_management_view("", "alpha", ss)
    assert ss.selected_project is None
    assert result[5].get("value") == ""
    assert "选择" in result[6].get("value", "")
    for update in result[7:16]:
        assert update.get("interactive") is False
    assert result[16].get("value") == ""
    assert result[17].get("value") == ""
    assert result[18].get("visible") is False
    assert result[19].get("visible") is False
    assert result[20].get("value") == ""
    assert result[21].get("visible") is False
    assert result[22].get("visible") is False
    assert result[23].get("visible") is False
    assert result[24].get("value") == ""


def test_archive_confirmation_is_invalidated_by_a_to_b_to_a(bookshelf_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    _msg, first_confirm, *_ = handlers.archive_selected("alpha", "", ss)
    assert first_confirm.get("value") == "alpha"

    ss.set_selected("beta")
    ss.set_selected("alpha")
    msg, second_confirm, *_ = handlers.archive_selected(
        "alpha", first_confirm.get("value"), ss
    )
    assert "确认将「alpha」移入回收站" in msg
    assert second_confirm.get("value") == "alpha"
    assert os.path.isdir(bookshelf_workspace / "projects" / "alpha")

    msg, *_ = handlers.archive_selected("alpha", "alpha", ss)
    assert "已移入回收站" in msg
    assert not os.path.isdir(bookshelf_workspace / "projects" / "alpha")


def test_archive_rejects_a_stale_mirror_against_canonical_selection(
    bookshelf_workspace,
):
    ss = SessionState()
    ss.set_selected("beta")
    message, _confirm, _selected, _info = handlers.archive_selected(
        "alpha", "alpha", ss
    )
    assert "当前书架选择已变化" in message
    assert os.path.isdir(bookshelf_workspace / "projects" / "alpha")


def test_cleanup_transient_reset_after_selection_context_change(bookshelf_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    # The repository may have no candidate files in this fixture; use the
    # shared reconciliation contract to assert the reset semantics directly.
    ss.set_selected("beta")
    updates = handlers.reconcile_bookshelf_selection(ss, "beta")
    assert updates[0].get("value") == "beta"
    assert updates[11].get("value") == ""
    assert updates[12].get("visible") is False
    assert updates[13].get("visible") is False
    assert updates[14].get("value") == ""
    assert updates[15].get("visible") is False
    assert updates[16].get("visible") is False


def test_storage_handlers_and_wiring_have_four_outputs(monkeypatch):
    plan = {
        "code": "PLAN_OK",
        "from_version": 2,
        "file_count": 1,
        "total_bytes": 2,
        "conflicts": [],
        "unknown_paths": [],
        "relative_path_records": [],
        "token": "token",
        "blockers": [],
    }
    monkeypatch.setattr(
        "ui.project_catalog_handlers.ProjectStorageService.plan_storage_upgrade",
        lambda _name: plan,
    )
    scan = handlers.scan_selected_storage_upgrade("alpha")
    assert len(scan) == 4
    assert scan[1] == "token"
    assert scan[2].get("visible") is True
    assert scan[3].get("visible") is True

    monkeypatch.setattr(
        "ui.project_catalog_handlers.ProjectStorageService.upgrade_storage",
        lambda _name, _token: {
            "file_count": 1,
            "total_bytes": 2,
            "relative_path_records": [],
            "backup_path": "/tmp/backup.zip",
        },
    )
    execute = handlers.execute_selected_storage_upgrade("alpha", "token")
    cancel = handlers.cancel_selected_storage_upgrade()
    assert len(execute) == len(cancel) == 4
    for result in (execute, cancel):
        assert result[1] == ""
        assert result[2].get("visible") is False
        assert result[3].get("visible") is False

    block, page = _page_in_blocks()
    try:
        assert len(storage_upgrade_outputs(page)) == 4
        assert len(cleanup_outputs(page)) == 4
        assert len(bookshelf_management_outputs(page, gr.Dropdown())) == 25
    finally:
        block.__exit__(None, None, None)


def test_data_dir_switch_resets_old_session_context(monkeypatch, tmp_path):
    new_root = tmp_path / "new-root"
    ss = SessionState(
        project="old",
        script={"meta": {"title": "old"}},
        bindings={"旁白": "old.wav"},
    )
    ss.set_selected("old")
    ss.set_snapshot(object())
    ss.synthesis = object()
    ss.set_catalog_query("阿尔法")
    monkeypatch.setattr(
        "ui.settings_handlers.ProjectService.set_data_dir",
        lambda _path: str(new_root),
    )

    message, returned = apply_data_dir(str(new_root), ss)
    assert "数据目录已设置" in message
    assert returned == str(new_root)
    assert ss.selected_project is None
    assert ss.project is None
    assert ss.script is None
    assert ss.bindings == {}
    assert ss.project_snapshot is None
    assert ss.synthesis is None
    assert ss.catalog_query == "阿尔法"


def test_data_dir_switch_failure_preserves_old_session_context(monkeypatch, tmp_path):
    snapshot = object()
    synthesis = object()
    ss = SessionState(
        project="beta",
        script={"meta": {"title": "贝塔"}},
        bindings={"旁白": "beta.wav"},
    )
    ss.set_selected("alpha")
    ss.set_snapshot(snapshot)
    ss.synthesis = synthesis
    ss.set_catalog_query("阿尔法")
    ss.begin_archive_confirmation()
    before_revision = ss.selection_revision
    before_confirmation = ss._archive_confirmation_revision

    def _fail(_path):
        raise OSError("cannot write config")

    monkeypatch.setattr("ui.settings_handlers.ProjectService.set_data_dir", _fail)
    message, returned = apply_data_dir(str(tmp_path / "failed-root"), ss)

    assert "设置失败" in message
    assert returned == ""
    assert ss.project == "beta"
    assert ss.selected_project == "alpha"
    assert ss.script == {"meta": {"title": "贝塔"}}
    assert ss.bindings == {"旁白": "beta.wav"}
    assert ss.project_snapshot is snapshot
    assert ss.synthesis is synthesis
    assert ss.catalog_query == "阿尔法"
    assert ss.selection_revision == before_revision
    assert ss._archive_confirmation_revision == before_confirmation


def test_cleanup_token_cannot_cross_project(bookshelf_workspace):
    alpha_segments = Path(
        bookshelf_workspace / "projects" / "alpha" / "02_生成音频" / "分段音频"
    )
    alpha_segments.mkdir(parents=True, exist_ok=True)
    candidate = alpha_segments / "stale.part"
    candidate.write_bytes(b"stale")

    alpha_plan = ProjectStorageRepository.scan_cleanup("alpha")
    result = ProjectStorageService.execute_cleanup("beta", alpha_plan.token)

    assert result["stale"] is True
    assert candidate.exists()


def test_ui_ready_catalog_load_is_separate_from_prewarm():
    app_path = Path(__file__).parents[1] / "app.py"
    source = app_path.read_text(encoding="utf-8")
    assert "app.load(\n        catalog_ui.refresh_bookshelf_management_view_with_hierarchy" in source
    assert "app.load(_on_ui_ready_prewarm)" in source
    tree = ast.parse(source)
    load_callbacks = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "load" or not node.args:
            continue
        callback = node.args[0]
        load_callbacks.append(ast.unparse(callback))
    assert "catalog_ui.refresh_bookshelf_management_view_with_hierarchy" in load_callbacks
    assert "_on_ui_ready_prewarm" in load_callbacks
