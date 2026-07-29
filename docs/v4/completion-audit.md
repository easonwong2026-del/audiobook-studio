# v4.0 完成审计

## 需求证据矩阵

| 需求 | 实现证据 | 验证证据 |
|---|---|---|
| 本地唯一原文 | `SourceImportService`、source metadata/SHA | source import/hash/encoding tests |
| 无损语义坐标 | `SourceSegmenter`、`ScriptDocument.validate` | quote、chapter、gap、overlap、round-trip tests |
| AI 仅角色路由 | `speaker-routing-v1`、专用 provider adapter | unknown/duplicate/missing/batch isolation tests |
| unresolved 不阻止创建 | v4 creation + routing/review | creation/routing tests |
| 声音/表演/发音独立 | production models/repository | serialization/snapshot tests |
| segment/task 解耦 | `SynthesisPlanner`/PlanTask | one→many、many→one tests |
| 可配置长度与安全拆分 | TTS profile + TextMeasurer | punctuation/Chinese/metric tests |
| 局部失效 | stable task ID/input fingerprint/runtime sync | voice/text/performance/profile reuse tests |
| SQLite 事务恢复 | runtime migrations/claim/recover | v1→v5 migration、running recovery tests |
| cache 校验 | AudioCacheRepository | hit/corruption/SHA tests |
| OOM 仅拆当前 task | SynthesisExecutor parent/child | bounded depth/leaf resolve tests |
| 常驻 IndexTTS2 | signature-aware adapter/RLock | one-engine fake signature tests |
| 无敏感文本运行监控 | `synthesis_metrics` + RuntimeMonitor | metric/cache/VRAM restart tests |
| 章节装配 | ChapterAssembler | sample rate/channel/pause/fingerprint tests |
| v3 copy migration | V3ToV4MigrationService | backup/fidelity/idempotency/failure tests |
| v4 默认 UI 与 v3 回滚 | app v4 shell + legacy pages | glue/page/compatibility tests |
| v4 export | V4ExportService | ordered chapter WAV export test |

## 尚需目标 Windows 实机证据

仓库实现和无 GPU CI 不能证明以下外部事实：

- `D:\AudiobookStudio\project\index-tts` 的实际 commit SHA/dirty 状态；
- 该 checkout 的真实 constructor/infer 签名；
- RTX 5070 Ti Laptop 12GB 上 40/60/80/100/120 token benchmark；
- 峰值/空闲 VRAM、长时稳定性和实际音质。

这些不是以当前 Mac 结果替代的“已完成项”。运行 `tools/benchmark_indextts2.py` 后，把 JSON 放入项目 `runtime/benchmarks`，再将 profile 的 `provisional-unbenchmarked` 改为 verified。

## 自动门禁

最终分支必须通过：

```bash
python -m pytest -q
python -m compileall .
git diff --check
ruff check <v4 changed files>
```

并检查所有堆叠 PR 的精确 head Actions。全仓 Ruff 的旧 v3 findings 单独记录；本次没有把风格批量改写冒充架构退役。
