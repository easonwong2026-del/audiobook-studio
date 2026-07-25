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

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui.theme import THEME, LIGHT_CSS
from ui.navigation import _goto, _GROUPS, create_nav_buttons
from ui.shared import create_status_bar
from ui.pages import (
    create_overview_page,
    create_project_page,
    create_review_page,
    create_export_page,
    create_supplement_page,
    create_voice_page,
    create_synthesis_page,
)
from lib import tts_engine
from lib import script_loader
from lib import segment_cache
from lib import config
from lib import project_manager as _pm
from lib import progress as synth_progress
from lib import audio_pipeline
from lib import voice_lib
from lib import dataframe_style as df_style
from lib import __version__

from services import ProjectService, ExportService, SynthesisService, SupplementService, SupplementTaskState
from services.session import SessionState
from services.synthesis import SynthesisState

BASE = os.path.dirname(os.path.abspath(__file__))
with gr.Blocks(theme=THEME, title="Audiobook Studio v3.1.0") as app:
    ss = gr.State(SessionState())
app.queue().launch(server_name="0.0.0.0", server_port=7862, share=False, inbrowser=True, allowed_paths=[config.get_data_dir()])