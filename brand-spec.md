# Brand Specification: Audiobook Studio（Stripe 浅色招牌风）

> 由设计系统专家（彩格调）从已确认需求摘要生成。项目无独立品牌，属"从暗色紫迁移到 Stripe 浅色渐变体系"，故按品牌提取协议的 Codify/Vocalise 步骤产出规范，并融合 Stripe 设计基因。

## Brand Proposition（一句话主张）

> **Audiobook Studio 把文字变成有温度的声音——以 Stripe 式的明亮、克制与专业，为有声书创作者提供可靠、高效、状态可视的本地语音合成工作台。**

## Colors

| Token | HEX | Usage |
|-------|-----|-------|
| `--brand-primary` | `#635BFF` | 主色（CTA / 激活态 / 链接 / 进度填充），Stripe blurple |
| `--brand-gradient-blue` | `#0073E6` | 渐变三色之一（起点） |
| `--brand-gradient-purple` | `#635BFF` | 渐变三色之一（中点） |
| `--brand-gradient-pink` | `#FF6FB5` | 渐变三色之一（终点） |
| `--brand-gradient-primary` | `linear-gradient(115deg,#0073E6 0%,#635BFF 48%,#FF6FB5 100%)` | 克制点缀渐变（仅 3 处） |
| `--brand-bg` | `#F6F9FC` | 应用/主工作区底色 |
| `--brand-surface` | `#FFFFFF` | 侧边栏、卡片、面板 |
| `--brand-text` | `#1A1F36` | 主文本（近黑） |
| `--brand-accent-success` | `#1CA672` | 成功状态 |
| `--brand-accent-warning` | `#D9822B` | 警告状态 |
| `--brand-accent-danger` | `#DF1B41` | 错误状态 |
| `--brand-accent-info` | `#0073E6` | 信息/进行中 |

## Typography

- Heading font: `Inter`, `-apple-system`, `BlinkMacSystemFont`, `"Segoe UI"`, `"PingFang SC"`, `"Microsoft YaHei"`, sans-serif
- Body font: 同上（中文正文 14px / 1.6）
- Mono font: `"SF Mono"`, `"JetBrains Mono"`, `"Roboto Mono"`, Menlo, Consolas, monospace（日志终端）
- Font weights used: 400 / 500 / 600 / 700

## Visual Characteristics

- Border radius: 克制圆角（按钮 8px、组件 10px、卡片 16px、徽章 pill）
- Shadow style: 3 档极淡柔和阴影（基于近黑低透明度，无重投影）
- Spacing density: 正常偏紧凑（借鉴 Linear/Warp，适配长时工作）
- Icon style: 描边型（lucide 风格）、18px、stroke 1.75

## Gradient Usage Boundaries（渐变用法边界）

渐变 `--brand-gradient-primary` **仅允许 3 处**（稀缺性 = 强调）：
1. 顶部 hero / 品牌展示区（如概览页顶部 banner）
2. 侧边栏顶 logo 条（logo 标记 + 2px 渐变分隔线）
3. 主 CTA 按钮（每视图至多 1 个，如「开始合成」「一键导出」）

其余区域一律纯色，保持专业克制。

## Voice & Tone（品牌语调）

明亮、克制、专业、可靠。像 Stripe 一样把复杂的能力藏在整洁的界面之后；像 Linear/Warp 一样让密集信息依然清晰可读。视觉信号克制——渐变只作强调，绝大部分区域靠留白、细边框与柔和阴影建立秩序。

## Don'ts（禁用项）

1. 禁止把渐变铺满整个背景或作页面底色。
2. 禁止浅色上使用低对比文字（正文 ≥4.5:1）。
3. 禁止沿用旧暗色主色 `#7C5CFC`，必须迁移到 `#635BFF`。
4. 禁止混合暗色与浅色元素（Phase 1 无暗色模式）。
5. 禁止重投影 / 硬边阴影；只用 3 档柔和阴影。
6. 禁止同一视图出现多个渐变 CTA。
7. 禁止语义色仅靠颜色区分（须搭配图标/文字，照顾色盲）。
8. 禁止圆角 >16px 或 <6px。

## Vocalise（视觉语言）

Audiobook Studio 的视觉语言是明亮、克制且值得信赖的。它以充足的留白与细边框建立秩序，用 Stripe 标志性的蓝→紫→粉斜向渐变作为稀缺的强调信号，其余区域保持纯色与柔和阴影的专业克制。最具辨识度的特征是顶部常驻的合成状态条与侧边栏 logo 条的渐变点缀。生成时应避免浓重装饰与暗色惯性，始终保持"专业音频工具"的可靠感。
