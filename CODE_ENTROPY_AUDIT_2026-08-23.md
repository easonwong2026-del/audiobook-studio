# Repository Entropy Audit — Safe Cleanup Round 1

日期：2026-08-23
Baseline：`19c943b1d9b4481a34aa7c552414b7f24afbd5c9`（当时最新 `origin/main`）
分支：`refactor/code-entropy-cleanup-r1`

## 范围和证据规则

本轮先在未修改的 baseline 上完成全仓 `rg`、`git grep origin/main`、AST 和 Git
历史审计，再执行删除。`vulture` 或“看起来旧”不构成删除证据。

删除门槛：

1. 无生产调用者；
2. 不是必须保留的兼容 API；
3. 新 UI 已有等价实现并成为唯一 live wiring；
4. 底层 Service / Repository、状态契约和磁盘格式不变；
5. 既有行为测试与新增结构测试共同覆盖新路径。

Baseline：

- Full suite：`1252 passed, 26 skipped`
- Windows selected workflow 同款集合：`329 passed`

## 当前 live authority

书架目录权威链为：

`ProjectRepository.list_project_summaries()`
→ `ProjectCatalogService.scan_hierarchy()` / `scan()`
→ `ui/project_catalog_handlers.py`
→ `ui/wiring/project_catalog_wiring.py`
→ `app.py` 内 `wire_project_catalog(...)`

`a0cfb24` / PR #45 同时引入统一 Project Catalog wiring 并移除旧项目页的管理事件
注册。当前 live UI 的打开、目录、备份、清理、Storage v3、Integrity、Archive、
Backup Restore 和 Recycle Bin 均注册到 `catalog_handlers.*`。

## 重点 symbol / file 分类

“current callers”记录 baseline 删除前状态。

| symbol/file | category | current callers | replacement | compatibility reason | delete_now | risk | evidence |
|---|---|---|---|---|---:|---|---|
| `app.scan_project_cleanup` | DUPLICATE | 仅定义，无生产/测试调用 | `catalog_handlers.scan_selected_cleanup` | 无 | yes | low | `a0cfb24` 删除旧 click wiring；新 wiring 绑定 `bookshelf_cleanup`；两者调用同一 `ProjectStorageService.scan_cleanup` |
| `app.execute_project_cleanup` | DUPLICATE | 仅定义 | `catalog_handlers.execute_selected_cleanup` | 无 | yes | low | 新 wiring 绑定 confirm；两者调用同一 tokenized `execute_cleanup` |
| `app.cancel_project_cleanup` | DUPLICATE | 仅定义 | `catalog_handlers.cancel_selected_cleanup` | 无 | yes | low | 新 wiring 绑定 cancel，项目磁盘不变契约相同 |
| `app.check_project_integrity` | DUPLICATE | 仅定义 | `catalog_handlers.check_selected_integrity` | 无 | yes | low | 新 wiring 绑定 Integrity；同一 `ProjectStorageService.check_integrity` |
| `app.repair_project_integrity` | DUPLICATE | 仅定义 | `catalog_handlers.repair_selected_integrity` | 无 | yes | low | 新 wiring 绑定 repair；同一 `ProjectStorageService.repair_integrity` |
| `app.create_project_backup` | DUPLICATE | 仅定义 | `catalog_handlers.create_selected_backup` | 无 | yes | low | 新 wiring 绑定 Backup；同一 `ProjectBackupService.create_backup` |
| `app.restore_project_backup` | DUPLICATE | 仅定义 | `catalog_handlers.restore_backup_global` | 无 | yes | low | 新 wiring 绑定 Backup Restore；同一 `ProjectBackupService.restore_backup` |
| `app.refresh_archived_projects` | DUPLICATE | 仅被两个旧 app 回收站 handler 调用 | `catalog_handlers.refresh_archived_projects_global` / `render_archived_projects` | 无 | yes | low | live Recycle Bin refresh 只接新 handler；表格和 archive ID 契约保留 |
| `app.restore_archived_project` | DUPLICATE | 仅定义；内部调用上述旧 refresh | `catalog_handlers.restore_archived_global` + management refresh | 无 | yes | low | live restore 链在 wiring 中随后统一刷新 catalog；底层 restore 不变 |
| `app.permanently_delete_archived_project` | DUPLICATE | 仅定义；内部调用上述旧 refresh | `catalog_handlers.permanently_delete_archived_global` + management refresh | 无 | yes | low | live delete 链保留 checkbox 二次确认并统一刷新 |
| `app.refresh_bookshelf` | DUPLICATE | 仅旧结构测试要求存在；无 live event | `catalog_handlers.render_bookshelf_rows` / `refresh_bookshelf_management_view_with_hierarchy` | 无 | yes | low | `refresh_overview` 明确拒绝 legacy 路径；live app load/navigation/creation 均走 catalog handler |
| `ProjectService.list_projects` | DUPLICATE | 仅 `app.refresh_bookshelf` 与 `tests/test_o4_bookshelf.py` | `ProjectCatalogService.scan` | 无；不是 MCP 方法 | yes | low | 删除 app caller 后只剩旧测试；新目录测试改走 Catalog |
| `ProjectRepository.list_projects` | DUPLICATE | 旧 Service + 一个 repository 测试 | `ProjectRepository.list_project_summaries` | 无 | yes | low | 新方法是 `ProjectSummary` 唯一书架摘要源，并支持 hierarchy/fallback |
| `lib.project_manager.list_projects` | DUPLICATE | 零调用（定义之外无 `pm.list_projects`） | 上述 Catalog authority | 无实际调用者 | yes | low | 全仓 alias 搜索无调用；仅此 wrapper 被删除，模块整体保留 |
| `domain/results.py` / `OperationResult` | DEAD | 零 import、零测试、零 `domain.__init__` export；设计文档有一处非调用提及 | 当前代码使用明确异常/结果 dict | 无历史兼容导出 | yes | low | Python `rg`、`git grep origin/main` 仅命中该文件自身；历史只在 `720dd25` 添加，未形成调用链；同步更新设计文档的原型说明 |
| `app.hide_project_from_list` | DEAD（deferred） | 仅定义 | 无严格等价新 handler；Archive 会移动磁盘，语义不同 | 无已证明契约 | no | medium | 虽零调用，但不满足“等价新实现”门槛，Round 1 保守保留 |
| `app.restore_project_to_list` | DEAD（deferred） | 仅定义 | 无严格等价新 handler；Recycle Bin restore 语义不同 | 无已证明契约 | no | medium | 与 hide 成对，不能把 archive/restore 误判为等价 |
| `app.migrate_project_copy` | DEAD（deferred） | 仅定义 | Storage v3 upgrade 为原位布局迁移，不等价于 copy-to-root | 无已证明契约 | no | medium | 零 live wiring，但磁盘语义不等价，本轮不删 |
| `app.select_project_from_bookshelf` | COMPAT | 无 live wiring | live 为 `catalog_handlers.select_bookshelf_row` | 源码明确标记 old integrations no-op；防止误接线写 `p_sel` | no | low | 返回 `gr.skip()`；结构测试继续证明 live select 走 catalog handler |
| `app.render_chapter_tree` | MISPLACED | `_open_chain_rest`、`_post_archive_reconcile` | 后续可迁移到 UI handler/component | 当前项目页章节树仍 live | no | medium | 两条 live `.then(render_chapter_tree, ...)`；删除会改变 UI |
| `ProjectService.list_project_summaries` | COMPAT | `mcp_server/tools/projects.py` | 不替换 | MCP public `list_projects` contract | no | high | 与已删除的 `list_projects` 名称相近但用途/返回格式不同，必须保护 |
| `mcp_server.tools.projects.list_projects` | COMPAT | MCP server registry/public tool | 不替换 | MCP public contract frozen | no | high | 继续调用 `ProjectService.list_project_summaries` |
| `app.py` | HOTSPOT | 主 UI composition / wiring | 后续拆分轮次 | 本轮禁止大重构 | no | high | 文件大但大量 live handler；只移除已证明 duplicate 的连续块 |
| `repositories/project_repo.py` | HOTSPOT | 全项目/生产/兼容路径 | 无 | storage v1/v2/v3 与 meta repair 受冻结范围保护 | no | high | 只删除旧 summary 方法；其余 resolver/schema/repair 均未动 |
| `ProjectRepository.list_project_summaries` read/repair path | HOTSPOT | `ProjectCatalogService.scan_hierarchy` | 无 | 当前 `_load_meta` 可能触发 `_repair_meta`；属于既有磁盘语义 | no | high | 文档称轻量只读，但当前实现可能读取完整 script 并写回 repair；仅记录，不在熵清理中改行为 |

## `lib/project_manager.py` compatibility wrapper 审计

禁止整体删除。该模块的 mutable `WORKSPACE_ROOT` / `LEGACY_ROOT` 仍由旧测试和
integration monkeypatch，`ProjectService.set_data_dir` 也会同步它们。

| symbol/file | category | current callers | replacement | compatibility reason | delete_now | risk | evidence |
|---|---|---|---|---|---:|---|---|
| `_repository` | COMPAT | 模块内全部 disk wrappers | `ProjectRepository` | 同步 mutable roots 后委托 canonical repo | no | high | 删除会破坏 wrapper root 兼容 |
| `_resolve_dir` | COMPAT | 无 in-repo caller | `ProjectRepository.get_project_dir` | 私有 legacy surface；未与本轮链条相关 | no | low | 不因 unused 单独扩展删除范围 |
| `scan_projects` | COMPAT | `tests/workflows/test_data_dir_switch.py`，可能旧 integration | `ProjectRepository.scan_projects` | mutable-root legacy API | no | medium | 数据目录切换兼容测试仍使用 |
| `create_project` | COMPAT | 多个 legacy/workflow tests | `ProjectCreationService` / Repository | 广泛 fixture/integration API | no | high | 多测试直接 `pm.create_project` |
| `open_project` | COMPAT | `services/synthesis.py`、`lib/snapshot.py`、`lib/progress.py` | `ProjectRepository.load_project` | **生产调用仍存在** | no | high | `services/synthesis.py` 明确 `import project_manager as pm` 并调用 |
| `load_snapshot` | COMPAT | legacy snapshot tests | `ProjectRepository.load_snapshot` | 测试/旧 integration API | no | medium | 现有契约测试直接调用 |
| `delete_project` | COMPAT | 无 in-repo caller | `ProjectRepository.delete_project` | legacy CRUD surface | no | medium | 未与已证明的 bookshelf summary 链相关 |
| `get_project_dir` | COMPAT | 大量 legacy/runtime tests | `ProjectRepository.get_project_dir` | mutable-root test/integration surface | no | high | 广泛直接调用 |
| `update_segment_status` | COMPAT | legacy workflow/progress tests | Repository method | 历史生产状态写入 API | no | high | 状态/磁盘语义在冻结范围 |
| `get_remaining` | COMPAT | project manager / synthesis lifecycle tests | Repository method | 恢复语义兼容 | no | high | done-but-missing-wav 契约受测试 |
| `_meta_path`, `_load_meta`, `_repair_meta`, `_save_meta` | COMPAT | 无 wrapper 外部 caller；repository 自有同名实现 live | Repository private methods | legacy private surface；不在 summary chain | no | medium | 单独清理可能影响外部 monkeypatch，留待专门兼容退役 |
| `get_synthesis_overrides` | COMPAT | project manager tests | Repository method | 与 setter 成对的 legacy persistence API | no | medium | 文件格式兼容受测试 |
| `set_synthesis_overrides` | COMPAT | `app.py` + tests | Repository method | **生产调用仍存在** | no | high | 合成参数持久化仍 live |
| `_project_status` | COMPAT | dataframe style contract tests | Repository method | 状态文字/颜色兼容 | no | medium | UI 状态词映射测试依赖 |
| `build_chapter_tree` | MISPLACED | `app.render_chapter_tree` + O4 tests | 后续 UI/repository split | **生产 UI 仍调用** | no | high | 当前 app 打开链 live |
| `get_synthesis_selections` | COMPAT | `app.py` + O5 tests | Repository method | **生产调用仍存在** | no | high | scope selection persistence |
| `set_synthesis_selections` | COMPAT | `app.py` + O5 tests | Repository method | **生产调用仍存在** | no | high | scope selection persistence |
| `build_role_choices` | MISPLACED | `ui/components/voice_binding.py` | 后续纯 UI helper | **生产 UI 仍调用** | no | medium | 不属于 dead project management |
| `build_bound_role_choices` | MISPLACED | `ui/components/voice_binding.py` + supplement tests | 后续纯 UI helper | **生产 UI 仍调用** | no | medium | 不属于 dead project management |
| `list_projects` | DUPLICATE | 零直接调用 | Project Catalog authority | 无 | yes | low | 本轮唯一删除的 compatibility wrapper |

## Confirmed deleted

- `app.py` 中 10 个已被 Catalog handlers 完整替代的 Project Management handlers：
  cleanup 3 个、integrity 2 个、backup/restore 2 个、Recycle Bin 3 个。
- `app.refresh_bookshelf`。
- 旧 summary chain：
  `ProjectService.list_projects` → `ProjectRepository.list_projects`；
  同时删除零调用的 `lib.project_manager.list_projects` wrapper。
- `domain/results.py` / `OperationResult`。

底层 Service、Repository 磁盘实现未删除；只是移除已经脱离 wiring 的重复入口。

## Retained compatibility

- `app.select_project_from_bookshelf`：明确的 old integration no-op。
- `ProjectService.list_project_summaries` 与 MCP `list_projects`：MCP public contract。
- `lib/project_manager.py` 除 `list_projects` 外全部保留，尤其保护
  `services/synthesis.py -> pm.open_project`。
- `domain/__init__.py` package marker：本轮不扩大为 package 删除。

## Deferred misplaced

- `app.render_chapter_tree` / `lib.project_manager.build_chapter_tree`。
- `lib.project_manager.build_role_choices` /
  `build_bound_role_choices`。

这些逻辑仍 live，只是层级不理想；不能以“位于 app.py / 旧模块”为由删除。

## Frozen hotspots

- `app.py` composition 与 runtime-related handlers。
- `repositories/project_repo.py` storage/meta repair 路径。
- `ProjectRepository.list_project_summaries -> _load_meta -> _repair_meta` 的潜在
  scan-write 行为。
- ProductionRuntime、TaskRepository、TTS engine/prewarm、Voice Cast、Storage
  resolver/migration、Merge/Assembly、QA/Repair、Export、MCP 均未改。

## Remaining cleanup candidates

- `hide_project_from_list` / `restore_project_to_list`：需先决定是否正式退役
  hidden-catalog 磁盘语义，不能用 Archive/Recycle Bin 冒充等价替代。
- `migrate_project_copy`：需先决定 copy-to-new-root 产品契约，Storage v3 原位迁移
  不是等价实现。
- `domain/__init__.py`：若未来确认没有任何外部 package import，可单独退役。
- `project_manager` 中只剩 test/external compatibility 的 private wrappers：需要专门
  deprecation/compatibility round，不能在本轮顺带删除。

## 测试证据

新增 `tests/test_project_catalog_authority.py`：

- Catalog scan 只委托 `ProjectRepository.list_project_summaries`；
- 旧三层 `list_projects` API 不存在；
- Project Catalog 所有 live actions 逐按钮映射到
  `ui.project_catalog_handlers`；
- app duplicate handlers 不会复活；
- legacy no-op 与 live `render_chapter_tree` 明确保留。

保留并复用：

- selected/opened isolation：`tests/test_project_catalog_handlers.py`、
  `tests/test_catalog_state_consistency.py`、
  `tests/test_windows_archive_dropdown_reconcile.py`；
- Archive/Restore/Recycle Bin：
  `tests/test_catalog_state_consistency.py`、`tests/test_project_storage.py`；
- Cleanup/Integrity：`tests/test_bookshelf_management_closure.py`、
  `tests/test_project_storage.py`；
- Backup Restore：`tests/test_project_backup.py`；
- Storage v3：`tests/test_storage_layout_v3.py`、
  `tests/test_storage_migration_rollback.py`；
- live wiring：`tests/test_app_wiring.py`、
  `tests/test_catalog_refresh_integration.py`。

---

# Repository Entropy Cleanup Round 2A — Project View / Chapter Tree Boundary Extraction

日期：2026-08-23
Baseline：`aa9151566abcb722cd3f29efe9dbd04d0a6ee62f`（PR #55 squash merge commit，当前最新 `main`）
分支：`refactor/project-view-boundary-r2a`

## 启动门槛与基线证据

PR #55 已确认 `MERGED`，其 `merge_commit_sha` 为 `aa9151566abcb722cd3f29efe9dbd04d0a6ee62f`，且该 commit 即当前 `origin/main`；不要求 squash 前的 PR head `d7b262c94ba064ca2554008ff5b97fd75a8c41b3` 成为 main ancestor。启动前工作树 clean，内容验证为 PR #55 的 Round 1 清理已包含在该 main commit 中。

Round 2A 修改前真实 baseline：

- Full suite：`1255 passed, 26 skipped, 76 warnings`，50.33s；coverage 81%（`18142` statements）。
- Windows selected workflow 同款集合（`PYTHONUTF8=1`）：`329 passed, 19 warnings`，9.92s。

## Chapter Tree 全仓引用审计

在未修改的 `aa9151566...` 基线上，对 Python、测试、MCP、scripts、docs、动态导入/属性访问和 monkeypatch 进行了 `rg`/AST 审计：

| symbol/file | category | current callers | replacement | compatibility reason | delete_now | risk | evidence |
|---|---|---|---|---|---:|---|---|
| `app.render_chapter_tree` | MISPLACED → extracted | `_open_chain_rest`、`_post_archive_reconcile` 两条 live `.then` | `ui.project_view_handlers.render_chapter_tree` | 无独立外部兼容契约；原 app callback 只委托 pm renderer | yes | medium | baseline `rg` 只有上述两条生产 wiring；Round 2A 后 app 无同名 FunctionDef，两个链仍以 `[p_sel] → [p_chapter_tree]` 接线 |
| `lib.project_manager.build_chapter_tree` | MISPLACED → deleted | 仅 `app.render_chapter_tree` 与 O4 tests；无 services/MCP/scripts/dynamic caller | `Project View handler` + `ProjectService.open_project` | 不属于 `services/synthesis.py` 等 compatibility surface | yes | medium | baseline 全仓 alias 搜索仅命中定义、app 委托、O4 tests/docs；新 handler 输出与 baseline fixture 完全一致 |
| `ui.project_view_handlers.render_chapter_tree` | live replacement | `_open_chain_rest`、`_post_archive_reconcile` | — | 当前 Project View 唯一 HTML owner | no | low | AST wiring tests + exact HTML/status tests |
| `services.ProjectService.open_project` | COMPAT/live read boundary | Project View handler 及既有 project flows | `ProjectRepository.load_project` | service/public compatibility API，保留 mutable-root/legacy load 语义 | no | high | handler 只走 service；禁止 app/ui 直接 open/json |
| `app.select_project_from_bookshelf` | COMPAT | old integrations only；live bookshelf 不调用 | `catalog_handlers.select_bookshelf_row` | 源码明确为 legacy no-op；selected/opened 隔离契约仍需保留 | no | low | Round 1 结构契约及现有 selected/opened tests |

## Round 2A 行为与边界证明

- 新 handler 保留空态 `<i>未打开项目</i>`、`<details>` 顺序、`chapter_identity.chapter_label`、完成计数、✅/❌/⬜ 状态图标、segment id/role/text（40 字预览）和异常 fallback。
- 同一临时项目 fixture 的 baseline `pm.build_chapter_tree()` 与 candidate `render_chapter_tree()` 输出逐字相同；测试固化 `p_progress` 的完整 HTML，并覆盖 empty/missing/done/failed/pending/count。
- `_open_chain_rest` 与 `_post_archive_reconcile` 仍按原事件顺序消费 `p_sel`，输出 `p_chapter_tree`；没有读取 `selected_project`，所以 bookshelf selection 不会加载或改写 Project View。
- Archive/open reconcile 的既有 state tests 保留；本轮只替换 callback owner，不改事件输入、输出、状态机或磁盘格式。
- `lib/project_manager.py` 仍保留全部其他 compatibility wrappers；特别是 `services/synthesis.py -> pm` import、Storage/legacy roots、Voice Cast 等均未改。

## Round 2A 变更清单

### Confirmed deleted

- `app.py::render_chapter_tree`（7 行旧委托函数）。
- `lib/project_manager.py::build_chapter_tree` 及其仅为该 renderer 服务的 `logging`、`chapter_identity` import（45 行实现/import 减少）。

### Added / moved ownership

- 新增 `ui/project_view_handlers.py::render_chapter_tree`，只通过 `ProjectService.open_project()` 读取，保留原 HTML 行为。
- 两条 app 刷新链只改为 `project_view_ui.render_chapter_tree`，不改变链顺序或契约。

### Retained compatibility

- `lib/project_manager.py` 未整体删除；`open_project`、snapshot、status、synthesis、role 等 wrappers 继续保留。
- `app.select_project_from_bookshelf` 继续作为 old-integration no-op。
- `ProjectCatalogService`/`project_catalog_handlers`/catalog wiring 和 Round 1 删除项未在本轮重做。

### Deferred / frozen

- `hide_project_from_list`、`restore_project_to_list`、`migrate_project_copy` 仍按 Round 1 结论 deferred。
- `app.py` 仍是 HOTSPOT；本轮未拆分其他 handlers，未触碰 ProductionRuntime、startup state machine、Storage v1/v2/v3、Merge/Assembly、QA/Repair、Export、Quick TTS、Voice Cast、MCP contract。

## Round 2A verification

- Targeted boundary/O4/catalog tests：`37 passed`。
- Candidate full pytest：`1261 passed, 26 skipped, 76 warnings`，51.32s；coverage `18119` statements / 81%。相对 baseline 多出的 6 个通过项来自本轮新增结构/行为测试；无 candidate-only failure。
- Windows selected workflow 同款集合：`329 passed, 19 warnings`，8.38s；与 baseline 同样无失败。
- `python -m compileall -q .`：pass。
- Ruff changed Python files `--select F`：pass（`All checks passed!`）。
- `git diff --check`：pass。

## Remaining cleanup candidates after Round 2A

- 仅保留 Round 1 deferred 项和 `project_manager` 中有真实 compatibility caller 的 wrappers；不要把“模块较大”或 vulture unused 作为删除理由。

---

# Repository Entropy Cleanup Round 2B — Voice Presentation Helpers Boundary Extraction

日期：2026-08-23
Baseline：`67a4b0bf7dbd6d893df7fea48f7f4e083a2d95da`（PR #56 squash merge commit，当前最新 `origin/main`）
分支：`refactor/voice-presentation-boundary-r2b`

## 启动与引用证据

PR #56 状态为 `MERGED`，merge commit 已进入 `origin/main`；未要求 Round 2A 原 head `ee6211fce8f2bbc90fab66b2b5e506ba5c05077d` 成为 main ancestor。main 内容验证通过：app 两条链使用 `project_view_ui.render_chapter_tree`、`ui/project_view_handlers.py` 存在、app 无 `render_chapter_tree` 定义、`lib.project_manager.build_chapter_tree` 已退出；启动前工作树 clean。

修改前 baseline：

- Full suite：`1261 passed, 26 skipped, 76 warnings`，51.02s；coverage `18119` statements / 81%。
- Windows selected workflow 同款集合：`329 passed, 19 warnings`，9.75s。
- A–I helper fixture 输出已在 `tests/test_voice_presentation_boundary.py` 中逐 tuple 记录，覆盖全未绑定、部分绑定、分类、多角色同分类、未绑定/未分类 tail、bound-only、description、name-only、空 metadata。

| symbol/file | category | baseline callers | replacement | compatibility reason | delete_now | risk | evidence |
|---|---|---|---|---|---:|---|---|
| `lib.project_manager.build_role_choices` | MISPLACED | 仅 `ui/components/voice_binding.py` 直接生产调用；无测试直接调用、无 MCP/scripts/re-export/dynamic caller | `ui.components.voice_binding.build_role_choices` | 未发现 external/public compatibility contract；原始 commit 只引入纯 UI helper | yes | low | 全仓 `rg` + AST/import inspection；唯一生产路径是 voice presentation module |
| `lib.project_manager.build_bound_role_choices` | MISPLACED | `ui/components/voice_binding.py`；`tests/test_supplement.py` 有一个内部测试直接调用 | `ui.components.voice_binding.build_bound_role_choices` | 测试是仓内契约，已迁移到 UI-owned helper；无 external/MCP/script caller | yes | low | qualified alias 搜索仅命中上述 UI caller、内部测试和历史 docs；无 re-export |
| `ui.components.voice_binding.format_role_choices` | live UI formatter | 测试及潜在 UI consumers | 内部直接调用同模块 `build_role_choices` | description/name formatting contract 保持 | no | low | G/H/I fixture 输出逐 tuple 等价 |
| `ui.components.voice_binding.format_bound_role_choices` | live UI formatter | `app.refresh_supplement_roles`、voice UI tests | 内部直接调用同模块 `build_bound_role_choices` | Voice Cast / supplement dropdown contract 保持 | no | medium | existing voice workspace + supplement tests |

## Caller graph

修改前：

```
app.refresh_supplement_roles
  -> ui.components.voice_binding.format_bound_role_choices
     -> _pm.build_bound_role_choices
        -> lib.project_manager.build_bound_role_choices

ui.components.voice_binding.format_role_choices
  -> _pm.build_role_choices
     -> lib.project_manager.build_role_choices

tests/test_supplement.py
  -> pm.build_bound_role_choices   [internal test contract only]
```

修改后：

```
app.refresh_supplement_roles
  -> ui.components.voice_binding.format_bound_role_choices
     -> ui.components.voice_binding.build_bound_role_choices

ui.components.voice_binding.format_role_choices
  -> ui.components.voice_binding.build_role_choices

tests/test_supplement.py / boundary fixtures
  -> ui.components.voice_binding.build_bound_role_choices
```

`ui/components/voice_binding.py` 已完全退出对 `lib.project_manager` 的该项依赖；`lib/project_manager.py` 仍保留其他 compatibility wrappers，特别是 `services/synthesis.py -> pm`，本轮没有处理。

## Behavior equivalence

- `build_role_choices`：category precedence、明确分类先行、未绑定/未分类 tail 顺序、同 category 内排序、原始 role value 全部与 baseline 一致。
- `build_bound_role_choices`：只返回 truthy binding，保持 `script["voices"]` 顺序，label/value 一致。
- `format_role_choices`：description 优先、name fallback、空 metadata fallback 以及 category label 均逐 tuple 一致。
- `format_bound_role_choices`：bound filtering、description/name formatting、原始 role value 均逐 tuple 一致。
- 未修改 Voice Cast bind/unbind、`voice_bindings.json`、role_categories 持久化、reference audio、supplement synthesis、Quick TTS、engine 或 runtime。

## Round 2B change summary

### Confirmed deleted

- `lib/project_manager.py::build_role_choices`。
- `lib/project_manager.py::build_bound_role_choices`。
- `ui/components/voice_binding.py` 的 `from lib import project_manager as _pm`。

### Added / moved ownership

- `ui/components/voice_binding.py` 直接拥有两个纯 presentation helper。
- `format_role_choices` / `format_bound_role_choices` 改为同模块直接调用。
- `tests/test_supplement.py` 改为验证 UI-owned helper；新增 A–I baseline/candidate fixture 和 ownership tests。

### Retained compatibility

- `lib/project_manager.py` 其他 wrapper 全部保留。
- `app.select_project_from_bookshelf`、Round 1/2A deferred candidates 保持不变。
- Voice Cast persistence/workflow contracts 保持不变。

### Deferred / frozen

- `hide_project_from_list`、`restore_project_to_list`、`migrate_project_copy`、`select_project_from_bookshelf`。
- startup/runtime、Storage、Catalog hierarchy、Merge/Assembly、QA/Repair、Export、MCP、Voice Cast workflow 和依赖版本。

## Round 2B verification

- Targeted voice presentation/workspace/supplement tests：`59 passed, 1 skipped, 20 warnings`。
- Candidate full pytest：`1272 passed, 26 skipped, 76 warnings`，51.33s；coverage `18098` statements / 81%。相对 baseline 多出的 11 个通过项来自本轮 A–I 行为/结构测试；无 candidate-only failure。
- Windows selected workflow 同款集合：`329 passed, 19 warnings`，8.30s；warnings 数量与 baseline 一致。
- `python -m compileall -q .`：pass。
- Ruff changed Python files `--select F`：pass。
- `git diff --check`：pass。
