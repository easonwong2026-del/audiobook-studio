"""生产与质检阶段的内部导航 UI。"""
from __future__ import annotations

import gradio as gr


def create_production_navigation() -> dict:
    """创建生产阶段内部导航，不引入业务逻辑或额外顶级入口。"""
    with gr.Group(visible=False, elem_id="grp-production-nav") as group:
        gr.Markdown("### 生产与质检")
        gr.Markdown("在同一生产阶段内按顺序完成合成、试听质检和角色补录。")
        stage = gr.Radio(
            label="生产阶段",
            choices=[
                ("合成中心", "synth"),
                ("试听质检", "review"),
                ("角色补录", "supplement"),
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
