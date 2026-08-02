# V4 角色分析流水线

V4 项目创建现在按以下顺序自动执行：

```text
本地导入与原文坐标
  → 逐章角色提取（character-extraction-v1）
  → 全书角色/别名统一（character-consolidation-v1）
  → 高可信角色自动确认（默认 confidence >= 0.90）
  → 按章节/场景/连续对白组路由（speaker-routing-v2）
  → 一致性复查与待确认摘要
```

## 安全边界

AI 只能返回候选、候选 ID 和已存在的 `speaker_id`。正式角色仍由
`CharacterConsolidationService` 的规则写入；未知候选 ID、未知角色 ID、低置信度、
别名冲突和仅名称相似的合并都会被拒绝或保留为待确认。人工确认、锁定和手工修改
优先于自动结果。

全书统一输入保存在 `runtime/character_consolidation/consolidation.json`，包含源指纹、
候选输入指纹和严格响应。逐章提取检查点保存在
`runtime/character_extraction/checkpoints.json`；对白路由检查点继续使用
`runtime/runtime.db`。分析摘要保存在 `runtime/analysis.json`，一致性报告保存在
`runtime/character_consistency.json`。

## 角色工作台

「角色与声音」默认展示角色卡片：正式名、别名、主要/次要角色、对白数量、置信度和
音色绑定状态。AI 未配置时项目仍会先落盘，并显示“继续 AI 分析”；候选确认、合并、
锁定、别名修改、人工指派和重跑按钮保留在默认折叠的“高级角色整理”中。

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
