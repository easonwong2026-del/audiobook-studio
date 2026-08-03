"""PR #22 域三：角色与声音页布局稳定性（source assertion）。

锁定关键布局参数，防止右侧配置区再变回 ``min_width=0``：
- 右列 ``gr.Column(scale=3, min_width=600, elem_classes=["voice-config-panel"])``；
- 左列 ``gr.Column(scale=1, min_width=280, elem_classes=["role-list-panel"])``；
- theme（渲染后 LIGHT_CSS）含 ``.voice-workspace > .voice-config-panel`` 的
  ``min-width:600px``；
- theme 不再存在 ``.role-list-panel,.voice-config-panel{min-width:0!important}``
  并集规则；
- ``.voice-workspace`` 含 ``flex-wrap:wrap``（窄窗口自然换行）。
"""
from __future__ import annotations

from pathlib import Path

from ui.theme import LIGHT_CSS

ROOT = Path(__file__).resolve().parents[2]
VOICE = (ROOT / "ui/pages/voice_page.py").read_text(encoding="utf-8")
THEME_SRC = (ROOT / "ui/theme.py").read_text(encoding="utf-8")


def test_right_column_is_config_panel_with_min_width_600():
    assert (
        'with gr.Column(scale=3, min_width=600, elem_classes=["voice-config-panel"]):'
        in VOICE
    )


def test_left_column_is_role_list_panel_with_min_width_280():
    assert (
        'with gr.Column(scale=1, min_width=280, elem_classes=["role-list-panel"]):'
        in VOICE
    )


def test_theme_keeps_voice_config_panel_min_width_600():
    assert ".voice-workspace > .voice-config-panel" in LIGHT_CSS
    assert ".voice-workspace > .voice-config-panel { flex:1 1 600px!important; min-width:600px!important; max-width:100%!important; }" in LIGHT_CSS


def test_theme_keeps_left_panel_basis_and_min_width():
    assert ".voice-workspace > .role-list-panel { flex:0 0 300px!important; min-width:280px!important; }" in LIGHT_CSS


def test_theme_no_longer_forces_union_min_width_zero():
    assert (
        ".role-list-panel,.voice-config-panel { min-width:0!important;"
        not in LIGHT_CSS
    )
    assert (
        ".role-list-panel,.voice-config-panel { min-width:0!important;"
        not in THEME_SRC
    )


def test_voice_workspace_wraps_instead_of_nowrap():
    assert ".voice-workspace { gap:16px!important; align-items:start!important; flex-wrap:wrap!important; }" in LIGHT_CSS
    assert "flex-wrap:nowrap!important" not in LIGHT_CSS.split(".voice-workspace")[1].split(".role-list-panel")[0]


def test_narrow_window_config_panel_spans_full_width():
    # @media (max-width: 900px) 内右列铺满（窄窗口自然上下排列）
    assert ".voice-workspace > .voice-config-panel { min-width:0!important; flex-basis:100%!important; }" in LIGHT_CSS


def test_embedded_advanced_role_page_is_width_constrained():
    assert "#grp-v4-role-embedded" in LIGHT_CSS
    assert "max-width:100%" in LIGHT_CSS


def test_voice_page_keeps_existing_components():
    """保留全部既有功能组件（绑定/推荐/资产管理/高级角色整理）。"""
    for name in (
        "v_bind", "v_unbind", "v_recommend", "v_recommendations",
        "v_lib_browser", "v_save_btn", "v_continue_analysis",
        "advanced_role",
    ):
        assert f'"{name}"' in VOICE
