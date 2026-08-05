"""项目管理阶段 UI builder — 项目切换与结构查看。"""
from __future__ import annotations

import gradio as gr

from services import ProjectService


def create_project_page() -> dict:
    """创建项目管理页面（不含新建项目入口）。"""
    with gr.Group(visible=False, elem_id="grp-project") as grp_project:
        with gr.Row(equal_height=True, elem_classes=["stage-row"]), gr.Column(scale=1, elem_classes=["stage-card"]):
                gr.Markdown("#### 选择项目")
                with gr.Row():
                    p_sel = gr.Dropdown(
                        label="项目",
                        choices=ProjectService.scan_projects(),
                        scale=4,
                    )
                    p_refresh = gr.Button("刷新", size="sm", scale=1)
                with gr.Row():
                    p_open = gr.Button("打开项目", variant="primary")
                    p_open_dir = gr.Button("打开项目目录", size="sm")
                    p_del = gr.Button("删除项目文件", variant="stop", size="sm")
                p_open_msg = gr.Markdown("")

        gr.Markdown("#### 书稿结构")
        p_summary = gr.Markdown("打开项目后显示书名、角色与合成概览。")
        p_chapter_tree = gr.HTML(value="<div class='inline-empty'>打开项目后在这里查看章节结构。</div>")
        p_storage = gr.Markdown("项目目录、存储占用和完整性状态会显示在这里。")
        with gr.Row():
            p_storage_refresh = gr.Button("刷新存储信息", size="sm")
            p_cache_clear = gr.Button("清理项目缓存", size="sm")
        p_storage_msg = gr.Markdown("")

    return {
        "group": grp_project,
        "p_sel": p_sel,
        "p_refresh": p_refresh,
        "p_open": p_open,
        "p_del": p_del,
        "p_open_msg": p_open_msg,
        "p_summary": p_summary,
        "p_chapter_tree": p_chapter_tree,
        "p_open_dir": p_open_dir,
        "p_storage": p_storage,
        "p_storage_refresh": p_storage_refresh,
        "p_cache_clear": p_cache_clear,
        "p_storage_msg": p_storage_msg,
    }
