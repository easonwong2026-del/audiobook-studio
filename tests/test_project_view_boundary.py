"""Round 2A structural contracts for the Project View / Chapter Tree boundary."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
APP_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
PROJECT_VIEW_TREE = ast.parse(
    (ROOT / "ui" / "project_view_handlers.py").read_text(encoding="utf-8")
)
PROJECT_VIEW_SOURCE = (ROOT / "ui" / "project_view_handlers.py").read_text(
    encoding="utf-8"
)
PROJECT_MANAGER_SOURCE = (ROOT / "lib" / "project_manager.py").read_text(
    encoding="utf-8"
)


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"missing function: {name}")


def _is_name_list(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.List)
        and len(node.elts) == 1
        and isinstance(node.elts[0], ast.Name)
        and node.elts[0].id == name
    )


def _has_chapter_tree_then(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "then"
            and len(node.args) >= 3
        ):
            continue
        callback, inputs, outputs = node.args[:3]
        if (
            isinstance(callback, ast.Attribute)
            and isinstance(callback.value, ast.Name)
            and callback.value.id == "project_view_ui"
            and callback.attr == "render_chapter_tree"
            and _is_name_list(inputs, "p_sel")
            and _is_name_list(outputs, "p_chapter_tree")
        ):
            return True
    return False


def test_app_does_not_own_chapter_tree_implementation():
    definitions = {
        node.name
        for node in APP_TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "render_chapter_tree" not in definitions
    assert "_pm.build_chapter_tree" not in APP_SOURCE
    assert "from ui import project_view_handlers as project_view_ui" in APP_SOURCE


def test_open_and_archive_refresh_chains_delegate_to_project_view_handler():
    assert _has_chapter_tree_then(_function(APP_TREE, "_open_chain_rest"))
    assert _has_chapter_tree_then(_function(APP_TREE, "_post_archive_reconcile"))


def test_project_view_handler_uses_legal_project_service_boundary():
    function = _function(PROJECT_VIEW_TREE, "render_chapter_tree")
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open_project"
    ]
    assert calls, "Chapter Tree must load through ProjectService.open_project"
    assert "ProjectService.open_project(project)" in PROJECT_VIEW_SOURCE
    assert "open(" not in PROJECT_VIEW_SOURCE.replace("open_project", "")
    assert "json" not in PROJECT_VIEW_SOURCE


def test_project_manager_no_longer_contains_chapter_tree_or_ui_dependency():
    assert "def build_chapter_tree(" not in PROJECT_MANAGER_SOURCE
    assert "chapter_identity" not in PROJECT_MANAGER_SOURCE
    assert "from ui" not in PROJECT_MANAGER_SOURCE


def test_project_view_chain_keeps_selected_opened_project_isolation():
    """The view consumes p_sel, never the bookshelf's selected_project mirror."""
    for name in ("_open_chain_rest", "_post_archive_reconcile"):
        function = _function(APP_TREE, name)
        assert _has_chapter_tree_then(function)
        assert "selected_project" not in ast.unparse(function)
