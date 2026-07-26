"""导航系统（从 app.py 抽离）。"""
from __future__ import annotations
from pathlib import Path

import gradio as gr


_BRAND_MARK_PATH = str(
    Path(__file__).resolve().parents[1] / "assets" / "brand" / "audiobook-studio-logo-mark-v1.png"
)

# 顶级导航只呈现用户生产流程，而不是内部功能模块。
# 元数据： (页面 ID, 按钮标签, elem_id)
NAV_ITEMS = [
    ("overview", "🏠 工作台", "nav-overview"),
    ("project", "① 项目", "nav-project"),
    ("voices", "② 角色与声音", "nav-voices"),
    ("synth", "③ 生产与质检", "nav-synth"),
    ("export", "④ 交付", "nav-export"),
]

# 内部 Group 与顶级导航的映射。质检和补录在“生产与质检”阶段连续呈现，
# 既保留已有页面组件和事件接线，也不再让用户从顶级导航判断该选哪个功能。
GROUP_ITEMS = [
    "overview",
    "project",
    "voices",
    "production-nav",
    "synth",
    "review",
    "export",
    "supplement",
]

# 页面 Group 列表（运行时由 app.py 填充，顺序与 GROUP_ITEMS 一致）
_GROUPS: list[gr.Group] = []


def _goto(which: str) -> tuple:
    """导航切换：返回 8 个 gr.update(visible=...) 元组。

    Args:
        which: 顶级目标页面 ID（与 NAV_ITEMS 中的 page_id 对应）。

    Returns:
        8 元素组，每个为 ``gr.update(visible=...)``，顺序与 GROUP_ITEMS / _GROUPS 一致。
    """
    production = which in {"synth", "review", "supplement"}
    return tuple(
        gr.update(
            visible=(
                page_id == which
                or (production and page_id == "production-nav")
            )
        )
        for page_id in GROUP_ITEMS
    )


def create_nav_buttons() -> dict[str, gr.Button]:
    """创建侧边栏导航按钮（在 ``with gr.Blocks()`` 上下文内调用）。

    包含侧边栏 Column（黑色背景）和所有工作流导航按钮，
    每个按钮的 elem_id 与 NAV_ITEMS 定义一致。

    Returns:
        ``{"nav_overview": gr.Button, "nav_project": gr.Button,
           "nav_voices": gr.Button, "nav_synth": gr.Button,
           "nav_export": gr.Button}``
    """
    buttons: dict[str, gr.Button] = {}
    with gr.Column(scale=0, min_width=248, elem_classes=["sidebar"]):
        with gr.Row(equal_height=True, elem_classes=["brand-lockup"]):
            gr.Image(
                value=_BRAND_MARK_PATH,
                show_label=False,
                interactive=False,
                container=False,
                width=46,
                height=46,
                elem_classes=["brand-mark"],
            )
            gr.Markdown("<div class='logo-bar'><span>AUDIOBOOK</span>有声书工作台</div>")
        gr.Markdown("<div class='sidebar-caption'>从剧本到可交付音频</div>")
        for page_id, label, elem_id in NAV_ITEMS:
            btn = gr.Button(label, elem_classes=["nav-btn"], elem_id=elem_id)
            buttons[f"nav_{page_id}"] = btn
    return buttons
