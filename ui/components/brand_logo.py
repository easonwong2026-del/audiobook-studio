"""Audiobook Studio 品牌标识组件。

品牌资源路径、展示尺寸和锁定组合集中在此处，避免页面自行拼接图片与文字。
裁切后的侧边栏资源保留原始像素，不使用 CSS filter，深浅背景均可直接展示。
"""
from __future__ import annotations

from pathlib import Path

import gradio as gr

BRAND_MARK_PATH = str(
    Path(__file__).resolve().parents[2]
    / "assets"
    / "brand"
    / "audiobook-studio-sidebar-mark-v1.png"
)
BRAND_MARK_SIZE = 52


def create_brand_logo() -> dict[str, gr.Component]:
    """创建侧边栏品牌锁定组合。"""
    with gr.Row(equal_height=True, elem_classes=["brand-lockup"]):
        mark = gr.Image(
            value=BRAND_MARK_PATH,
            show_label=False,
            show_download_button=False,
            show_fullscreen_button=False,
            interactive=False,
            container=False,
            width=BRAND_MARK_SIZE,
            height=BRAND_MARK_SIZE,
            elem_classes=["brand-mark"],
        )
        wordmark = gr.HTML(
            "<div class='logo-bar'><span>AUDIOBOOK STUDIO</span>"
            "<strong>有声书工作台</strong></div>"
        )
    return {"mark": mark, "wordmark": wordmark}
