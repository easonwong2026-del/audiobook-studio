"""补录页接线 AST 回归（无需 import gradio，与 test_app_wiring.py 同风格）。

验证：
  - 「角色补录」被收纳到顶级「生产与质检」阶段，而非单独导航按钮。
  - 新增 grp-supplement 分组（ui/pages/supplement_page.py 中 elem_id="grp-supplement"）。
  - _GROUPS 在 app.py 运行时装填 8 项（含生产阶段内部导航）。
  - _goto 定义在 ui/navigation.py 中，返回 8 个 gr.update。
  - 新增 handler 均定义且接 ss（首参或含 ss），并已接线。
"""
from __future__ import annotations

import ast
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# app.py 源码
APP_PATH = os.path.join(PROJECT_ROOT, "app.py")
with open(APP_PATH, encoding="utf-8") as f:
    SRC = f.read()
TREE = ast.parse(SRC)

# ui/navigation.py 源码
NAV_PATH = os.path.join(PROJECT_ROOT, "ui", "navigation.py")
with open(NAV_PATH, encoding="utf-8") as f:
    NAV_SRC = f.read()
NAV_TREE = ast.parse(NAV_SRC)

# ui/pages/supplement_page.py 源码
SUP_PATH = os.path.join(PROJECT_ROOT, "ui", "pages", "supplement_page.py")
with open(SUP_PATH, encoding="utf-8") as f:
    SUP_SRC = f.read()
SUP_TREE = ast.parse(SUP_SRC)


def find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _arg_ids_with_vararg(fn):
    names = [a.arg for a in fn.args.args]
    if fn.args.vararg is not None:
        names.append("*" + fn.args.vararg.arg)
    return names


def test_supplement_belongs_to_production_stage():
    assert '"nav-synth"' in NAV_SRC, "navigation.py 缺少生产与质检入口"
    assert '"nav-supplement"' not in NAV_SRC, "补录不应继续作为顶级导航"
    assert '"production-nav"' in NAV_SRC, "缺少生产阶段内部导航分组"
    assert 'which in {"synth", "review", "supplement"}' in NAV_SRC, \
        "生产阶段内部页面应由同一导航控制"


def test_grp_supplement_present():
    # grp-supplement 分组在 supplement_page.py 中定义
    assert 'elem_id="grp-supplement"' in SUP_SRC, "缺少 grp-supplement 分组"


def test_groups_tuple_has_eight_items():
    # v3.3.1: _GROUPS[:] = [10 个 group]（新增 create_project、settings）
    for node in ast.walk(TREE):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript):
                    if isinstance(t.value, ast.Name) and t.value.id == "_GROUPS":
                        val = node.value
                        assert isinstance(val, (ast.List, ast.Tuple)), \
                            f"_GROUPS[:] 赋值应为列表/元组（实际 {type(val).__name__}）"
                        assert len(val.elts) == 11, \
                                f"_GROUPS[:] 应为 11 项（实际 {len(val.elts)}）"
                        return
    raise AssertionError("未找到 _GROUPS[:] = [...] 赋值")


def test_goto_returns_internal_group_updates_for_five_stage_navigation():
    fn = find_func(NAV_TREE, "_goto")
    assert fn is not None, "navigation.py 中未定义 _goto"
    has_tuple_return = False
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Call):
            if isinstance(n.value.func, ast.Name) and n.value.func.id == "tuple":
                has_tuple_return = True
                break
    assert has_tuple_return, "_goto 未返回 tuple(...) 调用"
    # 验证 NAV_ITEMS 是五阶段工作流。
    for node in ast.walk(NAV_TREE):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "GROUP_ITEMS":
                    assert isinstance(node.value, (ast.List, ast.Tuple)), \
                        "GROUP_ITEMS 应为列表"
                    # v3.3.1: 10 items (+create_project, +settings)
                    assert len(node.value.elts) == 11, \
                        f"GROUP_ITEMS 应为 11 项（实际 {len(node.value.elts)}）"
                    break
    # 验证还有一个 _SETTINGS_ITEM 常量
    for node in ast.walk(NAV_TREE):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_SETTINGS_ITEM":
                    return  # Found
    raise AssertionError("未找到 _SETTINGS_ITEM 定义")
    raise AssertionError("未找到 NAV_ITEMS 定义")


def test_supplement_handlers_defined_and_take_ss():
    expected = {
        "refresh_supplement_roles": True,   # ss 首参
        "do_supplement_parse_json": True,   # 含 ss
        "do_supplement_synth": True,        # 含 ss（末参）
        "do_supplement_export": True,       # 含 ss（末参）
        "play_supplement_preview": True,    # 含 ss（��参）
    }
    for h, _ in expected.items():
        fn = find_func(TREE, h)
        assert fn is not None, f"未定义 {h}"
        names = _arg_ids_with_vararg(fn)
        assert "ss" in names, f"{h} 未接 ss（实际参数：{names}）"


def test_supplement_refresh_wired_to_production_stage():
    assert "nav_synth.click(" in SRC, "生产与质检入口缺接线"
    assert "refresh_supplement_roles, [ss], [sup_role]" in SRC, \
        "进入生产与质检后未懒刷新补录角色"


def test_parse_json_wired():
    assert "do_supplement_parse_json" in SRC
    assert "sup_json_parse.click(do_supplement_parse_json" in SRC, \
        "小 JSON 解析按钮未接线到 do_supplement_parse_json"


def test_synth_wired():
    assert "sup_synth.click(do_supplement_synth" in SRC, \
        "补合成按钮未接线到 do_supplement_synth"


def test_export_wired():
    assert "sup_export.click(do_supplement_export" in SRC, \
        "导出按钮未接线到 do_supplement_export"


def test_preview_wired():
    assert "play_supplement_preview(" in SRC, "试听���调用 play_supplement_preview"
    assert "sup_play_all.click(" in SRC and "sup_play_seg.click(" in SRC, \
        "整段 / 逐句试听按钮未接线"
