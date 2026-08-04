"""新建项目页面 — 从原始书稿或结构化 JSON 创建项目。

分成两个入口：
- 主入口：从原始书稿（TXT/DOCX/EPUB）创建
- 高级入口：从 structured_script.json 创建（折叠）
"""
from __future__ import annotations

import gradio as gr


def create_create_project_page() -> dict:
    with gr.Group(visible=False, elem_id="grp-create-project", elem_classes=["page-shell"]) as grp:
        gr.Markdown("### 创建 v4 项目 · Source-first")

        with gr.Row(equal_height=True, elem_classes=["stage-row"]):
            with gr.Column(scale=1, elem_classes=["stage-card"]):
                cp_name = gr.Textbox(label="项目名称", placeholder="例如：甲方来了")

            with gr.Column(scale=2, elem_classes=["stage-card"]):
                cp_source = gr.File(
                    label="原始书稿文件",
                    file_types=[".txt", ".docx", ".epub"],
                    type="filepath",
                )

        cp_source_text = gr.Textbox(
            label="或粘贴当前章节原文（上传/粘贴内容按一章处理）",
            placeholder="这里的内容就是要分析和合成的完整当前章节，不会自动拆分全书。",
            lines=8,
            max_lines=20,
        )

        with gr.Row():
            cp_title = gr.Textbox(label="作品名（可选）", placeholder="默认使用文件名")
            cp_author = gr.Textbox(label="作者（可选）")

        cp_slot_status = gr.Markdown("⚪ 上传书稿或输入项目名称后，将立即检查名称状态")
        cp_cleanup = gr.Button(
            "清理残留并重试",
            variant="stop",
            size="sm",
            visible=False,
        )

        with gr.Row():
            cp_config_summary = gr.Markdown(
                "##### v4 创建流程\n"
                "默认快速章节分析：一次只提交当前章节，完成校验后立即保存角色、剧本和合成入口；"
                "AI 未配置时章节仍会先保存，可稍后继续分析。"
            )

        with gr.Row():
            cp_create = gr.Button("创建并快速分析当前章节", variant="primary")

        cp_status = gr.Markdown("")
        cp_result = gr.Markdown("")

        with gr.Accordion("旧版兼容：从 v3 结构化剧本创建", open=False):
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
            cp_json_slot_status = gr.Markdown("⚪ 上传 JSON 或输入项目名称后检查")
            with gr.Row():
                cp_json_create = gr.Button("从 JSON 创建", variant="primary")
                cp_json_cleanup = gr.Button(
                    "清理残留并重试",
                    variant="stop",
                    size="sm",
                    visible=False,
                )
            cp_json_result = gr.Markdown("")

    return {
        "group": grp,
        "cp_name": cp_name,
        "cp_source": cp_source,
        "cp_source_text": cp_source_text,
        "cp_title": cp_title,
        "cp_author": cp_author,
        "cp_slot_status": cp_slot_status,
        "cp_cleanup": cp_cleanup,
        "cp_config_summary": cp_config_summary,
        "cp_create": cp_create,
        "cp_status": cp_status,
        "cp_result": cp_result,
        "cp_json_name": cp_json_name,
        "cp_json_file": cp_json_file,
        "cp_json_status": cp_json_status,
        "cp_json_slot_status": cp_json_slot_status,
        "cp_json_cleanup": cp_json_cleanup,
        "cp_json_create": cp_json_create,
        "cp_json_result": cp_json_result,
    }
