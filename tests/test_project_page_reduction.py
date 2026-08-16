"""T03 项目页减法 + 选择/打开隔离（AST 回归，无法 import app.py）。

断言：
- ``ui/pages/project_page.py`` 不再渲染「更多操作」/「回收站」Accordion，
  且不再包含 ``p_cleanup`` / ``p_backup`` / ``p_trash_*`` 等资产管理控件
  （别名键 ``: None`` 允许保留，不渲染控件）；
- ``app.py`` 的 ``ov_bookshelf.select`` 只设 selected，不再链式 ``open_project``；
- 项目页保留选择项目 / 打开项目 / 项目信息 / 存储 / 书稿结构。
"""
from __future__ import annotations

import ast
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

PROJECT_PAGE_PATH = os.path.join(PROJECT_ROOT, "ui", "pages", "project_page.py")
with open(PROJECT_PAGE_PATH, encoding="utf-8") as file:
    PROJECT_SRC = file.read()
PROJECT_TREE = ast.parse(PROJECT_SRC)

APP_PATH = os.path.join(PROJECT_ROOT, "app.py")
with open(APP_PATH, encoding="utf-8") as file:
    APP_SRC = file.read()
APP_TREE = ast.parse(APP_SRC)


def _assigned_names(tree):
    """收集所有赋值语句的目标变量名（含解包与下标目标）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        names.add(name.id)
    return names


def test_project_page_has_no_more_actions_accordion():
    assert 'gr.Accordion("更多操作"' not in PROJECT_SRC, "「更多操作」Accordion 应移除"
    assert 'gr.Accordion("回收站"' not in PROJECT_SRC, "「回收站」Accordion 应移除"


def test_project_page_has_no_asset_management_controls():
    assigned = _assigned_names(PROJECT_TREE)
    for control in (
        "p_open_dir", "p_archive", "p_cleanup", "p_cleanup_confirm",
        "p_cleanup_cancel", "p_cleanup_token", "p_integrity",
        "p_integrity_repair", "p_backup", "p_restore", "p_trash_table",
        "p_trash_sel", "p_trash_refresh", "p_trash_restore",
        "p_trash_confirm", "p_trash_delete",
    ):
        assert control not in assigned, f"project_page.py 不应再渲染控件 {control}"


def test_project_page_keeps_core_workflow_controls():
    assigned = _assigned_names(PROJECT_TREE)
    for control in ("p_sel", "p_refresh", "p_open", "p_summary", "p_storage", "p_chapter_tree"):
        assert control in assigned, f"项目页应保留核心工作流控件 {control}"


def _select_call(tree):
    """找到 ``ov_bookshelf.select(...)`` 的 Call 节点。"""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "select"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ov_bookshelf"
        ):
            return node
    return None


def _build_parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def test_bookshelf_select_no_longer_opens_project():
    """书架 select 只设 selected，不再链 open_project。"""
    # 新接线引用 catalog_ui.select_bookshelf_row
    assert "ov_bookshelf.select(\n        catalog_ui.select_bookshelf_row" in APP_SRC or \
        "ov_bookshelf.select(catalog_ui.select_bookshelf_row" in APP_SRC, \
        "ov_bookshelf.select 应接到 catalog_ui.select_bookshelf_row"
    # 旧接线（select → 回填 p_sel → then(open_project)）不得存在
    assert "select_project_from_bookshelf, [ov_bookshelf], [p_sel]).then(open_project" not in APP_SRC, \
        "书架 select 不应再链式打开项目"
    # AST：select 调用不能作为 .then(open_project) 链的一环
    node = _select_call(APP_TREE)
    assert node is not None, "未找到 ov_bookshelf.select 接线"
    parents = _build_parent_map(APP_TREE)
    current = node
    while current is not None:
        if (
            isinstance(current, ast.Call)
            and isinstance(current.func, ast.Attribute)
            and current.func.attr == "then"
            and current.args
            and isinstance(current.args[0], ast.Name)
            and current.args[0].id == "open_project"
        ):
            raise AssertionError("书架 select 仍链式打开项目")
        current = parents.get(current)


def test_open_project_still_wired_from_project_page_and_quick_action():
    """打开项目仍可从项目页 p_open 与概览快捷 ov_open 触发。"""
    assert "p_open.click(open_project," in APP_SRC
    assert "ov_open.click(" in APP_SRC
    assert ".then(open_project, [p_sel, ss]" in APP_SRC


def test_project_page_old_aliases_kept_as_none():
    """旧页面字典键保留为 None 别名（兼容扩展，不渲染控件）。"""
    assert '"p_archive": None' in PROJECT_SRC
    assert '"p_trash_table": None' in PROJECT_SRC
