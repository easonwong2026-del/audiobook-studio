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
                    p_archive = gr.Button("移入回收站", size="sm")
                    p_permanent_confirm = gr.Checkbox(label="确认永久删除", value=False, scale=1)
                    p_permanent_delete = gr.Button("永久删除", variant="stop", size="sm")
                p_open_msg = gr.Markdown("")
                p_hide = gr.Button("仅从项目列表移除（保留本地文件）", size="sm")
                with gr.Row():
                    p_restore_name = gr.Textbox(label="恢复隐藏项目名称", scale=2)
                    p_restore_list = gr.Button("恢复到项目列表", size="sm")

        gr.Markdown("#### 书稿结构")
        p_summary = gr.Markdown("打开项目后显示书名、角色与合成概览。")
        p_chapter_tree = gr.HTML(value="<div class='inline-empty'>打开项目后在这里查看章节结构。</div>")
        p_storage = gr.Markdown("项目目录、存储占用和完整性状态会显示在这里。")
        with gr.Row():
            p_storage_refresh = gr.Button("刷新存储信息", size="sm")
            p_cache_clear = gr.Button("清理试听缓存", size="sm")
            p_cleanup_scan = gr.Button("扫描安全清理项", size="sm")
            p_cleanup_execute = gr.Button("执行安全清理", size="sm")
        p_cleanup_token = gr.State("")
        with gr.Row():
            p_integrity_check = gr.Button("检查项目完整性", size="sm")
            p_integrity_repair = gr.Button("修复安全问题", size="sm")
        with gr.Row():
            p_backup_dir = gr.Textbox(label="备份目录（留空使用数据目录/backups）", scale=2)
            p_backup = gr.Button("创建项目备份", size="sm")
        with gr.Row():
            p_restore_file = gr.File(label="选择项目备份 ZIP", file_count="single", type="filepath", scale=2)
            p_restore = gr.Button("恢复项目备份", size="sm")
        with gr.Row():
            p_migrate_root = gr.Textbox(label="迁移目标项目根目录（复制并校验，源项目保留）", scale=2)
            p_migrate = gr.Button("迁移项目", size="sm")
        p_storage_msg = gr.Markdown("")

    return {
        "group": grp_project,
        "p_sel": p_sel,
        "p_refresh": p_refresh,
        "p_open": p_open,
        "p_archive": p_archive,
        "p_del": p_archive,
        "p_hide": p_hide,
        "p_restore_name": p_restore_name,
        "p_restore_list": p_restore_list,
        "p_permanent_confirm": p_permanent_confirm,
        "p_permanent_delete": p_permanent_delete,
        "p_open_msg": p_open_msg,
        "p_summary": p_summary,
        "p_chapter_tree": p_chapter_tree,
        "p_open_dir": p_open_dir,
        "p_storage": p_storage,
        "p_storage_refresh": p_storage_refresh,
        "p_cache_clear": p_cache_clear,
        "p_cleanup_scan": p_cleanup_scan,
        "p_cleanup_execute": p_cleanup_execute,
        "p_cleanup_token": p_cleanup_token,
        "p_integrity_check": p_integrity_check,
        "p_integrity_repair": p_integrity_repair,
        "p_backup_dir": p_backup_dir,
        "p_backup": p_backup,
        "p_restore_file": p_restore_file,
        "p_restore": p_restore,
        "p_migrate_root": p_migrate_root,
        "p_migrate": p_migrate,
        "p_storage_msg": p_storage_msg,
    }
