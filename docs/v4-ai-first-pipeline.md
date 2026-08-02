# V4 AI-first 剧本导演链路

V4 新项目在 AI 调用前只保存原文、章节范围、原文 SHA 和一个 pending 区间。
`speakers.json` 此时只有锁定的 `旁白`；`SourceSegmenter.segment()` 的规则结果不再
进入新项目主路径。

```text
source/source.txt
  -> chapter ranges / transport chunks
  -> BookUnderstandingService（滚动人物圣经）
  -> AIScriptDirectorService（AI 原文分段 + 绝对坐标）
  -> AIScriptReviewService（逐章全书审稿补丁）
  -> V4 speakers/script/voices
```

## 持久化边界

- `runtime/ai_first/book_understanding.json`：逐章人物记忆和完成状态。
- `runtime/ai_first/script_director.json`：逐章导演批次和完成状态。
- `runtime/ai_first/script_review.json`：逐章审稿补丁。
- `runtime/character_bible.json`：最终人物圣经，是机器角色的唯一来源。
- `runtime/pending_voice_migrations.json`：重分析时无法安全迁移的音色绑定。
- `revisions/ai-analysis-*`：重分析前的 script、speakers、voices 快照。

规则只验证 JSON、坐标、文本精确匹配、顺序、覆盖率、speaker ID 和人工锁定保护。
原文不会写入日志，Provider 错误只保留脱敏摘要。

## 推理模式

V4 人物圣经、剧本导演和全书审稿使用带任务名的 Provider 请求。DeepSeek 仅在
模型 ID 或显式环境配置表明支持 reasoning 时发送 `thinking: enabled`；普通连接、
格式任务和不支持该字段的模型会省略它。V3 旧批协议保留独立的 disabled 字段，
不影响 V4 请求。

## 关闭服务

设置页的「关闭服务」只操作当前实例的 owner PID：先停止接收新任务、保存运行状态，
再停止 V4/V3 worker、关闭 SQLite 使用、释放 IndexTTS2/CUDA 和 Gradio。Windows
备用入口为 `stop.bat`（等价于 `python launcher.py --stop`），不会扫描端口或杀死
其他 Python、WorkBuddy、Codex 进程。
