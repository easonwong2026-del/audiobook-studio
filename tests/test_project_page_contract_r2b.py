"""Round IA-2B Project Page / selected-opened contract coverage."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import gradio as gr

from services.project_catalog import RELATION_INVALID, RELATION_ORPHAN
from services.session import SessionState
from ui import project_catalog_handlers as catalog_handlers
from ui.pages.overview_page import create_overview_page
from ui.wiring.project_catalog_wiring import bookshelf_management_outputs


ROOT = Path(__file__).parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)
NAV_SOURCE = (ROOT / "ui" / "navigation.py").read_text(encoding="utf-8")
CATALOG_WIRING_SOURCE = (
    ROOT / "ui" / "wiring" / "project_catalog_wiring.py"
).read_text(encoding="utf-8")
PAGES_INIT_SOURCE = (ROOT / "ui" / "pages" / "__init__.py").read_text(
    encoding="utf-8"
)


def _summary(status: str, message: str):
    return SimpleNamespace(
        project_name="chapter",
        project_kind="chapter",
        relation_status=status,
        relation_message=message,
        title="章节标题",
        author="作者",
        chapter_title="章节标题",
        chapter_order=1,
        parent_project_name=None,
        project_id="chapter-id",
        parent_project_id=None,
        chapters=1,
        segments=1,
        completed=0,
        failed=0,
        status="⚪未开始",
    )


def test_project_page_and_project_view_compatibility_modules_are_retired():
    assert not (ROOT / "ui" / "pages" / "project_page.py").exists()
    assert not (ROOT / "ui" / "project_view_handlers.py").exists()
    imported_names = {
        alias.name
        for node in ast.walk(APP_TREE)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called_names = {
        node.func.id
        for node in ast.walk(APP_TREE)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "create_project_page" not in imported_names
    assert "create_project_page" not in called_names
    assert "\n    create_project_page," not in PAGES_INIT_SOURCE
    for control in (
        "p_sel",
        "p_refresh",
        "p_open",
        "p_open_msg",
        "p_summary",
        "p_storage",
        "p_chapter_tree",
        "grp_project",
    ):
        assert control not in APP_SOURCE


def test_session_has_separate_selected_and_opened_truth_sources():
    fields = set(SessionState.__dataclass_fields__)
    assert {"project", "selected_project"} <= fields
    assert "p_sel" not in fields

    ss = SessionState(project="opened")
    ss.set_selected("selected")
    assert ss.project == "opened"
    assert ss.selected_project == "selected"


def test_catalog_contract_has_no_project_selector_dependency():
    management_signature = inspect.signature(bookshelf_management_outputs)
    refresh_signature = inspect.signature(
        catalog_handlers.refresh_bookshelf_management_view_with_hierarchy
    )
    assert "project_sel" not in management_signature.parameters
    assert list(refresh_signature.parameters) == ["search_query", "ss"]
    assert "project_sel" not in CATALOG_WIRING_SOURCE
    assert "deps[\"project_sel\"]" not in CATALOG_WIRING_SOURCE

    with gr.Blocks():
        page = create_overview_page()
    assert len(bookshelf_management_outputs(page)) == 24
    assert len(bookshelf_management_outputs(page, include_hierarchy=True)) == 32


def test_navigation_has_only_live_top_level_items_and_no_hidden_project_buttons():
    nav_tree = ast.parse(NAV_SOURCE)
    assignments = {
        target.id: node.value
        for node in nav_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    nav_items = ast.literal_eval(assignments["NAV_ITEMS"])
    group_items = ast.literal_eval(assignments["GROUP_ITEMS"])
    assert [item[0] for item in nav_items] == [
        "overview",
        "voices",
        "synth",
        "export",
    ]
    assert group_items == [
        "overview",
        "create_project",
        "voices",
        "production-nav",
        "synth",
        "review",
        "export",
        "supplement",
        "settings",
    ]
    assert "nav_project" not in NAV_SOURCE
    assert "nav_create_project" not in NAV_SOURCE
    assert "nav_project" not in APP_SOURCE
    assert "nav_create_project" not in APP_SOURCE


def test_open_and_create_wiring_use_live_session_contract():
    hydrate_calls = [
        node
        for node in ast.walk(APP_TREE)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "then"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "hydrate_opened_project"
        )
    ]
    assert hydrate_calls
    assert any(
        len(node.args[2].elts) == 6
        for node in hydrate_calls
        if len(node.args) >= 3 and isinstance(node.args[2], ast.List)
    )
    assert '"open_project_outputs": [' in APP_SOURCE
    assert "p_sel" not in APP_SOURCE
    assert "project_sel" not in APP_SOURCE
    assert "p_summary" not in APP_SOURCE
    assert "p_open.click" not in APP_SOURCE
    assert 'page["bookshelf_open"].click(' in CATALOG_WIRING_SOURCE


def test_open_chain_keeps_live_downstream_refresh_and_removes_project_view_sinks():
    function = next(
        node
        for node in APP_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "_open_chain_rest"
    )
    source = ast.unparse(function)
    for marker in (
        "refresh_voice_cast_ui",
        "refresh_top_status",
        "preview_chapters",
        "refresh_quality_workspace",
        "recover_review_repair",
        "refresh_queue_list",
        "refresh_production_task",
        "render_preview",
        "render_scope_controls",
        "voice_ui.refresh_voice_lib",
        "refresh_production_check",
        "export_ui.refresh_export_default_dir",
        "export_ui.refresh_export_readiness",
        "catalog_ui.refresh_bookshelf_management_view_with_hierarchy",
    ):
        assert marker in source
    for marker in (
        "project_view_ui",
        "render_chapter_tree",
        "refresh_project_storage",
        "reconcile_project_selector",
    ):
        assert marker not in source


def test_inspector_distinguishes_orphan_and_invalid_relation_copy():
    ss = SessionState()
    orphan = catalog_handlers._selected_info(
        "chapter", ss, _summary(RELATION_ORPHAN, "缺少所属整书"), summaries=[]
    )
    invalid = catalog_handlers._selected_info(
        "chapter", ss, _summary(RELATION_INVALID, "所属整书身份冲突"), summaries=[]
    )
    assert "⚠ 未归属章节 · 缺少所属整书" in orphan
    assert "⚠ 关系无效 · 所属整书身份冲突" in invalid
    assert "⚠ 关系无效 · 缺少所属整书" not in orphan
    assert "⚠ 未归属章节 · 所属整书身份冲突" not in invalid
