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
        with gr.Row(equal_height=True, elem_classes=["bookshelf-toolbar"]):
            bookshelf_search = gr.Textbox(
                label="搜索项目（名称 / 书名 / 作者）",
                placeholder="输入关键词过滤书架…",
                scale=5,
            )
            bookshelf_refresh = gr.Button("刷新项目", size="sm", scale=1)
        ov_bookshelf = gr.Dataframe(
            headers=["项目", "章", "段进度", "状态"],
            datatype=["str", "str", "str", "str"],
            interactive=True,
            label="最近项目",
            wrap=True,
        )
        bookshelf_selected_proj = gr.State("")
        with gr.Group(elem_classes=["bookshelf-selection-card"]):
            gr.Markdown("### 当前选择")
            bookshelf_selected = gr.Markdown(
                "从书架选择项目后，可对选中项目执行管理操作；「打开项目」才会进入工作流。"
            )

            with gr.Row():
                bookshelf_open = gr.Button(
                    "打开项目", variant="primary", size="sm", interactive=False
                )
                bookshelf_open_dir = gr.Button(
                    "打开项目目录", size="sm", interactive=False
                )
                bookshelf_open_audio = gr.Button(
                    "打开生成音频", size="sm", interactive=False
                )
                bookshelf_open_delivery = gr.Button(
                    "打开导出成品", size="sm", interactive=False
                )
                bookshelf_archive = gr.Button(
                    "移入回收站", variant="stop", size="sm", interactive=False
                )

        with gr.Accordion("高级管理", open=False):
            with gr.Row(equal_height=True):
                bookshelf_backup = gr.Button(
                    "创建备份", size="sm", interactive=False
                )
                bookshelf_backup_dir = gr.Textbox(
                    label="备份目录（留空使用数据目录/backups）",
                    placeholder="可选",
                    scale=2,
                )
            with gr.Row(equal_height=True):
                bookshelf_cleanup = gr.Button(
                    "清理缓存", size="sm", interactive=False
                )
                bookshelf_cleanup_confirm = gr.Button(
                    "确认清理", variant="primary", size="sm", visible=False
                )
                bookshelf_cleanup_cancel = gr.Button(
                    "取消", size="sm", visible=False
                )
            with gr.Row(equal_height=True):
                bookshelf_storage = gr.Button(
                    "整理存储布局", size="sm", interactive=False
                )
                bookshelf_storage_confirm = gr.Button(
                    "确认整理", variant="primary", size="sm", visible=False
                )
                bookshelf_storage_cancel = gr.Button(
                    "取消", size="sm", visible=False
                )
            with gr.Row(equal_height=True):
                bookshelf_integrity = gr.Button(
                    "诊断", size="sm", interactive=False
                )
                bookshelf_integrity_repair = gr.Button(
                    "修复", variant="secondary", size="sm", visible=False
                )
            with gr.Row(equal_height=True):
                bookshelf_project_kind = gr.Markdown(
                    "项目类型：未选择",
                )
                bookshelf_parent_book = gr.Dropdown(
                    label="所属整书（逻辑关系）",
                    choices=[],
                    value=None,
                    interactive=False,
                    scale=2,
                )
                bookshelf_bind_chapter = gr.Button(
                    "绑定为章节", size="sm", interactive=False
                )
                bookshelf_unbind_chapter = gr.Button(
                    "解除章节关系", size="sm", interactive=False
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
                bookshelf_update_chapter = gr.Button(
                    "更新章节信息", variant="secondary", size="sm", interactive=False
                )
            bookshelf_relation_status = gr.Markdown(
                "选择项目后，可在此设置或解除所属整书。"
            )

            gr.Markdown("#### Chapter → Book 合并（独立事务工作流）")
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
                    "分析合并计划", variant="secondary", size="sm", interactive=False
                )
            merge_plan_result = gr.Markdown(
                "选择一个 Chapter 后，可分析其到目标 Book 的合并计划。"
            )
            merge_plan_state = gr.State(None)
            merge_resolution = gr.JSON(
                label="冲突 resolution（必须显式选择）",
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

            gr.Markdown("#### Whole-book Assembly（顺序编排，独立状态路径）")
            assembly_target_book = gr.Dropdown(
                label="目标 Book（跟随书架 selected Book）",
                choices=[],
                value=None,
                interactive=False,
            )
            assembly_dashboard = gr.Markdown(
                "选择 Book 后，这里会从持久化 merge history / transaction journal 重建当前整书装配状态。"
            )
            with gr.Row(equal_height=True):
                assembly_analyze = gr.Button(
                    "重新分析整书装配",
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
            assembly_plan_result = gr.Markdown(
                "从书架选择一个 Book 后，可分析其关联 Chapter 的整书装配。"
            )
            assembly_plan_state = gr.State(None)
            assembly_resolution = gr.JSON(
                label="按 Chapter 的 Voice Cast resolution（可选）",
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

        # 两步确认状态：记录「已确认的项目名」（字符串语义，"" 表示未确认）；
        # SessionState 另外绑定 selection revision，防止 A → B → A 复用旧确认。
        bookshelf_cleanup_token = gr.State("")
        bookshelf_storage_token = gr.State("")
        bookshelf_archive_confirm = gr.State("")
        bookshelf_msg = gr.Markdown("")

        with gr.Accordion("从备份恢复", open=False), gr.Row():
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
