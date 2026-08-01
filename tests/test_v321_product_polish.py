"""v3.2.1 release-hardening 的小范围 UI/版本回归。"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

from lib import __version__
from ui.components.voice_binding import (
    build_role_management_choices,
    build_role_management_rows,
    format_bound_role_choices,
    format_role_choices,
)

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_runtime_version_has_one_current_source():
    assert __version__ == "4.0.0"
    assert '__version__ = "4.0.0"' in _text("lib/__init__.py")
    assert 'title=f"Audiobook Studio v{__version__}"' in _text("app.py")
    launcher = _text("launcher.py")
    assert "from lib import __version__" in launcher
    assert 'return "3.1.1"' not in launcher


def test_gradio_runtime_stays_on_supported_major_version():
    requirements = _text("requirements.txt")
    assert "gradio>=5.50,<6" in requirements
    assert "huggingface-hub" not in requirements
    assert "pydantic" not in requirements


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
    import app

    snapshot = SimpleNamespace(
        script={"voices": {"旁白": {"description": "沉稳男中音"}, "妈妈": {"description": "温柔女声"}}},
        bindings={"旁白": "/tmp/narrator.wav", "妈妈": None},
    )
    session = SimpleNamespace(project="demo", bindings=snapshot.bindings)
    monkeypatch.setattr(app, "_snap", lambda _session: snapshot)
    result = app.select_role_from_list("妈妈", session)
    assert result[0] == "妈妈"
    assert "当前角色：妈妈" in result[1]
    assert result[2]["value"] is None
    assert result[4] == "*当前绑定音频：未选择*"


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
    tree = ast.parse(app)
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                and target.value.id == "_GROUPS" for target in node.targets)
    ]
    assert assignments and len(assignments[0].value.elts) == 11


def test_project_onboarding_and_user_facing_quality_labels_exist():
    example = json.loads(_text("structured_script.example.json"))
    assert set(example["voices"]) == {"旁白", "小雨"}
    create_page = _text("ui/pages/create_project_page.py")
    synthesis_page = _text("ui/pages/synthesis_page.py")
    export_page = _text("ui/pages/export_page.py")
    assert "gr.File" in create_page
    assert "TXT/DOCX/EPUB" in create_page
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


def test_dashboard_and_page_titles_do_not_repeat_navigation():
    overview = _text("ui/pages/overview_page.py")
    dashboard = _text("ui/components/dashboard.py")
    voice = _text("ui/pages/voice_page.py")
    assert 'gr.Markdown("### 工作台")' not in overview
    assert "empty_dashboard_html()" in overview
    assert "有声书生产工作台" not in dashboard
    assert "选择项目后开始制作" in dashboard
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
    assert "def _tts_engine()" in app
    assert "def _audio_pipeline()" in app
