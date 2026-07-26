"""Audiobook Studio Design Tokens — 从 Pencil 设计提取的统一令牌。

所有颜色、间距、圆角等设计决策集中在此，
主题层（theme.py）和组件层从本模块引用，避免散落多处。

来源：pencil-new.pen / audiobookui.pen 设计画板
"""
from __future__ import annotations

# ── 颜色 ──────────────────────────────────────────────────────────
ACCENT        = "#D4F56A"   # 主操作色 / CTA / 激活指示
ACCENT_DEEP   = "#7D9F23"   # 强调文字 / 选中态边框
ACCENT_SOFT   = "#E5F5BD"   # 选中行背景 / 轻量高亮

SURFACE       = "#EDF2EC"   # 页面背景
SIDEBAR       = "#18221C"   # 侧栏背景（深绿）
CARD          = "#FFFFFF"   # 卡片 / 区块背景
PANEL         = "#F8FBF8"   # 次要面板背景
BORDER        = "#DFE7E0"   # 边框

TEXT_PRIMARY  = "#18221C"   # 一级文字
TEXT_MUTED    = "#647067"   # 二级 / 弱化文字

STATUS_OK     = "#1D7A4F"   # 成功绿
STATUS_WARN   = "#D68A1E"   # 警告橙
STATUS_ERR    = "#C81E2E"   # 错误红

# ── 间距 ──────────────────────────────────────────────────────────
SPACING_XS    = 4
SPACING_SM    = 8
SPACING_MD    = 12
SPACING_LG    = 16
SPACING_XL    = 24
SPACING_2XL   = 28

PAGE_PADDING  = SPACING_2XL  # 页面内边距
SECTION_GAP   = SPACING_MD   # 区块间距
CARD_PADDING  = SPACING_LG   # 卡片内边距

# ── 尺寸 ──────────────────────────────────────────────────────────
SIDEBAR_WIDTH    = 236       # 侧栏宽度
CONTENT_MAX_WIDTH = 1120     # 内容区最大宽度
HEADER_HEIGHT    = 70        # 顶栏高度
CANVAS_WIDTH     = 1440      # 画板宽度
NAV_ITEM_HEIGHT  = 44        # 导航条目高度

# ── 圆角 ──────────────────────────────────────────────────────────
RADIUS_CARD    = 14    # 卡片圆角
RADIUS_INPUT   = 10    # 输入框圆角
RADIUS_BTN     = 10    # 按钮圆角
RADIUS_NAV     = 12    # 导航按钮圆角

# ── 字体 ──────────────────────────────────────────────────────────
FONT_BODY   = "'Outfit', 'SpotifyMixUI', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
FONT_MONO   = "'JetBrains Mono', ui-monospace, monospace"

# ── 阴影 ──────────────────────────────────────────────────────────
SHADOW_CARD = "0 4px 16px rgba(0,0,0,0.06)"
SHADOW_DROPDOWN = "0 8px 24px rgba(0,0,0,0.08)"
