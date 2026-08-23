"""Round 3B contracts for the residual Project Page boundary."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)
PROJECT_VIEW_SOURCE = (ROOT / "ui" / "project_view_handlers.py").read_text(
    encoding="utf-8"
)
PROJECT_VIEW_TREE = ast.parse(PROJECT_VIEW_SOURCE)
PROJECT_PAGE_SOURCE = (ROOT / "ui" / "pages" / "project_page.py").read_text(
    encoding="utf-8"
)
CATALOG_SOURCE = (ROOT / "ui" / "project_catalog_handlers.py").read_text(
    encoding="utf-8"
)


def _top_level_function_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"missing function: {name}")


def _name_list(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.List)
        and len(node.elts) == 1
        and isinstance(node.elts[0], ast.Name)
        and node.elts[0].id == name
    )


def _has_then(function: ast.AST, callback: str, inputs: str, outputs: str) -> bool:
    owner, attr = callback.split(".", 1)
    for node in ast.walk(function):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "then"
            and len(node.args) >= 3
        ):
            continue
        callback_node, input_node, output_node = node.args[:3]
        if (
            isinstance(callback_node, ast.Attribute)
            and isinstance(callback_node.value, ast.Name)
            and callback_node.value.id == owner
            and callback_node.attr == attr
            and _name_list(input_node, inputs)
            and _name_list(output_node, outputs)
        ):
            return True
    return False


def _p_refresh_callback() -> ast.Call:
    for node in ast.walk(APP_TREE):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "click"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "p_refresh"
        ):
            continue
        return node
    raise AssertionError("missing p_refresh.click")


def test_storage_summary_has_project_view_owner_and_both_live_callers():
    assert "refresh_project_storage" not in _top_level_function_names(APP_TREE)
    assert "def refresh_project_storage(" in PROJECT_VIEW_SOURCE
    assert "ProjectStorageService.format_summary(ss.project)" in PROJECT_VIEW_SOURCE
    assert "from services.project_storage import ProjectStorageService" in PROJECT_VIEW_SOURCE
    for name in ("_open_chain_rest", "_post_archive_reconcile"):
        function = _function(APP_TREE, name)
        assert _has_then(
            function,
            "project_view_ui.refresh_project_storage",
            "ss",
            "p_storage",
        )


def test_storage_summary_empty_normal_and_exception_contract_is_unchanged(monkeypatch):
    from ui import project_view_handlers

    assert (
        project_view_handlers.refresh_project_storage(None)
        == "项目目录、存储占用和完整性状态会显示在这里。"
    )
    assert (
        project_view_handlers.refresh_project_storage(SimpleNamespace(project=""))
        == "项目目录、存储占用和完整性状态会显示在这里。"
    )

    monkeypatch.setattr(
        project_view_handlers.ProjectStorageService,
        "format_summary",
        staticmethod(lambda name: f"SUMMARY:{name}"),
    )
    assert (
        project_view_handlers.refresh_project_storage(SimpleNamespace(project="alpha"))
        == "SUMMARY:alpha"
    )

    def fail(_name):
        raise RuntimeError("baseline boom")

    monkeypatch.setattr(
        project_view_handlers.ProjectStorageService,
        "format_summary",
        staticmethod(fail),
    )
    assert (
        project_view_handlers.refresh_project_storage(SimpleNamespace(project="alpha"))
        == "#### 项目存储\n❌ 无法读取项目目录：baseline boom"
    )


def test_selector_wrappers_are_removed_and_live_paths_use_catalog_authority():
    names = _top_level_function_names(APP_TREE)
    assert "refresh_projects_full" not in names
    assert "refresh_p_sel" not in names
    assert "refresh_projects_full" not in APP_SOURCE
    assert "refresh_p_sel" not in APP_SOURCE

    callback = _p_refresh_callback().args[0]
    assert ast.unparse(callback) == "catalog_ui.reconcile_project_selector"
    inputs, outputs = _p_refresh_callback().args[1:3]
    assert _name_list(inputs, "ss")
    assert _name_list(outputs, "p_sel")
    assert _has_then(
        _function(APP_TREE, "_open_chain_rest"),
        "catalog_ui.reconcile_project_selector",
        "ss",
        "p_sel",
    )


def test_project_page_residual_callbacks_are_not_live_or_redeclared():
    names = _top_level_function_names(APP_TREE)
    for name in (
        "clear_project_view",
        "open_project_folder",
        "clear_project_cache",
        "delete_project",
    ):
        assert name not in names

    # The old project-page asset controls are aliases only; the live controls
    # are rendered and wired by the Project Catalog page.
    for control in (
        "p_open_dir",
        "p_cleanup",
        "p_backup",
        "p_trash_table",
    ):
        assert f'"{control}": None' in PROJECT_PAGE_SOURCE
    for owner in (
        "def open_selected_directory",
        "def scan_selected_cleanup",
        "def archive_selected",
    ):
        assert owner in CATALOG_SOURCE


def test_project_view_chains_keep_opened_selection_isolation():
    for name in ("_open_chain_rest", "_post_archive_reconcile"):
        function = _function(APP_TREE, name)
        source = ast.unparse(function)
        assert "selected_project" not in source
        assert _has_then(function, "project_view_ui.render_chapter_tree", "p_sel", "p_chapter_tree")
        assert _has_then(
            function,
            "project_view_ui.refresh_project_storage",
            "ss",
            "p_storage",
        )


def test_bookshelf_selection_uses_catalog_handler_without_project_page_update():
    assert "catalog_ui.select_bookshelf_row" in APP_SOURCE
    assert "select_project_from_bookshelf, [ov_bookshelf], [p_sel]" not in APP_SOURCE
