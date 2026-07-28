"""新建项目页面 — 从原始书稿或结构化 JSON 创建项目。

分成两个入口：
- 主入口：从原始书稿（TXT/DOCX/EPUB）创建
- 高级入口：从 structured_script.json 创建（折叠）
"""
from __future__ import annotations

import gradio as gr


def create_create_project_page() -> dict:
    with gr.Group(visible=False, elem_id="grp-create-project") as grp:
        gr.Markdown("### 从原始书稿创建")

        with gr.Row(equal_height=True, elem_classes=["stage-row"]):
            with gr.Column(scale=1, elem_classes=["stage-card"]):
                cp_name = gr.Textbox(label="项目名称", placeholder="例如：甲方来了")

            with gr.Column(scale=2, elem_classes=["stage-card"]):
                cp_source = gr.File(
                    label="原始书稿文件",
                    file_types=[".txt", ".docx", ".epub"],
                    type="filepath",
                )

        with gr.Row():
            cp_title = gr.Textbox(label="作品名（可选）", placeholder="默认使用文件名")
            cp_author = gr.Textbox(label="作者（可选）")

        with gr.Row():
            cp_config_summary = gr.Markdown(
                "##### 当前 AI 配置\n"
                "默认 Provider：**Local**（离线分析）\n\n"
                "前往 *设置 → AI 模型* 配置远程 Provider。"
            )

        with gr.Row():
            cp_create = gr.Button("AI 分析并创建项目", variant="primary")

        cp_status = gr.Markdown("")
        cp_result = gr.Markdown("")

        with gr.Accordion("高级：从结构化剧本创建", open=False):
            with gr.Row(equal_height=True, elem_classes=["stage-row"]):
                with gr.Column(scale=1, elem_classes=["stage-card"]):
                    cp_json_name = gr.Textbox(label="项目名称", placeholder="例如：甲方来了")
                with gr.Column(scale=2, elem_classes=["stage-card"]):
                    cp_json_file = gr.File(
                        label="structured_script.json",
                        file_types=[".json"],
                        type="filepath",
                    )
            cp_json_status = gr.Markdown("")
            with gr.Row():
                cp_json_create = gr.Button("从 JSON 创建", variant="primary")
            cp_json_result = gr.Markdown("")

    return {
        "group": grp,
        "cp_name": cp_name,
        "cp_source": cp_source,
        "cp_title": cp_title,
        "cp_author": cp_author,
        "cp_config_summary": cp_config_summary,
        "cp_create": cp_create,
        "cp_status": cp_status,
        "cp_result": cp_result,
        "cp_json_name": cp_json_name,
        "cp_json_file": cp_json_file,
        "cp_json_status": cp_json_status,
        "cp_json_create": cp_json_create,
        "cp_json_result": cp_json_result,
    }
