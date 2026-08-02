# v4 数据模型

## Project

`project.json` 的 `schema_version` 为 `audiobook-project-v4`，记录项目 ID、名称、作品标题、作者、创建/更新时间、源文件相对路径、脚本相对路径和运行时数据库相对路径。v3 reader 不应把它误认为旧项目。

## Source

`source/source.txt` 是规范化后的不可变文本。`source/source.meta.json` 的 `schema_version` 为 `audiobook-source-v1`，记录原文件名、格式、检测编码、规范化版本、字符数、SHA-256 和导入时间。

修改原文必须建立新 revision，不能就地覆盖后继续使用旧坐标。

## Script

`script/script.json` 的 `schema_version` 为 `audiobook-script-v4`，并带递增 `revision`。章节包含有序片段；每个片段至少包含：

- `segment_id`
- `chapter_id`
- `start`、`end`（Python 字符偏移，左闭右开）
- `kind`：`narration` 或 `dialogue`
- `speaker_id`：可为空
- `speaker_source`：`rule`、`router`、`manual` 或 `unresolved`
- `status`：`confirmed` 或 `unresolved`
- `dialogue_type`：`narration`、`dialogue`、`suspected_dialogue` 或 `quotation`；用于区分明确对白、疑似对白与引用/强调内容
- 可选 `text_override`

`source[start:end]` 是片段原文。相邻片段间只允许空白未覆盖；非空白字符不得丢失、重复或重叠。`text_override` 是显式生产覆盖，不改变原文和坐标。

## Speakers

`script/speakers.json` 的 `schema_version` 为 `audiobook-speakers-v1`，并带递增 `revision`。speaker 包含稳定 ID、显示名、narrator/character 类型、aliases、状态和 locked。旁白固定且锁定；显示名与 aliases 在目录内唯一，以支持改名和别名合并而无需批量改 segment。

## Character candidates

`script/character_candidates.json` 使用 `audiobook-character-candidates-v1`，按 source SHA 保存 AI 候选、别名、置信度、原文证据、来源和审核状态。候选不是 `Speaker`；只有人工确认或明确合并后才进入 `speakers.json`。旧项目缺少该文件时按空候选读取。

## TTS Profile

`production/tts_profile.json` 使用 `audiobook-tts-profile-v1`。profile 描述引擎、硬件假设、推理选项、切分阈值、情绪和运行时恢复策略，不属于 speaker routing。

## Runtime database

`synthesis_tasks` 保存任务 ID、计划 revision、章节/角色、cache key、状态、尝试次数、父任务、拆分深度、输出路径、错误分类，以及 created/started/completed/updated 时间。状态集合：

`pending running completed failed stale cancelled skipped`

`cache_entries` 保存 cache key、音频相对路径、文件 SHA、时长、采样率、声道数、大小、valid 和时间。`migrations` 保存已应用 schema migration。程序启动时在事务内把上次遗留的 `running` 恢复为 `pending` 并标记 `interrupted`。

## 空白与停顿

Phase 1 不为纯空白建立 segment。segment 之间允许且只允许空白 gap，坐标仍保留在 source 范围中；planner 必须根据 gap 中的换行数量推导段落/章节停顿。任何非空白 gap 都是完整性错误。

## Revision 与历史

source 用 SHA-256；script、speakers 和后续 production JSON 用正整数 revision。人工写入在后续 repository 中必须先保存 `revisions/` 快照再原子替换。Phase 1 项目创建只产生 revision 1，不提供编辑命令。
