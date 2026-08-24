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
                bookshelf_project_kind = gr.Markdown("项目类型：未选择")
                bookshelf_relation_status = gr.Markdown(
                    "选择项目后显示关系状态。",
                    elem_classes=["inspector-relation-status"],
                )

                with gr.Accordion("编辑章节关系", open=False, elem_classes=["inspector-accordion"]):
                    bookshelf_parent_book = gr.Dropdown(
                        label="所属整书（逻辑关系）",
                        choices=[],
                        value=None,
                        interactive=False,
                    )
                    with gr.Row(equal_height=True):
                        bookshelf_chapter_title = gr.Textbox(
                            label="章节标题（可选）",
                            placeholder="选择章节后编辑",
                            interactive=False,
                            scale=2,
                        )
                        bookshelf_chapter_order = gr.Textbox(
                            label="章节顺序（正整数，可选）",
                            placeholder="例如 1",
                            interactive=False,
                            scale=1,
                        )
                    with gr.Row(equal_height=True):
                        bookshelf_bind_chapter = gr.Button("绑定为章节", size="sm", interactive=False)
                        bookshelf_update_chapter = gr.Button(
                            "更新章节信息", variant="secondary", size="sm", interactive=False
                        )
                        bookshelf_unbind_chapter = gr.Button(
                            "解除章节关系", size="sm", interactive=False
                        )

                gr.Markdown("#### Chapter → Book 合并")
                with gr.Row(equal_height=True):
                    merge_source_chapter = gr.Dropdown(
                        label="来源 Chapter",
                        choices=[],
                        value=None,
                        interactive=False,
                        scale=2,
                    )
                    merge_target_book = gr.Dropdown(
                        label="目标 Book",
                        choices=[],
                        value=None,
                        interactive=False,
                        scale=2,
                    )
                merge_analyze = gr.Button(
                    "分析合并",
                    variant="secondary",
                    size="sm",
                    interactive=False,
                )
                with gr.Accordion("高级：合并计划与执行", open=False, elem_classes=["inspector-accordion"]):
                    merge_plan_result = gr.Markdown(
                        "分析后显示 MergePlan、冲突 / 警告与执行资格。"
                    )
                    merge_plan_state = gr.State(None)
                    merge_resolution = gr.JSON(
                        label="冲突 resolution（高级）",
                        value={"voice_conflicts": {}},
                    )
                    merge_confirm = gr.Checkbox(
                        label="我确认按当前 Plan / resolution 执行一次 Chapter → Book 合并",
                        value=False,
                        interactive=False,
                    )
                    merge_confirmation_state = gr.State(None)
                    merge_execute = gr.Button(
                        "执行 Chapter → Book 合并",
                        variant="primary",
                        size="sm",
                        interactive=False,
                    )
                    merge_execution_result = gr.Markdown("")
                    merge_transaction_state = gr.State(None)

                gr.Markdown("#### 整书装配")
                assembly_target_book = gr.Dropdown(
                    label="目标整书",
                    choices=[],
                    value=None,
                    interactive=False,
                )
                assembly_dashboard = gr.Markdown(
                    "选择一个可装配的整书后显示已装配、待装配、阻塞与失败摘要。"
                )
                with gr.Row(equal_height=True):
                    assembly_analyze = gr.Button(
                        "分析整书装配",
                        variant="secondary",
                        size="sm",
                        interactive=False,
                    )
                    assembly_resume = gr.Button(
                        "继续未完成章节",
                        variant="primary",
                        size="sm",
                        interactive=False,
                    )
                with gr.Accordion("高级：装配计划与执行", open=False, elem_classes=["inspector-accordion"]):
                    assembly_plan_result = gr.Markdown(
                        "分析后显示 Whole-book Assembly Plan 与冲突。"
                    )
                    assembly_plan_state = gr.State(None)
                    assembly_resolution = gr.JSON(
                        label="按 Chapter 的 Voice Cast resolution（高级）",
                        value={"chapters": {}},
                    )
                    assembly_confirm = gr.Checkbox(
                        label="我确认按当前 Whole-book Assembly Plan 顺序执行",
                        value=False,
                        interactive=False,
                    )
                    assembly_confirmation_state = gr.State(None)
                    assembly_execute = gr.Button(
                        "开始整书装配",
                        variant="primary",
                        size="sm",
                        interactive=False,
                    )
                    assembly_execution_result = gr.Markdown("")
                    assembly_transaction_state = gr.State(None)

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
        "bookshelf_parent_book": bookshelf_parent_book,
        "bookshelf_bind_chapter": bookshelf_bind_chapter,
        "bookshelf_unbind_chapter": bookshelf_unbind_chapter,
        "bookshelf_project_kind": bookshelf_project_kind,
        "bookshelf_chapter_title": bookshelf_chapter_title,
        "bookshelf_chapter_order": bookshelf_chapter_order,
        "bookshelf_update_chapter": bookshelf_update_chapter,
        "bookshelf_relation_status": bookshelf_relation_status,
        "merge_source_chapter": merge_source_chapter,
        "merge_target_book": merge_target_book,
        "merge_analyze": merge_analyze,
        "merge_plan_result": merge_plan_result,
        "merge_plan_state": merge_plan_state,
        "merge_resolution": merge_resolution,
        "merge_confirm": merge_confirm,
        "merge_confirmation_state": merge_confirmation_state,
        "merge_execute": merge_execute,
        "merge_execution_result": merge_execution_result,
        "merge_transaction_state": merge_transaction_state,
        "assembly_target_book": assembly_target_book,
        "assembly_analyze": assembly_analyze,
        "assembly_dashboard": assembly_dashboard,
        "assembly_plan_result": assembly_plan_result,
        "assembly_plan_state": assembly_plan_state,
        "assembly_resolution": assembly_resolution,
        "assembly_confirm": assembly_confirm,
        "assembly_confirmation_state": assembly_confirmation_state,
        "assembly_execute": assembly_execute,
        "assembly_execution_result": assembly_execution_result,
        "assembly_transaction_state": assembly_transaction_state,
        "assembly_resume": assembly_resume,
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
