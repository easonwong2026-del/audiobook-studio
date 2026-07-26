"""工作台首页 UI builder。"""
from __future__ import annotations

import gradio as gr


def create_overview_page() -> dict:
    """创建以项目状态和下一步行动为中心的工作台。

    返回的 HTML 组件由 ``app.py`` 读取现有 ``SessionState`` 后刷新；页面本身不读盘、
    不调用 Service，保持 UI 与业务编排分离。
    """
    with gr.Group(visible=True, elem_id="grp-overview") as grp_overview:
        gr.Markdown("### 工作台")
        gr.Markdown("当前项目的生产状态、待处理问题与下一步行动。")
        ov_status = gr.HTML(elem_classes=["workbench-status"])

        with gr.Row(equal_height=True, elem_classes=["workbench-main-row"]):
            with gr.Column(scale=3):
                ov_progress = gr.HTML()
            with gr.Column(scale=2):
                ov_task = gr.HTML()

        with gr.Row(equal_height=True, elem_classes=["workbench-main-row"]):
            with gr.Column(scale=3):
                ov_issues = gr.HTML()
            with gr.Column(scale=2):
                gr.Markdown("#### 继续生产")
                gr.Markdown("系统会根据项目状态把你带到正确的阶段。")
                ov_open = gr.Button("打开 / 切换项目", variant="secondary")
                ov_voices = gr.Button("配置角色声音")
                ov_synth = gr.Button("进入生产与质检", variant="primary")
                ov_export = gr.Button("交付成品")

        gr.Markdown("#### 项目书架")
        gr.Markdown("选择任一项目即可打开。状态会在打开后同步到本工作台。")
        ov_bookshelf = gr.Dataframe(
            headers=["项目", "章", "段进度", "状态"],
            datatype=["str", "str", "str", "str"],
            interactive=True,
            label="最近项目",
            wrap=True,
        )

    return {
        "group": grp_overview,
        "ov_status": ov_status,
        "ov_progress": ov_progress,
        "ov_task": ov_task,
        "ov_issues": ov_issues,
        "ov_bookshelf": ov_bookshelf,
        "ov_open": ov_open,
        "ov_voices": ov_voices,
        "ov_synth": ov_synth,
        "ov_export": ov_export,
    }
