"""工作台首页 UI builder — 对齐 Pencil 工作台·项目状态画板。"""
from __future__ import annotations

import gradio as gr

from ui.components.dashboard import empty_dashboard_html


def create_overview_page() -> dict:
    """创建以项目状态和下一步行动为中心的工作台。

    书架区升级为**唯一主要项目管理入口**：搜索 + 书架 Dataframe + 选中信息 +
    管理动作（打开/打开目录/备份/清理/诊断/移入回收站）+ 全局「从备份恢复」+
    全局「回收站」。
    """
    initial_status, initial_progress, initial_task, initial_issues = empty_dashboard_html()
    with gr.Group(visible=True, elem_id="grp-overview") as grp_overview:
        ov_status = gr.HTML(value=initial_status, elem_classes=["workbench-status"])

        with gr.Row(equal_height=True, elem_classes=["workbench-main-row"]):
            with gr.Column(scale=3):
                ov_progress = gr.HTML(value=initial_progress)
            with gr.Column(scale=2):
                ov_task = gr.HTML(value=initial_task)

        with gr.Row(equal_height=True, elem_classes=["workbench-main-row"]):
            with gr.Column(scale=3):
                ov_issues = gr.HTML(value=initial_issues)
            with gr.Column(scale=2, elem_classes=["quick-actions"]):
                gr.Markdown("#### 快捷操作")
                ov_open = gr.Button("打开 / 切换项目", variant="secondary")
                ov_voices = gr.Button("配置角色声音")
                ov_synth = gr.Button("进入生产与质检", variant="primary")
                ov_export = gr.Button("交付成品")

        gr.Markdown("#### 项目书架")
        bookshelf_search = gr.Textbox(
            label="搜索项目（名称 / 书名 / 作者）",
            placeholder="输入关键词过滤书架…",
        )
        ov_bookshelf = gr.Dataframe(
            headers=["项目", "章", "段进度", "状态"],
            datatype=["str", "str", "str", "str"],
            interactive=True,
            label="最近项目",
            wrap=True,
        )
        bookshelf_selected_proj = gr.State("")
        bookshelf_selected = gr.Markdown(
            "从书架选择项目后，可对选中项目执行管理操作；「打开项目」才会进入工作流。"
        )

        with gr.Row():
            bookshelf_open = gr.Button("打开项目", variant="primary", size="sm")
            bookshelf_open_dir = gr.Button("打开项目目录", size="sm")
            bookshelf_backup = gr.Button("创建备份", size="sm")
            bookshelf_cleanup = gr.Button("清理缓存", size="sm")
            bookshelf_cleanup_confirm = gr.Button(
                "确认清理", variant="primary", size="sm", visible=False
            )
            bookshelf_cleanup_cancel = gr.Button("取消", size="sm", visible=False)
        bookshelf_backup_dir = gr.Textbox(
            label="备份目录（留空使用数据目录/backups）",
            placeholder="可选",
        )
        bookshelf_cleanup_token = gr.State("")
        with gr.Row():
            bookshelf_integrity = gr.Button("诊断", size="sm")
            bookshelf_integrity_repair = gr.Button(
                "修复", variant="secondary", size="sm", visible=False
            )
            bookshelf_archive = gr.Button("移入回收站", variant="stop", size="sm")
        # 两步确认状态：记录「已确认的项目名」（字符串语义，"" 表示未确认）。
        # 绑定项目名后，改选其他项目不会复用旧确认态（QA 缺陷修复）。
        bookshelf_archive_confirm = gr.State("")
        bookshelf_msg = gr.Markdown("")

        gr.Markdown("#### 从备份恢复")
        with gr.Row():
            bookshelf_restore_file = gr.File(
                label="项目备份 ZIP",
                file_count="single",
                type="filepath",
                scale=2,
            )
            bookshelf_restore = gr.Button("从备份恢复", size="sm")

        gr.Markdown("#### 回收站")
        bookshelf_trash_table = gr.Dataframe(
            headers=["原项目名称", "归档时间", "占用空间", "回收站标识"],
            datatype=["str", "str", "str", "str"],
            interactive=False,
            wrap=True,
        )
        bookshelf_trash_sel = gr.Dropdown(label="选择回收站项目", choices=[])
        with gr.Row():
            bookshelf_trash_refresh = gr.Button("刷新回收站", size="sm")
            bookshelf_trash_restore = gr.Button("恢复项目", variant="primary", size="sm")
        bookshelf_trash_confirm = gr.Checkbox("确认永久删除该回收站项目", value=False)
        bookshelf_trash_delete = gr.Button("永久删除回收站项目", variant="stop", size="sm")
        bookshelf_trash_status = gr.Markdown("")

    return {
        "group": grp_overview,
        "ov_status": ov_status,
        "ov_progress": ov_progress,
        "ov_task": ov_task,
        "ov_issues": ov_issues,
        "ov_bookshelf": ov_bookshelf,
        "ov_open": ov_open,
        "ov_voices": ov_voices,
        "ov_synth": ov_synth,
        "ov_export": ov_export,
        "bookshelf_search": bookshelf_search,
        "bookshelf_selected_proj": bookshelf_selected_proj,
        "bookshelf_selected": bookshelf_selected,
        "bookshelf_open": bookshelf_open,
        "bookshelf_open_dir": bookshelf_open_dir,
        "bookshelf_backup": bookshelf_backup,
        "bookshelf_backup_dir": bookshelf_backup_dir,
        "bookshelf_cleanup": bookshelf_cleanup,
        "bookshelf_cleanup_confirm": bookshelf_cleanup_confirm,
        "bookshelf_cleanup_cancel": bookshelf_cleanup_cancel,
        "bookshelf_cleanup_token": bookshelf_cleanup_token,
        "bookshelf_integrity": bookshelf_integrity,
        "bookshelf_integrity_repair": bookshelf_integrity_repair,
        "bookshelf_archive": bookshelf_archive,
        "bookshelf_archive_confirm": bookshelf_archive_confirm,
        "bookshelf_msg": bookshelf_msg,
        "bookshelf_restore_file": bookshelf_restore_file,
        "bookshelf_restore": bookshelf_restore,
        "bookshelf_trash_table": bookshelf_trash_table,
        "bookshelf_trash_sel": bookshelf_trash_sel,
        "bookshelf_trash_refresh": bookshelf_trash_refresh,
        "bookshelf_trash_restore": bookshelf_trash_restore,
        "bookshelf_trash_confirm": bookshelf_trash_confirm,
        "bookshelf_trash_delete": bookshelf_trash_delete,
        "bookshelf_trash_status": bookshelf_trash_status,
    }
