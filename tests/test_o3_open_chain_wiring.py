"""阶段三：打开链（open_project 首步 + _open_chain_rest）锁死测试。

锁死：
- open_project_full / _FULL_OUTPUTS / _FULL_INPUTS 已彻底删除（不再作为契约）；
- 打开项目统一链路经 .then 接好各页面刷新（顶栏 / 章节表 / 章节试听选项 /
  队列列表 / 章节树 / 合成预览 / 音色库 / 分类下拉 / 概览 / 项目下拉）。
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP_PATH = os.path.join(PROJECT_ROOT, "app.py")
with open(APP_PATH, encoding="utf-8") as f:
    SRC = f.read()


def test_no_22_tuple_contract():
    # 22 元组契约（open_project_full / _FULL_OUTPUTS）必须已删除，不再残留。
    assert "_FULL_OUTPUTS" not in SRC, "open_project_full 的 _FULL_OUTPUTS 应已删除"
    assert "def open_project_full" not in SRC, "open_project_full 应已删除"


def test_open_chain_wires_all_pages():
    assert ".then(open_project," in SRC, "打开链应以 .then(open_project, ...) 作为首步"
    assert ".then(refresh_top_status, [ss], [top_status])" in SRC, "缺失 refresh_top_status 接线"
    assert ".then(preview_chapters, [ss]" in SRC, "缺失 preview_chapters 接线"
    assert ".then(preview_chapter_options, [ss], [e_chapter_sel])" in SRC, "缺失 preview_chapter_options 接线"
    assert ".then(refresh_queue_list, [ss], [s_queue_list])" in SRC, "缺失 refresh_queue_list 接线"
    assert ".then(render_preview, [ss]" in SRC, "缺失 render_preview 接线"
    assert ".then(refresh_voice_lib, [v_lib_search, v_lib_category]" in SRC, "缺失 refresh_voice_lib 接线"
    assert ".then(\n        refresh_overview, [ss]," in SRC, "缺失 refresh_overview 接线"
    for component in ("ov_status", "ov_progress", "ov_task", "ov_issues", "ov_bookshelf"):
        assert component in SRC, f"工作台刷新未覆盖 {component}"
    assert ".then(refresh_p_sel, [ss], [p_sel])" in SRC, "缺失 refresh_p_sel 接线"
