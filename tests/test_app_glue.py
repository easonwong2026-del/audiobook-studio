"""静态验证：app.py 修复（无法 import，因为顶层 import gradio 需 GPU/UI 环境）

通过 AST 解析 + 字符串断言验证：
  - B5: create_project 的 return 均为 4 元组；p_create.click 的 outputs 仅 4 个且 p_sel 只出现一次
  - B4: do_export(fmt, bitrate, output_dir) 签名存在；e_go.click(do_export, [e_fmt, e_br, e_save_dir], ...) 含 e_br
  - B10: save_to_lib 的 return 为 3 元组且 outputs 含 e_voice
  - D4: preview_bound_voice 函数被定义，且 v_preview_btn.click(preview_bound_voice, ...) 接线
  - B12: from lib import script_loader 已 import；create_project 内调用 load_script / validate_script
"""
import ast
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP_PATH = os.path.join(PROJECT_ROOT, "app.py")
with open(APP_PATH, encoding="utf-8") as f:
    SRC = f.read()
TREE = ast.parse(SRC)
VOICE_WIRING_PATH = os.path.join(PROJECT_ROOT, "ui", "wiring", "voice_wiring.py")
with open(VOICE_WIRING_PATH, encoding="utf-8") as f:
    VOICE_WIRING_SRC = f.read()
VOICE_WIRING_TREE = ast.parse(VOICE_WIRING_SRC)


def has_import_from(module, name):
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == module
        and any(alias.name == name for alias in node.names)
        for node in TREE.body
    )


def find_func(name):
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def find_click(target_var):
    """找到 `target_var.click(...)` 的 Call 节点。"""
    for node in ast.walk(TREE):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "click"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == target_var):
            return node
    return None


def find_voice_page_event(target_key, event_name):
    """找到 ``page["target_key"].<event_name>(...)`` 的 Call 节点。"""
    for node in ast.walk(VOICE_WIRING_TREE):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == event_name
            and isinstance(node.func.value, ast.Subscript)
        ):
            continue
        owner = node.func.value
        if not (isinstance(owner.value, ast.Name) and owner.value.id == "page"):
            continue
        key = owner.slice.value if isinstance(owner.slice, ast.Constant) else None
        if key == target_key:
            return node
    return None


def _arg_ids(list_node):
    return [e.id if isinstance(e, ast.Name) else None for e in list_node.elts]


def _mapping_key(node, mapping_name):
    if not (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == mapping_name
        and isinstance(node.slice, ast.Constant)
    ):
        return None
    return node.slice.value


def test_create_project_returns_4tuple():
    fn = find_func("create_project")
    assert fn is not None, "未找到 create_project 函数"
    returns = [n for n in ast.walk(fn)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)]
    assert returns, "create_project 没有返回元组"
    for r in returns:
        n = len(r.value.elts)
        print(f"[B5] create_project return 元素数 = {n}")
        assert n == 4, f"create_project 的 return 应为 4 元组，实际 {n} 个"
    print(f"[B5] create_project 共 {len(returns)} 处 return，均为 4 元组 ✔")


def test_json_create_click_wiring():
    """新建项目只有 JSON 检查和 JSON 创建入口。"""
    node = find_click("cp_json_create")
    assert node is not None, "未找到 cp_json_create.click（JSON 导入入口）"
    assert len(node.args) >= 2, "cp_create.click 参数不足"
    inputs = node.args[1]
    assert isinstance(inputs, ast.List)
    ids = _arg_ids(inputs)
    assert ids == ["cp_json_name", "cp_json_file", "ss"], ids
    outputs = node.args[2]
    assert isinstance(outputs, ast.List)
    ids = _arg_ids(outputs)
    assert len(ids) == 2, f"outputs 应为 2 个，实际 {len(ids)}"
    assert ids.count("p_sel") == 1, "p_sel 在 outputs 中应只出现一次"
    assert find_click("cp_json_check") is not None


def test_do_export_signature():
    fn = find_func("do_export")
    assert fn is not None, "未找到 do_export 函数"
    arg_names = [a.arg for a in fn.args.args]
    print(f"[B4] do_export 形参 = {arg_names}")
    assert arg_names == ["fmt", "bitrate", "output_dir"], f"do_export 形参错误: {arg_names}"


def test_do_export_wiring():
    node = find_click("e_go")
    assert node is not None, "未找到 e_go.click"
    assert len(node.args) >= 2
    inputs = node.args[1]
    assert isinstance(inputs, ast.List)
    ids = _arg_ids(inputs)
    print(f"[B4] e_go.click inputs = {ids}")
    assert "e_br" in ids, "比特率 e_br 未传入 do_export（B4 接线缺失）"


def test_save_to_lib_returns_4tuple():
    """save_to_lib 5.2 增 v_save_category 刷新，返回 4 元组（含分类下拉更新）。"""
    fn = find_func("save_to_lib")
    assert fn is not None, "未找到 save_to_lib 函数"
    returns = [n for n in ast.walk(fn)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)]
    assert returns, "save_to_lib 没有返回元组"
    for r in returns:
        n = len(r.value.elts)
        print(f"[B10] save_to_lib return 元素数 = {n}")
        assert n == 4, f"save_to_lib 的 return 应为 4 元组（含 v_save_category），实际 {n} 个"


def test_save_to_lib_wiring():
    node = find_voice_page_event("v_save_btn", "click")
    assert node is not None, "未找到 v_save_btn.click"
    assert len(node.args) >= 3
    assert _mapping_key(node.args[0], "cb") == "save_to_lib"
    outputs = node.args[2]
    output_src = ast.unparse(outputs)
    print(f"[B10] v_save_btn.click outputs = {output_src}")
    assert "production_voice" in output_src, \
        "生产页音色下拉未出现在 save_to_lib 的 outputs（B10 接线缺失）"


def test_preview_bound_voice_defined_and_wired():
    fn = find_func("preview_bound_voice")
    assert fn is not None, "未定义 preview_bound_voice 函数（D4 缺失）"
    node = find_voice_page_event("v_preview_btn", "click")
    assert node is not None, "未找到 v_preview_btn.click（D4 接线缺失）"
    assert _mapping_key(node.args[0], "cb") == "preview_bound_voice", \
        "v_preview_btn 未接线到 preview_bound_voice"
    print("[D4] preview_bound_voice 已定义且已接线 ✔")


def test_preview_bound_voice_uses_full_three_sentences():
    """D4 完善：preview_bound_voice 必须合成并拼接完整三句测试句，而非仅返回第一句。"""
    fn = find_func("preview_bound_voice")
    assert fn is not None, "未定义 preview_bound_voice 函数（D4 缺失）"
    src = ast.unparse(fn)
    # 1) 必须调用 test_voice（合成三句测试句）
    assert "test_voice" in src, "preview_bound_voice 未调用 test_voice（未合成试音音频）"
    # 2) 必须把三句拼接成一段（而非只返回第 1 句）
    assert "_concat_wavs" in src, "preview_bound_voice 应把三句测试句拼接成一段连续音频"
    # 3) 旧实现只返回 test_voice(audio)[0]（第 1 句），不应再存在
    assert "test_voice(audio)[0]" not in src, "preview_bound_voice 不应只返回第一句测试句"
    print("[D4] preview_bound_voice 合成并拼接完整三句测试句 ✔")


def test_json_import_service_is_wired_without_source_analysis():
    assert has_import_from("lib", "script_loader"), "app.py 未 import script_loader（B12 缺失）"
    assert "create_ui.inspect_json" in SRC
    with open(
        os.path.join(PROJECT_ROOT, "services", "structured_script_import.py"),
        encoding="utf-8",
    ) as service_file:
        assert "StructuredScriptImportService" in service_file.read()
    assert "create_from_source" not in SRC
    assert "AI 分析并创建项目" not in SRC
