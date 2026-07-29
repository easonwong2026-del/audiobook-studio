# v4 实施计划

## Phase 0：设计与审计

冻结数据边界、当前调用链、IndexTTS2 运行时假设、迁移策略和退役清单。

## Phase 1：核心数据层（本 PR）

实现严格领域模型、本地 source import、确定性基础分段、speaker 初始目录、原子项目仓储、runtime.db migrations、项目创建应用服务、fake 边界和测试。v3 默认行为不变。

## Phase 2：角色路由

实现 `speaker-routing-v1`、真实 router adapter、人工确认和增量重路由。协议不传原文、情绪或 TTS 参数。

## Phase 3：合成规划

把 script、speaker、voice、performance、pronunciation 和 profile 编译为有 revision 的 synthesis plan；计算稳定 cache key。

## Phase 4：IndexTTS2 与 OOM 恢复

基于真实 Windows 安装审计实现 adapter，验证 5070 Ti Laptop 12GB profile，任务级重试、递归拆分、显存清理和受控引擎重启。

## Phase 5：迁移与 UI

提供 v3→v4 dry-run/copy migration、双读兼容、回滚验证，再逐步切换默认 UI。

## Phase 6：遗留退役

在独立分支和 PR 中按退役清单删除已无调用者的代码、重复配置和旧入口。必须先通过迁移与回滚门禁。

## PR 与风险门禁

| Phase | 合并前门禁 | 主要回滚 |
|---|---|---|
| 0/1 | schema/position/atomic/DB migration + v3 全回归 | 不启用 v4 reader |
| 2 | ID-only protocol、checkpoint resume、manual lock | 关闭 router adapter |
| 3 | 拆并边界与局部失效 property tests | 删除派生 plan |
| 4 | Windows provenance/benchmark、OOM/断点/音频 QA | 回到 Fake TTS |
| 5 | v3 copy migration、双读、备份恢复 | 默认入口切回 v3 |
| 6 | 无调用者证据和完整回滚演练 | revert 独立退役 PR |

最大风险依次是原文坐标漂移、角色错误覆盖人工结果、计划失效范围过大、OOM 无限拆分、缓存误命中、音频格式不一致和迁移破坏旧项目。每项必须在对应 Phase 有自动测试与可关闭开关。
