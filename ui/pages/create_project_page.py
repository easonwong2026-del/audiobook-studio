"""新建项目页面：外部 Agent JSON 是唯一入口。"""
from __future__ import annotations

import gradio as gr


def create_create_project_page() -> dict:
    with gr.Group(visible=False, elem_id="grp-create-project") as grp:
        gr.Markdown("### 从剧本分析 JSON 创建项目")
        gr.Markdown(
            "请先使用有声书分析 Skill 将小说分析为 `structured_script.json`，"
            "再导入工作台完成角色声音绑定、合成、试听质检和导出。"
        )

        with gr.Row(equal_height=True, elem_classes=["stage-row"]):
            with gr.Column(scale=2, elem_classes=["stage-card"]):
                cp_json_file = gr.File(
                    label="structured_script.json",
                    file_types=[".json"],
                    type="filepath",
                )
            with gr.Column(scale=1, elem_classes=["stage-card"]):
                cp_json_name = gr.Textbox(
                    label="项目名称",
                    placeholder="上传后自动填写，可手动修改",
                )

        cp_json_slot_status = gr.Markdown("⚪ 上传 JSON 后立即检查项目槽位")
        cp_json_cleanup = gr.Button(
            "将异常目录移到回收站并重新检查",
            variant="stop",
            size="sm",
            visible=False,
        )
        cp_json_preview = gr.Markdown(
            "### 等待导入\n上传 JSON 后显示作品、角色、章节和校验结果。"
        )
        with gr.Row():
            cp_json_check = gr.Button("检查 JSON", variant="secondary")
            cp_json_create = gr.Button(
                "创建并前往角色与声音",
                variant="primary",
                interactive=False,
            )
        cp_json_result = gr.Markdown("")
        # Per-operation result gate only; it is not a project selector or mirror.
        cp_json_success = gr.State(False)

    return {
        "group": grp,
        "cp_json_file": cp_json_file,
        "cp_json_name": cp_json_name,
        "cp_json_slot_status": cp_json_slot_status,
        "cp_json_cleanup": cp_json_cleanup,
        "cp_json_preview": cp_json_preview,
        "cp_json_check": cp_json_check,
        "cp_json_create": cp_json_create,
        "cp_json_result": cp_json_result,
        "cp_json_success": cp_json_success,
    }
