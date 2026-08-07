"""Regression contracts for the Windows right-pane and role-list layout."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_main_area_uses_intrinsic_width_safe_flex_contract():
    theme = _text("ui/theme.py")

    assert ".main-area {{ border:none!important; flex:1 1 0!important; width:0!important;" in theme
    assert "overflow-x:hidden!important" in theme
    assert "html {{" in theme
    assert "overflow-y:scroll!important" in theme
    assert "scrollbar-gutter:stable" in theme
    assert ".gradio-container {{" in theme
    assert "width:100%!important" in theme

    for page_id in (
        "grp-overview",
        "grp-create-project",
        "grp-project",
        "grp-voices",
        "grp-synth",
        "grp-review",
        "grp-export",
        "grp-settings",
    ):
        assert f".main-area > #{page_id}" in theme


def test_role_list_targets_the_actual_choice_container_and_keeps_fallback():
    theme = _text("ui/theme.py")
    voice_page = _text("ui/pages/voice_page.py")

    assert 'elem_classes=["role-management-list"]' in voice_page
    assert ".role-management-list > div:has(> label)" in theme
    assert '.role-management-list [role="radiogroup"]' in theme
    assert "flex-direction:column!important" in theme
    assert "flex-wrap:nowrap!important" in theme
    assert "overflow-y:auto!important" in theme
    assert "overflow-x:hidden!important" in theme
    assert ".role-management-list .wrap" in theme
