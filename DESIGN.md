# DESIGN.md v3.0 — Audiobook Studio（Stripe 浅色招牌风）

> 设计参考：Stripe（金融科技招牌风）× Linear（信息密度）× Warp（终端质感）
> 基调：明亮、克制、专业的本地语音合成工作台
> 适用：Gradio 5.x 应用，左侧分组侧边栏 + 右侧主工作区 + 顶部常驻合成状态条
> 模式：Phase 1 **仅浅色**，不提供暗色切换
> 版本：v3.0 ｜ 取代 v2.2（暗色 #0A0A0D + #7C5CFC）

本文件为原型构建师的**唯一样板输入**，结构如下：
1. 色彩系统　2. 排版系统　3. 间距与圆角　4. 阴影与层级　5. 组件规范　6. 品牌规范（brand-spec）　7. Gradio 5.x 主题落地

---

## 0. 设计令牌速查（`:root` CSS 变量，机读优先）

```css
:root {
  /* ===== 背景层级（浅色） ===== */
  --color-canvas:        #F6F9FC; /* 应用底色 / 主工作区背景 */
  --color-surface:       #FFFFFF; /* 侧边栏、卡片、面板 */
  --color-subtle:        #F1F4F9; /* 输入区底、内嵌区、悬停态 */
  --color-hover:         #F1F4F9; /* 悬停态（= subtle） */
  --color-selected:      #EEF0FF; /* 选中/激活项（浅紫底） */
  --color-selected-border:#635BFF;/* 选中项左边框 */

  /* ===== 品牌渐变三色（克制点缀） ===== */
  --gradient-blue:   #0073E6;
  --gradient-purple: #635BFF;
  --gradient-pink:   #FF6FB5;
  --gradient-primary: linear-gradient(115deg, #0073E6 0%, #635BFF 48%, #FF6FB5 100%);

  /* ===== 主色 #635BFF 及状态 ===== */
  --color-primary:          #635BFF; /* CTA / 激活态 / 链接 */
  --color-primary-hover:    #5149E0; /* hover（压深，提升对比） */
  --color-primary-active:   #4A43CC; /* 按下 */
  --color-primary-subtle:   #ECEAFF; /* 抑制态 / 浅紫底 */
  --color-primary-contrast: #FFFFFF; /* 主色上的文字 */
  --ring-focus: 0 0 0 3px rgba(99,91,255,0.35); /* 焦点环 */

  /* ===== 文本三级 ===== */
  --color-text-primary:   #1A1F36; /* 标题、正文（近黑） */
  --color-text-secondary: #425466; /* 描述、标签 */
  --color-text-tertiary:  #697386; /* 弱文本、meta（≥3:1） */

  /* ===== 边框两级 ===== */
  --color-border:        #E3E8EE; /* 主边框 */
  --color-border-subtle: #EFF3F7; /* 弱边框 / 内部分割 */

  /* ===== 语义色（成功/警告/错误/信息） ===== */
  --color-success:      #1CA672; --color-success-bg:      #E7F8F0; --color-success-text:      #0E7A4F;
  --color-warning:      #D9822B; --color-warning-bg:      #FBF1DE; --color-warning-text:      #8A5A0B;
  --color-danger:       #DF1B41; --color-danger-bg:       #FCE8EC; --color-danger-text:       #B3122F;
  --color-info:         #0073E6; --color-info-bg:         #E6F0FD; --color-info-text:         #0B5FCC;

  /* ===== 字体栈 ===== */
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', '微软雅黑', Roboto, Helvetica, Arial, sans-serif;
  --font-mono: 'SF Mono', 'JetBrains Mono', 'Roboto Mono', Menlo, Consolas, 'Liberation Mono', monospace;

  /* ===== 字级表 ===== */
  --fs-display: 32px; --fw-display: 700; --lh-display: 1.2;
  --fs-h1: 24px;      --fw-h1: 700;      --lh-h1: 1.3;
  --fs-h2: 18px;      --fw-h2: 600;      --lh-h2: 1.4;
  --fs-h3: 15px;      --fw-h3: 600;      --lh-h3: 1.4;
  --fs-body: 14px;    --fw-body: 400;    --lh-body: 1.6;   /* 中文正文 14px / 1.6 */
  --fs-small: 13px;   --fw-small: 400;   --lh-small: 1.5;
  --fs-caption: 12px; --fw-caption: 500; --lh-caption: 1.4;
  --fs-mono: 12.5px;  --fw-mono: 400;    --lh-mono: 1.6;

  /* ===== 间距 ===== */
  --space-xs: 4px; --space-sm: 8px; --space-md: 12px; --space-base: 16px;
  --space-lg: 24px; --space-xl: 32px; --space-2xl: 48px; --space-3xl: 64px;

  /* ===== 圆角 ===== */
  --radius-sm: 6px; --radius-btn: 8px; --radius-component: 10px;
  --radius-panel: 12px; --radius-card: 16px; --radius-pill: 999px;

  /* ===== 阴影（3 档柔和） ===== */
  --shadow-sm: 0 1px 2px rgba(26,31,54,0.06), 0 1px 1px rgba(26,31,54,0.04);
  --shadow-md: 0 4px 12px rgba(26,31,54,0.08), 0 2px 4px rgba(26,31,54,0.05);
  --shadow-lg: 0 12px 28px rgba(26,31,54,0.12), 0 6px 12px rgba(26,31,54,0.06);
  --shadow-primary: 0 4px 14px rgba(99,91,255,0.35);

  /* ===== 层级 z-index ===== */
  --z-base: 0; --z-sidebar: 100; --z-statusbar: 200;
  --z-dropdown: 300; --z-modal: 400; --z-toast: 500;

  /* ===== 布局尺寸 ===== */
  --sidebar-width: 248px; --sidebar-width-collapsed: 64px;
  --statusbar-height: 52px; --sidebar-logo-height: 56px;
  --content-max-width: 1200px;
}
```

> 对比度审计（WCAG AA）：白字 on `#635BFF` ≈ 4.55:1 ✔；`#635BFF` 文字 on 白 ≈ 5.8:1 ✔；`#1A1F36`/`#425466`/`#697386` on 白 ≥ 4.5:1 ✔；语义**文字**统一使用 `--*-text` 深一档变体以保 AA，亮色 `--*-*` 仅用于圆点/图标/边框。

---

## 1. 色彩系统

### 1.1 背景层级（浅色体系）

| 层级 | Token | HEX | 对应旧暗色（v2.2） | 用途 |
|------|-------|-----|-------------------|------|
| 页面底色 | `--color-canvas` | `#F6F9FC` | `#0A0A0D` | 主工作区背景、应用画布 |
| 面板底 | `--color-surface` | `#FFFFFF` | `#121216` | 侧边栏、卡片、弹层 |
| 输入区底 | `--color-subtle` | `#F1F4F9` | `#1A1A20` | 输入框、内嵌区、悬停 |
| 悬停态 | `--color-hover` | `#F1F4F9` | `#222226` | 列表行 / 按钮 hover |
| 选中态 | `--color-selected` | `#EEF0FF` | `#2A2A30` | 激活导航项（浅紫底） |

### 1.2 品牌渐变三色 + `gradient-primary`

```
--gradient-blue:   #0073E6
--gradient-purple: #635BFF
--gradient-pink:   #FF6FB5
--gradient-primary: linear-gradient(115deg, #0073E6 0%, #635BFF 48%, #FF6FB5 100%)
```
**用法边界（克制点缀，仅 3 处）**：① 顶部 hero / 品牌展示区（如概览页顶部 banner）② 侧边栏顶 logo 条（logo 标记 + 2px 渐变分隔线）③ 主 CTA 按钮（每视图至多 1 个）。其余区域一律纯色。

### 1.3 主色 `#635BFF` 及 hover/active/抑制态

| 状态 | Token | HEX | 用途 |
|------|-------|-----|------|
| 默认 | `--color-primary` | `#635BFF` | CTA 底、激活态、链接、进度填充、焦点环 |
| Hover | `--color-primary-hover` | `#5149E0` | 压深以提升对比 |
| Active | `--color-primary-active` | `#4A43CC` | 按下 |
| 抑制/浅底 | `--color-primary-subtle` | `#ECEAFF` | 选中浅紫底、禁用态底色 |
| 文字反色 | `--color-primary-contrast` | `#FFFFFF` | 主色块上的文字 |

### 1.4 文本三级

| 层级 | Token | HEX | 用途 |
|------|-------|-----|------|
| 主 | `--color-text-primary` | `#1A1F36` | 标题、强调正文（近黑，非纯黑） |
| 次 | `--color-text-secondary` | `#425466` | 描述、标签、正文 |
| 弱 | `--color-text-tertiary` | `#697386` | meta、时间戳、占位符（仅非关键信息） |

### 1.5 边框两级

| 层级 | Token | HEX | 用途 |
|------|-------|-----|------|
| 主边框 | `--color-border` | `#E3E8EE` | 卡片、输入框、侧边栏分隔 |
| 弱边框 | `--color-border-subtle` | `#EFF3F7` | 列表内部分割线、代码行号槽 |

### 1.6 语义色（成功/警告/错误/信息）

用于合成状态、段落状态、日志着色。文字一律用深一档 `--*-text` 变体保证 AA。

| 语义 | 强调点 | 浅底 | 文字 | 场景 |
|------|--------|------|------|------|
| 成功 | `--color-success` `#1CA672` | `--color-success-bg` `#E7F8F0` | `--color-success-text` `#0E7A4F` | 合成完成、导出成功、✅ |
| 警告 | `--color-warning` `#D9822B` | `--color-warning-bg` `#FBF1DE` | `--color-warning-text` `#8A5A0B` | 处理中、待绑定、⚠ |
| 错误 | `--color-danger` `#DF1B41` | `--color-danger-bg` `#FCE8EC` | `--color-danger-text` `#B3122F` | 合成失败、OOM、❌ |
| 信息 | `--color-info` `#0073E6` | `--color-info-bg` `#E6F0FD` | `--color-info-text` `#0B5FCC` | 通知、进行中、GPU 提示 |

### 1.7 暗 → 明 Gradio 主题映射（对照 v2.2 §2.6）

| 旧（暗色）Gradio 变量 | 旧值 | → 新 Token | 新值 |
|----------------------|------|-----------|------|
| `body_background_fill` | `#0A0A0D` | `--color-canvas` | `#F6F9FC` |
| `block_background_fill` | `#121216` | `--color-surface` | `#FFFFFF` |
| `block_border_color` | `#1E1E24` | `--color-border` | `#E3E8EE` |
| `input_background_fill` | `#1A1A20` | `--color-subtle` | `#F1F4F9` |
| `button_primary_background_fill` | `#7C5CFC` | `--color-primary` | `#635BFF` |
| `button_primary_background_fill_hover` | `#8F74FD` | `--color-primary-hover` | `#5149E0` |
| `button_secondary_background_fill` | `#1E1E28` | `--color-surface` | `#FFFFFF` |
| `button_secondary_text_color` | `#EDEDEF` | `--color-text-primary` | `#1A1F36` |
| `button_cancel_background_fill` | `#3D1A1A` | `--color-danger-bg` | `#FCE8EC` |
| `body_text_color` | `#E8E8ED` | `--color-text-primary` | `#1A1F36` |
| `body_text_color_subdued` | `#888894` | `--color-text-secondary` | `#425466` |

> 迁移要点：旧主色 violet `#7C5CFC` → **Stripe blurple `#635BFF`**（保留"专业紫"识别度，色相更偏蓝）；所有暗底反转为浅底；语义色由亮色（暗底可读）改为"亮点+深字"配对以适配浅底。

---

## 2. 排版系统

### 2.1 字体栈

```css
--font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI',
             'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', '微软雅黑',
             Roboto, Helvetica, Arial, sans-serif;
--font-mono: 'SF Mono', 'JetBrains Mono', 'Roboto Mono', Menlo, Consolas,
             'Liberation Mono', monospace;
```
- 标题与正文共用 `--font-sans`（Inter 系），靠字重区分层级；不引入第二套西文字体。
- 中文 fallback 顺序：PingFang SC（macOS/iOS）→ Microsoft YaHei（Windows）→ 系统默认，保证中英文混排对齐。
- 等宽仅用于日志终端与数字读数。

### 2.2 字级表

| 层级 | Size | Weight | Line-height | 用途 |
|------|------|--------|-------------|------|
| Display | 32px / 2rem | 700 | 1.2 | 概览页 hero / 品牌展示标题 |
| H1 | 24px / 1.5rem | 700 | 1.3 | 各分类页标题 |
| H2 | 18px / 1.125rem | 600 | 1.4 | 卡片标题、区块标题 |
| H3 | 15px / 0.9375rem | 600 | 1.4 | 分组 / 列表标题 |
| Body | 14px / 0.875rem | 400 | 1.6 | **正文（中文 14px / 1.6）** |
| Body-Small | 13px | 400 | 1.5 | 辅助说明、表格单元格 |
| Caption | 12px | 500 | 1.4 | 徽章、标签、状态、时间戳 |
| Mono | 12.5px | 400 | 1.6 | 日志终端 |

### 2.3 排版哲学

- 仅 400 / 500 / 600 / 700 四档字重；标题 600–700，正文 400，标签 500。
- 中文正文固定 **14px / 1.6**，长时阅读不疲劳；数据密集区（日志、GPU 仪表）用 12.5px 等宽提升可读性（借鉴 Linear/Warp）。
- 标题与正文对比靠字号 + 字重 + 颜色三级，而非颜色堆砌。

---

## 3. 间距与圆角

### 3.1 Spacing scale

| Token | Value | 用途 |
|-------|-------|------|
| `--space-xs` | 4px | 行内细微间距、图标与文字间隙 |
| `--space-sm` | 8px | 紧凑间距、徽章内边距 |
| `--space-md` | 12px | 列表项内边距、卡片内小组 |
| `--space-base` | 16px | 默认间距、控件间距 |
| `--space-lg` | 24px | 区块内边距、卡片 padding |
| `--space-xl` | 32px | 主工作区外边距、区块间距 |
| `--space-2xl` | 48px | 大区块分隔 |
| `--space-3xl` | 64px | 概览页 hero 留白 |

### 3.2 Radius

| Token | Value | 用途 |
|-------|-------|------|
| `--radius-sm` | 6px | 徽章、小控件、输入框 |
| `--radius-btn` | 8px | 按钮 |
| `--radius-component` | 10px | 输入控件、日志终端、下拉 |
| `--radius-panel` | 12px | 内嵌子面板 |
| `--radius-card` | 16px | 卡片（12–16px 区间取上限） |
| `--radius-pill` | 999px | 状态徽章、胶囊按钮 |

> 圆角区间约束：组件 8–12px、卡片 12–16px、按钮 8px（已锁定）；禁止 <6px（生硬）或 >16px（失克制）。

### 3.3 容器与尺寸

- 侧边栏宽 `--sidebar-width: 248px`（可折叠至 `--sidebar-width-collapsed: 64px`）。
- 顶部状态条高 `--statusbar-height: 52px`；侧边栏 logo 条高 `--sidebar-logo-height: 56px`。
- 主工作区内容最大宽 `--content-max-width: 1200px`，居中；页面整体为全高仪表盘布局（非居中落地页）。

---

## 4. 阴影与层级

### 4.1 三档柔和阴影

| 档 | Token | 值 | 用途 |
|----|-------|-----|------|
| 静息 | `--shadow-sm` | `0 1px 2px rgba(26,31,54,.06), 0 1px 1px rgba(26,31,54,.04)` | 卡片、输入控件 |
| 浮动 | `--shadow-md` | `0 4px 12px rgba(26,31,54,.08), 0 2px 4px rgba(26,31,54,.05)` | 顶部状态条（运行中）、下拉、弹层、悬浮卡 |
| 抬升 | `--shadow-lg` | `0 12px 28px rgba(26,31,54,.12), 0 6px 12px rgba(26,31,54,.06)` | 模态框、命令面板 |
| 主色光晕 | `--shadow-primary` | `0 4px 14px rgba(99,91,255,.35)` | 主 CTA hover 强调 |

> 浅色体系阴影须极淡（基于近黑 `#1A1F36` 低透明度），禁止重投影 / 硬边阴影。

### 4.2 Z-index

`--z-base:0` → `--z-sidebar:100` → `--z-statusbar:200` → `--z-dropdown:300` → `--z-modal:400` → `--z-toast:500`

---

## 5. 组件规范

### 5.1 侧边栏（分组可折叠）

```
┌─────────────────────────────┐  ← logo 条 (56px)：logo 标记(渐变方) + "Audiobook Studio"
│ ▍（2px 渐变分隔线）           │     仅此处使用渐变点缀
├─────────────────────────────┤
│ ▸ 概览                        │  ← 分组标题：12px/600/tertiary，含 chevron
│   项目进度总览                 │  ← 导航项：14px/400/secondary
│   最近项目快捷恢复             │  ├ hover: --color-hover
│   统计卡片                     │  └ active: --color-selected 底 + 3px --color-primary 左边框 + 文字 primary
│ ▸ 项目                        │
│ ▸ 音色资产                    │
│ ▸ 合成                        │
│ ▸ 试听与质检                  │
│ ▸ 导出                        │
└─────────────────────────────┘
```

- **容器**：`width: var(--sidebar-width)`；`background: var(--color-surface)`；`border-right: 1px solid var(--color-border)`；`position: sticky; top: var(--statusbar-height)`；`z-index: var(--z-sidebar)`；`height: calc(100vh - var(--statusbar-height))`；可滚动。
- **logo 条**：`height: var(--sidebar-logo-height)`；logo 标记为 28px 圆角方块填充 `var(--gradient-primary)`；应用名 15px/600 `--color-text-primary`；底部 2px `var(--gradient-primary)` 发丝线（唯一渐变处）。
- **分组标题**：`padding: 8px 12px`；`font: 12px/600`；`color: var(--color-text-tertiary)`；`letter-spacing: .02em`；右侧 chevron 旋转表示展开/收起；分组间 `8px` 间距。
- **导航项**：`display:flex; gap:8px; align-items:center; padding: 8px 12px; margin: 2px 8px; border-radius: var(--radius-btn); font: 14px/400; color: var(--color-text-secondary)`。
  - hover → `background: var(--color-hover)`。
  - **active** → `background: var(--color-selected); color: var(--color-primary); font-weight:500; border-left: 3px solid var(--color-primary);`（图标同步变 primary）。
- **图标**：18px、描边型（lucide 风格）、`stroke-width:1.75`；默认 `--color-text-tertiary`，active `--color-primary`。
- **折叠态**：宽 64px，仅图标，hover 出 tooltip，隐藏分组标题文字。

### 5.2 主工作区卡片

- **页面容器**：`background: var(--color-canvas)`；`padding: var(--space-lg) var(--space-xl)`；`overflow-y:auto`；`min-height: calc(100vh - var(--statusbar-height))`。
- **内容包裹**：`max-width: var(--content-max-width); margin: 0 auto`。
- **页面标题行**：H1（24/700）+ 副标题（13/400 tertiary）+ 右侧操作按钮。
- **卡片**：`background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-card); padding: var(--space-lg); box-shadow: var(--shadow-sm)`。
- **卡片头**：H2（18/600）+ 可选"查看全部"链接（`--color-primary`）。
- **统计卡（网格）**：半径 12px、padding 16px；标签 caption tertiary；数值 H1/24/700 `--color-text-primary`；变化量 small。
- **内嵌子面板**：`background: var(--color-subtle); border-radius: var(--radius-panel)`（inset 观感）。

### 5.3 顶部合成状态条（全局常驻）

```
┌──────────────────────────────────────────────────────────────────────┐
│ ● 合成中 │ 段落 12/80 · 34% ▓▓▓▓▓▓▓░░░ │ GPU 78% ▓▓▓ │ 剩余 ~6 分 [⏸] │
│ (状态点)  (进度)                      (GPU 小计)      (ETA)  (暂停)     │
└──────────────────────────────────────────────────────────────────────┘
```
- **容器**：`position: sticky; top:0; height: var(--statusbar-height); width:100%`；`background: var(--color-surface)`；`border-bottom: 1px solid var(--color-border)`；**运行中**加 `box-shadow: var(--shadow-md)`；`z-index: var(--z-statusbar)`；`display:flex; align-items:center; gap: var(--space-lg); padding: 0 var(--space-lg)`。
- **左·状态点**：10px 圆点 + 标签。running→`--color-info`（pulse）；paused→`--color-warning`；idle→`--color-text-tertiary`。
- **中·进度**：6px 轨道（`--color-subtle`，pill）+ 填充 `--color-primary`；右侧文字 caption「段落 12/80 · 34%」。`white-space:nowrap`。
- **右·GPU 仪表**：「GPU 78%」+ 微型条（<70% `--color-info`、70–90% `--color-warning`、>90% `--color-danger`）；ETA 文案；暂停/继续小按钮。
- **自适应**：窄屏隐藏 ETA 文字，保留状态点 + 进度 + GPU。

### 5.4 按钮（主 / 次 / 危险 + 主 CTA 渐变）

- **基础**：`font: 14px/500; height: 36px; padding: 0 16px; border-radius: var(--radius-btn); border:1px solid transparent; transition: 120ms; cursor:pointer`。
- **Primary（实色）**：`background: var(--color-primary); color: var(--color-primary-contrast)`；hover→`background: var(--color-primary-hover); box-shadow: var(--shadow-primary)`；active→`background: var(--color-primary-active)`。
- **CTA（渐变，每视图至多 1 个，如「开始合成」「一键导出」）**：`background: var(--gradient-primary); color:#fff`；hover→`translateY(-1px); box-shadow: var(--shadow-primary)`；active→`translateY(0)`。
- **Secondary**：`background: var(--color-surface); border:1px solid var(--color-border); color: var(--color-text-primary)`；hover→`background: var(--color-hover); border-color:#D2D9E2`。
- **Ghost（三级）**：`background: transparent; color: var(--color-text-secondary)`；hover→`background: var(--color-hover)`。
- **Danger**：实色 `background: var(--color-danger); color:#fff`（强破坏确认）；ghost `background: var(--color-surface); border:1px solid var(--color-danger); color: var(--color-danger); hover: background: var(--color-danger-bg)`（停止/删除）。
- **Disabled**：`background: var(--color-subtle); color: var(--color-text-tertiary); cursor:not-allowed; box-shadow:none`。

### 5.5 进度条

- **轨道**：`height: 6px`（紧凑）/ `8px`（hero）；`background: var(--color-subtle)`；`border-radius: var(--radius-pill)`；`overflow:hidden`。
- **填充**：`background: var(--color-primary)`；整体/hero 进度可用 `var(--gradient-primary)`（作为动效强调，仍属克制）；宽度行内设定。
- **不确定态**：`var(--gradient-primary)` 滑光动画（仅 ETA 未知时使用）。
- **段级微条**：高 4px，按状态着色（success/warning/danger/info/idle）。

### 5.6 状态徽章（done / warn / fail / …）

- **基础**：`display:inline-flex; align-items:center; gap:4px; height:20px; padding:0 8px; border-radius: var(--radius-pill); font: 12px/600; border:1px solid transparent`。
- **done / success**：`background: var(--color-success-bg); color: var(--color-success-text);` 圆点 `--color-success`。
- **warn**：`background: var(--color-warning-bg); color: var(--color-warning-text);` 圆点 `--color-warning`。
- **fail / error**：`background: var(--color-danger-bg); color: var(--color-danger-text);` 圆点 `--color-danger`。
- **running / info**：`background: var(--color-info-bg); color: var(--color-info-text);` 圆点 `--color-info`（pulse）。
- **idle / neutral**：`background: var(--color-subtle); color: var(--color-text-secondary);` 圆点 `--color-text-tertiary`。
- **可访问性**：色盲场景必须搭配图标/文字，不可仅靠颜色区分。

### 5.7 日志终端（等宽、浅底）

- **容器**：`background: var(--color-subtle)`（或近白 `#FBFCFE`）；`border:1px solid var(--color-border)`；`border-radius: var(--radius-component)`；`padding: var(--space-md)`；`font: var(--font-mono); font-size: var(--fs-mono); line-height: var(--lh-mono); color: var(--color-text-secondary)`；`overflow:auto; max-height:320px; white-space:pre-wrap; autoscroll` 到底部。
- **行结构**：`[timestamp 弱] [LEVEL 标签] message`。行内距 `2px 0`（借鉴 Linear/Warp 密度），可见 60+ 行；可选行号槽（右分隔 `--color-border-subtle`）。
- **语法着色**：INFO→`--color-text-secondary`；OK/SUCCESS→`--color-success-text`；WARN→`--color-warning-text`；ERROR→`--color-danger-text`；timestamp→`--color-text-tertiary`；高亮（段 id）→`--color-primary`。
- **Phase 1 仅浅底**；未来 `--terminal-dark`（底 `#0A0A0D`）保留但不在本期范围。

### 5.8 输入控件（补充核心组件）

- `height: 36px; background: var(--color-surface); border:1px solid var(--color-border); border-radius: var(--radius-component); padding: 0 12px; font: 14px/400; color: var(--color-text-primary)`。
- focus → `border-color: var(--color-primary); box-shadow: var(--ring-focus)`。
- placeholder → `color: var(--color-text-tertiary)`。

### 5.9 布局骨架（新结构，取代 v2.2 四 Tab）

```
┌──────────────────────────────────────────────────────────────┐
│ 顶部合成状态条（全局常驻 · 52px · 跨所有分类可见）              │
├──────────────┬───────────────────────────────────────────────┤
│ 侧边栏 248px  │  主工作区（max-width 1200px，居中）            │
│ ┌──────────┐ │  ┌─────────────────────────────────────────┐  │
│ │ logo 渐变 │ │  │ 页面标题 H1 + 右侧操作                     │  │
│ └──────────┘ │  ├─────────────────────────────────────────┤  │
│ ▸ 概览       │  │ 卡片组（统计卡 / 列表 / 设置面板）          │  │
│   项目进度   │  │                                           │  │
│   最近恢复   │  │                                           │  │
│   统计卡片   │  └─────────────────────────────────────────┘  │
│ ▸ 项目       │                                               │
│ ▸ 音色资产   │                                               │
│ ▸ 合成       │                                               │
│ ▸ 试听与质检 │                                               │
│ ▸ 导出       │                                               │
└──────────────┴───────────────────────────────────────────────┘
```
IA 映射：概览(进度总览/最近恢复/统计) · 项目(新建/打开·书架/设置) · 音色资产(音色库/角色绑定/试音) · 合成(合成控制/任务与断点续跑/实时日志) · 试听与质检(段落试听/合并预览/重合成) · 导出(章节列表/导出设置/一键导出)。

---

## 6. 品牌规范（brand-spec）

### 6.1 品牌主张（一句话）

> **Audiobook Studio 把文字变成有温度的声音——以 Stripe 式的明亮、克制与专业，为有声书创作者提供可靠、高效、状态可视的本地语音合成工作台。**

### 6.2 主色与渐变用法边界

- **渐变 `--gradient-primary` 仅允许 3 处**：① 顶部 hero / 品牌展示区　② 侧边栏顶 logo 条（logo 标记 + 2px 分隔线）　③ 主 CTA 按钮（每视图至多 1 个）。其余一律纯色。
- **主色 `#635BFF` 用于**：链接、激活态、进度填充、主按钮底色、焦点环、状态条进行中指示。
- **语义色**用于状态与日志；文字须用深一档 `--*-text` 变体。
- **稀缺性原则**：渐变 = 强调信号，越稀有越有效；同一视图禁止出现多个渐变 CTA。

### 6.3 禁用项（Cautions / Don'ts）

1. ❌ 禁止把渐变铺满整个背景或作为页面底色（仅克制点缀）。
2. ❌ 禁止在浅色上使用低对比文字（正文 ≥4.5:1，弱文本仅用于非必要 meta）。
3. ❌ 禁止沿用旧暗色主色 `#7C5CFC` 作新主色——必须迁移到 `#635BFF`。
4. ❌ 禁止混合暗色与浅色元素（Phase 1 无暗色模式）。
5. ❌ 禁止重投影 / 硬边阴影——只用 3 档柔和阴影。
6. ❌ 禁止卡片 / 侧边栏用纯色大字配低对比；标题用 `--color-text-primary`。
7. ❌ 禁止同一视图多个渐变 CTA（破坏强调稀缺性）。
8. ❌ 禁止语义色相互混淆（done/warn/fail 须可区分，且搭配图标/文字以照顾色盲）。
9. ❌ 禁止圆角 >16px（失克制）或 <6px（生硬）。
10. ❌ 禁止浅色上用纯黑 `#000` 作文本（用 `#1A1F36` 近黑）。

### 6.4 视觉语言描述（Vocalise）

Audiobook Studio 的视觉语言是**明亮、克制且值得信赖**的。它以充足的留白与细边框建立秩序，用 Stripe 标志性的蓝→紫→粉斜向渐变作为稀缺的强调信号，其余区域保持纯色与柔和阴影的专业克制。最具辨识度的特征是**顶部常驻的合成状态条**与**侧边栏 logo 条的渐变点缀**。生成时应避免浓重装饰与暗色惯性，始终保持"专业音频工具"的可靠感。

---

## 7. Gradio 5.x 主题落地（快速参考）

```python
import gradio as gr

theme = gr.themes.Default(
    primary_hue=gr.themes.Color(
        c50="#EEF0FF", c100="#E0E2FF", c200="#C7CAFF", c300="#A6ABFF",
        c400="#847CFC", c500="#635BFF", c600="#5149E0", c700="#4038C0",
        c800="#2E289A", c900="#1B1870",
    ),
    neutral_hue=gr.themes.Color(
        c50="#F6F9FC", c100="#EEF2F7", c200="#DDE4EC", c300="#C7D0DB",
        c400="#A3AFBE", c500="#8290A1", c600="#697386", c700="#4B5563",
        c800="#36404F", c900="#1A1F36",
    ),
).set(
    # 背景层级（暗→明）
    body_background_fill="#F6F9FC",
    block_background_fill="#FFFFFF",
    block_border_color="#E3E8EE",
    input_background_fill="#F1F4F9",
    input_background_fill_dark="#F1F4F9",
    # 主色（#7C5CFC → #635BFF）
    button_primary_background_fill="#635BFF",
    button_primary_background_fill_hover="#5149E0",
    button_primary_text_color="#FFFFFF",
    button_secondary_background_fill="#FFFFFF",
    button_secondary_background_fill_hover="#F1F4F9",
    button_secondary_text_color="#1A1F36",
    button_cancel_background_fill="#FFFFFF",
    button_cancel_background_fill_hover="#FCE8EC",
    button_cancel_text_color="#DF1B41",
    button_cancel_border_color="#DF1B41",
    # 文本（暗→明反转）
    body_text_color="#1A1F36",
    body_text_color_subdued="#697386",
    link_text_color="#635BFF",
)
```

> 布局从 `gr.Tabs` 四 Tab 改为「左侧 `gr.Column`(sidebar) + 右侧 `gr.Column`(main) + 顶部常驻状态条」；状态条用 `gr.Row` 固定 + sticky，跨所有分类可见。按钮语义沿 v2.2：`variant="primary"`（开始合成/导出）、`"secondary"`（录音/上传/试听）、`"stop"`（暂停/停止，映射到 danger ghost）。

---

> 版本：v3.0 ｜ 参考品牌：Stripe · Linear · Warp ｜ 适用 Gradio 5.x ｜ Phase 1 仅浅色
