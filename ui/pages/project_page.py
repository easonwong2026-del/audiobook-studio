"""普通用户友好的项目管理页。

技术维护能力已迁移到书架（概览页项目书架）：打开目录 / 创建备份 / 清理缓存 /
诊断修复 / 移入回收站 / 从备份恢复 / 回收站，均在书架对「选中项目」执行。
本页只保留当前生产工作流内容：选择项目、打开项目、项目信息、存储摘要、书稿结构。
"""
from __future__ import annotations

import gradio as gr

from services.project_catalog import ProjectCatalogService


def create_project_page() -> dict:
    """创建项目管理页面（不含新建项目入口）。"""
    with gr.Group(visible=False, elem_id="grp-project") as grp_project:
        with gr.Group(elem_classes=["stage-card"]):
            gr.Markdown("#### 选择项目")
            with gr.Row():
                p_sel = gr.Dropdown(
                    label="项目",
                    choices=[
                        summary.project_name
                        for summary in ProjectCatalogService.scan()
                    ],
                    scale=4,
                )
                p_refresh = gr.Button("刷新", size="sm", scale=1)
            with gr.Row():
                p_open = gr.Button("打开项目", variant="primary", scale=2)
            p_open_msg = gr.Markdown("")

            p_summary = gr.Markdown("打开项目后显示书名、作者、章节、片段和合成进度。")
            p_storage = gr.Markdown("打开项目后显示数据占用和最近修改时间。")

        gr.Markdown("#### 书稿结构")
        p_chapter_tree = gr.HTML(value="<div class='inline-empty'>打开项目后在这里查看章节结构。</div>")

    return {
        "group": grp_project,
        "p_sel": p_sel,
        "p_refresh": p_refresh,
        "p_open": p_open,
        "p_open_msg": p_open_msg,
        "p_summary": p_summary,
        "p_chapter_tree": p_chapter_tree,
        "p_storage": p_storage,
        # Kept as Python-level aliases for extensions that imported the old
        # page dictionary; no obsolete controls are rendered.
        "p_open_dir": None,
        "p_archive": None,
        "p_del": None,
        "p_cleanup": None,
        "p_cleanup_msg": None,
        "p_cleanup_cancel": None,
        "p_cleanup_confirm": None,
        "p_cleanup_token": None,
        "p_integrity": None,
        "p_integrity_repair": None,
        "p_integrity_msg": None,
        "p_backup_dir": None,
        "p_backup": None,
        "p_restore_file": None,
        "p_restore": None,
        "p_trash_table": None,
        "p_trash_sel": None,
        "p_trash_refresh": None,
        "p_trash_restore": None,
        "p_trash_confirm": None,
        "p_trash_delete": None,
        "p_trash_msg": None,
        "p_storage_msg": p_open_msg,
    }
