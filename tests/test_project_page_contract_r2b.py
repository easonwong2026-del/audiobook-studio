"""Round IA-2B Project Page / selected-opened contract coverage."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import gradio as gr

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
VOICE_WIRING_SOURCE = (ROOT / "ui" / "wiring" / "voice_wiring.py").read_text(
    encoding="utf-8"
)
PAGES_INIT_SOURCE = (ROOT / "ui" / "pages" / "__init__.py").read_text(
    encoding="utf-8"
)
PRODUCTION_SOURCE = "\n".join(
    path.read_text(encoding="utf-8")
    for root in (ROOT / "ui", ROOT / "services", ROOT / "lib")
    for path in root.rglob("*.py")
)
PRODUCTION_SOURCE += "\n" + APP_SOURCE


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
    refresh_signature = inspect.signature(catalog_handlers.refresh_bookshelf_management_view)
    assert "project_sel" not in management_signature.parameters
    assert list(refresh_signature.parameters) == ["search_query", "ss"]
    assert "project_sel" not in CATALOG_WIRING_SOURCE
    assert "deps[\"project_sel\"]" not in CATALOG_WIRING_SOURCE

    with gr.Blocks():
        page = create_overview_page()
    assert len(bookshelf_management_outputs(page)) == 24


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
            and node.func.attr in {"then", "success"}
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
        "catalog_ui.refresh_bookshelf_management_view",
    ):
        assert marker in source
    for marker in (
        "project_view_ui",
        "render_chapter_tree",
        "refresh_project_storage",
        "reconcile_project_selector",
    ):
        assert marker not in source


def test_ia2a_legacy_workbench_sinks_remain_permanently_retired():
    assert not (ROOT / "ui" / "components" / "dashboard.py").exists()
    for marker in (
        "ov_status",
        "ov_progress",
        "ov_task",
        "ov_issues",
        "ov_open",
        "ov_voices",
        "ov_synth",
        "ov_export",
        "refresh_overview",
        "_dashboard_snapshot",
        "grp-workbench-legacy-sink",
    ):
        assert marker not in PRODUCTION_SOURCE


def test_voice_wiring_does_not_receive_project_compatibility_injection():
    assert '"project": p_sel' not in APP_SOURCE
    assert "p_sel" not in VOICE_WIRING_SOURCE
    assert 'context["project"]' not in VOICE_WIRING_SOURCE
    assert 'context["session"]' in VOICE_WIRING_SOURCE
    assert 'context["production_voice"]' in VOICE_WIRING_SOURCE
