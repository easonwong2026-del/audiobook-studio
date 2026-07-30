# Phase 3：TTS 合成计划

## 输入与输出

Planner 只消费 source、script、speakers、voices、performance、pronunciation 和 TTS profile，输出可重建的 `audiobook-synthesis-plan-v1`。它不调用真实 TTS，也不写运行状态。

每个 plan 记录 source SHA 与六类 revision。task 使用稳定 ID、实际朗读文本、原 segment/坐标、speaker/voice、continuation、拼接参数和输入指纹。semantic segment 与 task 是不同模型，支持一对多拆分和多对一合并。

## 文本测量与拆分

`TextMeasurer` 是可替换协议；内置字符和保守 token 估算。profile 明确选择 metric，planner 拒绝不匹配的 measurer。

拆分顺序为段落、句末、分号、逗号、冒号、安全字符；所有结果不超过 profile maximum。安全回退避免 Unicode 组合字符中间和英文单词中间，除非没有可行边界。长 segment 子任务保持 speaker/voice/设置，第二块起标记 continuation，并使用短停顿。

## 短片段合并

仅合并同章节、同 speaker、同 voice、非 continuation、均低于 minimum 且合并后不超 maximum 的相邻任务。unresolved 和未绑定 voice 的 segment 不进入 plan，而是在 PlanningResult 中报告。合并保留 source gap，使段落空白不丢失。

## 指纹与局部失效

输入指纹包含 engine/model、推理 options、TTS 自动情绪设置、voice fingerprint、actual text、发音和人工 performance。task ID 基于章节、segment IDs 和 part index，避免前面任务拆分导致全书 ID 漂移。

`InvalidationService` 比较旧新计划：

- task ID 与输入指纹相同：复用；
- 同 ID 指纹变化：旧 task stale，新 task 待生成；
- 旧 ID 消失：旧 task stale；
- 新 ID 出现：新 task 待生成；
- 仅相关章节成品 stale。

profile 的纯拆分阈值 revision 变化不会无条件改变缓存指纹；只有边界或真实推理输入变化的任务失效。

## 持久化和预览

`ProductionRepository` 原子写入四类生产输入和派生 plan，并在覆盖前保存 `revisions/production-*` 快照。`plan_preview.py` 和隔离预览 UI 只读取 plan，不执行 TTS。Phase 5 再接入默认 v4 project shell。
