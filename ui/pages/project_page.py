"""项目管理阶段 UI builder — 项目切换与查看。"""
from __future__ import annotations

import gradio as gr

from services import ProjectService


def create_project_page() -> dict:
    """创建项目管理页面（不含新建项目入口）。"""
    with gr.Group(visible=False, elem_id="grp-project", elem_classes=["page-shell"]) as grp_project:
        with gr.Row(equal_height=True, elem_classes=["stage-row"]):
            with gr.Column(scale=1, elem_classes=["stage-card"]):
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
                    p_migrate = gr.Button(
                        "复制并升级到 V4", size="sm"
                    )
                    p_del = gr.Button("删除项目", variant="stop", size="sm")
                p_open_msg = gr.Markdown("")
                p_migrate_msg = gr.Markdown(
                    "V3 项目可「复制并升级到 V4」：原项目保持不变，"
                    "生成新的 V4 项目（含备份，重复迁移复用上次结果）。"
                )

        gr.Markdown("#### 书稿结构")
        p_summary = gr.Markdown("打开项目后显示书名、角色与合成概览。")
        with gr.Row():
            p_dir_md = gr.Markdown("项目目录：未打开项目")
            p_open_dir = gr.Button("打开项目目录", size="sm", scale=1, visible=False)
        p_open_dir_msg = gr.Markdown("")
        p_chapter_tree = gr.HTML(value="<div class='inline-empty'>打开项目后在这里查看章节结构。</div>")

    return {
        "group": grp_project,
        "p_sel": p_sel,
        "p_refresh": p_refresh,
        "p_open": p_open,
        "p_migrate": p_migrate,
        "p_del": p_del,
        "p_open_msg": p_open_msg,
        "p_migrate_msg": p_migrate_msg,
        "p_summary": p_summary,
        "p_dir_md": p_dir_md,
        "p_open_dir": p_open_dir,
        "p_open_dir_msg": p_open_dir_msg,
        "p_chapter_tree": p_chapter_tree,
    }
