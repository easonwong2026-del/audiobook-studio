"""导航系统（从 app.py 抽离）。"""
from __future__ import annotations

import gradio as gr

from ui.components.brand_logo import create_brand_logo

# 顶级导航只呈现用户生产流程，而不是内部功能模块。
# 元数据： (页面 ID, 按钮标签, elem_id)
#
# ``create_project`` / ``project`` 仍保留在内部 Group topology 中，供旧的
# open-chain、角色页和第三方扩展作为 compatibility sink 使用；它们不再是
# 用户可见的一级入口。新建项目由 Workbench 顶部主操作触发。
NAV_ITEMS = [
    ("overview", "🏠 工作台", "nav-overview"),
    ("voices", "① 角色与声音", "nav-voices"),
    ("synth", "② 生产与质检", "nav-synth"),
    ("export", "③ 交付", "nav-export"),
]

_SETTINGS_ITEM = ("settings", "⚙ 设置", "nav-settings")

# 内部 Group 与顶级导航的映射。
GROUP_ITEMS = [
    "overview",
    "create_project",
    "project",
    "voices",
    "production-nav",
    "synth",
    "review",
    "export",
    "supplement",
    "settings",
]

# 页面 Group 列表（运行时由 app.py 填充）
_GROUPS: list[gr.Group] = []


def _goto(which: str) -> tuple:
    """导航切换：返回 ``len(GROUP_ITEMS)`` 个 gr.update(visible=...)。"""
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
    """创建侧边栏导航按钮。"""
    buttons: dict[str, gr.Button] = {}
    with gr.Column(scale=0, min_width=248, elem_classes=["sidebar"]):
        create_brand_logo()
        gr.Markdown("<div class='sidebar-caption'>从剧本到可交付音频</div>")
        for page_id, label, elem_id in NAV_ITEMS:
            btn = gr.Button(label, elem_classes=["nav-btn"], elem_id=elem_id)
            buttons[f"nav_{page_id}"] = btn

        # Hidden compatibility controls.  The old pages are intentionally
        # kept in the component graph for the current open-chain contract, but
        # neither control is rendered as a user-facing navigation item.
        buttons["nav_create_project"] = gr.Button(
            "新建项目（兼容入口）",
            visible=False,
            elem_classes=["nav-btn", "nav-compat"],
            elem_id="nav-create-project",
        )
        buttons["nav_project"] = gr.Button(
            "项目管理（兼容入口）",
            visible=False,
            elem_classes=["nav-btn", "nav-compat"],
            elem_id="nav-project",
        )

        # 底部设置
        gr.Markdown("<div style='flex:1'></div>")
        settings_btn = gr.Button(
            _SETTINGS_ITEM[1], elem_classes=["nav-btn", "nav-settings-btn"],
            elem_id=_SETTINGS_ITEM[2],
        )
        buttons["nav_settings"] = settings_btn
    return buttons
