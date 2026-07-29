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
| `domain/v4/interfaces.py` Phase 1 placeholder | REMOVE_NOW | 无运行时调用者；已由严格 routing adapter 和 `tts/base_adapter.py` 替代 |
| `domain/v4/fakes.py` Phase 1 placeholder | REMOVE_NOW | 仅自测调用；Phase 2/4 测试已有协议兼容 fake，旧签名不再匹配实际服务 |
| `ui/v4_speaker_review.py` UI-neutral placeholder | REMOVE_NOW | 仅自测调用；集成工作流已直接调用 `SpeakerReviewService` |

Phase 1 不批量删除遗留代码。上述 `REMOVE_NOW` 项只在独立 Phase 6 分支删除。

## Phase 6 caller evidence

执行：

```bash
rg -n 'domain\.v4\.interfaces|SpeakerRouter|TtsAdapter' --glob '*.py'
rg -n 'FakeSpeakerRouter|FakeTtsAdapter' --glob '*.py'
rg -n 'ui\.v4_speaker_review|unresolved_review_rows|assign_review_rows' --glob '*.py'
```

删除前结果仅命中定义文件及其专用测试，没有应用调用者。删除后结果为空。

v3 `ProjectRepository`、`ProjectCreationService`、`ScriptDirectorService`、`TaskRepository`、`lib.queue`、`lib.tts_engine` 和旧 Gradio 页面仍有明确兼容调用者，维持 `REMOVE_AFTER_MIGRATION`/`ADAPT`，本阶段不删除。v4 默认启用并不等于所有用户项目已迁移；保留这些路径是回滚要求，而不是遗漏。
