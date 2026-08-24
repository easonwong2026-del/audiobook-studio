"""Current voice-workspace and user-facing production UI contracts."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from ui.components.voice_binding import (
    build_role_management_choices,
    build_role_management_rows,
    format_bound_role_choices,
    format_role_choices,
)
from ui.theme import LIGHT_CSS, THEME
from ui.tokens import (
    ACCENT_DEEP,
    ACCENT_SOFT,
    BORDER,
    CARD,
    PANEL,
    SIDEBAR,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_RULE_RE = re.compile(r"(?P<selectors>[^{}]+)\{(?P<body>[^{}]*)\}", re.DOTALL)


def _css_rules(css: str) -> list[tuple[list[str], str]]:
    """Return simple CSS rule blocks from rendered CSS (nested rules are skipped)."""
    css = _CSS_COMMENT_RE.sub("", css)
    return [
        (
            [re.sub(r"\s+", " ", selector.strip()) for selector in match.group("selectors").split(",")],
            match.group("body"),
        )
        for match in _CSS_RULE_RE.finditer(css)
    ]


def _extract_css_rule(css: str, selector: str) -> str:
    """Find one rendered CSS declaration block by an exact selector member."""
    wanted = re.sub(r"\s+", " ", selector.strip())
    for selectors, body in _css_rules(css):
        if wanted in selectors:
            return body
    raise AssertionError(f"CSS selector not found: {selector}")


def _assert_css_declarations(rule: str, *declarations: str) -> None:
    actual = {}
    for match in re.finditer(
        r"(?P<property>[-\w]+)\s*:\s*(?P<value>[^;]+)",
        rule,
    ):
        value = re.sub(r"\s+", "", match.group("value")).lower()
        if value.endswith("!important"):
            value = value[: -len("!important")]
        actual[match.group("property").lower()] = value

    missing = [
        declaration
        for declaration in declarations
        if (
            (property_name := declaration.split(":", 1)[0].lower()) not in actual
            or actual[property_name]
            != re.sub(r"\s+", "", declaration.split(":", 1)[1]).lower()
        )
    ]
    assert not missing, f"Missing CSS declarations: {missing}; rule={rule!r}"


def _broad_color_wildcards(css: str) -> list[str]:
    """Reject only wildcard selectors that impose color on an entire surface."""
    broad = re.compile(
        r"^(?:\*|html\s+\*|body(?:\.dark)?\s+\*|\.gradio-container\s+\*|"
        r"(?:html|body(?:\.dark)?|\.gradio-container)\s*>\s*\*)$"
    )
    result = []
    for selectors, body in _css_rules(css):
        if "color:" not in re.sub(r"\s+", "", body):
            continue
        result.extend(selector for selector in selectors if broad.fullmatch(selector))
    return result


def test_role_choices_include_description_without_changing_value():
    script = {
        "voices": {
            "旁白": {"description": "沉稳男中音"},
            "小雨": {"description": "清亮女声"},
        }
    }
    choices = format_role_choices(script, {"旁白": None, "小雨": "/tmp/x.wav"})
    labels = {value: label for label, value in choices}
    assert labels["旁白"].endswith("旁白（沉稳男中音）")
    assert labels["小雨"].endswith("小雨（清亮女声）")
    assert [value for _, value in format_bound_role_choices(script, {"小雨": "/tmp/x.wav"})] == ["小雨"]


def test_role_management_rows_scale_and_filter_without_mutating_bindings():
    roles = {
        f"角色{i:02d}": {"description": f"声线描述 {i:02d}"}
        for i in range(55)
    }
    bindings = {"角色00": "/tmp/role00.wav", "角色42": "/tmp/role42.wav"}
    rows = build_role_management_rows({"voices": roles}, bindings)
    choices = build_role_management_choices({"voices": roles}, bindings)
    assert len(rows) == 55
    assert len(choices) == 55
    assert rows[0] == ["角色00", "声线描述 00", "✅ 已绑定"]
    assert rows[42][2] == "✅ 已绑定"
    assert rows[54][2] == "⚠ 待绑定"
    filtered = build_role_management_rows({"voices": roles}, bindings, "42")
    assert filtered == [["角色42", "声线描述 42", "✅ 已绑定"]]
    assert bindings["角色00"] == "/tmp/role00.wav"


def test_role_management_choices_keep_role_value_and_multiline_summary():
    choices = build_role_management_choices(
        {"voices": {"妈妈": {"description": "温柔女声，30岁"}}},
        {"妈妈": None},
    )
    assert choices == [("妈妈\n温柔女声，30岁\n⚠ 待绑定", "妈妈")]


def test_role_list_selection_loads_right_hand_configuration(monkeypatch):
    from ui import voice_handlers as voice_ui

    snapshot = SimpleNamespace(
        script={"voices": {"旁白": {"description": "沉稳男中音"}, "妈妈": {"description": "温柔女声"}}},
        bindings={"旁白": "/tmp/narrator.wav", "妈妈": None},
    )
    session = SimpleNamespace(project="demo", bindings=snapshot.bindings)
    monkeypatch.setattr(voice_ui, "_snapshot", lambda _session: snapshot)
    result = voice_ui.select_role_from_list("妈妈", session)
    assert result[0] == "妈妈"
    assert "当前角色：妈妈" in result[1]
    assert result[2]["value"] is None
    assert result[4] == "*当前绑定音频：未选择*"


def test_voice_cast_finalize_starts_hidden_until_formal_project_is_open():
    import gradio as gr

    from ui.pages.voice_page import create_voice_page

    with gr.Blocks():
        page = create_voice_page()
    finalize = page["v_cast_finalize"]
    assert finalize.visible is False
    assert finalize.interactive is False


def test_production_check_parses_snapshot_raw_script(monkeypatch):
    import app

    snapshot = SimpleNamespace(
        script={
            "meta": {"title": "验收书"},
            "voices": {"旁白": {"description": "沉稳男中音"}},
            "chapters": [{"id": 1, "title": "第一章", "segments": [
                {"id": "1-001", "role": "旁白", "text": "测试"},
            ]}],
        },
        bindings={"旁白": None},
    )
    session = SimpleNamespace(project="demo")
    monkeypatch.setattr(app, "_snap", lambda _session: snapshot)
    result = app.refresh_production_check(session)
    assert "✅ 剧本有效" in result
    assert "未绑定声音" in result


def test_production_stage_has_internal_navigation_and_check():
    navigation = _text("ui/navigation.py")
    production = _text("ui/components/production_nav.py")
    app = _text("app.py")
    assert '"production-nav"' in navigation
    assert '"🎛 合成中心", "synth"' in production
    assert '"🔍 试听质检", "review"' in production
    assert '"🎤 角色补录", "supplement"' in production
    assert "def refresh_production_check" in app
    assert "production_stage.change(_goto" in app
def test_script_example_and_user_facing_quality_labels_exist():
    example = json.loads(_text("structured_script.example.json"))
    assert set(example["voices"]) == {"旁白", "小雨"}
    synthesis_page = _text("ui/pages/synthesis_page.py")
    export_page = _text("ui/pages/export_page.py")
    assert '("快速", 1)' in synthesis_page
    assert '("标准", 2)' in synthesis_page
    assert '("高质量", 3)' in synthesis_page
    assert "e_save_dir_hint" in export_page


def test_brand_logo_is_a_reusable_unfiltered_component():
    component = _text("ui/components/brand_logo.py")
    navigation = _text("ui/navigation.py")
    theme = _text("ui/theme.py")
    assert "audiobook-studio-sidebar-mark-v1.png" in component
    assert "def create_brand_logo" in component
    assert "create_brand_logo()" in navigation
    assert "filter:none!important" in theme
    assert "object-fit:contain!important" in theme


def test_launcher_icon_assets_are_available_in_png_and_multisize_ico():
    for relative in (
        "icon.png",
        "icon.ico",
        "assets/brand/audiobook-studio-icon-v3.2.1.png",
        "assets/brand/audiobook-studio-icon-v3.2.1.ico",
    ):
        assert (ROOT / relative).is_file()

    assert (ROOT / "icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    ico = (ROOT / "icon.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(ico[4:6], "little") == 7


def test_theme_keeps_text_readable_and_voice_layout_compact():
    theme = _text("ui/theme.py")
    voice = _text("ui/pages/voice_page.py")
    assert 'body_background_fill=SURFACE' in theme
    assert '[data-testid="block-label"]' in theme
    assert ".sidebar .nav-btn" in theme
    assert ".voice-workspace {{ gap:16px!important;" in theme
    assert ".role-management-list label:has(input:checked)" in theme
    assert ".voice-config-footer" in theme
    assert ".voice-reference-upload .audio-container button.boundedheight" in theme
    assert 'elem_classes=["voice-reference-upload"]' in voice


def test_theme_keeps_gradio_550_components_readable_in_dark_preference():
    expected_dark_tokens = {
        "body_text_color_subdued_dark": TEXT_MUTED,
        "block_info_text_color_dark": TEXT_MUTED,
        "block_label_text_color_dark": TEXT_MUTED,
        "block_title_text_color_dark": TEXT_PRIMARY,
        "input_background_fill_dark": CARD,
        "input_background_fill_focus_dark": CARD,
        "input_border_color_dark": BORDER,
        "checkbox_label_background_fill_dark": CARD,
        "checkbox_label_background_fill_selected_dark": ACCENT_SOFT,
        "checkbox_label_text_color_dark": TEXT_PRIMARY,
        "checkbox_label_text_color_selected_dark": TEXT_PRIMARY,
    }
    for token, value in expected_dark_tokens.items():
        assert getattr(THEME, token) == value

    # Use the rendered CSS: the source f-string contains doubled braces and is
    # not the stylesheet that Gradio actually receives.
    assert _broad_color_wildcards(LIGHT_CSS) == []

    radio = _extract_css_rule(
        LIGHT_CSS,
        '.gradio-container label[data-testid$="-radio-label"]',
    )
    _assert_css_declarations(
        radio,
        f"background:{CARD}",
        f"color:{TEXT_PRIMARY}",
        f"border:1px solid {BORDER}",
        "opacity:1",
    )
    radio_hover = _extract_css_rule(
        LIGHT_CSS,
        '.gradio-container label[data-testid$="-radio-label"]:hover',
    )
    _assert_css_declarations(
        radio_hover,
        f"background:{PANEL}",
        f"border-color:{ACCENT_DEEP}",
    )
    for selected_selector in (
        '.gradio-container label[data-testid$="-radio-label"].selected',
        '.gradio-container label[data-testid$="-radio-label"]:has(input:checked)',
    ):
        selected = _extract_css_rule(LIGHT_CSS, selected_selector)
        _assert_css_declarations(
            selected,
            f"background:{ACCENT_SOFT}",
            f"color:{TEXT_PRIMARY}",
            f"border-color:{ACCENT_DEEP}",
        )
        assert "#2e2e2e" not in selected.lower()

    for selector in (
        '.gradio-container input[role="listbox"]',
        '.gradio-container [data-testid="textbox"]',
        ".gradio-container textarea",
    ):
        component = _extract_css_rule(LIGHT_CSS, selector)
        _assert_css_declarations(
            component,
            f"background:{CARD}",
            f"color:{TEXT_PRIMARY}",
            f"-webkit-text-fill-color:{TEXT_PRIMARY}",
        )

    for selector in (
        '.gradio-container input[role="listbox"]:disabled',
        '.gradio-container [data-testid="textbox"]:disabled',
        ".gradio-container textarea:disabled",
    ):
        disabled = _extract_css_rule(LIGHT_CSS, selector)
        _assert_css_declarations(
            disabled,
            f"background:{PANEL}",
            f"color:{TEXT_PRIMARY}",
            f"-webkit-text-fill-color:{TEXT_PRIMARY}",
            "opacity:1",
        )

    helper = _extract_css_rule(
        LIGHT_CSS,
        '.gradio-container [data-testid="block-info"] + div .prose',
    )
    _assert_css_declarations(helper, f"color:{TEXT_MUTED}")
    placeholder = _extract_css_rule(LIGHT_CSS, "input::placeholder")
    _assert_css_declarations(
        placeholder,
        f"color:{THEME.input_placeholder_color_dark}",
        "opacity:1",
    )

    body = _extract_css_rule(LIGHT_CSS, "body")
    _assert_css_declarations(
        body,
        f"--block-title-text-color-dark:{TEXT_PRIMARY}",
        f"--checkbox-label-background-fill-dark:{CARD}",
        f"--checkbox-label-background-fill-selected-dark:{ACCENT_SOFT}",
        f"--checkbox-label-text-color-dark:{TEXT_PRIMARY}",
        f"--input-background-fill-dark:{CARD}",
        f"--input-text-color:{TEXT_PRIMARY}",
    )

    sidebar = _extract_css_rule(LIGHT_CSS, ".sidebar")
    _assert_css_declarations(sidebar, f"background:{SIDEBAR}")
    sidebar_info = _extract_css_rule(
        LIGHT_CSS,
        '.sidebar [data-testid="block-info"]',
    )
    _assert_css_declarations(
        sidebar_info,
        f"color:{THEME.button_secondary_text_color}",
        f"-webkit-text-fill-color:{THEME.button_secondary_text_color}",
    )
    nav = _extract_css_rule(LIGHT_CSS, ".sidebar .nav-btn")
    _assert_css_declarations(
        nav,
        "background:transparent",
        "color:#c7d0c9",
    )
    secondary = _extract_css_rule(LIGHT_CSS, ".gr-button.secondary")
    _assert_css_declarations(
        secondary,
        f"background:{THEME.button_secondary_background_fill}",
        f"color:{THEME.button_secondary_text_color}",
    )


def test_dashboard_and_page_titles_do_not_repeat_navigation():
    overview = _text("ui/pages/overview_page.py")
    voice = _text("ui/pages/voice_page.py")
    assert 'gr.Markdown("### 工作台")' not in overview
    assert 'gr.Markdown("## 项目工作台")' in overview
    assert "empty_dashboard_html" not in overview
    assert not (ROOT / "ui/components/dashboard.py").exists()
    assert 'gr.Markdown("### 角色与声音")' in voice
    assert 'label="搜索角色"' in voice
    assert 'v_role = gr.State' in voice
    assert 'v_role = gr.Dropdown' not in voice
    assert '##### ① 选择角色' not in voice
    voice_wiring = _text("ui/wiring/voice_wiring.py")
    assert 'page["v_table"].change(' in voice_wiring
    assert 'cb["select_role_from_list"]' in voice_wiring


def test_voice_category_filters_before_voice_selection():
    voice = _text("ui/pages/voice_page.py")
    category_at = voice.index('label="音色分类"')
    list_at = voice.index('label="音色列表"')
    bind_at = voice.index('gr.Button("确认绑定"')
    assert category_at < list_at < bind_at


def test_heavy_audio_modules_are_not_eager_app_imports():
    app = _text("app.py")
    assert "\nfrom lib import tts_engine\n" not in app
    assert "\nfrom lib import audio_pipeline\n" not in app
    assert "def _tts_engine()" not in app
    assert "RuntimeTTSService.test_voice_and_concat_wavs" in app
    assert "def _audio_pipeline()" in app
