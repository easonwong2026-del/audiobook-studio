"""PR #22 域二：统一导航机制回归（AST/source assertion，参考 test_v332_ui_structure.py）。

验证：
- app.py 不再出现裸内联 ``classList.add('active')``（全部收敛到 ``activate_js``）；
- 所有 ``nav_*.click`` / ``ov_*`` / 书架 / 打开链 / ``creation_chain`` 均使用
  ``js=activate_js(page_id)``（页面可见性与左侧高亮同一目标值，单源）；
- ``nav_active_elem_id`` 生产内部页（synth/review/supplement/production-nav）
  统一映射 ``nav-synth``。
"""
from __future__ import annotations

import ast
from pathlib import Path

from ui.navigation import activate_js, nav_active_elem_id

ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP)
NAV_SRC = (ROOT / "ui/navigation.py").read_text(encoding="utf-8")


def test_app_has_no_bare_inline_active_js():
    """app.py 不再出现裸内联 classList.add('active')（全部收敛到 activate_js）。"""
    assert "classList.add('active')" not in APP


def test_navigation_module_defines_single_source_helpers():
    assert "def nav_active_elem_id(" in NAV_SRC
    assert "def activate_js(" in NAV_SRC
    assert "def go(" in NAV_SRC
    assert "def _goto(" in NAV_SRC


def test_nav_clicks_use_activate_js():
    """每个 nav_*.click 都使用 js=activate_js(page_id)。"""
    for page_id in (
        "overview", "project", "create_project", "v4", "v4_role",
        "settings", "voices", "synth", "export",
    ):
        assert f'js=activate_js("{page_id}")' in APP, (
            f"nav_{page_id}.click 未使用 js=activate_js({page_id!r})"
        )


def test_creation_chain_goto_voices_activates_voices():
    """创建项目成功后自动进入角色与声音页，左侧同步高亮。"""
    assert "_open_chain_rest(creation_chain).then(" in APP
    index = APP.index("_open_chain_rest(creation_chain)")
    segment = APP[index:index + 300]
    assert 'lambda: _goto("voices")' in segment
    assert 'js=activate_js("voices")' in segment


def test_overview_shortcuts_use_activate_js():
    """概览页快捷操作（书架/打开/voices/synth/export）统一走 activate_js。"""
    assert 'js=activate_js("project")' in APP
    assert 'js=activate_js("voices")' in APP
    assert 'js=activate_js("synth")' in APP
    assert 'js=activate_js("export")' in APP


def test_production_stage_change_keeps_no_activate_js():
    """生产内部切换不附加 activate_js（高亮保持 nav-synth，行为正确）。"""
    assert "production_stage.change(_goto" in APP
    assert "production_stage.change(_goto, [production_stage], _GROUPS)" in APP


def test_production_internal_pages_map_to_nav_synth():
    for page_id in ("synth", "review", "supplement", "production-nav"):
        assert nav_active_elem_id(page_id) == "nav-synth"


def test_activate_js_targets_correct_elem():
    assert "nav-voices" in activate_js("voices")
    assert "nav-overview" in activate_js("overview")
    assert "nav-synth" in activate_js("review")
    assert "nav-synth" in activate_js("production-nav")
    assert "classList.add('active')" in activate_js("voices")


def test_activate_js_removes_all_active_classes():
    js = activate_js("voices")
    assert "querySelectorAll('.nav-btn')" in js
    assert "forEach" in js
    assert "classList.remove('active')" in js
