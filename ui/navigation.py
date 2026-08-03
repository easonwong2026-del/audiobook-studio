"""导航系统（从 app.py 抽离）。"""
from __future__ import annotations

import os

import gradio as gr

from ui.components.brand_logo import create_brand_logo

# 顶级导航只呈现用户生产流程，而不是内部功能模块。
# 元数据： (页面 ID, 按钮标签, elem_id)
NAV_ITEMS = [
    ("overview", "🏠 工作台", "nav-overview"),
    ("create_project", "① 新建项目", "nav-create-project"),
    ("v4", "✨ v4 工作流", "nav-v4"),
    ("v4_role", "🧪 v4 角色工作台", "nav-v4-role"),
    ("project", "② 项目管理", "nav-project"),
    ("voices", "③ 角色与声音", "nav-voices"),
    ("synth", "④ 生产与质检", "nav-synth"),
    ("export", "⑤ 交付", "nav-export"),
]

# 主导航隐藏的页面（V4 独立工作台 / V4 角色工作台收敛为内部调试入口）。
# 开发模式（环境变量 AUDIOBOOK_STUDIO_DEV_MODE=1）下重新显示。
_HIDDEN_NAV = {"v4", "v4_role"}

_SETTINGS_ITEM = ("settings", "⚙ 设置", "nav-settings")

# 内部 Group 与顶级导航的映射（保留 "v4"/"v4_role"，供开发模式 / 内部跳转使用）。
GROUP_ITEMS = [
    "overview",
    "create_project",
    "v4",
    "v4_role",
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


def dev_mode_enabled() -> bool:
    """开发模式开关：环境变量 ``AUDIOBOOK_STUDIO_DEV_MODE=1`` 时显示独立 V4 工作台。"""
    return os.environ.get("AUDIOBOOK_STUDIO_DEV_MODE") == "1"


def _nav_visible(page_id: str) -> bool:
    if page_id not in _HIDDEN_NAV:
        return True
    return dev_mode_enabled()


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


def go(which: str) -> tuple:
    """兼容别名：与 ``_goto`` 完全一致（页面可见性单源）。"""
    return _goto(which)


def nav_active_elem_id(page_id: str) -> str:
    """从目标页面值推导左侧导航高亮 elem_id（单源；生产内部页映射 nav-synth）。

    映射规则（DESIGN §6.7）：``synth/review/supplement/production-nav`` 均高亮
    ``nav-synth``；其余页面按 ``NAV_ITEMS`` / ``_SETTINGS_ITEM`` 一一对应。
    """
    if page_id in {"synth", "review", "supplement", "production-nav"}:
        return "nav-synth"
    if page_id == "settings":
        return _SETTINGS_ITEM[2]
    for pid, _label, elem_id in NAV_ITEMS:
        if pid == page_id:
            return elem_id
    raise ValueError(f"unknown page_id: {page_id}")


def activate_js(page_id: str) -> str:
    """唯一一份 `.active` 高亮 JS 模板（由 ``nav_active_elem_id`` 生成）。

    所有程序化跳转（nav_*.click / ov_* / 书架 / 打开链 / creation_chain）都
    必须使用 ``js=activate_js(page_id)``，保证页面可见性与左侧高亮来自同一目标值。
    """
    elem_id = nav_active_elem_id(page_id)
    return (
        "(x) => { document.querySelectorAll('.nav-btn').forEach(b => "
        "b.classList.remove('active')); document.getElementById('"
        f"{elem_id}')?.classList.add('active'); }}"
    )


def create_nav_buttons() -> dict[str, gr.Button]:
    """创建侧边栏导航按钮。"""
    buttons: dict[str, gr.Button] = {}
    with gr.Column(scale=0, min_width=248, elem_classes=["sidebar"]):
        create_brand_logo()
        gr.Markdown("<div class='sidebar-caption'>从剧本到可交付音频</div>")
        for page_id, label, elem_id in NAV_ITEMS:
            btn = gr.Button(
                label,
                elem_classes=["nav-btn"],
                elem_id=elem_id,
                visible=_nav_visible(page_id),
            )
            buttons[f"nav_{page_id}"] = btn

        # 底部设置
        gr.Markdown("<div style='flex:1'></div>")
        settings_btn = gr.Button(
            _SETTINGS_ITEM[1], elem_classes=["nav-btn", "nav-settings-btn"],
            elem_id=_SETTINGS_ITEM[2],
        )
        buttons["nav_settings"] = settings_btn
    return buttons
