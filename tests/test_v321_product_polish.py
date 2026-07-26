"""v3.2.1 release-hardening 的小范围 UI/版本回归。"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from lib import __version__
from ui.components.voice_binding import format_bound_role_choices, format_role_choices


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_runtime_version_has_one_current_source():
    assert __version__ == "3.2.0"
    assert '__version__ = "3.2.0"' in _text("lib/__init__.py")
    assert 'title=f"Audiobook Studio v{__version__}"' in _text("app.py")
    launcher = _text("launcher.py")
    assert "from lib import __version__" in launcher
    assert 'return "3.1.1"' not in launcher


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


def test_production_stage_has_internal_navigation_and_check():
    navigation = _text("ui/navigation.py")
    production = _text("ui/components/production_nav.py")
    app = _text("app.py")
    assert '"production-nav"' in navigation
    assert '"合成中心", "synth"' in production
    assert '"试听质检", "review"' in production
    assert '"角色补录", "supplement"' in production
    assert "def refresh_production_check" in app
    assert "production_stage.change(_goto" in app
    tree = ast.parse(app)
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                and target.value.id == "_GROUPS" for target in node.targets)
    ]
    assert assignments and len(assignments[0].value.elts) == 8


def test_project_onboarding_and_user_facing_quality_labels_exist():
    example = json.loads(_text("structured_script.example.json"))
    assert set(example["voices"]) == {"旁白", "小雨"}
    project_page = _text("ui/pages/project_page.py")
    synthesis_page = _text("ui/pages/synthesis_page.py")
    export_page = _text("ui/pages/export_page.py")
    assert "gr.DownloadButton" in project_page
    assert "structured_script.example.json" in project_page
    assert '("快速", 1)' in synthesis_page
    assert '("标准", 2)' in synthesis_page
    assert '("高质量", 3)' in synthesis_page
    assert "e_save_dir_hint" in export_page
