"""跨页面共享组件（从 app.py 抽离）。"""
from __future__ import annotations

import gradio as gr


def create_status_bar() -> dict:
    """创建顶部全局状态栏。"""
    with gr.Row(elem_classes=["top-status-bar"]):
        with gr.Column(scale=3):
            gr.HTML("<div class='top-brand'><strong>Audiobook Studio</strong></div>")
        with gr.Column(scale=7):
            top_status = gr.Markdown("*等待打开项目…*")
        with gr.Column(scale=0, min_width=120):
            studio_exit = gr.Button(
                "退出 Studio",
                variant="stop",
                size="sm",
                elem_id="studio-exit",
            )

    with gr.Group(visible=False, elem_classes=["studio-exit-confirm"]) as studio_exit_confirmation:
        studio_exit_prompt = gr.Markdown()
        with gr.Row():
            studio_exit_cancel = gr.Button("取消", size="sm")
            studio_exit_confirm = gr.Button("确认退出", variant="stop", size="sm")

    return {
        "top_status": top_status,
        "studio_exit": studio_exit,
        "studio_exit_confirmation": studio_exit_confirmation,
        "studio_exit_prompt": studio_exit_prompt,
        "studio_exit_cancel": studio_exit_cancel,
        "studio_exit_confirm": studio_exit_confirm,
    }
