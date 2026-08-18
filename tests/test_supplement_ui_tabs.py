"""统一补录 / Quick TTS 页面结构回归。"""
from __future__ import annotations

import ast
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

SUP_PATH = os.path.join(PROJECT_ROOT, "ui", "pages", "supplement_page.py")
with open(SUP_PATH, encoding="utf-8") as f:
    SUP_SRC = f.read()
SUP_TREE = ast.parse(SUP_SRC)

APP_PATH = os.path.join(PROJECT_ROOT, "app.py")
with open(APP_PATH, encoding="utf-8") as f:
    APP_SRC = f.read()
APP_TREE = ast.parse(APP_SRC)


def test_single_shared_operation_area():
    """声音来源只有一个 Radio，操作区不按模式重复。"""
    assert SUP_SRC.count("gr.Radio(") == 1
    assert "utility_mode = gr.Radio(" in SUP_SRC
    assert SUP_SRC.count("utility_synth = gr.Button") == 1
    assert SUP_SRC.count("utility_preview = gr.Button") == 1
    assert SUP_SRC.count("utility_audio = gr.Audio") == 1
    assert SUP_SRC.count("utility_export = gr.Button") == 1


def test_json_import_feeds_shared_text():
    """JSON 导入只作为项目补录高级输入，并回填共享文本。"""
    assert "utility_json = gr.File(" in SUP_SRC
    assert "utility_json_parse = gr.Button(" in SUP_SRC
    assert "utility_text = gr.Textbox(" in SUP_SRC
    assert "do_utility_parse_json" in APP_SRC


def test_shared_handler_dispatches_business_modes():
    """统一 handler 只 dispatch，不复制底层 service。"""
    assert "def do_utility_tts_synth(" in APP_SRC
    assert 'if mode == "project_role":' in APP_SRC
    assert 'if mode != "library_voice":' in APP_SRC
    assert "SupplementService" in APP_SRC
    assert "QuickTTSService" in APP_SRC


def test_page_has_two_clear_voice_sources():
    """页面使用一个声音来源选择器和两个业务面板。"""
    assert '"使用项目角色", "project_role"' in SUP_SRC
    assert '"自行选择音色", "library_voice"' in SUP_SRC
    assert "utility_project_group" in SUP_SRC
    assert "utility_library_group" in SUP_SRC
    assert "补录与临时配音" in SUP_SRC


def test_quick_tts_controls_present():
    """自选音色模式提供声音下拉和引擎信息，共享操作区。"""
    for token in (
        "utility_voice", "utility_text", "utility_engine", "utility_synth",
        "utility_export", "utility_open_folder",
    ):
        assert token in SUP_SRC, f"临时配音缺少 {token}"


def test_export_ux_controls_present():
    """导出 UX：自定义导出名称 + 打开所在文件夹 + 保存位置。"""
    assert "utility_export_name" in SUP_SRC
    assert "utility_open_folder" in SUP_SRC
    assert "utility_save_loc" in SUP_SRC


def test_mode_switch_clears_shared_result_state():
    """切换声音来源时清理共享 WAV / preview / export state。"""
    assert "def reset_utility_mode(" in APP_SRC
    assert "utility_result_mode" in APP_SRC
    assert "utility_result_project" in APP_SRC
    assert "utility_mode.change(" in APP_SRC
