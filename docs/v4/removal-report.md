# Phase 6 Legacy Retirement 报告

## 退役范围

Phase 6 只删除已经被严格 v4 实现替代、没有应用运行时调用者的占位层：

| 删除对象 | 删除原因 | 替代实现 |
|---|---|---|
| `domain/v4/interfaces.py` | Phase 1 的宽松 Router/TTS 协议已与真实服务签名脱节 | `services/speaker_routing_service.py` 的严格协议与 `tts/base_adapter.py` |
| `domain/v4/fakes.py` | 只被自身测试引用，无法代表真实 Phase 2/4 边界 | 路由、规划及执行测试中的协议级 fake |
| `ui/v4_speaker_review.py` | 无 Gradio 页面入口的中间占位层 | `ui/pages/v4_speaker_review_page.py` 与 `SpeakerReviewService` |
| `tests/v4/test_fakes.py` | 仅验证已退役 fake | 真实路由、执行与工作区回归测试 |

删除前后均检查 Python import、函数调用、Gradio 接线、配置字符串、测试引用和迁移器引用。删除后对上述符号的全仓搜索为空。

## 删除函数和配置

- 删除 `FakeSpeakerRouter.route`、`FakeTtsAdapter.synthesize`、`unresolved_review_rows`、`assign_review_rows` 等无运行时入口的占位函数。
- 将 5070 Ti Profile 的 Worker 配置名统一为 Prompt 规定的 `restart_worker_after_tasks` 与 `restart_on_vram_growth_mb`。
- 执行器保留旧键名读取作为项目级 Profile 回滚兼容，但新项目不再写旧键。
- 没有删除仍被 v3 兼容链使用的环境变量或配置；v4 adapter 只消费自身 Profile，不让旧导演参数静默改变 v4 计划。

## 新增的运行安全收口

- runtime schema v5 新增 `synthesis_metrics`，逐次记录 task ID、字符/Token 数、voice ID、自动情绪、耗时、音频时长、缓存命中、错误类型及可用时的 CUDA 显存指标。
- 指标不保存 `actual_text`、API Key、音色内容或模型内部输出。
- Worker 仅在任务边界安全关闭：达到任务阈值且显存高于基线、显存增长越界、空闲显存过低、不可恢复 CUDA 错误或连续两次可恢复 CUDA 错误。
- 已完成状态和内容寻址缓存位于 SQLite/磁盘中，Worker 重启不会丢失。
- v4 队列页面现显示章节进度、task 总数、完成、缓存命中、失败、stale，以及运行中任务的角色、长度、尝试次数和拆分深度。

## 明确保留的兼容层

以下模块仍有 v3 页面、迁移或回滚调用者，不能在本阶段删除：

- `repositories/project_repo.py`
- `services/project_creation.py`
- `services/script_director.py`
- `repositories/task_repo.py`
- `lib/queue.py`
- `lib/tts_engine.py`
- v3 Gradio 项目、导演、生产和导出页面

新建书稿默认走 v4；v3 写入入口只保留给显式旧 JSON 工作流和既有项目。v3 与 v4 的任务状态不会在同一项目内混用：v3 使用兼容仓储，v4 只使用 `runtime/runtime.db`。

删除条件是：受支持的 v3 项目均完成迁移、用户不再需要只读/回滚入口、动态调用审计为空，并经过独立发布周期验证。

## 依赖与代码量

本阶段没有为了制造删除量而移除依赖。Gradio、SQLite、音频和 v3 兼容依赖仍有有效调用者。

相对 Phase 5 基线，本阶段删除 5 个占位实现/测试文件（共 162 行），并增加安全审计、运行监控、迁移和回归代码。最终准确变更量以 PR #19 的 GitHub diff 为准。

## 验证

必须在 Phase 6 精确 head 运行：

```bash
python -m pytest -q
python -m compileall .
git diff --check
ruff check <v4 与本阶段修改文件>
```

同时检查：

```bash
rg -n 'domain\.v4\.interfaces|FakeSpeakerRouter|FakeTtsAdapter' --glob '*.py'
rg -n 'ui\.v4_speaker_review|unresolved_review_rows|assign_review_rows' --glob '*.py'
```

目标 Windows 的启动、真实 IndexTTS2 签名、RTX 5070 Ti 12GB 显存与音质仍按 `indextts-runtime-audit.md` 和 `indextts2-benchmark.md` 单独验收，不使用当前 Mac 的结果代替。

## 回滚

Phase 6 是独立堆叠 PR。代码回滚应 revert Phase 6 提交或关闭 PR #19；不要覆盖用户项目。runtime schema v5 只新增表，旧 v4 可执行文件会忽略该表，因此项目数据仍可回退读取。
