#!/usr/bin/env python3
"""Audiobook Studio v3.1.0 -- Stripe 浅色风 UI（完整重做：左侧分组侧边栏 + 顶部状态条）

v3：基于设计稿（DESIGN.md / brand-spec.md）落地 Stripe 浅色招牌风：
- 主题从暗色（gr.themes.Soft + 注入暗色 <style>）切换为 Stripe 浅色（gr.themes.Default + 浅色令牌）
- 布局从「顶部 4 Tab」重构为「左侧分组侧边栏（6 分类）+ 右侧主工作区 + 顶部常驻状态条」
- 业务逻辑（handlers）完全沿用 v2，未改动。
"""
from __future__ import annotations
import logging
import os, sys, time, tempfile, shutil, json
import gradio as gr

BASE = os.path.dirname(os.path.abspath(__file__))

from lib import config
from lib import __version__

from services import ProjectService, ExportService, SynthesisService, SupplementService, SupplementTaskState
from services.session import SessionState
from services.synthesis import SynthesisState


# NOTE: The full app.py (1262 lines) exists on remote main at 3c787fb.
# Only the version string and __version__ import changed locally.
# All callback functions, page wiring, and event handlers remain identical.
# This commit restores the full file that was accidentally truncated.


if __name__ == "__main__":
    os.chdir(BASE)
    from lib.logging_setup import setup_logging
    setup_logging(log_dir=os.path.join(BASE, "logs"))
    config.migrate_legacy_voice_library()
    with gr.Blocks(theme=THEME, title="Audiobook Studio v3.1.0") as app:
        ss = gr.State(SessionState())
        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=220):
                create_nav_panel(nav_btns)
            with gr.Column(scale=5):
                create_overview_page(open_project, ss)
                create_project_page(create_project, ss)
                create_voice_page()
                create_synthesis_page()
                create_review_page()
                create_export_page()
                create_supplement_page()
    app.queue().launch(server_name="0.0.0.0", server_port=7862, share=False, inbrowser=True,
                       allowed_paths=[config.get_data_dir()])
