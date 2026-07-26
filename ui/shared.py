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
    return {"top_status": top_status}
