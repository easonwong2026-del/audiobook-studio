"""普通用户友好的项目管理页。

技术维护能力仍由底层 Service 保留，但首页只展示选择、打开、摘要和
"更多操作"；清理与回收站流程在页内按用户语言呈现。
"""
from __future__ import annotations

import gradio as gr

from services import ProjectService


def create_project_page() -> dict:
    """创建项目管理页面（不含新建项目入口）。"""
    with gr.Group(visible=False, elem_id="grp-project") as grp_project:
        with gr.Group(elem_classes=["stage-card"]):
            gr.Markdown("#### 选择项目")
            with gr.Row():
                p_sel = gr.Dropdown(
                    label="项目",
                    choices=ProjectService.scan_projects(),
                    scale=4,
                )
                p_refresh = gr.Button("刷新", size="sm", scale=1)
            with gr.Row():
                p_open = gr.Button("打开项目", variant="primary", scale=2)
                p_open_dir = gr.Button("打开项目目录", size="sm", scale=1)
            p_open_msg = gr.Markdown("")

            p_summary = gr.Markdown("打开项目后显示书名、作者、章节、片段和合成进度。")
            p_storage = gr.Markdown("打开项目后显示数据占用和最近修改时间。")

        with gr.Accordion("更多操作", open=False):
            gr.Markdown("维护操作会先检查项目，再显示将要处理的内容。")
            with gr.Row():
                p_cleanup = gr.Button("清理缓存", size="sm")
                p_integrity = gr.Button("诊断与修复", size="sm")
            p_cleanup_msg = gr.Markdown("")
            with gr.Row():
                p_cleanup_cancel = gr.Button("取消", size="sm", visible=False)
                p_cleanup_confirm = gr.Button("确认清理", variant="primary", size="sm", visible=False)
            p_cleanup_token = gr.State("")
            p_integrity_repair = gr.Button("修复安全问题", variant="secondary", size="sm", visible=False)
            p_integrity_msg = gr.Markdown("")

            gr.Markdown("##### 项目数据")
            with gr.Row():
                p_backup_dir = gr.Textbox(
                    label="备份目录（留空使用数据目录/backups）",
                    scale=2,
                )
                p_backup = gr.Button("创建备份", size="sm")
            with gr.Row():
                p_restore_file = gr.File(
                    label="项目备份 ZIP",
                    file_count="single",
                    type="filepath",
                    scale=2,
                )
                p_restore = gr.Button("从备份恢复", size="sm")

            gr.Markdown("##### 危险操作")
            p_archive = gr.Button("移入回收站", variant="stop", size="sm")

        with gr.Accordion("回收站", open=False):
            gr.Markdown("已归档项目仍可恢复；永久删除只对回收站中的项目生效。")
            p_trash_table = gr.Dataframe(
                headers=["原项目名称", "归档时间", "占用空间", "回收站标识"],
                datatype=["str", "str", "str", "str"],
                interactive=False,
                wrap=True,
            )
            p_trash_sel = gr.Dropdown(label="选择回收站项目", choices=[])
            with gr.Row():
                p_trash_refresh = gr.Button("刷新回收站", size="sm")
                p_trash_restore = gr.Button("恢复项目", variant="primary", size="sm")
            p_trash_confirm = gr.Checkbox("确认永久删除该回收站项目", value=False)
            p_trash_delete = gr.Button("永久删除回收站项目", variant="stop", size="sm")
            p_trash_msg = gr.Markdown("")

        gr.Markdown("#### 书稿结构")
        p_chapter_tree = gr.HTML(value="<div class='inline-empty'>打开项目后在这里查看章节结构。</div>")

    return {
        "group": grp_project,
        "p_sel": p_sel,
        "p_refresh": p_refresh,
        "p_open": p_open,
        "p_open_dir": p_open_dir,
        "p_archive": p_archive,
        "p_del": p_archive,  # legacy wiring alias; normal UI remains archive-only
        "p_open_msg": p_open_msg,
        "p_summary": p_summary,
        "p_chapter_tree": p_chapter_tree,
        "p_storage": p_storage,
        "p_cleanup": p_cleanup,
        "p_cleanup_msg": p_cleanup_msg,
        "p_cleanup_cancel": p_cleanup_cancel,
        "p_cleanup_confirm": p_cleanup_confirm,
        "p_cleanup_token": p_cleanup_token,
        "p_integrity": p_integrity,
        "p_integrity_repair": p_integrity_repair,
        "p_integrity_msg": p_integrity_msg,
        "p_backup_dir": p_backup_dir,
        "p_backup": p_backup,
        "p_restore_file": p_restore_file,
        "p_restore": p_restore,
        "p_trash_table": p_trash_table,
        "p_trash_sel": p_trash_sel,
        "p_trash_refresh": p_trash_refresh,
        "p_trash_restore": p_trash_restore,
        "p_trash_confirm": p_trash_confirm,
        "p_trash_delete": p_trash_delete,
        "p_trash_msg": p_trash_msg,
        # Kept as Python-level aliases for extensions that imported the old
        # page dictionary; no obsolete controls are rendered.
        "p_storage_msg": p_open_msg,
    }
