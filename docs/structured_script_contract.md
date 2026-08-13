# structured_script.json 外部 Agent 协议

Audiobook Studio 不分析小说原文。外部 Agent 或 Skill 负责理解原文，并交付一个
`structured_script.json`；工作台只负责离线检查、项目管理、角色声音绑定、合成、
试听质检和导出。

## 顶层结构

规范入口是一个 JSON 对象：

```json
{
  "version": "3.0",
  "project_name": "可选的项目目录名",
  "meta": {},
  "voices": {},
  "chapters": []
}
```

`meta`、`voices`、`chapters` 是必需字段。`project_name` 和 `version` 是可选的。
为兼容已有 V3 文件，读取器仍接受 `characters`/`roles`/`cast`/`speakers` 作为
`voices` 的明确别名，接受 `sections`/`episodes`/`scenes` 作为 `chapters` 的明确
别名；新文件应使用规范字段。

## meta

`meta` 必须是对象。常用字段是：

| 字段 | 要求 |
| --- | --- |
| `title` | 作品名；缺失时工作台使用项目名作为展示回退 |
| `author` | 作者；缺失时显示“未填写” |
| `total_chapters` | 可选整数；存在时必须等于实际章节数 |
| `total_segments` | 可选整数；存在时必须等于实际片段数 |

工作台不会改写原始 JSON，也不会根据统计数字反向补齐片段。

## voices

`voices` 是角色名到角色描述对象的映射。角色名不能为空；描述字段可选，例如：

```json
"voices": {
  "旁白": {"description": "沉稳叙事"},
  "小雨": {"description": "清亮女声"}
}
```

每个片段的 `role` 或 `speaker` 必须精确命中这里的角色名。工作台不会自动创建、
合并或猜测角色；未知角色是阻止导入的错误。是否定义 `旁白` 会在导入预览中明确显示。

## chapters 与 segments

`chapters` 必须是非空数组；每章是对象，包含非空唯一的 `id`、非空 `title` 和非空
`segments` 数组。章节顺序和数组顺序就是合成与导出的顺序。

每个 `segment` 的核心字段如下：

| 字段 | 必填/可选 | 说明 |
| --- | --- | --- |
| `id` | 必填 | 全书唯一、非空；用于合成缓存、状态和质检跳转 |
| `role` 或 `speaker` | 必填 | 必须命中 `voices`；V3 读取器兼容两种写法 |
| `text` | 必填 | 非空原文；工作台保留其内容，不负责重写 |
| `emotion` | 可选 | 缺失按 `neutral`；支持 `neutral`、`angry`、`happy`、`sad`、`excited`、`whisper`、`cold`、`confident`、`fearful`、`hesitant`、`tense` |
| `emo_alpha` | 可选 | 情绪强度，`0.0–1.0`；也兼容 `emotion_strength` 或 `delivery.intensity` |
| `speech_rate` | 可选 | 语速，`0.7–1.5`；也兼容 `delivery.speed` |
| `pitch` | 可选 | 音高标注，`-12–12`；也兼容 `delivery.pitch` |
| `delivery` | 可选 | 可含 `speed`、`pitch`、`intensity`、`breath` |
| `pause_before` / `pause_after` | 可选 | 片段前后停顿，整数毫秒 `0–3000` |
| `pauses` | 可选 | 片段内停顿对象数组；每项 `position` 为文本字符位置，`duration` 为整数 `0–3000` 毫秒；`type` 可选且只能是 `pause_short`、`pause_long` 或 `pause_think` |
| `pinyin_hints` | 可选 | 传给 V3 TTS 的多音字提示对象 |

合成、章节拼接、字幕和导出依赖 `id`、`role`/`speaker`、`text` 及上述 V3 演绎
字段。片段顺序不会按角色或情绪重新排序。

## Canonical Contract 与 TTS Adapter 边界

`structured_script.json` 是与模型无关的 Canonical Contract。接入或切换
IndexTTS 版本不得改变字段名称、字段含义、默认值、段落顺序或原始 JSON；模型版本
差异只能由 Backend Adapter 在调用边界转换。

Adapter 的每次合成都应保留结构化映射报告，至少区分：

- `mapped`：直接映射到当前引擎的字段；
- `approximated`：通过等价或近似参数实现的字段，例如 IndexTTS 2.5 将 Canonical
  `speech_rate` 转为 `duration_factor`；
- `unsupported`：当前引擎不具备的能力，例如未提供 pitch/breath 控制；
- `ignored`：输入存在但当前调用没有可安全转换的值，例如未匹配到文本位置的
  `pinyin_hints`。

这些结果必须进入 runtime trace 或任务诊断，不能静默丢失。IndexTTS 2.5 的
`<text|pronunciation>`、`lang`、`duration_factor` 等调用格式只存在于 Adapter 内部；
Canonical JSON 保持原样。缓存、任务快照、质检 revision 和导出 manifest 必须同时带有
冻结的 `engine_identity`，避免 UI 显示新引擎而实际复用旧引擎音频。

## 导入结果

检查 JSON 时，工作台会显示作品、作者、章节数、片段数、角色数、旁白是否存在、未知
角色、校验状态、一致性状态、warning 数量和 error 数量。

以下情况拒绝导入：JSON 语法/编码错误、顶层不是对象、缺少或类型错误的 `meta`/
`voices`/`chapters`、空章节/片段、缺少 ID、重复章节/片段 ID、空文本、未知角色、
非法情绪、速度/音高/强度/停顿越界、非法 `pause.type`，以及 `meta` 中声明的统计数不一致。

章节 ID 可以是数字或字符串，但必须非空且全书唯一；片段 ID 必须是非空字符串或可
稳定转换为字符串的值，并且同样全书唯一。`characters`/`roles`/`cast`/`speakers`
和 `sections`/`episodes`/`scenes` 只是规范字段缺失时的明确兼容别名，不会改变上述
校验规则。

以下情况只产生 warning 并允许创建：片段过短或过长、相邻语速跳变、未使用角色、
角色名疑似别名，以及其他不改变结构合法性的质量提示。warning 会在创建前和成功消息中
展示。

创建前还会检查目标项目槽位。已有合法项目、Legacy 项目、不完整目录、临时目录和损坏
目录都不会被覆盖或自动删除；异常目录只能由用户明确移动到回收站。

## 离线校验

外部 Agent 交付文件前可运行：

```bash
python tools/validate_structured_script.py path/to/structured_script.json
```

该命令复用 `StructuredScriptImportService.inspect()`，与 UI 使用同一套
`lib.script_loader.validate_script()` 和 `services.script_consistency.check_script_consistency()`；
它不调用 AI、不读取 API Key、不连接网络。合法文件返回 `0`，存在错误返回非零退出码。

合法示例位于 `tests/fixtures/structured_script_valid.json`。
