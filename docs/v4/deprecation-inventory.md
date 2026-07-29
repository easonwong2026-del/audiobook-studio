# v4 遗留代码退役清单

状态定义：`KEEP`、`ADAPT`、`DEPRECATE`、`REMOVE_AFTER_MIGRATION`、`REMOVE_NOW`、`UNKNOWN`。

| 对象 | 状态 | 理由 / 门禁 |
|---|---|---|
| `lib/text_importer.py` | ADAPT | 复用安全解析，v4 增加编码、hash 和不可变 source 元数据 |
| `repositories/_atomic.py` | ADAPT | 思路保留，v4 需要文本、目录和 fsync 边界 |
| `repositories/project_repo.py` | REMOVE_AFTER_MIGRATION | v3 项目仓储；迁移和回滚完成前必须保留 |
| `services/project_creation.py` | REMOVE_AFTER_MIGRATION | v3 创建链路；默认 UI 切换前保留 |
| `services/script_director.py` | ADAPT | AI 能力后续降级为 router/分析 adapter，不再控制项目创建 |
| `lib/queue.py` JSON 状态 | DEPRECATE | v4 高频任务状态进入 SQLite |
| `repositories/task_repo.py` | REMOVE_AFTER_MIGRATION | v3 JSON 任务仓储 |
| `lib/directed_synthesis.py` | ADAPT | 生产规则编译转入 planner，音频处理概念可复用 |
| `lib/tts_engine.py` | ADAPT | Phase 4 以真实签名和 profile 实现 adapter |
| 字符中点 OOM 拆分 | DEPRECATE | 改为任务级、token-aware、父子可追踪拆分 |
| v3 `structured_script.json` | REMOVE_AFTER_MIGRATION | 由 source/script/speakers/plan 分层替代 |
| v3 UI 接线 | REMOVE_AFTER_MIGRATION | Phase 5 切换且验证回滚后处理 |
| 未确认调用者的辅助脚本 | UNKNOWN | Phase 6 用静态扫描和运行时证据分类 |

Phase 1 不批量删除遗留代码。`REMOVE_NOW` 当前为空；任何删除都进入独立 Phase 6 分支和 PR。
