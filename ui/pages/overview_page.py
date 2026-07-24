"""概览页 UI builder。"""
from __future__ import annotations
import gradio as gr


def create_overview_page() -> dict:
    """创建概览页组件。

    Returns:
        组件引用字典：group, ov_status, ov_bookshelf, ov_open, ov_synth, ov_export
    """
    with gr.Group(visible=True, elem_id="grp-overview") as grp_overview:
        gr.Markdown("### 概览")
        gr.Markdown("项目进度总览、最近项目与快捷操作。")
        ov_status = gr.Markdown("*等待打开项目…*")
        with gr.Row():
            with gr.Column(scale=2):
                gr.Markdown("#### 📚 最近项目")
                ov_bookshelf = gr.Dataframe(
                    headers=["项目", "章", "段进度", "状态"],
                    datatype=["str", "str", "str", "str"],
                    interactive=True,
                    label="书架（点选某行→直接打开项目）",
                    wrap=True,
                )
            with gr.Column(scale=1):
                gr.Markdown("#### ⚡ 快捷操作")
                ov_open = gr.Button("打开项目", variant="primary")
                ov_synth = gr.Button("开始合成")
                ov_export = gr.Button("导出成品")
    return {
        "group": grp_overview,
        "ov_status": ov_status,
        "ov_bookshelf": ov_bookshelf,
        "ov_open": ov_open,
        "ov_synth": ov_synth,
        "ov_export": ov_export,
    }
