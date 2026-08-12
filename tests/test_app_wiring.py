"""app.py 重构后 AST 回归（无法 import，因为顶层 import gradio 需 UI 环境）。

通过 AST 解析 + 字符串断言验证 R1 重构已落地：
  - 全局可变 ``S = {...}`` 已移除（改用 ``gr.State(SessionState())``）。
  - 各事件 handler 改接 ``ss``（do_export 用 ``*args`` 吸收 ss 以零改动过 glue 测试）。
  - app.py 引入了核心 service（ProjectService / ExportService / ProductionJobService）并调用。
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


def find_func(name):
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _arg_ids_with_vararg(fn):
    """返回函数形式参数名列表；若有 *args，追加 '*<name>'。"""
    names = [a.arg for a in fn.args.args]
    if fn.args.vararg is not None:
        names.append("*" + fn.args.vararg.arg)
    return names


def test_no_global_S_dict():
    """模块级不得再存在全局可变 S = {...}（原跨标签共享状态）。"""
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Name) and t.id == "S"
                        and isinstance(node.value, ast.Dict)):
                    raise AssertionError("app.py 仍存在全局 S = {...}，R1 重构未落地")


def test_gr_state_present():
    assert "gr.State" in SRC, "app.py 应使用 gr.State(SessionState()) 替代全局 S"


def test_services_imported_and_used():
    assert "from services" in SRC or "import services" in SRC, \
        "app.py 应引入 services 层"
    for svc in ("ProjectService", "ExportService", "ProductionJobService"):
        assert svc in SRC, f"app.py 未使用 {svc}"


def test_handlers_take_ss():
    """核心 handler 均接 ss（do_export 用 *args 吸收 ss）。"""
    for h in ["create_project", "save_to_lib", "do_synthesis", "preview_bound_voice",
              "bind_voice", "open_project", "regenerate_segment", "preview_chapters",
              "play_segment", "cancel"]:
        fn = find_func(h)
        assert fn is not None, f"未定义 {h}"
        names = _arg_ids_with_vararg(fn)
        assert "ss" in names, f"{h} 未接 ss（实际参数：{names}）"


def test_do_export_signature_with_vararg():
    fn = find_func("do_export")
    assert fn is not None, "未定义 do_export"
    names = _arg_ids_with_vararg(fn)
    # 前三参位置不变，ss 通过 *args 吸收以满足 glue 测试（零改动 60 旧测试）
    assert names[:3] == ["fmt", "bitrate", "output_dir"], names
    assert "*args" in names, "do_export 应用 *args 吸收 ss（D1 兼容方案）"


def test_do_export_subtitles_defined_and_wired():
    """O1 字幕走全新 handler do_export_subtitles，且首参为 ss、已接线。"""
    fn = find_func("do_export_subtitles")
    assert fn is not None, "app.py 未定义 do_export_subtitles"
    names = _arg_ids_with_vararg(fn)
    assert names[0] == "ss", f"do_export_subtitles 首参应为 ss（实际：{names}）"
    # 接线：导出页「生成字幕」按钮 → do_export_subtitles
    assert "e_subtitle_btn.click(do_export_subtitles" in SRC, \
        "O1 字幕按钮未接线到 do_export_subtitles"


def test_refresh_top_status_defined_and_wired():
    """O11 顶栏状态栏刷新 handler 已定义、首参为 ss、已接线。"""
    fn = find_func("refresh_top_status")
    assert fn is not None, "app.py 未定义 refresh_top_status"
    names = _arg_ids_with_vararg(fn)
    assert names[0] == "ss", f"refresh_top_status 首参应为 ss（实际：{names}）"
    # 接线：多个事件链尾部 .then(refresh_top_status, ...)
    assert ".then(refresh_top_status" in SRC, \
        "O11 状态栏刷新未接线（缺少 .then(refresh_top_status ...)）"


def test_do_synthesis_first_arg_is_ss():
    """do_synthesis 首参仍为 ss（2.3 O2 仅在其后追加可选参数，红线保全）。"""
    fn = find_func("do_synthesis")
    assert fn is not None, "未定义 do_synthesis"
    names = _arg_ids_with_vararg(fn)
    assert names[0] == "ss", f"do_synthesis 首参应为 ss（实际：{names}）"


def test_main_groups_follow_navigation_order():
    group_assign = next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "_GROUPS"
            for target in node.targets
        )
    )
    names = [
        element.id
        for element in group_assign.value.elts
        if isinstance(element, ast.Name)
    ]
    assert names == [
        "grp_overview", "grp_create_project", "grp_project", "grp_voices",
        "grp_production_nav", "grp_synth", "grp_review", "grp_export",
        "grp_supplement", "grp_settings",
    ]
