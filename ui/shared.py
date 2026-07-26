"""跨页面共享组件（从 app.py 抽离）。"""
from __future__ import annotations
import gradio as gr


def create_status_bar() -> dict:
    """创建顶部全局状态栏。

    包含产品标识和动态项目状态文本，
    在 ``with gr.Blocks()`` 上下文内调用。

    Returns:
        ``{"top_status": gr.Markdown}``
    """
    with gr.Row(elem_classes=["top-status-bar"]):
        with gr.Column(scale=3):
            gr.HTML("<div class='top-brand'><span>AUDIOBOOK STUDIO</span><strong>生产工作台</strong></div>")
        with gr.Column(scale=7):
            top_status = gr.Markdown("*等待打开项目…*")
    return {"top_status": top_status}


def create_sidebar_container() -> dict:
    """创建侧边栏容器（不含导航按钮，仅 Column）。

    返回空的侧边栏 Column，导航按钮由
    ``ui.navigation.create_nav_buttons()`` 填充内部。
    在 ``with gr.Blocks()`` 上下文内调用。

    Returns:
        ``{"sidebar_col": gr.Column}``
    """
    with gr.Column(scale=0, min_width=248, elem_classes=["sidebar"]) as col:
        pass
    return {"sidebar_col": col}
