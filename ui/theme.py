"""主题与 CSS（从 app.py 抽离）。

颜色与间距引用自 ui/tokens.py，确保与 Pencil 设计源一致。
"""
from __future__ import annotations

import gradio as gr

from ui.tokens import (
    ACCENT,
    ACCENT_DEEP,
    ACCENT_SOFT,
    BORDER,
    CARD,
    PANEL,
    RADIUS_INPUT,
    SHADOW_CARD,
    SIDEBAR,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

THEME = gr.themes.Default(
    primary_hue=gr.themes.Color(
        c50="#f9fce3", c100="#f0f7b8", c200="#e0ec70", c300=ACCENT,
        c400="#b8d856", c500="#9bbf3f", c600=ACCENT_DEEP, c700="#5e7818",
        c800="#3f5410", c900="#26360a", c950="#161f05",
    ),
    neutral_hue=gr.themes.Color(
        c50="#ffffff", c100="#f7f7f7", c200="#ededed", c300="#d4d4d4",
        c400="#a0a0a0", c500="#7d7d7d", c600="#5e5e5e", c700="#454545",
        c800="#2e2e2e", c900="#1c1c1c", c950="#0a0a0a",
    ),
    font=[gr.themes.GoogleFont("Outfit"), "ui-sans-serif", "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace", "monospace"],
).set(
    # 画布与表面
    body_background_fill=SURFACE,
    body_background_fill_dark=SURFACE,
    background_fill_primary=CARD,
    background_fill_primary_dark=CARD,
    background_fill_secondary=PANEL,
    background_fill_secondary_dark=PANEL,
    panel_background_fill=PANEL,
    panel_background_fill_dark=PANEL,
    panel_border_color=BORDER,
    panel_border_color_dark=BORDER,
    block_background_fill=CARD,
    block_background_fill_dark=CARD,
    block_border_color=BORDER,
    block_border_color_dark=BORDER,
    block_radius="16px",
    block_info_text_color=TEXT_MUTED,
    block_info_text_color_dark=TEXT_MUTED,
    block_label_text_color=TEXT_MUTED,
    block_label_text_color_dark=TEXT_MUTED,
    block_title_text_color=TEXT_PRIMARY,
    block_title_text_color_dark=TEXT_PRIMARY,
    # 主色（黄绿仅做 CTA/激活——功能色）
    button_primary_background_fill=ACCENT,
    button_primary_background_fill_hover="#c4e55a",
    button_primary_text_color=TEXT_PRIMARY,
    button_primary_border_color="transparent",
    button_secondary_background_fill=SIDEBAR,
    button_secondary_background_fill_hover="#2A3A32",
    button_secondary_text_color="#ffffff",
    button_secondary_border_color="transparent",
    button_cancel_background_fill=CARD,
    button_cancel_background_fill_hover="#fff5f5",
    button_cancel_text_color="#f14658",
    button_cancel_border_color="#f14658",
    # 输入
    input_background_fill=CARD,
    input_background_fill_dark=CARD,
    input_background_fill_focus=CARD,
    input_background_fill_focus_dark=CARD,
    input_border_color=BORDER,
    input_border_color_dark=BORDER,
    input_border_color_focus=TEXT_PRIMARY,
    input_border_color_focus_dark=TEXT_PRIMARY,
    input_radius=RADIUS_INPUT,
    input_placeholder_color="#9a9a9a",
    input_placeholder_color_dark="#7d8980",
    # 单选 / 复选（Gradio 5.50 共用 checkbox label token）
    checkbox_background_color=CARD,
    checkbox_background_color_dark=CARD,
    checkbox_background_color_focus=PANEL,
    checkbox_background_color_focus_dark=PANEL,
    checkbox_background_color_hover=PANEL,
    checkbox_background_color_hover_dark=PANEL,
    checkbox_background_color_selected=ACCENT,
    checkbox_background_color_selected_dark=ACCENT,
    checkbox_border_color=BORDER,
    checkbox_border_color_dark=BORDER,
    checkbox_border_color_focus=ACCENT_DEEP,
    checkbox_border_color_focus_dark=ACCENT_DEEP,
    checkbox_border_color_hover=ACCENT_DEEP,
    checkbox_border_color_hover_dark=ACCENT_DEEP,
    checkbox_border_color_selected=ACCENT_DEEP,
    checkbox_border_color_selected_dark=ACCENT_DEEP,
    checkbox_label_background_fill=CARD,
    checkbox_label_background_fill_dark=CARD,
    checkbox_label_background_fill_hover=PANEL,
    checkbox_label_background_fill_hover_dark=PANEL,
    checkbox_label_background_fill_selected=ACCENT_SOFT,
    checkbox_label_background_fill_selected_dark=ACCENT_SOFT,
    checkbox_label_border_color=BORDER,
    checkbox_label_border_color_dark=BORDER,
    checkbox_label_border_color_hover=ACCENT_DEEP,
    checkbox_label_border_color_hover_dark=ACCENT_DEEP,
    checkbox_label_border_color_selected=ACCENT_DEEP,
    checkbox_label_border_color_selected_dark=ACCENT_DEEP,
    checkbox_label_text_color=TEXT_PRIMARY,
    checkbox_label_text_color_dark=TEXT_PRIMARY,
    checkbox_label_text_color_selected=TEXT_PRIMARY,
    checkbox_label_text_color_selected_dark=TEXT_PRIMARY,
    # 文本
    body_text_color=TEXT_PRIMARY,
    body_text_color_subdued=TEXT_MUTED,
    body_text_color_dark=TEXT_PRIMARY,
    body_text_color_subdued_dark=TEXT_MUTED,
    link_text_color=ACCENT_DEEP,
    # 阴影
    shadow_drop=SHADOW_CARD,
    # 表格
    table_text_color=TEXT_PRIMARY,
    table_border_color="#e5e5e5",
    table_even_background_fill=CARD,
    table_odd_background_fill="#fafafa",
    table_radius="14px",
)

LIGHT_CSS = f"""<style>
/* ===== Pencil Design Tokens (CSS custom properties) ===== */
:root {{
  --pencil-accent: {ACCENT};
  --pencil-accent-deep: {ACCENT_DEEP};
  --pencil-accent-soft: {ACCENT_SOFT};
  --pencil-surface: {SURFACE};
  --pencil-sidebar: {SIDEBAR};
  --pencil-card: {CARD};
  --pencil-panel: {PANEL};
  --pencil-border: {BORDER};
  --pencil-text-primary: {TEXT_PRIMARY};
  --pencil-text-muted: {TEXT_MUTED};
}}

/* ===== 下拉 / 列表 选项（浅色底深字 + 圆角） ===== */
[role="option"], .item, .options li, ul[role="listbox"] li {{ color:{TEXT_PRIMARY}!important; background:{CARD}!important; border-radius:8px!important; padding:8px 12px!important; }}
[role="option"]:hover, .item:hover {{ color:{TEXT_PRIMARY}!important; background:#f5f5f5!important; }}
footer {{ display:none!important }}

/* ===== 全局画布与变量（浅色 Stripe 风 + 大圆角） ===== */
body, .gradio-container {{
  font-family:'Outfit','SpotifyMixUI',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif,'Apple Color Emoji','Segoe UI Emoji','Segoe UI Symbol'!important;
  --body-text-color:{TEXT_PRIMARY}!important;
  --body-text-color-dark:{TEXT_PRIMARY}!important;
  --body-text-color-subdued:{TEXT_MUTED}!important;
  --body-text-color-subdued-dark:{TEXT_MUTED}!important;
  --block-info-text-color:{TEXT_MUTED}!important;
  --block-info-text-color-dark:{TEXT_MUTED}!important;
  --block-label-text-color:{TEXT_MUTED}!important;
  --block-label-text-color-dark:{TEXT_MUTED}!important;
  --block-title-text-color:{TEXT_PRIMARY}!important;
  --block-title-text-color-dark:{TEXT_PRIMARY}!important;
  --block-background-fill:{CARD}!important;
  --block-background-fill-dark:{CARD}!important;
  --block-border-color:{BORDER}!important;
  --block-border-color-dark:{BORDER}!important;
  --table-odd-background-fill:#f7faf7!important;
  --table-even-background-fill:{CARD}!important;
  --border-color-primary:{BORDER}!important;
  --table-radius:14px!important;
  --color-accent:{ACCENT}!important;
  --input-background-fill:{CARD}!important;
  --input-background-fill-dark:{CARD}!important;
  --input-background-fill-focus:{CARD}!important;
  --input-background-fill-focus-dark:{CARD}!important;
  --input-border-color:{BORDER}!important;
  --input-border-color-dark:{BORDER}!important;
  --input-border-color-focus:{ACCENT_DEEP}!important;
  --input-border-color-focus-dark:{ACCENT_DEEP}!important;
  --input-placeholder-color:{TEXT_MUTED}!important;
  --input-placeholder-color-dark:{TEXT_MUTED}!important;
  --input-text-color:{TEXT_PRIMARY}!important;
  --input-radius:{RADIUS_INPUT}!important;
  --block-radius:16px!important;
  --checkbox-background-color:{CARD}!important;
  --checkbox-background-color-dark:{CARD}!important;
  --checkbox-background-color-focus:{PANEL}!important;
  --checkbox-background-color-focus-dark:{PANEL}!important;
  --checkbox-background-color-hover:{PANEL}!important;
  --checkbox-background-color-hover-dark:{PANEL}!important;
  --checkbox-background-color-selected:{ACCENT}!important;
  --checkbox-background-color-selected-dark:{ACCENT}!important;
  --checkbox-label-background-fill:{CARD}!important;
  --checkbox-label-background-fill-dark:{CARD}!important;
  --checkbox-label-background-fill-hover:{PANEL}!important;
  --checkbox-label-background-fill-hover-dark:{PANEL}!important;
  --checkbox-label-background-fill-selected:{ACCENT_SOFT}!important;
  --checkbox-label-background-fill-selected-dark:{ACCENT_SOFT}!important;
  --checkbox-label-border-color:{BORDER}!important;
  --checkbox-label-border-color-dark:{BORDER}!important;
  --checkbox-label-border-color-hover:{ACCENT_DEEP}!important;
  --checkbox-label-border-color-hover-dark:{ACCENT_DEEP}!important;
  --checkbox-label-border-color-selected:{ACCENT_DEEP}!important;
  --checkbox-label-border-color-selected-dark:{ACCENT_DEEP}!important;
  --checkbox-label-text-color:{TEXT_PRIMARY}!important;
  --checkbox-label-text-color-dark:{TEXT_PRIMARY}!important;
  --checkbox-label-text-color-selected:{TEXT_PRIMARY}!important;
  --checkbox-label-text-color-selected-dark:{TEXT_PRIMARY}!important;
  background:{SURFACE}!important;
}}
html {{
  overflow-y:scroll!important;
  scrollbar-gutter:stable;
}}
body {{
  min-width:0!important;
  overflow-x:hidden!important;
}}
.gradio-container {{
  width:100%!important;
  max-width:1440px!important;
  min-width:0!important;
  margin:0 auto!important;
  padding:20px 22px!important;
  box-sizing:border-box!important;
}}
.gradio-container > main.fillable,
.gradio-container > main.fillable > .wrap,
.gradio-container > main.fillable > .wrap > .contain,
.gradio-container > main.fillable > .wrap > .contain > .column,
.gradio-container > main.fillable > .wrap > .contain > .column > .row {{
  width:100%!important;
  max-width:100%!important;
  min-width:0!important;
  box-sizing:border-box!important;
}}
.gradio-container > main.fillable > .wrap > .contain > .column > .row {{
  align-items:stretch!important;
}}

/* ===== 文本输入 / 数字（白底深字 + 圆角 + focus 黄绿阴影） ===== */
textarea {{ font-family:'JetBrains Mono',monospace!important; font-size:13px!important; color:{TEXT_PRIMARY}!important; background:{CARD}!important; border-radius:10px!important; border:1px solid {BORDER}!important; padding:10px 12px!important; transition:all 0.15s!important; }}
textarea:focus {{ outline:none!important; border-color:{ACCENT_DEEP}!important; box-shadow:0 0 0 3px rgba(212,245,106,0.28)!important; }}
input, input.border-none {{ color:{TEXT_PRIMARY}!important; -webkit-text-fill-color:{TEXT_PRIMARY}!important; background:{CARD}!important; border-radius:10px!important; border:1px solid {BORDER}!important; padding:10px 12px!important; transition:all 0.15s!important; }}
input:focus {{ outline:none!important; border-color:{ACCENT_DEEP}!important; box-shadow:0 0 0 3px rgba(212,245,106,0.28)!important; }}
.gr-textbox input, .gr-number input, .gr-textbox textarea {{ background:{CARD}!important; color:{TEXT_PRIMARY}!important; border-radius:10px!important; border:1px solid {BORDER}!important; }}
input::placeholder, textarea::placeholder {{ color:#7d8980!important; opacity:1!important; }}
input:disabled, textarea:disabled, [aria-disabled="true"] input {{
  color:#68756c!important; -webkit-text-fill-color:#68756c!important;
  background:#f1f5f1!important; opacity:1!important;
}}

/* ===== Gradio 5.50 component content（固定浅色主题，兼容 body.dark） ===== */
.gradio-container [data-testid="block-info"] {{
  color:{TEXT_PRIMARY}!important;
  -webkit-text-fill-color:{TEXT_PRIMARY}!important;
}}
.gradio-container [data-testid="block-info"] + div .prose,
.gradio-container [data-testid="block-info"] + div .prose * {{
  color:{TEXT_MUTED}!important;
}}
.gradio-container input[role="listbox"],
.gradio-container [data-testid="textbox"],
.gradio-container textarea {{
  color:{TEXT_PRIMARY}!important;
  -webkit-text-fill-color:{TEXT_PRIMARY}!important;
  background:{CARD}!important;
}}
.gradio-container input[role="listbox"]:disabled,
.gradio-container [data-testid="textbox"]:disabled,
.gradio-container textarea:disabled {{
  color:{TEXT_PRIMARY}!important;
  -webkit-text-fill-color:{TEXT_PRIMARY}!important;
  background:{PANEL}!important;
  opacity:1!important;
}}
.gradio-container label[data-testid$="-radio-label"],
.gradio-container label[data-testid$="-checkbox-label"] {{
  background:{CARD}!important;
  color:{TEXT_PRIMARY}!important;
  border:1px solid {BORDER}!important;
  opacity:1!important;
}}
.gradio-container label[data-testid$="-radio-label"] span,
.gradio-container label[data-testid$="-checkbox-label"] span {{
  color:{TEXT_PRIMARY}!important;
  -webkit-text-fill-color:{TEXT_PRIMARY}!important;
}}
.gradio-container label[data-testid$="-radio-label"]:hover,
.gradio-container label[data-testid$="-checkbox-label"]:hover {{
  background:{PANEL}!important;
  border-color:{ACCENT_DEEP}!important;
}}
.gradio-container label[data-testid$="-radio-label"].selected,
.gradio-container label[data-testid$="-radio-label"]:has(input:checked),
.gradio-container label[data-testid$="-checkbox-label"].selected,
.gradio-container label[data-testid$="-checkbox-label"]:has(input:checked) {{
  background:{ACCENT_SOFT}!important;
  color:{TEXT_PRIMARY}!important;
  border-color:{ACCENT_DEEP}!important;
}}
.gradio-container label[data-testid$="-radio-label"].disabled,
.gradio-container label[data-testid$="-checkbox-label"].disabled,
.gradio-container label[data-testid$="-radio-label"][aria-disabled="true"],
.gradio-container label[data-testid$="-checkbox-label"][aria-disabled="true"] {{
  opacity:1!important;
}}
.sidebar [data-testid="block-info"] {{ color:#ffffff!important; -webkit-text-fill-color:#ffffff!important; }}
[data-testid="block-label"] {{
  color:{TEXT_PRIMARY}!important; background:#eef3ef!important;
  border:1px solid {BORDER}!important; border-radius:7px!important;
}}
[data-testid="block-label"] span, [data-testid="block-label"] svg {{ color:{TEXT_PRIMARY}!important; }}

/* ===== 滑动条（白底轨道 + 黄绿手柄 + 深边） ===== */
.gr-slider .gr-box {{ background:{CARD}!important; border:1px solid {BORDER}!important; border-radius:10px!important; }}
.gr-slider .range {{ background:#e5e5e5!important; }}
.gr-slider .handle {{ background:{ACCENT}!important; border-color:{TEXT_PRIMARY}!important; box-shadow:0 2px 6px rgba(0,0,0,0.15)!important; }}
.gr-slider .gr-slider-label, .gr-slider .gr-slider-label > * {{ color:{TEXT_MUTED}!important; }}

/* ===== 下拉框（白底深字 + 圆角 + focus 黄绿阴影 + 弹层阴影） ===== */
.gr-dropdown {{ background:transparent!important; }}
.gr-dropdown .gr-box, .gr-dropdown .wrap, .gr-dropdown input, .gr-dropdown label, .gr-dropdown label .gr-box {{ background:{CARD}!important; color:{TEXT_PRIMARY}!important; border-radius:10px!important; border:1px solid {BORDER}!important; padding:8px 12px!important; transition:all 0.15s!important; }}
.gr-dropdown .gr-box:focus-within {{ border-color:{TEXT_PRIMARY}!important; box-shadow:0 0 0 3px rgba(212,245,106,0.3)!important; }}
.gr-dropdown .gr-box span, .gr-dropdown input::placeholder {{ color:{TEXT_MUTED}!important; }}
.gr-dropdown .gr-panel, .gr-dropdown [role="listbox"], .gr-dropdown .gr-dropdown-panel, .gr-dropdown .wrap {{ background:{CARD}!important; color:{TEXT_PRIMARY}!important; border:1px solid {BORDER}!important; border-radius:12px!important; box-shadow:0 8px 24px rgba(0,0,0,0.08)!important; overflow:hidden!important; }}
.gr-dropdown [role="option"] {{ color:{TEXT_PRIMARY}!important; background:{CARD}!important; border-radius:8px!important; padding:8px 12px!important; }}
.gr-dropdown [role="option"]:hover {{ color:{TEXT_PRIMARY}!important; background:#f5f5f5!important; }}

/* ===== 按钮（三档：主=黄绿；次=深绿；默认=白底描边） ===== */
button, .gr-button, .btn {{ text-transform:none!important; letter-spacing:0!important; font-weight:600!important; font-size:14px!important; border-radius:10px!important; background:{CARD}!important; color:{TEXT_PRIMARY}!important; border:1px solid {BORDER}!important; padding:8px 16px!important; transition:all 0.15s!important; box-shadow:none!important; }}
button:hover, .gr-button:hover {{ transform:translateY(-1px)!important; }}
.gr-button.primary, button.primary {{ background:{ACCENT}!important; color:{TEXT_PRIMARY}!important; border:1px solid {ACCENT}!important; }}
.gr-button.primary:hover {{ background:#c4e55a!important; box-shadow:0 4px 12px rgba(212,245,106,0.4)!important; }}
.gr-button.secondary, button.secondary {{ background:{SIDEBAR}!important; color:#ffffff!important; border:1px solid {SIDEBAR}!important; }}
.gr-button.secondary:hover {{ background:#2A3A32!important; border-color:#2A3A32!important; }}
.gr-button.stop, button.stop {{ background:{CARD}!important; color:#f14658!important; border:1.5px solid #f14658!important; }}
.gr-button.stop:hover {{ background:rgba(241,70,88,0.08)!important; }}
/* size="sm" 小按钮 */
.gr-button.size_sm, button.size_sm {{ padding:4px 10px!important; font-size:12px!important; border-radius:8px!important; }}

/* ===== 音频上传 / 录制（白底 + 圆角 + 边框） ===== */
.gr-audio, .gr-audio .gr-box, .gr-audio .wrap, .gr-microphone, .gr-microphone .gr-box, .gr-microphone .wrap {{ background:{CARD}!important; color:{TEXT_PRIMARY}!important; border-radius:14px!important; border:1px solid {BORDER}!important; padding:12px!important; }}
.gr-audio .record-button, .gr-audio .upload-button, .gr-microphone .record-button {{ background:{ACCENT}!important; color:{TEXT_PRIMARY}!important; border:1px solid {ACCENT}!important; border-radius:999px!important; }}

/* ===== 文件上传区（白底深字 + 虚线） ===== */
.gr-file, .gr-file .wrap, .gr-file .gr-box, .gr-file .upload, .gr-file-upload {{ background:{CARD}!important; color:{TEXT_PRIMARY}!important; border:1px dashed {BORDER}!important; border-radius:14px!important; transition:all 0.15s!important; }}
.gr-file:hover {{ border-color:{TEXT_PRIMARY}!important; background:#fafafa!important; }}
.gr-file .file-name, .gr-file .file-name span {{ color:{TEXT_PRIMARY}!important; }}
.gr-file .upload .icon {{ color:{TEXT_MUTED}!important; }}

/* ===== 勾选组 / 单选组 / 复选框（白底深字；选中=黄绿边框） ===== */
.gr-checkbox, .gr-radio, .gr-checkbox-group, .gr-radiogroup, .gr-checkboxgroup, .gr-radio-group {{ background:transparent!important; color:{TEXT_PRIMARY}!important; }}
.gr-checkbox-group .wrap, .gr-radiogroup .wrap, .gr-checkboxgroup .wrap, .gr-radio-group .wrap {{ background:transparent!important; }}
.gr-checkbox label, .gr-radio label, .gr-checkbox-group label, .gr-radiogroup label, .gr-checkboxgroup label, .gr-radio-group label {{ background:{CARD}!important; color:{TEXT_PRIMARY}!important; border:1px solid {BORDER}!important; border-radius:10px!important; padding:8px 12px!important; transition:all 0.15s!important; }}
.gr-checkbox input, .gr-radio input, .gr-checkbox-group input, .gr-radiogroup input {{ accent-color:{ACCENT}!important; }}
.gr-checkbox-group label:has(input:checked), .gr-radiogroup label:has(input:checked), .gr-checkboxgroup label:has(input:checked), .gr-radio-group label:has(input:checked) {{ border-color:{ACCENT}!important; background:rgba(212,245,106,0.1)!important; color:{TEXT_PRIMARY}!important; }}

/* ===== 表格 Dataframe（白底、圆角、表头浅灰、行 hover） ===== */
.gr-dataframe, .gr-dataframe .wrap {{ background:{CARD}!important; border-radius:14px!important; border:1px solid {BORDER}!important; max-width:100%!important; overflow-x:auto!important; }}
.gr-dataframe table {{ background:{CARD}!important; color:{TEXT_PRIMARY}; border-collapse:collapse; border-radius:14px!important; overflow:hidden!important; }}
.gr-dataframe th {{ background:#fafafa!important; color:{TEXT_MUTED}!important; border-bottom:1px solid {BORDER}!important; font-size:12px!important; font-weight:600!important; text-transform:uppercase!important; letter-spacing:0.5px!important; padding:12px 16px!important; }}
.gr-dataframe td {{ background:{CARD}!important; color:{TEXT_PRIMARY}; border-bottom:1px solid #f0f0f0!important; padding:14px 16px!important; font-size:14px!important; }}
.gr-dataframe tr:hover td {{ background:#fafafa!important; }}
.gr-dataframe tr:last-child td {{ border-bottom:none!important; }}
.gr-dataframe .label, .gr-dataframe label {{ color:{TEXT_MUTED}!important; }}

/* ===== 通用容器（白底 + 圆角） ===== */
.gr-block, .gr-box, .wrap, .panel {{ color:{TEXT_PRIMARY}!important; border-radius:16px!important; }}
.gr-group {{ background:{CARD}!important; border-color:{BORDER}!important; border-radius:16px!important; padding:18px!important; }}

/* ===== Markdown 表格 ===== */
.prose table {{ color:{TEXT_PRIMARY}!important; border-collapse:collapse; border-radius:12px; overflow:hidden; }}
.prose th, .prose td {{ border-bottom:1px solid {BORDER}!important; padding:10px 14px; }}
.prose th {{ color:{TEXT_MUTED}!important; background:#fafafa; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; }}
.prose tr:last-child td {{ border-bottom:none!important; }}

/* ===== 侧边栏（深绿背景） ===== */
.sidebar {{ background:{SIDEBAR}!important; border:1px solid #2c3a30!important; border-radius:20px!important; padding:22px 14px!important; min-width:260px!important; margin-right:8px!important; align-self:flex-start!important; height:auto!important; flex-grow:0!important; }}
.brand-lockup {{ align-items:center!important; gap:12px!important; margin:0 4px!important; }}
.sidebar .brand-mark {{ flex:0 0 52px!important; min-width:52px!important; width:52px!important; }}
.sidebar .brand-mark img {{
  width:52px!important; height:52px!important; object-fit:contain!important;
  border-radius:12px!important; filter:none!important; opacity:1!important;
}}
.sidebar .brand-mark button {{
  width:52px!important; height:52px!important; padding:0!important;
  background:transparent!important; border:0!important; pointer-events:none!important;
}}
.sidebar .brand-mark button:hover {{ transform:none!important; }}
.sidebar .brand-mark .image-frame {{ width:52px!important; height:52px!important; }}
.logo-bar {{ color:#ffffff!important; padding:4px 2px; letter-spacing:-0.02em; line-height:1.2; }}
.logo-bar span {{ display:block; color:{ACCENT}!important; font-size:9px; font-weight:700; letter-spacing:.1em; margin-bottom:6px; white-space:nowrap; }}
.logo-bar strong {{ display:block; color:#ffffff!important; font-size:17px; font-weight:800; white-space:nowrap; }}
.sidebar-caption {{ color:#aeb9b1!important; font-size:12px!important; padding:0 14px 22px; }}
.sidebar .nav-btn {{ width:100%!important; justify-content:flex-start!important; text-align:left!important; margin:3px 0!important; border:1px solid transparent!important; background:transparent!important; color:#c7d0c9!important; border-radius:12px!important; font-weight:600!important; font-size:16px!important; text-transform:none!important; letter-spacing:0.01em!important; box-shadow:none!important; padding:12px 16px!important; transition:all 0.18s ease!important; position:relative!important; }}
.nav-btn:hover {{ background:rgba(255,255,255,0.08)!important; color:#ffffff!important; }}
/* active 状态：左侧黄绿指示条 + 浅黄绿背景 + 白色文字 + 微左缩进 */
.nav-btn.active {{ background:rgba(212,245,106,0.14)!important; border-color:rgba(212,245,106,0.25)!important; color:{ACCENT}!important; font-weight:700!important; padding-left:20px!important; }}
.nav-btn.active::before {{
  content:''!important;
  position:absolute!important;
  left:0!important; top:8px!important; bottom:8px!important;
  width:4px!important; background:{ACCENT}!important;
  border-radius:0 4px 4px 0!important;
}}

/* ===== 主工作区（浅色 + 大圆角 + 统一分区间距） ===== */
.app-shell-row {{ width:100%!important; max-width:100%!important; align-items:flex-start!important; flex-wrap:nowrap!important; gap:16px!important; }}
.app-shell-row > .sidebar {{ flex:0 0 260px!important; width:260px!important; min-width:260px!important; max-width:260px!important; }}
.app-shell-row > .main-area {{ flex:1 1 auto!important; width:auto!important; min-width:0!important; }}
.main-area {{ border:none!important; flex:1 1 0!important; width:0!important; max-width:none!important; min-width:0!important; padding:8px 20px 24px!important; background:transparent!important; box-sizing:border-box!important; overflow-x:hidden!important; }}
.main-area > * {{ width:100%!important; max-width:100%!important; min-width:0!important; box-sizing:border-box!important; }}
.main-area > .gr-group,
.main-area > #grp-overview,
.main-area > #grp-create-project,
.main-area > #grp-project,
.main-area > #grp-voices,
.main-area > #grp-synth,
.main-area > #grp-review,
.main-area > #grp-export,
.main-area > #grp-settings,
.main-area > #grp-production-nav,
.main-area > #grp-supplement {{
  width:100%!important;
  max-width:100%!important;
  min-width:0!important;
  box-sizing:border-box!important;
}}
.main-area .gr-row, .main-area .gr-column, .main-area .gr-box {{ min-width:0!important; max-width:100%!important; box-sizing:border-box!important; }}
.settings-page, .settings-page > *, .settings-card {{ width:100%!important; max-width:100%!important; min-width:0!important; box-sizing:border-box!important; }}
.settings-page .tabs, .settings-page .settings-tabs, .settings-page [role="tabpanel"] {{ width:100%!important; max-width:100%!important; min-width:0!important; box-sizing:border-box!important; }}
.settings-provider-row, .settings-actions, .settings-data-actions {{ flex-wrap:wrap!important; gap:10px!important; }}
.settings-provider-row > *, .settings-actions > *, .settings-data-actions > * {{ flex:1 1 180px!important; min-width:0!important; max-width:100%!important; box-sizing:border-box!important; }}
.settings-card input, .settings-card textarea {{ max-width:100%!important; min-width:0!important; overflow-wrap:anywhere!important; }}
.settings-card .prose, .settings-card .form, .settings-card .wrap {{ min-width:0!important; max-width:100%!important; box-sizing:border-box!important; }}
.diagnostics-table .table-wrap {{ max-width:100%!important; overflow-x:auto!important; }}
.diagnostics-report textarea {{ width:100%!important; max-width:100%!important; overflow-wrap:anywhere!important; white-space:pre-wrap!important; }}

/* ===== 页面标题 ===== */
.main-area > .gr-group > .prose:first-child h3,
.main-area > .gr-group > .prose:first-child h4 {{
  font-size:22px!important; font-weight:800!important; color:{TEXT_PRIMARY}!important;
  margin:0 0 14px!important; letter-spacing:-0.01em!important;
}}

/* ===== 功能分区卡片 ===== */
.gr-group .prose {{ margin:8px 0!important; }}
.gr-group hr {{ display:none!important; }}
.gr-group h3, .gr-group h4 {{ margin-top:20px!important; margin-bottom:10px!important; }}
.gr-group h3:first-of-type, .gr-group h4:first-of-type {{ margin-top:4px!important; }}

/* ===== 顶部状态条 ===== */
.top-status-bar {{ background:{CARD}!important; border:1px solid {BORDER}!important; border-radius:16px!important; padding:12px 18px!important; margin:4px 0 16px!important; box-shadow:0 4px 16px rgba(33,54,40,0.05)!important; align-items:center; }}
.top-brand span {{ display:block; color:{TEXT_MUTED}; font-size:10px; font-weight:800; letter-spacing:.12em; }}
.top-brand strong {{ display:block; color:{TEXT_PRIMARY}; font-size:16px; letter-spacing:-.02em; margin-top:2px; }}
.top-status-bar .prose {{ text-align:right; font-size:14px; }}

/* ===== 状态徽标 ===== */
.status-ok {{ color:#1D7A4F!important; font-weight:600!important; }}
.status-warn {{ color:#D68A1E!important; font-weight:600!important; }}
.status-err {{ color:#C81E2E!important; font-weight:600!important; }}

/* ===== 播放控件 ===== */
button[aria-label*="Play"] {{ border-radius:999px!important; background:{ACCENT}!important; color:{TEXT_PRIMARY}!important; }}
.play-round {{ border-radius:999px!important; background:{ACCENT}!important; }}

/* ===== 音频来源选择图标按钮（复位全局 button 样式） ===== */
.source-selection .icon {{
  background:transparent!important; border:none!important; padding:4px!important;
  width:22px!important; height:22px!important; border-radius:6px!important;
  color:var(--neutral-400)!important; cursor:pointer!important;
}}
.source-selection .icon:hover,
.source-selection .icon:focus {{
  color:var(--color-accent)!important;
}}
.source-selection .icon.selected {{
  color:var(--color-accent)!important;
}}
.source-selection .icon svg {{
  width:18px!important; height:18px!important; display:block!important;
}}

/* ===== 细滚动条 ===== */
::-webkit-scrollbar {{ width:8px; height:8px; }}
::-webkit-scrollbar-thumb {{ background:#c5c5c5; border-radius:4px; }}
::-webkit-scrollbar-track {{ background:transparent; }}

/* ===== 工作台 ===== */
.workbench-toolbar {{ align-items:end!important; gap:14px!important; margin-bottom:14px!important; flex-wrap:wrap!important; }}
.workbench-heading {{ min-width:220px!important; }}
.workbench-heading .prose h2 {{ margin:0!important; color:{TEXT_PRIMARY}!important; font-size:26px!important; letter-spacing:-.03em!important; }}
.workbench-subtitle {{ color:{TEXT_MUTED}!important; font-size:12px!important; margin:4px 0 0!important; }}
.workbench-search {{ min-width:240px!important; }}
.workbench-search label {{ margin-bottom:4px!important; }}
.workbench-new-project {{ min-width:118px!important; }}
.workbench-global-tools {{ margin-bottom:14px!important; }}
.workbench-split-row {{ align-items:flex-start!important; gap:16px!important; }}
.workbench-split-row > .bookshelf-panel {{ flex:1.7 1 0!important; min-width:0!important; }}
.workbench-split-row > .selected-inspector {{ flex:1 1 0!important; min-width:300px!important; }}
.bookshelf-panel,.selected-inspector {{ border:1px solid {BORDER}!important; border-radius:16px!important; background:{PANEL}!important; padding:16px!important; box-sizing:border-box!important; }}
.bookshelf-heading-row {{ align-items:center!important; margin-bottom:0!important; }}
.bookshelf-heading-row .prose h3 {{ margin:0!important; }}
.bookshelf-help {{ color:{TEXT_MUTED}!important; font-size:12px!important; margin:0 0 10px!important; }}
.bookshelf-table {{ min-width:0!important; }}
.selected-inspector > .prose:first-child h3 {{ margin:0 0 12px!important; }}
.selected-project-summary {{ border:1px solid {BORDER}; border-radius:12px; background:{CARD}; padding:12px 14px; min-height:150px; }}
.selected-project-summary .prose {{ margin:0!important; }}
.selected-project-summary h3 {{ color:{ACCENT_DEEP}!important; font-size:14px!important; margin:8px 0 4px!important; }}
.selected-project-summary h3:first-child {{ margin-top:0!important; }}
.selected-project-summary p,.selected-project-summary li {{ font-size:12px!important; line-height:1.45!important; }}
.inspector-open-project {{ width:100%!important; margin:12px 0 8px!important; }}
.inspector-relation-status {{ padding:8px 10px!important; border-radius:9px!important; background:#fbfdf9!important; border:1px solid #e4ebe1!important; font-size:12px!important; }}
.inspector-accordion {{ border:1px solid #e6ebe6!important; border-radius:12px!important; margin-top:12px!important; }}
.selected-inspector > .gr-markdown h4 {{ margin-top:18px!important; margin-bottom:8px!important; }}
.selected-inspector .gr-row {{ gap:8px!important; flex-wrap:wrap!important; }}
.selected-inspector .gr-row > * {{ min-width:0!important; }}
.selected-inspector .gr-dropdown,.selected-inspector .gr-textbox {{ min-width:0!important; }}
.inline-empty {{ color:#717b73; padding:12px 0; font-size:14px; }}

/* ===== 阶段页 ===== */
.stage-row {{ gap:14px; flex-wrap:wrap!important; }}
.stage-row > * {{ min-width:320px!important; }}
.stage-card {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:14px; padding:4px 16px 16px; }}
.binding-workspace,.production-command,.review-workspace,.delivery-workspace {{ border:1px solid {BORDER}!important; box-shadow:none!important; background:{PANEL}!important; }}
.production-command {{ border-color:#dbe9cc!important; background:#fcfef9!important; }}
.voice-flow-steps {{ display:flex; flex-wrap:wrap; gap:8px; margin:4px 0 14px; }}
.voice-flow-steps span {{ display:inline-flex; align-items:center; gap:5px; padding:8px 12px; border-radius:999px; background:{PANEL}; color:{TEXT_MUTED}; font-size:12px; font-weight:600; }}
.voice-flow-steps span.is-active {{ background:{ACCENT_SOFT}; color:{ACCENT_DEEP}; }}
.voice-flow-steps b {{ color:{ACCENT_DEEP}; }}
.voice-workspace {{ gap:16px!important; align-items:start!important; flex-wrap:nowrap!important; }}
.role-list-panel,.voice-config-panel {{ min-width:0!important; align-self:start!important; }}
.voice-workspace > .role-list-panel {{ flex:0 0 277px!important; }}
.role-list-panel {{ padding:16px!important; border:1px solid {BORDER}!important; border-radius:14px!important; background:{PANEL}!important; }}
.role-list-panel > .gr-markdown:first-child h3 {{ margin-top:0!important; margin-bottom:6px!important; }}
.role-list-panel > .gr-markdown:nth-child(2) {{ color:{TEXT_MUTED}!important; font-size:12px!important; margin:0 0 10px!important; }}
.role-list-search {{ margin-bottom:8px!important; }}
.role-list-search input {{ background:{CARD}!important; }}
.role-management-list {{ width:100%!important; min-width:0!important; max-width:100%!important; max-height:560px!important; overflow:hidden!important; border:1px solid {BORDER}!important; border-radius:12px!important; background:{CARD}!important; padding:4px!important; box-sizing:border-box!important; }}
/* Gradio 5.50 renders Radio choices in a direct div without role="radiogroup". */
.role-management-list .wrap {{ display:flex!important; flex-direction:column!important; width:100%!important; min-width:0!important; max-width:100%!important; max-height:550px!important; overflow-y:auto!important; overflow-x:hidden!important; }}
.role-management-list > div:has(> label),
.role-management-list [role="radiogroup"] {{
  display:flex!important;
  flex-direction:column!important;
  flex-wrap:nowrap!important;
  align-items:stretch!important;
  width:100%!important;
  min-width:0!important;
  max-width:100%!important;
  max-height:550px!important;
  overflow-y:auto!important;
  overflow-x:hidden!important;
}}
.role-management-list > div:has(> label) > label,
.role-management-list [role="radiogroup"] > label {{
  display:flex!important;
  flex:0 0 auto!important;
  width:100%!important;
  min-width:0!important;
  max-width:100%!important;
  box-sizing:border-box!important;
}}
.role-management-list label {{ display:flex!important; align-items:flex-start!important; width:100%!important; box-sizing:border-box!important; min-height:46px!important; padding:10px 12px!important; margin:0!important; border-bottom:1px solid #edf0ed!important; border-radius:9px!important; background:{CARD}!important; white-space:pre-line!important; line-height:1.35!important; color:{TEXT_PRIMARY}!important; cursor:pointer!important; transition:background .12s ease,border-color .12s ease!important; }}
.role-management-list label:hover {{ background:#eef6e7!important; }}
.role-management-list label:has(input:checked) {{ background:{ACCENT_SOFT}!important; border-left:3px solid {ACCENT_DEEP}!important; color:{TEXT_PRIMARY}!important; }}
.role-management-list label span {{ white-space:pre-line!important; }}
.role-management-list input {{ accent-color:{ACCENT_DEEP}!important; margin-top:4px!important; }}
.role-list-panel > .gr-markdown:last-child {{ color:{TEXT_MUTED}!important; font-size:12px!important; margin:8px 0 0!important; }}
.voice-config-panel > .gr-markdown:first-child h3 {{ margin-top:4px!important; margin-bottom:12px!important; }}
.voice-config-steps {{ display:flex!important; flex-direction:column!important; gap:10px!important; }}
.voice-binding-layout {{ gap:12px!important; align-items:start!important; flex-wrap:nowrap!important; }}
.voice-choice-card {{ flex:1 1 auto!important; min-width:0!important; }}
.voice-config-footer {{ gap:10px!important; align-items:center!important; flex-wrap:wrap!important; margin-top:12px!important; }}
.voice-config-footer > * {{ min-width:0!important; max-width:100%!important; box-sizing:border-box!important; }}
.voice-config-footer .voice-bind-action {{ flex:0 1 180px!important; width:auto!important; min-height:44px!important; height:auto!important; }}
.voice-binding-steps {{ display:grid!important; grid-template-columns:1fr!important; gap:10px!important; align-items:start!important; }}
.voice-step-card {{ width:auto!important; min-width:0!important; background:{CARD}!important; border:1px solid {BORDER}!important; border-radius:12px!important; padding:4px 12px 12px!important; min-height:0!important; align-self:start!important; }}
.voice-reference-upload .audio-container button.boundedheight {{ height:150px!important; min-height:150px!important; }}
.voice-step-card h5 {{ margin-bottom:4px!important; color:{TEXT_PRIMARY}!important; }}
.voice-step-card .gr-markdown {{ color:{TEXT_MUTED}!important; font-size:12px!important; }}
.production-tabs {{ margin:8px 0 12px!important; }}
.production-tabs .wrap {{ display:flex!important; gap:8px!important; border:0!important; background:transparent!important; }}
.production-tabs label {{ flex:1!important; border:1px solid {BORDER}!important; border-radius:999px!important; background:{CARD}!important; color:{TEXT_MUTED}!important; padding:9px 14px!important; text-align:center!important; cursor:pointer!important; }}
.production-tabs label:has(input:checked) {{ background:{ACCENT}!important; border-color:{ACCENT}!important; color:{TEXT_PRIMARY}!important; }}
.production-check {{ margin-top:10px!important; padding:12px 14px!important; border:1px solid #e4ebe1!important; border-radius:12px!important; background:#fbfdf9!important; }}
.export-default-hint {{ color:#5f6c61!important; font-size:12px!important; margin:5px 0 10px!important; }}
.advanced-settings,.asset-accordion,.settings-accordion,.supplement-accordion,.run-log {{ border:1px solid #e6ebe6!important; border-radius:12px!important; margin-top:12px!important; }}
.run-log textarea {{ background:#203128!important; color:#edf5ef!important; border-color:#395044!important; }}
#grp-review {{ margin-top:16px!important; }} #grp-supplement {{ margin-top:8px!important; }}
#grp-review > :first-child {{ font-size:20px!important; margin-bottom:2px!important; }}
#grp-review > :nth-child(2) {{ margin-bottom:14px!important; }}
.review-status {{ min-height:24px!important; margin:6px 0 10px!important; padding:7px 10px!important; border:1px solid #e4ebe1!important; border-radius:9px!important; background:#fbfdf9!important; color:{TEXT_MUTED}!important; }}
#grp-review .gr-audio {{ min-width:0!important; width:100%!important; }}


/* ===== 生产内部导航 (Pencil 三级 tab) ===== */
#grp-production-nav {{ margin-top:0!important; padding:8px 0 4px!important; background:transparent!important; border:none!important; box-shadow:none!important; }}
#grp-production-nav .production-tabs {{ margin:0!important; }}
@media (max-width: 900px) {{
  .gradio-container {{ padding:12px!important; }}
  .sidebar {{ min-width:100%!important; margin:0 0 12px!important;
    border-radius:20px!important;
  }}
  .app-shell-row {{ flex-wrap:wrap!important; }}
  .app-shell-row > .sidebar {{ flex:1 1 100%!important; width:100%!important; max-width:100%!important; }}
  .app-shell-row > .main-area {{ flex:1 1 100%!important; width:100%!important; }}
  .main-area {{ width:100%!important; padding:8px 12px 20px!important; }}
  .voice-workspace {{ flex-wrap:wrap!important; }}
  .voice-binding-layout {{ flex-wrap:wrap!important; }}
  .voice-config-footer {{ flex-wrap:wrap!important; }}
  .voice-config-footer > * {{ flex:1 1 100%!important; min-width:0!important; }}
  .voice-config-footer .voice-bind-action {{ flex:1 1 100%!important; width:100%!important; }}
  .voice-binding-steps {{ grid-template-columns:1fr!important; }}
  .workbench-split-row > *, .stage-row > *, .voice-step-card {{ min-width:100%!important; }}
  .workbench-toolbar > * {{ min-width:100%!important; }}
}}
@media (max-width: 1180px) and (min-width: 901px) {{
  .gradio-container {{ padding:16px!important; }}
  .sidebar {{ min-width:220px!important; padding-left:12px!important; padding-right:12px!important; }}
  .main-area {{ padding-left:12px!important; padding-right:12px!important; }}
  .voice-workspace > .role-list-panel {{ flex-basis:220px!important; }}
  .voice-binding-layout {{ flex-wrap:wrap!important; }}
  .voice-choice-card {{ flex:1 1 480px!important; }}
  .settings-actions > *, .settings-data-actions > * {{ flex-basis:150px!important; }}
}}
</style>
<script>
(function(){{
  function activate(btn){{ document.querySelectorAll('.nav-btn').forEach(function(x){{x.classList.remove('active');}}); if(btn) btn.classList.add('active'); }}
  function init(){{ var ov=document.getElementById('nav-overview'); if(ov) ov.classList.add('active'); }}
  if(document.readyState!=='loading'){{ init(); }} else {{ document.addEventListener('DOMContentLoaded', init); }}
  document.addEventListener('click', function(e){{ var b=e.target.closest && e.target.closest('.nav-btn'); if(b) activate(b); }});
}})();
</style>"""
