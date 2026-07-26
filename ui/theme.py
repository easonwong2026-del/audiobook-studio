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
    body_background_fill="#dde6dc",
    body_background_fill_dark="#dde6dc",
    block_background_fill="#ffffff",
    block_background_fill_dark="#ffffff",
    block_border_color="#e5e5e5",
    block_border_color_dark="#e5e5e5",
    block_radius="16px",
    block_label_text_color="#6b6b6b",
    block_title_text_color="#0a0a0a",
    # 主色（黄绿仅做 CTA/激活——功能色）
    button_primary_background_fill="#d4f56a",
    button_primary_background_fill_hover="#c4e55a",
    button_primary_text_color="#0a0a0a",
    button_primary_border_color="transparent",
    button_secondary_background_fill="#0a0a0a",
    button_secondary_background_fill_hover="#2a2a2a",
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
    body_text_color="#0a0a0a",
    body_text_color_subdued="#6b6b6b",
    body_text_color_dark="#0a0a0a",
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
  --body-text-color:#0a0a0a!important;
  --body-text-color-subdued:#6b6b6b!important;
  --block-label-text-color:#6b6b6b!important;
  --block-background-fill:#ffffff!important;
  --block-border-color:#e5e5e5!important;
  --table-odd-background-fill:#fafafa!important;
  --table-even-background-fill:#ffffff!important;
  --border-color-primary:#d4d4d4!important;
  --table-radius:14px!important;
  --color-accent:#d4f56a!important;
  --input-background-fill:#ffffff!important;
  --input-background-fill-focus:#ffffff!important;
  --input-border-color:#d4d4d4!important;
  --input-border-color-focus:#0a0a0a!important;
  --input-text-color:#0a0a0a!important;
  --input-radius:10px!important;
  --block-radius:16px!important;
  background:#dde6dc!important;
}

/* ===== 文本输入 / 数字（白底深字 + 圆角 + focus 黄绿阴影） ===== */
textarea { font-family:'JetBrains Mono',monospace!important; font-size:13px!important; color:#0a0a0a!important; background:#ffffff!important; border-radius:10px!important; border:1px solid #d4d4d4!important; padding:10px 12px!important; transition:all 0.15s!important; }
textarea:focus { outline:none!important; border-color:#0a0a0a!important; box-shadow:0 0 0 3px rgba(212,245,106,0.3)!important; }
input, input.border-none { color:#0a0a0a!important; -webkit-text-fill-color:#0a0a0a!important; background:#ffffff!important; border-radius:10px!important; border:1px solid #d4d4d4!important; padding:10px 12px!important; transition:all 0.15s!important; }
input:focus { outline:none!important; border-color:#0a0a0a!important; box-shadow:0 0 0 3px rgba(212,245,106,0.3)!important; }
.gr-textbox input, .gr-number input, .gr-textbox textarea { background:#ffffff!important; color:#0a0a0a!important; border-radius:10px!important; border:1px solid #d4d4d4!important; }
input::placeholder, textarea::placeholder { color:#9a9a9a!important; }

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

/* ===== 按钮（三档：主=黄绿底黑字；次/默认=黑底白字；危险=红边红字透明底） ===== */
button, .gr-button, .btn { text-transform:none!important; letter-spacing:0!important; font-weight:600!important; font-size:14px!important; border-radius:10px!important; background:#0a0a0a!important; color:#ffffff!important; border:1px solid transparent!important; padding:8px 16px!important; transition:all 0.15s!important; box-shadow:none!important; }
button:hover, .gr-button:hover { transform:translateY(-1px)!important; }
.gr-button.primary, button.primary { background:#d4f56a!important; color:#0a0a0a!important; border:1px solid #d4f56a!important; }
.gr-button.primary:hover { background:#c4e55a!important; box-shadow:0 4px 12px rgba(212,245,106,0.4)!important; }
.gr-button.secondary, button.secondary { background:#0a0a0a!important; color:#ffffff!important; border:1px solid #0a0a0a!important; }
.gr-button.secondary:hover { background:#2a2a2a!important; }
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
.gr-group { background:#ffffff!important; border-color:#e5e5e5!important; border-radius:16px!important; padding:20px!important; }

/* ===== Markdown 表格 ===== */
.prose table { color:#0a0a0a!important; border-collapse:collapse; border-radius:12px; overflow:hidden; }
.prose th, .prose td { border-bottom:1px solid #f0f0f0!important; padding:10px 14px; }
.prose th { color:#6b6b6b!important; background:#fafafa; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; }
.prose tr:last-child td { border-bottom:none!important; }

/* ===== 侧边栏（按生产阶段组织） ===== */
.sidebar { background:#101411!important; border:none!important; border-radius:20px!important; padding:22px 14px!important; min-width:260px!important; margin-right:8px!important; }
.brand-lockup { align-items:center!important; gap:8px!important; margin:0 4px!important; }
.sidebar .brand-mark { flex:0 0 46px!important; min-width:46px!important; width:46px!important; }
.sidebar .brand-mark img { width:46px!important; height:46px!important; object-fit:cover!important; border-radius:13px!important; }
.logo-bar { font-weight:800; font-size:20px; color:#ffffff; padding:6px 2px 4px; letter-spacing:-0.02em; line-height:1.15; }
.logo-bar span { display:block; color:#d4f56a; font-size:10px; font-weight:700; letter-spacing:.14em; margin-bottom:6px; }
.sidebar-caption { color:#8c958e!important; font-size:12px!important; padding:0 14px 22px; }
.nav-btn { width:100%!important; justify-content:flex-start!important; text-align:left!important; margin:3px 0!important; border:none!important; background:transparent!important; color:#a0a0a0!important; border-radius:12px!important; font-weight:600!important; font-size:16px!important; text-transform:none!important; letter-spacing:0.01em!important; box-shadow:none!important; padding:12px 16px!important; transition:all 0.18s ease!important; position:relative!important; }
.nav-btn:hover { background:rgba(255,255,255,0.08)!important; color:#ffffff!important; }
/* active 状态：左侧黄绿指示条 + 浅黄绿背景 + 白色文字 + 微左缩进 */
.nav-btn.active { background:rgba(212,245,106,0.12)!important; color:#d4f56a!important; font-weight:700!important; padding-left:20px!important; }
.nav-btn.active::before {
  content:''!important;
  position:absolute!important;
  left:0!important; top:8px!important; bottom:8px!important;
  width:4px!important; background:#d4f56a!important;
  border-radius:0 4px 4px 0!important;
}

/* ===== 主工作区（浅色 + 大圆角 + 统一分区间距） ===== */
.main-area { border:none!important; padding:12px 28px 28px!important; background:transparent!important; }

/* ===== 页面标题：统一风格，每页第一个标题用大号粗体 ===== */
#grp-overview > :first-child, #grp-project > :first-child,
#grp-voices > :first-child, #grp-synth > :first-child,
#grp-review > :first-child, #grp-export > :first-child {
  font-size:22px!important; font-weight:800!important; color:#0a0a0a!important;
  margin-bottom:4px!important; letter-spacing:-0.01em!important;
}
/* 页面副标题/说明文字：柔和灰色 */
#grp-overview > :nth-child(2), #grp-project > :nth-child(2),
#grp-voices > :nth-child(2), #grp-synth > :nth-child(2),
#grp-review > :nth-child(2), #grp-export > :nth-child(2) {
  color:#6b6b6b!important; font-size:14px!important; margin-top:0!important; margin-bottom:16px!important;
}

/* ===== 功��分区卡片（每个 gr.Group 内部子区域） ===== */
.gr-group .prose { margin:8px 0!important; }
.gr-group hr { display:none!important; }  /* 隐藏 markdown 分割��� --- */
.gr-group h3, .gr-group h4 { margin-top:20px!important; margin-bottom:10px!important; }
.gr-group h3:first-of-type, .gr-group h4:first-of-type { margin-top:4px!important; }

/* ===== 顶部状态条（白底深字 + 圆角 + 阴影） ===== */
.top-status-bar { background:#ffffff!important; border:1px solid #e5e5e5!important; border-radius:16px!important; padding:14px 20px!important; margin:12px 0 20px!important; box-shadow:0 4px 16px rgba(0,0,0,0.04)!important; align-items:center; }
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
.workbench-hero { min-height:164px; display:flex; justify-content:space-between; align-items:center; gap:24px; padding:26px 28px; border-radius:16px; color:#ffffff; background:linear-gradient(125deg,#131a14 0%,#26352a 100%); overflow:hidden; }
.workbench-hero .eyebrow { color:#d4f56a; font-size:11px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
.workbench-hero h2 { color:#ffffff!important; font-size:30px!important; line-height:1.12!important; margin:7px 0 8px!important; letter-spacing:-.035em!important; }
.workbench-hero p { color:#c4cdc5!important; margin:0!important; max-width:620px; font-size:14px!important; }
.hero-progress { flex:0 0 auto; width:88px; height:88px; border:5px solid #d4f56a; border-radius:50%; display:flex; flex-direction:column; justify-content:center; align-items:center; background:rgba(255,255,255,.07); }
.hero-progress span { color:#ffffff; font-size:20px; font-weight:800; letter-spacing:-.04em; }
.hero-progress small { color:#c4cdc5; font-size:10px; text-align:center; line-height:1.1; width:60px; }
.hero-icon { font-size:42px; }
.empty-state .hero-icon { border-radius:18px; background:rgba(212,245,106,.12); padding:18px; }
.workbench-main-row { gap:14px; margin-top:14px!important; }
.dashboard-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-bottom:12px; }
.metric-card,.workbench-card { border:1px solid #e6ebe4; background:#ffffff; border-radius:14px; padding:17px 18px; box-shadow:0 3px 10px rgba(16,20,17,.035); }
.metric-card span,.card-eyebrow { display:block; color:#6b746d; font-size:11px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.metric-card strong { display:block; color:#111812; font-size:27px; line-height:1; margin:8px 0 4px; letter-spacing:-.04em; }
.metric-card strong i { color:#9aa39d; font-size:16px; font-style:normal; margin:0 3px; }
.metric-card small,.workbench-card p { color:#707a72; font-size:12px; line-height:1.45; margin:4px 0 0; }
.metric-track { height:6px; overflow:hidden; border-radius:999px; background:#edf1ed; margin-top:14px; }
.metric-track b { display:block; height:100%; border-radius:inherit; background:#1f8a5b; }
.metric-track.voices b { background:#c2e65b; }
.next-step-card { border-color:#d5e9af; background:#f9fdeF; }
.next-step-card strong,.task-card strong,.workbench-issues strong { display:block; color:#172218; font-size:16px; margin-top:8px; }
.task-card { height:100%; background:#f8faf8; }
.workbench-issues { height:100%; }
.workbench-issues ul { list-style:none; padding:0; margin:12px 0 0; }
.workbench-issues li { display:flex; align-items:center; gap:9px; color:#354036; font-size:13px; padding:7px 0; border-bottom:1px solid #edf0ed; }
.workbench-issues li:last-child { border-bottom:0; }
.issue-dot { width:7px; height:7px; border-radius:50%; flex:0 0 auto; background:#e3aa2f; }
.issue-dot.error { background:#d95563; }.issue-dot.info { background:#5c859d; }.issue-dot.warning { background:#e3aa2f; }
.workbench-issues.is-clear { background:#f4fbf6; border-color:#d7eadc; }
.inline-empty { color:#717b73; padding:12px 0; font-size:14px; }

/* ===== v3.2 阶段页 ===== */
.stage-row { gap:16px; }.stage-card { background:#fbfcfb; border:1px solid #e7ece7; border-radius:14px; padding:4px 16px 16px; }
.binding-workspace,.production-command,.review-workspace,.delivery-workspace { border:1px solid #e5ebe5!important; box-shadow:none!important; background:#fbfcfb!important; }
.production-command { border-color:#dbe9cc!important; background:#fcfef9!important; }
.advanced-settings,.asset-accordion,.settings-accordion,.supplement-accordion,.run-log { border:1px solid #e6ebe6!important; border-radius:12px!important; margin-top:12px!important; }
.run-log textarea { background:#151b16!important; color:#dce6dc!important; border-color:#263126!important; }
#grp-review { margin-top:16px!important; } #grp-supplement { margin-top:8px!important; }
#grp-review > :first-child { font-size:20px!important; margin-bottom:2px!important; }
#grp-review > :nth-child(2) { margin-bottom:14px!important; }

@media (max-width: 900px) {
  .sidebar { min-width:100%!important; margin:0 0 12px!important; }
  .dashboard-metrics { grid-template-columns:1fr; }
  .workbench-hero { padding:22px; }.hero-progress { display:none; }
  .main-area { padding:8px 12px 20px!important; }
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
