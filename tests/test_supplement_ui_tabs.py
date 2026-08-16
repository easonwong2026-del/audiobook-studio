"""PR B 修复 3：补录页输入来源去重 —— Tabs 唯一，无第二套 Radio。

覆盖：
- supplement_page.py 不再有「输入来源」Radio（sup_mode 是隐藏 State）；
- 粘贴台词 / 导入 JSON 两个 Tab 的 select 事件正确驱动 sup_mode；
- do_supplement_synth 仍以 sup_mode 参数接收（paste/json payload 控制不变）；
- 页面包含「补录与临时配音」模式 Tabs（项目补录 / 临时配音）。
"""
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


def test_no_duplicate_input_source_radio():
    """输入来源必须由 Tabs 唯一决定，页面不得再有第二套 Radio。"""
    assert "gr.Radio(" not in SUP_SRC, "补录页不应再有任何 Radio（含输入来源）"
    # sup_mode 必须是 gr.State（隐藏），不再是 gr.Radio
    assert "sup_mode = gr.State(" in SUP_SRC


def test_tab_select_events_drive_sup_mode():
    """粘贴台词 / 导入 JSON Tab 的 select 事件驱动 sup_mode 状态。"""
    assert "sup_tab_paste.select(lambda: \"paste\", None, sup_mode)" in SUP_SRC
    assert "sup_tab_json.select(lambda: \"json\", None, sup_mode)" in SUP_SRC


def test_do_supplement_synth_signature_preserved():
    """do_supplement_synth 仍接收 sup_mode（paste/json 分流逻辑不变）。"""
    fn = None
    for node in APP_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == "do_supplement_synth":
            fn = node
            break
    assert fn is not None
    names = [a.arg for a in fn.args.args]
    assert names[1] == "sup_mode"
    assert "if sup_mode == \"json\"" in APP_SRC


def test_supplement_page_has_mode_tabs():
    """页面包含模式 Tabs：项目补录 / 临时配音。"""
    assert "with gr.Tab(\"项目补录\")" in SUP_SRC
    assert "with gr.Tab(\"临时配音\")" in SUP_SRC
    assert "补录与临时配音" in SUP_SRC


def test_quick_tts_controls_present():
    """临时配音模式提供声音下拉 / 台词 / 引擎信息 / 生成 / 导出控件。"""
    for token in (
        "qt_voice", "qt_text", "qt_engine", "qt_synth", "qt_export",
        "qt_open_folder",
    ):
        assert token in SUP_SRC, f"临时配音缺少 {token}"


def test_export_ux_controls_present():
    """导出 UX：自定义导出名称 + 打开所在文件夹 + 保存位置。"""
    assert "sup_export_name" in SUP_SRC
    assert "sup_open_folder" in SUP_SRC
    assert "sup_save_loc" in SUP_SRC
