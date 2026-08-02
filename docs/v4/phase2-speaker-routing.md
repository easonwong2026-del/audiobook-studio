# Phase 2：角色路由

## 边界

本阶段只处理语义片段到 speaker 的归属，不生成原文副本、情绪、韵律、voice 或 TTS 参数。v3 `ScriptDirectorService` 和默认 UI 不变；新链路直接消费 v4 `script.json` 与 `speakers.json`。

```text
SourceSegmenter 规则确认
  → unresolved dialogue
  → bounded context + segment IDs
  → DeepSeek/OpenAI speaker-routing-v2 adapter
  → strict allowed-speaker validation
  → checkpoint
  → local speaker ID mapping
  → review/lock
```

## 协议

请求先提供正式角色和别名白名单，响应只允许：

```json
{
  "schema_version": "speaker-routing-v2",
  "assignments": [
    {
      "segment_id": "segment_000002",
      "speaker_id": "speaker_xxx",
      "candidate_name": null,
      "confidence": 0.93
    },
    {
      "segment_id": "segment_000005",
      "speaker_id": null,
      "candidate_name": "新人物",
      "confidence": 0.42
    }
  ]
}
```

- 未知、重复 segment ID 或额外字段使当前批次失败。
- 缺失 assignment、`speaker_id: null` 与低置信度均保留 unresolved，不使全书失败。
- adapter 只收到当前批次 ID 和有限上下文。
- 已 confirmed/manual 的片段不会进入远程批次。
- `speaker_id` 必须属于请求白名单；新名字只能进入 `character_candidates.json`，不得映射为正式 speaker ID。

角色候选使用独立的 `character-extraction-v1` 协议逐章提取，再按同名和明确别名合并；每个候选至少保留一条原文证据，人工确认后才写入 `speakers.json`。

## 检查点

`runtime.db.routing_batches` 记录 source SHA、script revision、provider、model、batch ID、segment IDs、assignment、状态、次数和错误。它不保存请求上下文、整本原文或 API key。

完成批次不会重复请求；失败批次可重试；异常退出遗留的 running 批次恢复为 pending。批次错误隔离，不回滚其他已完成批次。

## 人工审核

`SpeakerReviewService` 提供：

- 只列出 unresolved；
- 单个或批量指定已有 speaker；
- 创建并可锁定新 speaker；
- 合并角色并保留旧显示名/aliases；
- 人工 assignment 标记 `speaker_source=manual`；
- script 与 speakers revision 递增。

`ui/v4_speaker_review.py` 提供严格 handler，`ui/pages/v4_speaker_review_page.py` 提供隔离的 Gradio 审核面板（列表、已有/新建角色、锁定和批量应用控件）。Phase 2 不把未完成的 v4 工作流接入默认 v3 导航；Phase 5 的 v4 project shell 负责传入项目 state 和显示该 group。

## 不在本阶段

- synthesis plan、voice/performance/pronunciation 失效传播执行；
- IndexTTS2、任务执行、音频缓存和章节拼接；
- v3 项目迁移或默认 UI 切换。
