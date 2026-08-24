"""纯模块：gr.Dataframe 的 Apple 暗色配色 + 状态语义着色包装（层 B 数据来源）。

纪律（与 ``lib/progress.py`` 同）：
- **禁止 ``import gradio``**：本模块不依赖任何前端运行时，可独立单测。
- 仅产出纯数据结构（调色板常量、着色契约 dict），由 ``app.py`` 在 UI 边界调用。

两层着色机制说明（详见增量设计文档 ``增量设计_表格Apple配色.md``）：
- 层 A（整体可读 + Apple 底色）：在 ``app.py`` 顶部 ``gr.HTML`` 注入的 ``<style>`` 中，
  对 ``body``/``.gradio-container`` 用 ``!important`` 覆盖 dataframe 的 CSS 变量
  （斑马底 / 文字 / 边框 / 圆角 / 选中描边）。
- 层 B（状态语义色）：本模块导出的 :func:`style_dataframe` 产出
  ``{"data", "headers", "metadata": {"styling": [[...]]}}`` 契约值；
  ``gr.Dataframe`` 渲染时会**直接把每个单元格的样式字符串写成 inline style**，
  属组件自身渲染路径，架构上 100% 生效，不依赖任何 CSS 注入。

本模块只负责「状态列上色」（仅写 ``color``，绝不写背景），因此不会破坏层 A 的斑马纹。
"""
from __future__ import annotations

from typing import Optional

# ───────────────────────────── 2.1 基础底色 / 文字 / 边框 / 圆角（层 A 变量覆盖用） ─────────────────────────────
APPLE_WINDOW_BG = "#0A0A0D"          # 页面/窗口底（沿用现有）
APPLE_TABLE_ODD = "#16161C"          # 单元格奇行底
APPLE_TABLE_EVEN = "#1C1C1E"        # 单元格偶行底
APPLE_HEADER_TEXT = "#C7C7D1"        # 表头文字
APPLE_BODY_TEXT = "#E8E8ED"          # 正文文字（同时惠及 O13 Markdown 表）
APPLE_SECONDARY_TEXT = "rgba(235,235,245,0.6)"  # 次要文字（预览列等）
APPLE_BORDER = "#2A2A33"             # 边框/分隔线
APPLE_RADIUS = "10px"                # 圆角
APPLE_ACCENT = "#6A4FE8"             # 选中描边（沿用现有紫，不改为 Apple 蓝，避免与「运行中=蓝」撞色）

# ───────────────────────────── 2.2 状态语义色（Apple 系统色） ─────────────────────────────
APPLE_GREEN = "#30D158"              # 完成 / 成功
APPLE_BLUE = "#0A84FF"               # 运行中
APPLE_RED = "#FF453A"                # 失败 / 已取消（负面终态）
APPLE_ORANGE = "#FF9F0A"             # 已暂停
APPLE_GREY = "#8E8E93"               # 待合成（灰）
APPLE_TERTIARY_GREY = "#6C6C70"      # 跳过（更暗的三级灰，区别于 pending）

# O3 队列列表：键为状态图标字符（见 lib.progress.SEGMENT_STATUS_ICONS），值对应语义色。
ICON_COLORS: dict[str, str] = {
    "✅": APPLE_GREEN,            # done 完成
    "⏳": APPLE_BLUE,            # running 运行中
    "❌": APPLE_RED,             # error 失败
    "⏸": APPLE_ORANGE,          # paused 已暂停
    "⛔": APPLE_RED,             # cancelled 已取消（与 error 同为红，由图标区分）
    "⬜": APPLE_GREY,            # pending 待合成
    "⏭": APPLE_TERTIARY_GREY,   # skipped 跳过
}

# O4 书架：键为 lib.project_manager._project_status 实际产出的完整状态字符串（含 emoji 前缀）。
STATUS_WORD_COLORS: dict[str, str] = {
    "✅完成": APPLE_GREEN,        # 完成 → 绿
    "🟢进行中": APPLE_BLUE,      # 进行中 → 蓝
    "🟡部分": APPLE_ORANGE,      # 部分 → 橙
    "🔴有失败": APPLE_RED,       # 有失败 → 红
    "⚪未开始": APPLE_GREY,      # 未开始 → 灰
}

# Dataframe 列定义（与 Workbench 组件定义一致，集中来源避免硬编码）。
# ``结构`` 是 Catalog hierarchy 的展示列；``ProjectSummary.chapters`` 仍
# 只用于项目自身 structured_script 的章节数，不能拿来代替 Book 的子项目数。
BOOKSHELF_HEADERS = ["项目", "结构", "段进度", "状态", "最近修改"]
VOICE_HEADERS = ["名称", "分类", "大小(KB)", "试听"]


def style_dataframe(
    data: list[list],
    headers: list[str],
    status_col: Optional[int] = None,
    status_color_map: Optional[dict] = None,
    default_text: str = "#E8E8ED",
    header_color: str = "#C7C7D1",
) -> dict:
    """将二维行数据包装为 ``gr.Dataframe`` 的着色契约值（层 B）。

    仅对 ``status_col`` 指定列的单元格写入 ``color:<hex>`` 样式字符串；其余单元格
    留空字符串 ``""``（交给层 A 的 CSS 变量控制背景与基础文字色，不写背景 → 斑马纹不破）。

    Args:
        data: 原始二维行数据（list[list]），原样透传。
        headers: 列标题，需与喂入的 ``gr.Dataframe`` 组件 ``headers`` 一致。
        status_col: 状态语义列索引（None 表示不上色，如 O5/O9）。
        status_color_map: ``{cell_value: hex_color}`` 映射（如 ICON_COLORS / STATUS_WORD_COLORS）。
        default_text: 状态列单元格未命中映射时的回落文字色。
        header_color: 预留表头文字色（当前层 A 已统一处理，保留参数向后兼容契约签名）。

    Returns:
        契约 dict：``{"data": data, "headers": headers, "metadata": {"styling": styling}}``。
        ``styling`` 是与 ``data`` 同形的 ``list[list[str]]``；空数据返回 ``styling=[]``。
    """
    color_map: dict = status_color_map or {}
    styling: list[list[str]] = []
    for row in data:
        style_row: list[str] = []
        for c, cell in enumerate(row):
            if status_col is not None and c == status_col:
                color = color_map.get(cell, default_text)
                style_row.append(f"color:{color}")
            else:
                style_row.append("")
        styling.append(style_row)
    return {
        "data": data,
        "headers": headers,
        "metadata": {
            "styling": styling,
        },
    }
