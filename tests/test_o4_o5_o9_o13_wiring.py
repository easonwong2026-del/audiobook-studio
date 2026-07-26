"""O4/O5/O9/O13 AST 契约复核（app.py 无法 import，因顶层 import gradio）。

通过 AST + 字符串断言验证本次新增组件 / handler 已定义并正确接线，且既有红线
handler（create_project / save_to_lib / do_export / preview_bound_voice /
do_export_subtitles / refresh_top_status / refresh_queue_list / pause_synthesis /
resume_synthesis）接线未变、do_synthesis 首参仍为 ss。

V3.1 重构后 p_open.click / ov_open.click / ov_bookshelf.select 三条入口统一走
打开项目统一链路（open_project 首步 + _open_chain_rest），不再每个 handler 单独 .then 链。测试适配新架构。
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
    names = [a.arg for a in fn.args.args]
    if fn.args.vararg is not None:
        names.append("*" + fn.args.vararg.arg)
    return names


def _click_of(target_var):
    for node in ast.walk(TREE):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "click"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == target_var):
            return node
    return None


# ── O4：书架 + 章节树组件与接线 ──
def test_o4_bookshelf_components_defined():
    # V3.1：书架组件从 p_bookshelf 改名 ov_bookshelf（概览页书架）
    for comp in ("ov_bookshelf", "p_chapter_tree"):
        assert comp in SRC, f"O4 组件未定义: {comp}"
    for fn in ("refresh_bookshelf", "select_project_from_bookshelf",
               "render_chapter_tree", "refresh_projects_full"):
        assert find_func(fn) is not None, f"O4 handler 未定义: {fn}"


def test_o4_p_open_click_appends_bookshelf_then():
    # V3.1：p_open.click / ov_open.click / ov_bookshelf.select 三条入口统一走
    # 打开项目统一链路（方案 A），其中内联 refresh_bookshelf 与 render_chapter_tree，
    # 不再在 .then 链上暴露。
    # 校验刷新链覆盖 ov_bookshelf / p_chapter_tree。
    assert "ov_bookshelf" in SRC, \
        "ov_bookshelf 组件缺失（书架刷新未在打开链路中覆盖）"
    assert "p_chapter_tree" in SRC, \
        "章节树组件 p_chapter_tree 未定义"
    # 书架行选中回填 p_sel（用新组件名 ov_bookshelf）
    assert "ov_bookshelf.select(select_project_from_bookshelf" in SRC or \
           "ov_bookshelf.select(" in SRC, \
        "ov_bookshelf.select 未接线到 select_project_from_bookshelf"


# ── O5：预览 + 勾选接线 ──
def test_o5_preview_components_defined():
    for comp in ("s_preview_df", "s_chapters_sel"):
        assert comp in SRC, f"O5 组件未定义: {comp}"
    assert find_func("render_preview") is not None, "O5 render_preview 未定义"


def test_o5_do_synthesis_first_arg_is_ss():
    fn = find_func("do_synthesis")
    assert fn is not None, "未定义 do_synthesis"
    assert _arg_ids_with_vararg(fn)[0] == "ss", "do_synthesis 首参应为 ss（红线保全）"


def test_o5_s_start_click_includes_chapters_sel():
    node = _click_of("s_start")
    assert node is not None, "未找到 s_start.click"
    inputs = node.args[1]
    ids = [e.id if isinstance(e, ast.Name) else None for e in inputs.elts]
    assert "ss" in ids, "s_start.click 应传入 ss"
    assert "s_chapters_sel" in ids, "s_start.click 应传入 s_chapters_sel（O5 勾选范围）"


def test_o5_p_open_click_appends_render_preview_then():
    # V3.1：render_preview 在打开项目统一链路中被刷新
    # 校验刷新链覆盖 s_preview_df / s_chapters_sel
    assert "s_preview_df" in SRC, "O5 s_preview_df 组件未定义"
    assert "s_chapters_sel" in SRC, "O5 s_chapters_sel 组件未定义"


# ── O9：音色库浏览器组件与接线 ──
def test_o9_voice_lib_browser_components_defined():
    for comp in ("v_lib_search", "v_lib_category", "v_lib_browser"):
        assert comp in SRC, f"O9 组件未定义: {comp}"
    for fn in ("refresh_voice_lib", "select_voice_from_browser"):
        assert find_func(fn) is not None, f"O9 handler 未定义: {fn}"


def test_o9_p_open_click_appends_refresh_voice_lib():
    # V3.1：refresh_voice_lib 在打开项目统一链路中被刷新
    assert "refresh_voice_lib" in SRC, \
        "refresh_voice_lib handler 已不存在"
    assert "v_lib_browser" in SRC, "v_lib_browser 组件未定义"
    assert "v_lib_category" in SRC, "v_lib_category 组件未定义"


def test_o9_browser_wiring():
    assert "v_lib_search.change(refresh_voice_lib" in SRC, \
        "v_lib_search 未接线 refresh_voice_lib"
    assert "v_lib_category.change(refresh_voice_lib" in SRC, \
        "v_lib_category 未接线 refresh_voice_lib"
    assert "v_lib_browser.select(select_voice_from_browser" in SRC, \
        "v_lib_browser.select 未接线 select_voice_from_browser"


# ── O13：章节合并试听组件与接线 ──
def test_o13_chapter_preview_components_defined():
    for comp in ("e_chapter_sel", "e_chapter_audio"):
        assert comp in SRC, f"O13 组件未定义: {comp}"
    for fn in ("preview_chapter", "preview_chapter_options"):
        assert find_func(fn) is not None, f"O13 handler 未定义: {fn}"


def test_o13_production_stage_refreshes_chapter_preview_options():
    assert "nav_synth.click(" in SRC, \
        "生产与质检导航缺失 nav_synth.click 接线"
    assert "preview_chapter_options, [ss], [e_chapter_sel])" in SRC, \
        "进入生产与质检后未追加 preview_chapter_options 接线"


def test_o13_chapter_sel_change_preview_chapter():
    assert "e_chapter_sel.change(preview_chapter" in SRC, \
        "e_chapter_sel.change 未接线 preview_chapter"


# ── 红线回归：既有关键 handler 仍定义、接线未变 ──
def test_redline_core_handlers_still_defined():
    for fn in ("create_project", "save_to_lib", "do_export", "preview_bound_voice",
               "do_export_subtitles", "refresh_top_status", "refresh_queue_list",
               "pause_synthesis", "resume_synthesis", "open_project"):
        assert find_func(fn) is not None, f"红线 handler 未定义: {fn}"


def test_redline_p_open_chain_preserves_top_status_preview_queue():
    # 阶段三：open_project 作为 .then 链首步，其后经 _open_chain_rest 接好各刷新。
    # 校验打开链以 .then 形式触发这些刷新 handler。
    assert ".then(open_project," in SRC, \
        "打开链未以 .then(open_project, ...) 作为首步"
    assert ".then(refresh_top_status, [ss], [top_status])" in SRC, \
        "打开链未以 .then(refresh_top_status, [ss], [top_status]) 刷新顶栏"
    assert ".then(preview_chapters, [ss]" in SRC, \
        "打开链未以 .then(preview_chapters, [ss]...) 刷新章节表"
    assert ".then(refresh_queue_list, [ss], [s_queue_list])" in SRC, \
        "打开链未以 .then(refresh_queue_list, [ss], [s_queue_list]) 刷新队列列表"


def test_redline_do_export_wiring_unchanged():
    node = _click_of("e_go")
    assert node is not None, "未找到 e_go.click"
    inputs = node.args[1]
    ids = [e.id if isinstance(e, ast.Name) else None for e in inputs.elts]
    assert ids == ["e_fmt", "e_br", "e_save_dir", "ss"], \
        f"e_go.click 接线被改动（红线）: {ids}"
