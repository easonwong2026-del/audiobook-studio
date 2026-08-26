"""Workbench UI builder.

The Workbench is the only visible project-context entry point.  Its left side
is the existing flat Catalog/Dataframe presentation and its right side is an
inspector for ``SessionState.selected_project``.  Project Page controls remain
available through their compatibility contract; the old dashboard and quick
action sinks are retired.
"""
from __future__ import annotations

import gradio as gr

def create_overview_page() -> dict:
    """Create the Workbench shell and its selected-project inspector."""
    with gr.Group(visible=True, elem_id="grp-overview") as grp_overview:
        with gr.Row(elem_classes=["workbench-toolbar"]):
            with gr.Column(scale=3, elem_classes=["workbench-heading"]):
                gr.Markdown("## 项目工作台")
                gr.Markdown(
                    "项目选择、项目管理与项目打开的唯一入口。",
                    elem_classes=["workbench-subtitle"],
                )
            bookshelf_search = gr.Textbox(
                label="搜索项目",
                placeholder="搜索项目、书名或作者…",
                show_label=True,
                scale=4,
                elem_classes=["workbench-search"],
            )
            workbench_new_project = gr.Button(
                "新建项目",
                variant="primary",
                scale=1,
                elem_classes=["workbench-new-project"],
            )

        # Restore / Trash are global actions.  They deliberately sit outside
        # the Selected Project inspector so they cannot be mistaken for an
        # operation on the currently selected row.
        with gr.Accordion("全局工具", open=False, elem_classes=["workbench-global-tools"]):
            with gr.Row(equal_height=True):
                bookshelf_restore_file = gr.File(
                    label="项目备份 ZIP",
                    file_count="single",
                    type="filepath",
                    scale=2,
                )
                bookshelf_restore = gr.Button("从备份恢复", size="sm")
            with gr.Accordion("回收站", open=False):
                bookshelf_trash_table = gr.Dataframe(
                    headers=["原项目名称", "归档时间", "占用空间", "回收站标识"],
                    datatype=["str", "str", "str", "str"],
                    interactive=False,
                    wrap=True,
                )
                bookshelf_trash_sel = gr.Dropdown(label="选择回收站项目", choices=[])
                with gr.Row():
                    bookshelf_trash_refresh = gr.Button("刷新回收站", size="sm")
                    bookshelf_trash_restore = gr.Button(
                        "恢复项目", variant="primary", size="sm"
                    )
                bookshelf_trash_confirm = gr.Checkbox(
                    "确认永久删除该回收站项目", value=False
                )
                bookshelf_trash_delete = gr.Button(
                    "永久删除回收站项目", variant="stop", size="sm"
                )
                bookshelf_trash_status = gr.Markdown("")

        with gr.Row(equal_height=False, elem_classes=["workbench-split-row"]):
            # ── 左栏：Catalog-backed bookshelf ──
            with gr.Column(scale=3, elem_classes=["bookshelf-panel"]):
                with gr.Row(equal_height=True, elem_classes=["bookshelf-heading-row"]):
                    gr.Markdown("### 项目书架")
                    bookshelf_refresh = gr.Button("刷新", size="sm", scale=0)
                gr.Markdown(
                    "普通点选只改变当前选择；打开项目需要在右侧 Inspector 显式确认。",
                    elem_classes=["bookshelf-help"],
                )
                ov_bookshelf = gr.Dataframe(
                    headers=["项目", "结构", "段进度", "状态", "最近修改"],
                    datatype=["str", "str", "str", "str", "str"],
                    interactive=False,
                    label="",
                    wrap=True,
                    elem_classes=["bookshelf-table"],
                )

            # ── 右栏：Selected Project Inspector ──
            with gr.Column(scale=2, elem_classes=["selected-inspector"]):
                gr.Markdown("### 当前 Selected Project")
                bookshelf_selected = gr.Markdown(
                    "从书架选择项目后，这里显示项目上下文与管理动作。",
                    elem_classes=["selected-project-summary"],
                )
                bookshelf_selected_proj = gr.State("")

                bookshelf_open = gr.Button(
                    "打开项目",
                    variant="primary",
                    interactive=False,
                    elem_classes=["inspector-open-project"],
                )

                with gr.Accordion("维护", open=False, elem_classes=["inspector-accordion"]):
                    with gr.Row(equal_height=True):
                        bookshelf_open_dir = gr.Button("打开项目目录", size="sm", interactive=False)
                        bookshelf_open_audio = gr.Button("打开生成音频", size="sm", interactive=False)
                        bookshelf_open_delivery = gr.Button("打开导出成品", size="sm", interactive=False)
                    with gr.Row(equal_height=True):
                        bookshelf_backup_dir = gr.Textbox(
                            label="备份目录（留空使用数据目录/backups）",
                            placeholder="可选",
                            scale=2,
                        )
                        bookshelf_backup = gr.Button("创建备份", size="sm", interactive=False)
                    with gr.Row(equal_height=True):
                        bookshelf_cleanup = gr.Button("清理缓存", size="sm", interactive=False)
                        bookshelf_cleanup_confirm = gr.Button(
                            "确认清理", variant="primary", size="sm", visible=False
                        )
                        bookshelf_cleanup_cancel = gr.Button("取消", size="sm", visible=False)
                    with gr.Row(equal_height=True):
                        bookshelf_storage = gr.Button("整理存储布局", size="sm", interactive=False)
                        bookshelf_storage_confirm = gr.Button(
                            "确认整理", variant="primary", size="sm", visible=False
                        )
                        bookshelf_storage_cancel = gr.Button("取消", size="sm", visible=False)
                    with gr.Row(equal_height=True):
                        bookshelf_integrity = gr.Button("诊断", size="sm", interactive=False)
                        bookshelf_integrity_repair = gr.Button(
                            "修复", variant="secondary", size="sm", visible=False
                        )

                with gr.Accordion("危险操作", open=False, elem_classes=["inspector-accordion"]):
                    bookshelf_archive = gr.Button(
                        "移入回收站",
                        variant="stop",
                        size="sm",
                        interactive=False,
                    )

                bookshelf_msg = gr.Markdown("")

        # Two-step confirmation state stays bound to the selected-project
        # context; the handler/session revision contract is unchanged.
        bookshelf_cleanup_token = gr.State("")
        bookshelf_storage_token = gr.State("")
        bookshelf_archive_confirm = gr.State("")
        bookshelf_archive_event = gr.State(0)

    return {
        "group": grp_overview,
        "ov_bookshelf": ov_bookshelf,
        "workbench_new_project": workbench_new_project,
        "bookshelf_search": bookshelf_search,
        "bookshelf_refresh": bookshelf_refresh,
        "bookshelf_selected_proj": bookshelf_selected_proj,
        "bookshelf_selected": bookshelf_selected,
        "bookshelf_open": bookshelf_open,
        "bookshelf_open_dir": bookshelf_open_dir,
        "bookshelf_open_audio": bookshelf_open_audio,
        "bookshelf_open_delivery": bookshelf_open_delivery,
        "bookshelf_backup": bookshelf_backup,
        "bookshelf_backup_dir": bookshelf_backup_dir,
        "bookshelf_cleanup": bookshelf_cleanup,
        "bookshelf_cleanup_confirm": bookshelf_cleanup_confirm,
        "bookshelf_cleanup_cancel": bookshelf_cleanup_cancel,
        "bookshelf_cleanup_token": bookshelf_cleanup_token,
        "bookshelf_storage": bookshelf_storage,
        "bookshelf_storage_token": bookshelf_storage_token,
        "bookshelf_storage_confirm": bookshelf_storage_confirm,
        "bookshelf_storage_cancel": bookshelf_storage_cancel,
        "bookshelf_integrity": bookshelf_integrity,
        "bookshelf_integrity_repair": bookshelf_integrity_repair,
        "bookshelf_archive": bookshelf_archive,
        "bookshelf_archive_confirm": bookshelf_archive_confirm,
        "bookshelf_archive_event": bookshelf_archive_event,
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
