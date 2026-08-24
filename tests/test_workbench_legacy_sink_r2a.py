"""Round IA-2A caller and visible-replacement contracts."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)
OVERVIEW_SOURCE = (ROOT / "ui/pages/overview_page.py").read_text(encoding="utf-8")
NAV_SOURCE = (ROOT / "ui/navigation.py").read_text(encoding="utf-8")
CATALOG_WIRING_SOURCE = (
    ROOT / "ui/wiring/project_catalog_wiring.py"
).read_text(encoding="utf-8")
VOICE_WIRING_SOURCE = (ROOT / "ui/wiring/voice_wiring.py").read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    for node in APP_TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"missing app.py function: {name}")


def _constant_assignment(source: str, name: str):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"missing constant assignment: {name}")


def test_hidden_workbench_sinks_and_dashboard_refresh_are_retired():
    sinks = (
        "ov_status",
        "ov_progress",
        "ov_task",
        "ov_issues",
        "ov_open",
        "ov_voices",
        "ov_synth",
        "ov_export",
    )
    for sink in sinks:
        assert sink not in APP_SOURCE
        assert sink not in OVERVIEW_SOURCE
    assert "grp-workbench-legacy-sink" not in OVERVIEW_SOURCE
    assert "refresh_overview" not in APP_SOURCE
    assert "_dashboard_snapshot" not in APP_SOURCE
    assert "empty_dashboard_html" not in APP_SOURCE
    assert not (ROOT / "ui/components/dashboard.py").exists()


def test_visible_navigation_and_workbench_open_replacements_remain_wired():
    nav_items = _constant_assignment(NAV_SOURCE, "NAV_ITEMS")
    assert [item[0] for item in nav_items] == [
        "overview",
        "voices",
        "synth",
        "export",
    ]
    assert '_SETTINGS_ITEM = ("settings"' in NAV_SOURCE
    for nav_button in ("nav_voices", "nav_synth", "nav_export"):
        assert f"{nav_button}.click(" in APP_SOURCE
    assert '"bookshelf_open": bookshelf_open' in OVERVIEW_SOURCE
    assert 'page["bookshelf_open"].click(' in CATALOG_WIRING_SOURCE
    assert "open_chain_rest" in CATALOG_WIRING_SOURCE
    assert "ov_bookshelf.select(" in APP_SOURCE


def test_voice_wiring_no_longer_receives_dead_project_injection():
    calls = [
        node
        for node in ast.walk(APP_TREE)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "wire_voice_page"
        )
    ]
    assert len(calls) == 1
    context = calls[0].args[1]
    assert isinstance(context, ast.Dict)
    keys = {
        key.value
        for key in context.keys
        if isinstance(key, ast.Constant)
    }
    assert keys == {"session", "production_voice", "callbacks"}
    assert 'context["project"]' not in VOICE_WIRING_SOURCE
    assert 'context["callbacks"]' in VOICE_WIRING_SOURCE
    assert 'context["session"]' in VOICE_WIRING_SOURCE
    assert 'context["production_voice"]' in VOICE_WIRING_SOURCE


def test_open_chain_keeps_downstream_refresh_contract_without_dashboard_tuple():
    chain = _function_source("_open_chain_rest")
    assert "refresh_overview" not in chain
    for marker in (
        "export_ui.reconcile_export_state",
        "refresh_voice_cast_ui",
        "catalog_ui.reconcile_project_selector",
        "refresh_top_status",
        "preview_chapters",
        "refresh_quality_workspace",
        "recover_review_repair",
        "refresh_queue_list",
        "refresh_production_task",
        "project_view_ui.render_chapter_tree",
        "project_view_ui.refresh_project_storage",
        "render_preview",
        "render_scope_controls",
        "voice_ui.refresh_voice_lib",
        "refresh_production_check",
        "export_ui.refresh_export_default_dir",
        "export_ui.refresh_export_readiness",
        "catalog_ui.refresh_bookshelf_management_view_with_hierarchy",
    ):
        assert marker in chain, f"open chain lost {marker}"
    assert "refresh_merge_after(chain)" in CATALOG_WIRING_SOURCE
    assert "merge_refresh" in CATALOG_WIRING_SOURCE
    assert "assembly_refresh" in CATALOG_WIRING_SOURCE


def test_project_page_compatibility_contract_is_frozen_for_ia_2b():
    for marker in (
        "grp_project",
        "p_sel",
        "p_open",
        "p_refresh",
        "p_summary",
        "p_storage",
        "p_chapter_tree",
        "create_project_page",
    ):
        assert marker in APP_SOURCE
    assert (ROOT / "ui/pages/project_page.py").exists()
    assert "project_view_ui" in APP_SOURCE
