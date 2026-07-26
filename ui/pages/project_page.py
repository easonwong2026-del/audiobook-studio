"""项目阶段 UI builder。"""
from __future__ import annotations

import os
from pathlib import Path

import gradio as gr

from lib import config
from services import ProjectService


_EXAMPLE_SCRIPT_PATH = str(Path(__file__).resolve().parents[2] / "structured_script.example.json")


def create_project_page() -> dict:
    """创建项目导入、切换与工作区设置页面。"""
    with gr.Group(visible=False, elem_id="grp-project") as grp_project:
        gr.Markdown("### 项目")
        gr.Markdown("导入书稿、打开已有项目，并确认章节结构是否可进入角色配置。")

        with gr.Row(equal_height=True, elem_classes=["stage-row"]):
            with gr.Column(scale=1, elem_classes=["stage-card"]):
                gr.Markdown("#### 新建项目")
                gr.Markdown("导入由前置脚本工具生成的 `structured_script.json`。")
                gr.Markdown("第一次使用？先下载示例，查看角色、章节和段落的格式。")
                gr.DownloadButton(
                    "下载示例 structured_script.json",
                    value=_EXAMPLE_SCRIPT_PATH,
                    variant="secondary",
                    size="sm",
                )
                p_name = gr.Textbox(label="项目名称", placeholder="例如：甲方来了")
                p_script = gr.File(label="结构化书稿", file_types=[".json"])
                p_create = gr.Button("创建项目", variant="primary")
                p_create_msg = gr.Markdown("")

            with gr.Column(scale=1, elem_classes=["stage-card"]):
                gr.Markdown("#### 继续已有项目")
                gr.Markdown("打开后，工作台会更新角色、合成和交付状态。")
                with gr.Row():
                    p_sel = gr.Dropdown(label="选择项目", choices=ProjectService.scan_projects(), scale=4)
                    p_refresh = gr.Button("刷新", size="sm", scale=1)
                with gr.Row():
                    p_open = gr.Button("打开项目", variant="primary")
                    p_del = gr.Button("删除项目", variant="stop", size="sm")
                p_open_msg = gr.Markdown("")

        gr.Markdown("#### 书稿结构")
        p_summary = gr.Markdown("打开项目后显示书名、角色与合成概览。")
        p_chapter_tree = gr.HTML(value="<div class='inline-empty'>打开项目后在这里查看章节结构。</div>")

        with gr.Accordion("工作区设置", open=False, elem_classes=["settings-accordion"]):
            gr.Markdown("项目、音色和产物默认保存在独立数据目录；更改后会立即应用到本会话。")
            data_dir_box = gr.Textbox(
                label="数据保存位置",
                value=os.path.normpath(config.get_data_dir()),
                placeholder="例如：D:\\AudiobookStudio",
            )
            with gr.Row():
                data_apply = gr.Button("应用保存位置", variant="secondary")
                data_open = gr.Button("打开数据文件夹")
            data_dir_msg = gr.Markdown("")

    return {
        "group": grp_project,
        "p_name": p_name,
        "p_script": p_script,
        "p_create": p_create,
        "p_create_msg": p_create_msg,
        "p_sel": p_sel,
        "p_refresh": p_refresh,
        "p_open": p_open,
        "p_del": p_del,
        "p_open_msg": p_open_msg,
        "p_summary": p_summary,
        "p_chapter_tree": p_chapter_tree,
        "data_dir_box": data_dir_box,
        "data_apply": data_apply,
        "data_open": data_open,
        "data_dir_msg": data_dir_msg,
    }
