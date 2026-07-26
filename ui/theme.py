"""主题与 CSS（从 app.py 抽离）。"""
from __future__ import annotations
import gradio as gr

THEME = gr.themes.Default(
    primary_hue=gr.themes.Color(
        c50="#f9fce3", c100="#f0f7b8", c200="#e0ec70", c300="#d4f56a",
        c400="#b8d856", c500="#9bbf3f", c600="#7a9a2a", c700="#5e7818",
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
    # 画布与表面（浅色 Stripe 风，纵深靠边框+阴影不靠明暗）
    body_background_fill="#edf2ec",
    body_background_fill_dark="#edf2ec",
    block_background_fill="#ffffff",
    block_background_fill_dark="#ffffff",
    block_border_color="#dce5dd",
    block_border_color_dark="#dce5dd",
    block_radius="16px",
    block_label_text_color="#6b6b6b",
    block_title_text_color="#0a0a0a",
    # 主色（黄绿仅做 CTA/激活——功能色）
    button_primary_background_fill="#d4f56a",
    button_primary_background_fill_hover="#c4e55a",
    button_primary_text_color="#0a0a0a",
    button_primary_border_color="transparent",
    button_secondary_background_fill="#274133",
    button_secondary_background_fill_hover="#335642",
    button_secondary_text_color="#ffffff",
    button_secondary_border_color="transparent",
    button_cancel_background_fill="#ffffff",
    button_cancel_background_fill_hover="#fff5f5",
    button_cancel_text_color="#f14658",
    button_cancel_border_color="#f14658",
    # 输入
    input_background_fill="#ffffff",
    input_background_fill_focus="#ffffff",
    input_border_color="#d4d4d4",
    input_border_color_focus="#0a0a0a",
    input_radius="10px",
    input_placeholder_color="#9a9a9a",
    # 文本（浅色，主/次二元层级）
    body_text_color="#18221c",
    body_text_color_subdued="#5f6e64",
    body_text_color_dark="#18221c",
    link_text_color="#5e7818",
    # 阴影
    shadow_drop="0 4px 16px rgba(0,0,0,0.06)",
    # 表格
    table_text_color="#0a0a0a",
    table_border_color="#e5e5e5",
    table_even_background_fill="#ffffff",
    table_odd_background_fill="#fafafa",
    table_radius="14px",
    # 字体放在构造器
)

LIGHT_CSS = """<style>
/* ===== 下�� / 列表 选项（浅色底深字 + 圆角） ===== */
[role="option"], .item, .options li, ul[role="listbox"] li { color:#0a0a0a!important; background:#ffffff!important; border-radius:8px!important; padding:8px 12px!important; }
[role="option"]:hover, .item:hover { color:#0a0a0a!important; background:#f5f5f5!important; }
footer { display:none!important }

/* ===== 全局画布与变量（浅色 Stripe 风 + 大圆角） ===== */
body, .gradio-container {
  font-family:'Outfit','SpotifyMixUI',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif,'Apple Color Emoji','Segoe UI Emoji','Segoe UI Symbol'!important;
  --body-text-color:#18221c!important;
  --body-text-color-subdued:#5f6e64!important;
  --block-label-text-color:#5f6e64!important;
  --block-background-fill:#ffffff!important;
  --block-border-color:#dce5dd!important;
  --table-odd-background-fill:#f7faf7!important;
  --table-even-background-fill:#ffffff!important;
  --border-color-primary:#cbd7cd!important;
  --table-radius:14px!important;
  --color-accent:#d4f56a!important;
  --input-background-fill:#ffffff!important;
  --input-background-fill-focus:#ffffff!important;
  --input-border-color:#cbd7cd!important;
  --input-border-color-focus:#355642!important;
  --input-text-color:#18221c!important;
  --input-radius:10px!important;
  --block-radius:16px!important;
  background:#edf2ec!important;
}
.gradio-container { max-width:1440px!important; margin:0 auto!important; padding:20px 22px!important; }

/* ===== 文本输入 / 数字（白底深字 + 圆角 + focus 黄绿阴影） ===== */
textarea { font-family:'JetBrains Mono',monospace!important; font-size:13px!important; color:#18221c!important; background:#ffffff!important; border-radius:10px!important; border:1px solid #cbd7cd!important; padding:10px 12px!important; transition:all 0.15s!important; }
textarea:focus { outline:none!important; border-color:#355642!important; box-shadow:0 0 0 3px rgba(212,245,106,0.28)!important; }
input, input.border-none { color:#18221c!important; -webkit-text-fill-color:#18221c!important; background:#ffffff!important; border-radius:10px!important; border:1px solid #cbd7cd!important; padding:10px 12px!important; transition:all 0.15s!important; }
input:focus { outline:none!important; border-color:#355642!important; box-shadow:0 0 0 3px rgba(212,245,106,0.28)!important; }
.gr-textbox input, .gr-number input, .gr-textbox textarea { background:#ffffff!important; color:#18221c!important; border-radius:10px!important; border:1px solid #cbd7cd!important; }
input::placeholder, textarea::placeholder { color:#7d8980!important; opacity:1!important; }
input:disabled, textarea:disabled, [aria-disabled="true"] input {
  color:#68756c!important; -webkit-text-fill-color:#68756c!important;
  background:#f1f5f1!important; opacity:1!important;
}
[data-testid="block-label"] {
  color:#415047!important; background:#eef3ef!important;
  border:1px solid #d8e2da!important; border-radius:7px!important;
}
[data-testid="block-label"] span, [data-testid="block-label"] svg { color:#415047!important; }

/* ===== 滑动条（白底轨道 + 黄绿手柄 + 深边） ===== */
.gr-slider .gr-box { background:#ffffff!important; border:1px solid #d4d4d4!important; border-radius:10px!important; }
.gr-slider .range { background:#e5e5e5!important; }
.gr-slider .handle { background:#d4f56a!important; border-color:#0a0a0a!important; box-shadow:0 2px 6px rgba(0,0,0,0.15)!important; }
.gr-slider .gr-slider-label, .gr-slider .gr-slider-label > * { color:#6b6b6b!important; }

/* ===== 下拉框（白底深字 + 圆角 + focus 黄绿阴影 + 弹层阴影） ===== */
.gr-dropdown { background:transparent!important; }
.gr-dropdown .gr-box, .gr-dropdown .wrap, .gr-dropdown input, .gr-dropdown label, .gr-dropdown label .gr-box { background:#ffffff!important; color:#0a0a0a!important; border-radius:10px!important; border:1px solid #d4d4d4!important; padding:8px 12px!important; transition:all 0.15s!important; }
.gr-dropdown .gr-box:focus-within { border-color:#0a0a0a!important; box-shadow:0 0 0 3px rgba(212,245,106,0.3)!important; }
.gr-dropdown .gr-box span, .gr-dropdown input::placeholder { color:#6b6b6b!important; }
.gr-dropdown .gr-panel, .gr-dropdown [role="listbox"], .gr-dropdown .gr-dropdown-panel, .gr-dropdown .wrap { background:#ffffff!important; color:#0a0a0a!important; border:1px solid #e5e5e5!important; border-radius:12px!important; box-shadow:0 8px 24px rgba(0,0,0,0.08)!important; overflow:hidden!important; }
.gr-dropdown [role="option"] { color:#0a0a0a!important; background:#ffffff!important; border-radius:8px!important; padding:8px 12px!important; }
.gr-dropdown [role="option"]:hover { color:#0a0a0a!important; background:#f5f5f5!important; }

/* ===== 按钮（三档：主=黄绿；次=深绿；默认=白底描边） ===== */
button, .gr-button, .btn { text-transform:none!important; letter-spacing:0!important; font-weight:600!important; font-size:14px!important; border-radius:10px!important; background:#ffffff!important; color:#243128!important; border:1px solid #cbd7cd!important; padding:8px 16px!important; transition:all 0.15s!important; box-shadow:none!important; }
button:hover, .gr-button:hover { transform:translateY(-1px)!important; }
.gr-button.primary, button.primary { background:#d4f56a!important; color:#0a0a0a!important; border:1px solid #d4f56a!important; }
.gr-button.primary:hover { background:#c4e55a!important; box-shadow:0 4px 12px rgba(212,245,106,0.4)!important; }
.gr-button.secondary, button.secondary { background:#274133!important; color:#ffffff!important; border:1px solid #274133!important; }
.gr-button.secondary:hover { background:#335642!important; border-color:#335642!important; }
.gr-button.stop, button.stop { background:#ffffff!important; color:#f14658!important; border:1.5px solid #f14658!important; }
.gr-button.stop:hover { background:rgba(241,70,88,0.08)!important; }
/* size="sm" 小按钮（保留圆角、高度） */
.gr-button.size_sm, button.size_sm { padding:4px 10px!important; font-size:12px!important; border-radius:8px!important; }

/* ===== 音频上传 / 录制（白底 + 圆角 + 边框） ===== */
.gr-audio, .gr-audio .gr-box, .gr-audio .wrap, .gr-microphone, .gr-microphone .gr-box, .gr-microphone .wrap { background:#ffffff!important; color:#0a0a0a!important; border-radius:14px!important; border:1px solid #e5e5e5!important; padding:12px!important; }
.gr-audio .record-button, .gr-audio .upload-button, .gr-microphone .record-button { background:#d4f56a!important; color:#0a0a0a!important; border:1px solid #d4f56a!important; border-radius:999px!important; }

/* ===== 文件上传区（白底深字 + 虚线） ===== */
.gr-file, .gr-file .wrap, .gr-file .gr-box, .gr-file .upload, .gr-file-upload { background:#ffffff!important; color:#0a0a0a!important; border:1px dashed #d4d4d4!important; border-radius:14px!important; transition:all 0.15s!important; }
.gr-file:hover { border-color:#0a0a0a!important; background:#fafafa!important; }
.gr-file .file-name, .gr-file .file-name span { color:#0a0a0a!important; }
.gr-file .upload .icon { color:#6b6b6b!important; }

/* ===== 勾选组 / 单选组 / 复选框（白底深字；选中=黄绿边框） ===== */
.gr-checkbox, .gr-radio, .gr-checkbox-group, .gr-radiogroup, .gr-checkboxgroup, .gr-radio-group { background:transparent!important; color:#0a0a0a!important; }
.gr-checkbox-group .wrap, .gr-radiogroup .wrap, .gr-checkboxgroup .wrap, .gr-radio-group .wrap { background:transparent!important; }
.gr-checkbox label, .gr-radio label, .gr-checkbox-group label, .gr-radiogroup label, .gr-checkboxgroup label, .gr-radio-group label { background:#ffffff!important; color:#0a0a0a!important; border:1px solid #d4d4d4!important; border-radius:10px!important; padding:8px 12px!important; transition:all 0.15s!important; }
.gr-checkbox input, .gr-radio input, .gr-checkbox-group input, .gr-radiogroup input { accent-color:#d4f56a!important; }
.gr-checkbox-group label:has(input:checked), .gr-radiogroup label:has(input:checked), .gr-checkboxgroup label:has(input:checked), .gr-radio-group label:has(input:checked) { border-color:#d4f56a!important; background:rgba(212,245,106,0.1)!important; color:#0a0a0a!important; }

/* ===== 表格 Dataframe（白底、圆角、表头浅灰、行 hover） ===== */
.gr-dataframe, .gr-dataframe .wrap { background:#ffffff!important; border-radius:14px!important; border:1px solid #e5e5e5!important; overflow:hidden!important; }
.gr-dataframe table { background:#ffffff!important; color:#0a0a0a; border-collapse:collapse; border-radius:14px!important; overflow:hidden!important; }
.gr-dataframe th { background:#fafafa!important; color:#6b6b6b!important; border-bottom:1px solid #e5e5e5!important; font-size:12px!important; font-weight:600!important; text-transform:uppercase!important; letter-spacing:0.5px!important; padding:12px 16px!important; }
.gr-dataframe td { background:#ffffff!important; color:#0a0a0a; border-bottom:1px solid #f0f0f0!important; padding:14px 16px!important; font-size:14px!important; }
.gr-dataframe tr:hover td { background:#fafafa!important; }
.gr-dataframe tr:last-child td { border-bottom:none!important; }
.gr-dataframe table tr > :first-child { min-width:200px!important; }
.gr-dataframe .label, .gr-dataframe label { color:#6b6b6b!important; }

/* ===== 通用容器（白底 + 圆角） ===== */
.gr-block, .gr-box, .wrap, .panel { color:#0a0a0a!important; border-radius:16px!important; }
.gr-group { background:#ffffff!important; border-color:#dce5dd!important; border-radius:16px!important; padding:18px!important; }

/* ===== Markdown 表格 ===== */
.prose table { color:#0a0a0a!important; border-collapse:collapse; border-radius:12px; overflow:hidden; }
.prose th, .prose td { border-bottom:1px solid #f0f0f0!important; padding:10px 14px; }
.prose th { color:#6b6b6b!important; background:#fafafa; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; }
.prose tr:last-child td { border-bottom:none!important; }

/* ===== 侧边栏（按生产阶段组织） ===== */
.sidebar { background:#18211b!important; border:1px solid #2c3a30!important; border-radius:20px!important; padding:22px 14px!important; min-width:260px!important; margin-right:8px!important; align-self:flex-start!important; height:auto!important; flex-grow:0!important; }
.brand-lockup { align-items:center!important; gap:12px!important; margin:0 4px!important; }
.sidebar .brand-mark { flex:0 0 52px!important; min-width:52px!important; width:52px!important; }
.sidebar .brand-mark img {
  width:52px!important; height:52px!important; object-fit:contain!important;
  border-radius:12px!important; filter:none!important; opacity:1!important;
}
.sidebar .brand-mark button {
  width:52px!important; height:52px!important; padding:0!important;
  background:transparent!important; border:0!important; pointer-events:none!important;
}
.sidebar .brand-mark button:hover { transform:none!important; }
.sidebar .brand-mark .image-frame { width:52px!important; height:52px!important; }
.logo-bar { color:#ffffff!important; padding:4px 2px; letter-spacing:-0.02em; line-height:1.15; }
.logo-bar span { display:block; color:#d4f56a!important; font-size:9px; font-weight:700; letter-spacing:.1em; margin-bottom:6px; white-space:nowrap; }
.logo-bar strong { display:block; color:#ffffff!important; font-size:17px; font-weight:800; white-space:nowrap; }
.sidebar-caption { color:#aeb9b1!important; font-size:12px!important; padding:0 14px 22px; }
.sidebar .nav-btn { width:100%!important; justify-content:flex-start!important; text-align:left!important; margin:3px 0!important; border:1px solid transparent!important; background:transparent!important; color:#c7d0c9!important; border-radius:12px!important; font-weight:600!important; font-size:16px!important; text-transform:none!important; letter-spacing:0.01em!important; box-shadow:none!important; padding:12px 16px!important; transition:all 0.18s ease!important; position:relative!important; }
.nav-btn:hover { background:rgba(255,255,255,0.08)!important; color:#ffffff!important; }
/* active 状态：左侧黄绿指示条 + 浅黄绿背景 + 白色文字 + 微左缩进 */
.nav-btn.active { background:rgba(212,245,106,0.14)!important; border-color:rgba(212,245,106,0.25)!important; color:#d4f56a!important; font-weight:700!important; padding-left:20px!important; }
.nav-btn.active::before {
  content:''!important;
  position:absolute!important;
  left:0!important; top:8px!important; bottom:8px!important;
  width:4px!important; background:#d4f56a!important;
  border-radius:0 4px 4px 0!important;
}

/* ===== 主工作区（浅色 + 大圆角 + 统一分区间距） ===== */
.main-area { border:none!important; min-width:0!important; padding:8px 20px 24px!important; background:transparent!important; }

/* ===== 页面标题：标题表达当前任务，不重复左侧导航名称 ===== */
.main-area > .gr-group > .prose:first-child h3,
.main-area > .gr-group > .prose:first-child h4 {
  font-size:22px!important; font-weight:800!important; color:#0a0a0a!important;
  margin:0 0 14px!important; letter-spacing:-0.01em!important;
}

/* ===== 功��分区卡片（每个 gr.Group 内部子区域） ===== */
.gr-group .prose { margin:8px 0!important; }
.gr-group hr { display:none!important; }  /* 隐藏 markdown 分割��� --- */
.gr-group h3, .gr-group h4 { margin-top:20px!important; margin-bottom:10px!important; }
.gr-group h3:first-of-type, .gr-group h4:first-of-type { margin-top:4px!important; }

/* ===== 顶部状态条（白底深字 + 圆角 + 阴影） ===== */
.top-status-bar { background:#ffffff!important; border:1px solid #dce5dd!important; border-radius:16px!important; padding:12px 18px!important; margin:4px 0 16px!important; box-shadow:0 4px 16px rgba(33,54,40,0.05)!important; align-items:center; }
.top-brand span { display:block; color:#6b6b6b; font-size:10px; font-weight:800; letter-spacing:.12em; }
.top-brand strong { display:block; color:#0a0a0a; font-size:16px; letter-spacing:-.02em; margin-top:2px; }
.top-status-bar .prose { text-align:right; font-size:14px; }

/* ===== 状态徽标（适配浅色背景，深色语义色） ===== */
.status-ok { color:#1d7a4f!important; font-weight:600!important; }
.status-warn { color:#8a5a0e!important; font-weight:600!important; }
.status-err { color:#a01828!important; font-weight:600!important; }

/* ===== 播放控件：圆形按钮（适配浅色，黄绿填充） ===== */
button[aria-label*="Play"] { border-radius:999px!important; background:#d4f56a!important; color:#0a0a0a!important; }
.play-round { border-radius:999px!important; background:#d4f56a!important; }

/* ===== 细滚动条 ===== */
::-webkit-scrollbar { width:8px; height:8px; }
::-webkit-scrollbar-thumb { background:#c5c5c5; border-radius:4px; }
::-webkit-scrollbar-track { background:transparent; }

/* ===== v3.2 工作台 ===== */
.workbench-status { margin-bottom:4px!important; }
.workbench-hero { min-height:148px; display:flex; justify-content:space-between; align-items:center; gap:24px; padding:24px 26px; border-radius:16px; color:#ffffff; background:linear-gradient(125deg,#24392c 0%,#355642 100%); overflow:hidden; }
.workbench-hero .eyebrow { color:#d4f56a; font-size:11px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.workbench-hero h2 { color:#ffffff!important; font-size:30px!important; line-height:1.12!important; margin:7px 0 8px!important; letter-spacing:-.035em!important; }
.workbench-hero p { color:#c4cdc5!important; margin:0!important; max-width:620px; font-size:14px!important; }
.hero-progress { flex:0 0 auto; width:88px; height:88px; border:5px solid #d4f56a; border-radius:50%; display:flex; flex-direction:column; justify-content:center; align-items:center; background:rgba(255,255,255,.07); }
.hero-progress span { color:#ffffff; font-size:20px; font-weight:800; letter-spacing:-.04em; }
.hero-progress small { color:#c4cdc5; font-size:10px; text-align:center; line-height:1.1; width:60px; }
.hero-icon { font-size:42px; }
.empty-state .hero-icon { border-radius:18px; background:rgba(212,245,106,.12); padding:18px; }
.workbench-main-row { gap:14px; margin-top:14px!important; align-items:stretch!important; flex-wrap:wrap!important; }
.workbench-main-row > * { min-width:280px!important; }
.dashboard-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-bottom:12px; }
.metric-card,.workbench-card { border:1px solid #dfe7e0; background:#ffffff; border-radius:14px; padding:16px 18px; box-shadow:0 3px 10px rgba(33,54,40,.04); }
.metric-card span,.card-eyebrow { display:block; color:#6b746d; font-size:11px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.metric-card strong { display:block; color:#111812; font-size:27px; line-height:1; margin:8px 0 4px; letter-spacing:-.04em; }
.metric-card strong i { color:#9aa39d; font-size:16px; font-style:normal; margin:0 3px; }
.metric-card small,.workbench-card p { color:#707a72; font-size:12px; line-height:1.45; margin:4px 0 0; }
.metric-track { height:6px; overflow:hidden; border-radius:999px; background:#edf1ed; margin-top:14px; }
.metric-track b { display:block; height:100%; border-radius:inherit; background:#1f8a5b; }
.metric-track.voices b { background:#c2e65b; }
.next-step-card { border-color:#d5e9af; background:#f9fdeF; }
.next-step-card strong,.task-card strong,.workbench-issues strong { display:block; color:#172218; font-size:16px; margin-top:8px; }
.task-card { height:100%; min-height:0; background:#f7faf7; }
.workbench-issues { height:100%; min-height:0; }
.workbench-issues ul { list-style:none; padding:0; margin:12px 0 0; }
.workbench-issues li { display:flex; align-items:center; gap:9px; color:#354036; font-size:13px; padding:7px 0; border-bottom:1px solid #edf0ed; }
.workbench-issues li:last-child { border-bottom:0; }
.issue-dot { width:7px; height:7px; border-radius:50%; flex:0 0 auto; background:#e3aa2f; }
.issue-dot.error { background:#d95563; }.issue-dot.info { background:#5c859d; }.issue-dot.warning { background:#e3aa2f; }
.workbench-issues.is-clear { background:#f4fbf6; border-color:#d7eadc; }
.inline-empty { color:#717b73; padding:12px 0; font-size:14px; }

/* ===== v3.2 阶段页 ===== */
.stage-row { gap:14px; flex-wrap:wrap!important; }.stage-row > * { min-width:320px!important; }
.stage-card { background:#f8fbf8; border:1px solid #dfe7e0; border-radius:14px; padding:4px 16px 16px; }
.binding-workspace,.production-command,.review-workspace,.delivery-workspace { border:1px solid #dfe7e0!important; box-shadow:none!important; background:#f8fbf8!important; }
.production-command { border-color:#dbe9cc!important; background:#fcfef9!important; }
.voice-flow-steps { display:flex; flex-wrap:wrap; gap:8px; margin:4px 0 14px; }
.voice-flow-steps span { display:inline-flex; align-items:center; gap:5px; padding:7px 11px; border-radius:999px; background:#f0f6e8; color:#43553e; font-size:12px; font-weight:600; }
.voice-flow-steps b { color:#6d8d1d; }
.voice-workspace { gap:16px!important; align-items:start!important; flex-wrap:nowrap!important; }
.role-list-panel,.voice-config-panel { min-width:0!important; align-self:start!important; }
.voice-workspace > .role-list-panel { flex:0 0 340px!important; }
.role-list-panel { padding:16px!important; border:1px solid #dfe7e0!important; border-radius:14px!important; background:#f8fbf8!important; }
.role-list-panel > .gr-markdown:first-child h3 { margin-top:0!important; margin-bottom:6px!important; }
.role-list-panel > .gr-markdown:nth-child(2) { color:#647067!important; font-size:12px!important; margin:0 0 10px!important; }
.role-list-search { margin-bottom:8px!important; }
.role-list-search input { background:#ffffff!important; }
.role-management-list { max-height:560px!important; overflow:auto!important; border:1px solid #dfe7e0!important; border-radius:12px!important; background:#ffffff!important; padding:4px!important; }
.role-management-list .wrap { display:flex!important; flex-direction:column!important; width:100%!important; max-height:550px!important; overflow:auto!important; }
.role-management-list label { display:flex!important; align-items:flex-start!important; width:100%!important; box-sizing:border-box!important; min-height:64px!important; padding:10px 12px!important; margin:0!important; border-bottom:1px solid #edf0ed!important; border-radius:9px!important; background:#ffffff!important; white-space:pre-line!important; line-height:1.35!important; color:#18221c!important; cursor:pointer!important; transition:background .12s ease,border-color .12s ease!important; }
.role-management-list label:last-child { border-bottom:0!important; }
.role-management-list label:hover { background:#eef6e7!important; }
.role-management-list label:has(input:checked) { background:#e5f5bd!important; border-left:3px solid #9cc52e!important; color:#18221c!important; }
.role-management-list label span { white-space:pre-line!important; }
.role-management-list input { accent-color:#7d9f23!important; margin-top:4px!important; }
.role-list-panel > .gr-markdown:last-child { color:#6b746d!important; font-size:12px!important; margin:8px 0 0!important; }
.voice-config-panel > .gr-markdown:first-child h3 { margin-top:4px!important; margin-bottom:12px!important; }
.voice-config-steps { display:flex!important; flex-direction:column!important; gap:10px!important; }
.voice-config-footer { gap:10px!important; align-items:start!important; }
.voice-config-footer > * { min-width:0!important; }
.voice-binding-steps { display:grid!important; grid-template-columns:1fr!important; gap:10px!important; align-items:start!important; }
.voice-step-card { width:auto!important; min-width:0!important; background:#ffffff!important; border:1px solid #dfe7e0!important; border-radius:12px!important; padding:4px 12px 12px!important; min-height:0!important; align-self:start!important; }
.voice-reference-upload .audio-container button.boundedheight { height:176px!important; min-height:176px!important; }
.voice-step-card h5 { margin-bottom:4px!important; }
.voice-step-card .gr-markdown { color:#6b746d!important; font-size:12px!important; }
.production-tabs { margin:8px 0 12px!important; }
.production-tabs .wrap { display:flex!important; gap:8px!important; border:0!important; background:transparent!important; }
.production-tabs label { flex:1!important; border:1px solid #dce6d8!important; border-radius:999px!important; background:#ffffff!important; color:#526052!important; padding:9px 14px!important; text-align:center!important; cursor:pointer!important; }
.production-tabs label:has(input:checked) { background:#d4f56a!important; border-color:#d4f56a!important; color:#0a0a0a!important; }
.production-check { margin-top:10px!important; padding:12px 14px!important; border:1px solid #e4ebe1!important; border-radius:12px!important; background:#fbfdf9!important; }
.export-default-hint { color:#5f6c61!important; font-size:12px!important; margin:5px 0 10px!important; }
.advanced-settings,.asset-accordion,.settings-accordion,.supplement-accordion,.run-log { border:1px solid #e6ebe6!important; border-radius:12px!important; margin-top:12px!important; }
.run-log textarea { background:#203128!important; color:#edf5ef!important; border-color:#395044!important; }
#grp-review { margin-top:16px!important; } #grp-supplement { margin-top:8px!important; }
#grp-review > :first-child { font-size:20px!important; margin-bottom:2px!important; }
#grp-review > :nth-child(2) { margin-bottom:14px!important; }

@media (max-width: 900px) {
  .gradio-container { padding:12px!important; }
  .sidebar { min-width:100%!important; margin:0 0 12px!important; }
  .dashboard-metrics { grid-template-columns:1fr; }
  .workbench-hero { padding:22px; }.hero-progress { display:none; }
  .main-area { padding:8px 12px 20px!important; }
  .voice-workspace { flex-wrap:wrap!important; }
  .voice-config-footer { flex-wrap:wrap!important; }
  .voice-config-footer > * { min-width:100%!important; }
  .voice-binding-steps { grid-template-columns:1fr!important; }
  .workbench-main-row > *, .stage-row > *, .voice-step-card { min-width:100%!important; }
}
</style>
<script>
(function(){
  function activate(btn){ document.querySelectorAll('.nav-btn').forEach(function(x){x.classList.remove('active');}); if(btn) btn.classList.add('active'); }
  function init(){ var ov=document.getElementById('nav-overview'); if(ov) ov.classList.add('active'); }
  if(document.readyState!=='loading'){ init(); } else { document.addEventListener('DOMContentLoaded', init); }
  document.addEventListener('click', function(e){ var b=e.target.closest && e.target.closest('.nav-btn'); if(b) activate(b); });
})();
</script>"""
