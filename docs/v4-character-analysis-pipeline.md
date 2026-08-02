# V4 AI-first 角色与剧本导演流水线

V4 项目创建现在按以下顺序自动执行。创建阶段不运行规则角色识别：

```text
导入原文、章节边界和坐标
  → AI 顺序阅读全书并滚动更新人物圣经
  → AI 直接读取原文生成剧本段落、对白/旁白/内心/引用分类
  → 本地坐标、覆盖率和 speaker ID 校验
  → AI 逐章全书审稿并返回修正补丁
  → 保存正式角色、剧本和声音绑定迁移结果
```

## 安全边界

新项目的 `speakers.json` 在 AI 运行前只有锁定的 `旁白`，剧本只有
`dialogue_type=unanalysed` 的原文 pending 区间。`SourceSegmenter.segment()` 仍保留
给旧离线测试和高级兼容功能，但不再是 V4 默认入口。

AI 是所有人物、对白归属、内心独白和引用判断的第一语义决策者；规则只负责 JSON
协议、坐标/原文精确匹配、顺序、重叠/遗漏、speaker ID、非法枚举和人工锁定保护。
未知 speaker ID、改写原文或覆盖率不足的响应直接失败并保留可恢复检查点。

人物圣经检查点保存在 `runtime/ai_first/book_understanding.json`，正式人物圣经保存在
`runtime/character_bible.json`；剧本导演和审稿检查点分别保存在
`runtime/ai_first/script_director.json` 与 `runtime/ai_first/script_review.json`。
分析摘要保存在 `runtime/analysis.json`。旧提取/路由检查点仍可由高级兼容功能读取，
但不会成为新主链路的数据源。

## 角色工作台

「角色与声音」默认展示整理好的正式角色卡片：正式名、别名、主要/次要角色、对白
数量、置信度和音色绑定状态。AI 未配置时项目仍会先落盘，并显示“继续 AI 分析”；
候选确认、合并、锁定、别名修改、人工指派和“重新进行 AI 剧本分析”保留在默认折叠
的“高级角色整理”中。

## 准确率评估

`services.v4_accuracy_evaluation.evaluate_v4_accuracy` 接受本地 ground-truth JSON：

```json
{
  "characters": ["周建国"],
  "dialogue": {"segment_000001": "speaker_id-or-name"}
}
```

CLI 用法：

```text
python tools/evaluate_v4_accuracy.py PROJECT_DIR GROUND_TRUTH.json
```

输出明确报告角色准确率、自动对白归属准确率、自动覆盖率和错误类别；不会通过把
unresolved 内容强行计入自动结果来提高指标。
