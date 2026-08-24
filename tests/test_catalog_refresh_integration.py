"""T04 统一刷新集成：目录类变更链尾统一接 state-aware bookshelf refresh。

验证（设计 B5 / B9-9），AST + 纯函数：
- 移入回收站成功（archive）→ 链尾 state-aware hierarchy refresh；
- 回收站恢复成功（restore_archived）→ 链尾 state-aware hierarchy refresh；
- 回收站永久删除成功 → 链尾 state-aware hierarchy refresh；
- 从备份恢复成功（restore_backup）→ 链尾 state-aware hierarchy refresh；
- 创建项目成功（cp_json_create 链尾）→ hierarchy refresh；
- 切换数据目录（apply_data_dir）→ hierarchy refresh；
- ``_open_chain_rest``（打开项目后的工作流刷新）**不受影响**（不含目录刷新）。
"""
from __future__ import annotations

import ast
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP_PATH = os.path.join(PROJECT_ROOT, "app.py")
with open(APP_PATH, encoding="utf-8") as file:
    APP_SRC = file.read()
APP_TREE = ast.parse(APP_SRC)

WIRING_PATH = os.path.join(PROJECT_ROOT, "ui", "wiring", "project_catalog_wiring.py")
with open(WIRING_PATH, encoding="utf-8") as file:
    WIRING_SRC = file.read()
WIRING_TREE = ast.parse(WIRING_SRC)

SETTINGS_PATH = os.path.join(PROJECT_ROOT, "ui", "wiring", "settings_wiring.py")
with open(SETTINGS_PATH, encoding="utf-8") as file:
    SETTINGS_SRC = file.read()
SETTINGS_TREE = ast.parse(SETTINGS_SRC)


def _find_func(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _click_block_contains(component: str, needle: str) -> bool:
    """检查某组件的 .click(...) 语句块（其后 1500 字符）是否包含 needle。"""
    marker = f'page["{component}"].click('
    idx = WIRING_SRC.find(marker)
    if idx == -1:
        return False
    block = WIRING_SRC[idx:idx + 1500]
    return needle in block


def test_archive_chain_refreshes_catalog():
    assert _click_block_contains("bookshelf_archive", "management_refresh")


def test_backup_restore_chain_refreshes_catalog():
    assert _click_block_contains("bookshelf_restore", "management_refresh")


def test_trash_restore_chain_refreshes_catalog():
    assert _click_block_contains("bookshelf_trash_restore", "management_refresh")


def test_trash_delete_chain_refreshes_catalog():
    assert _click_block_contains("bookshelf_trash_delete", "management_refresh")


def test_catalog_wiring_has_four_mutation_refresh_subscriptions():
    """四条目录变更链均进入唯一 state-aware reconciliation helper。"""
    count = WIRING_SRC.count("management_refresh")
    assert count >= 4, f"state-aware refresh 订阅数应至少为 4，实际 {count}"


def test_create_project_chain_refreshes_catalog():
    assert "catalog_ui.refresh_bookshelf_management_view_with_hierarchy" in APP_SRC
    assert "voice_create_chain.then(\n        catalog_ui.refresh_bookshelf_management_view_with_hierarchy" in APP_SRC


def test_data_dir_chain_refreshes_catalog():
    """切换数据目录成功链尾统一刷新（settings_wiring 经 catalog_refresh 注入）。"""
    assert "catalog_refresh" in SETTINGS_SRC
    assert "data_dir_chain.then(fn, inputs, outputs)" in SETTINGS_SRC
    assert "wire_settings_page(" in APP_SRC
    assert "catalog_refresh=(" in APP_SRC


def test_open_chain_rest_untouched():
    """打开项目后的工作流刷新链不受目录刷新影响。"""
    fn = _find_func(APP_TREE, "_open_chain_rest")
    assert fn is not None, "_open_chain_rest 未定义"
    src = ast.unparse(fn)
    assert "refresh_project_catalog" not in src, "_open_chain_rest 不应含目录刷新"
    # 仍覆盖打开项目后的工作流关键刷新
    for marker in (
        "refresh_top_status", "preview_chapters", "refresh_queue_list",
        "render_preview", "voice_ui.refresh_voice_lib",
        "refresh_production_check", "refresh_export_default_dir",
        "catalog_ui.refresh_bookshelf_management_view_with_hierarchy",
    ):
        assert marker in src, f"_open_chain_rest 应保留 {marker} 刷新"
