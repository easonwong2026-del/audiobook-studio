"""导航系统（从 app.py 抽离）。"""
from __future__ import annotations
import gradio as gr

# 导航按钮元数据： (页面ID, 按钮标签, elem_id)
NAV_ITEMS = [
    ("overview", "概览", "nav-overview"),
    ("project", "项目", "nav-project"),
    ("voices", "音色资产", "nav-voices"),
    ("synth", "合成", "nav-synth"),
    ("review", "试听与质检", "nav-review"),
    ("export", "导出", "nav-export"),
    ("supplement", "角色补录", "nav-supplement"),
]

# 页面 Group 列表（运行时由 app.py 填充）
_GROUPS: list[gr.Group] = []


def _goto(which: str) -> tuple:
    """导航切换：返回 7 个 gr.update(visible=...) 元组。

    Args:
        which: 目标页面 ID（与 NAV_ITEMS 中的 page_id 对应）。

    Returns:
        7 元素组，每个为 ``gr.update(visible=...)``，顺序与 NAV_ITEMS / _GROUPS 一致。
    """
    return tuple(
        gr.update(visible=(page_id == which))
        for page_id, _, _ in NAV_ITEMS
    )


def create_nav_buttons() -> dict[str, gr.Button]:
    """创建侧边栏导航按钮（在 ``with gr.Blocks()`` 上下文内调用）。

    包含侧边栏 Column（黑色背景）和所有 7 个导航按钮，
    每个按钮的 elem_id 与 NAV_ITEMS 定义一致。

    Returns:
        ``{"nav_overview": gr.Button, "nav_project": gr.Button,
           "nav_voices": gr.Button, "nav_synth": gr.Button,
           "nav_review": gr.Button, "nav_export": gr.Button,
           "nav_supplement": gr.Button}``
    """
    buttons: dict[str, gr.Button] = {}
    with gr.Column(scale=0, min_width=248, elem_classes=["sidebar"]):
        gr.Markdown("<div class='logo-bar'>有声书工作台</div>")
        for page_id, label, elem_id in NAV_ITEMS:
            btn = gr.Button(label, elem_classes=["nav-btn"], elem_id=elem_id)
            buttons[f"nav_{page_id}"] = btn
    return buttons
