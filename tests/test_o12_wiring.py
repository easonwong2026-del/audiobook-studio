"""O12 / O3 AST 契约复核（app.py 无法 import，因顶层 import gradio 需 UI 环境）。

经由 AST 解析 + 字符串断言验证（设计 §0/§4.1/§10.2 AST 红线）：
- pause_synthesis / resume_synthesis / refresh_queue_list 已定义且首参 ss；
- s_pause.click(pause_synthesis, [ss], ...) / s_resume.click(resume_synthesis, [ss], ...) 接线；
- p_open.click(...) 链尾部 .then(refresh_queue_list, [ss], [s_queue_list]) 填充空闲列表；
- s_queue_list 为 gr.Dataframe 组件（在 ui/pages/synthesis_page.py 中定义）；
- do_synthesis 首参仍为 ss；
- do_export 三参 + e_go.click 含 e_br（红线保全未变）；
- e_subtitle_btn.click(do_export_subtitles ...) / .then(refresh_top_status ...) 红线未变。
"""
import sys
import os
import ast

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP_PATH = os.path.join(PROJECT_ROOT, "app.py")
with open(APP_PATH, encoding="utf-8") as f:
    SRC = f.read()
TREE = ast.parse(SRC)

# ui/pages/synthesis_page.py 源码
SYNTH_PATH = os.path.join(PROJECT_ROOT, "ui", "pages", "synthesis_page.py")
with open(SYNTH_PATH, encoding="utf-8") as f:
    SYNTH_SRC = f.read()
SYNTH_TREE = ast.parse(SYNTH_SRC)


def find_func(name):
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _arg_ids_with_vararg(fn):
    names = [a.arg for a in fn.args.args]
    if fn.args.vararg is not None:
        names.append("*" + fn.args.vararg.arg)
    return names


def test_new_handlers_defined_and_take_ss():
    for h in ["pause_synthesis", "resume_synthesis", "refresh_queue_list"]:
        fn = find_func(h)
        assert fn is not None, f"app.py 未定义 {h}"
        names = _arg_ids_with_vararg(fn)
        assert names[0] == "ss", f"{h} 首参应为 ss（实际：{names}）"


def test_do_synthesis_first_arg_is_ss():
    fn = find_func("do_synthesis")
    assert fn is not None, "未定义 do_synthesis"
    names = _arg_ids_with_vararg(fn)
    assert names[0] == "ss", f"do_synthesis 首参应为 ss（实际：{names}）"


def test_pause_click_wired():
    assert "s_pause.click(pause_synthesis, [ss]" in SRC, \
        "s_pause 未接线 pause_synthesis（O12 暂停按钮缺失）"


def test_resume_click_wired():
    assert "s_resume.click(resume_synthesis, [ss]" in SRC, \
        "s_resume 未接线 resume_synthesis（O12 恢复按钮缺失）"


def test_refresh_queue_list_wired_in_open_chain():
    # 阶段三：open_project 作为 .then 链首步，其后经 _open_chain_rest 接 refresh_queue_list。
    assert "s_queue_list" in SRC, \
        "未定义 s_queue_list(gr.Dataframe)（O3 队列列表组件缺失）"
    assert ".then(refresh_queue_list, [ss], [s_queue_list])" in SRC, \
        "打开链未以 .then(refresh_queue_list, [ss], [s_queue_list]) 形式接线 s_queue_list"


def test_s_queue_list_component_exists():
    # s_queue_list 在 phase 2 重构后定义于 ui/pages/synthesis_page.py
    assert "s_queue_list = gr.Dataframe(" in SYNTH_SRC, \
        "未定义 s_queue_list(gr.Dataframe)（O3 队列列表组件缺失）"


def test_s_start_click_outputs_includes_queue_list():
    # O3：s_start.click outputs 由 [s_log] 扩为 [s_log, s_queue_list]
    assert "outputs=[s_log, s_queue_list]" in SRC, \
        "s_start.click 的 outputs 未扩为 [s_log, s_queue_list]（O3 列表订阅缺失）"


def test_red_line_do_export_wiring_unchanged():
    # do_export 三参 + e_go.click 含 e_br（红线保全）
    node = None
    for n in ast.walk(TREE):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "click" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "e_go"):
            node = n
            break
    assert node is not None, "未找到 e_go.click"
    inputs = node.args[1]
    ids = [e.id if isinstance(e, ast.Name) else None for e in inputs.elts]
    assert "e_br" in ids, "e_go.click 未含 e_br（do_export 红线被改动）"


def test_red_line_subtitle_and_top_status_wiring_unchanged():
    assert "e_subtitle_btn.click(do_export_subtitles" in SRC, "O1 字幕红线被改动"
    assert ".then(refresh_top_status" in SRC, "O11 顶栏刷新红线被改动"
