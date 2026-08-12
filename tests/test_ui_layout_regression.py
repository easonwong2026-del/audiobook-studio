"""Regression contracts for the Windows right-pane and role-list layout."""
import ast
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


def test_settings_page_preserves_current_user_facing_sections():
    settings = _text("ui/pages/settings_page.py")
    theme = _text("ui/theme.py")

    assert 'elem_classes=["settings-page"]' in settings
    assert 'elem_classes=["settings-card"]' in settings
    for label in ("数据与项目", "TTS 与导出", "系统信息"):
        assert f'gr.Tab("{label}")' in settings
    for forbidden in ("AI 模型", "Provider", "API Key", "模型刷新", "连接测试"):
        assert forbidden not in settings
    assert ".settings-page" in theme
    assert '[role="tabpanel"]' in theme
    assert "@media (max-width: 1180px)" in theme


def test_voice_page_has_no_orphan_audition_components():
    voice = _text("ui/pages/voice_page.py")
    app = _text("app.py")

    for name in ("v_audition", "v_audition_audio", "v_audition_status", "v_feedback", "v_feedback_apply"):
        assert name not in voice
        assert f'page["{name}"]' not in app
    assert '"v_bind"' in voice
    assert '"v_current"' in voice


def test_settings_page_is_created_inside_main_col():
    app = _text("app.py")
    tree = ast.parse(app)
    main_col = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        for item in node.items
        if isinstance(item.optional_vars, ast.Name)
        and item.optional_vars.id == "main_col"
    )
    settings_calls = [
        node
        for node in ast.walk(main_col)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_settings_page"
    ]
    assert len(settings_calls) == 1
    assert "set_page = create_settings_page()" in app
    assert 'grp_settings = set_page["group"]' in app
