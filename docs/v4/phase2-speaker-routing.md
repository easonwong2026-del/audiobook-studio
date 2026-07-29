# Phase 2：角色路由

## 边界

本阶段只处理语义片段到 speaker 的归属，不生成原文副本、情绪、韵律、voice 或 TTS 参数。v3 `ScriptDirectorService` 和默认 UI 不变；新链路直接消费 v4 `script.json` 与 `speakers.json`。

```text
SourceSegmenter 规则确认
  → unresolved dialogue
  → bounded context + segment IDs
  → DeepSeek/OpenAI speaker-routing adapter
  → strict speaker-routing-v1 validation
  → checkpoint
  → local speaker ID mapping
  → review/lock
```

## 协议

响应只允许：

```json
{
  "schema_version": "speaker-routing-v1",
  "assignments": [
    {"segment_id": "segment_000002", "speaker": "林晚"},
    {"segment_id": "segment_000005", "speaker": null}
  ]
}
```

- 未知、重复 segment ID 或额外字段使当前批次失败。
- 缺失 assignment 与 `speaker: null` 均保留 unresolved，不使全书失败。
- adapter 只收到当前批次 ID 和有限上下文。
- 已 confirmed/manual 的片段不会进入远程批次。
- 本地按显示名和 aliases 查找 speaker；新名字映射为稳定 speaker ID。

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
