"""生产与质检阶段的内部导航 UI — 对齐 Pencil 三级 tab 设计。"""
from __future__ import annotations

import gradio as gr


def create_production_navigation() -> dict:
    """创建生产阶段内部导航，三 tab 对应合成、质检、补录。"""
    with gr.Group(visible=False, elem_id="grp-production-nav") as group:
        stage = gr.Radio(
            label="生产阶段",
            choices=[
                ("🎛 合成中心", "synth"),
                ("🔍 试听质检", "review"),
                ("🎤 角色补录", "supplement"),
            ],
            value="synth",
            interactive=True,
            elem_classes=["production-tabs"],
        )
        production_check = gr.Markdown(
            "#### 生产检查\n请先打开项目，系统会在进入生产阶段时检查剧本和角色声音。",
            elem_classes=["production-check"],
        )
    return {
        "group": group,
        "stage": stage,
        "production_check": production_check,
    }
