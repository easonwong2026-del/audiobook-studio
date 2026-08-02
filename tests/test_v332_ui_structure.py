"""v3.3.2 UI structure regressions that do not require starting Gradio."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP)
NAV = (ROOT / "ui/navigation.py").read_text(encoding="utf-8")
VOICE = (ROOT / "ui/pages/voice_page.py").read_text(encoding="utf-8")
SETTINGS = (ROOT / "ui/pages/settings_page.py").read_text(encoding="utf-8")


def _main_col_with():
    for node in ast.walk(APP_TREE):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            target = item.optional_vars
            if isinstance(target, ast.Name) and target.id == "main_col":
                return item.context_expr, node
    raise AssertionError("未找到 main_col")


def test_settings_page_is_created_inside_main_col():
    _, main_col = _main_col_with()
    calls = [
        node for node in ast.walk(main_col)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_settings_page"
    ]
    assert len(calls) == 1
    assert 'set_page = create_settings_page()' in APP
    assert 'grp_settings = set_page["group"]' in APP


def test_groups_match_navigation_items_in_order():
    group_assign = next(
        node for node in ast.walk(APP_TREE)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "_GROUPS"
            for target in node.targets
        )
    )
    group_names = [elt.id for elt in group_assign.value.elts if isinstance(elt, ast.Name)]
    assert group_names == [
        "grp_overview", "grp_create_project", "grp_v4", "grp_v4_role",
        "grp_project", "grp_voices",
        "grp_production_nav", "grp_synth", "grp_review", "grp_export",
        "grp_supplement", "grp_settings",
    ]
    assert '"settings"' in NAV
    assert '"settings"' in APP


def test_settings_page_has_full_width_classes_and_three_tabs():
    assert 'elem_classes=["settings-page"]' in SETTINGS
    assert 'elem_classes=["settings-card"]' in SETTINGS
    for label in ("AI 模型", "数据与存储", "系统信息"):
        assert f'gr.Tab("{label}")' in SETTINGS
    theme = (ROOT / "ui/theme.py").read_text(encoding="utf-8")
    assert ".settings-page" in theme
    assert '[role="tabpanel"]' in theme
    assert "@media (max-width: 1180px)" in theme


def test_voice_page_has_no_orphan_audition_components():
    for name in ("v_audition", "v_audition_audio", "v_audition_status", "v_feedback", "v_feedback_apply"):
        assert name not in VOICE
        assert f'page["{name}"]' not in APP
    assert '"v_bind"' in VOICE
    assert '"v_current"' in VOICE
