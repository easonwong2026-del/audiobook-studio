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

---

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

---

# Repository Entropy Cleanup Round 3B — Project Page Residual Boundary Cleanup

日期：2026-08-23

Baseline：`137577f438f9999f96b1118f932dd1686f77a479`（PR #57 squash merge，当前最新 `origin/main`）

分支：`refactor/project-page-residual-r3b`

## 启动与审计证据

PR #57 已确认 `MERGED`，`merge_commit_sha` 为上述 baseline，且已进入当前
`origin/main`；没有要求 squash 前的 PR head `933abc42...` 成为 main ancestor。
启动时工作树 clean。Round 2B 内容验证通过：`ui/components/voice_binding.py`
拥有 role presentation helpers，`lib/project_manager.py` 不再定义它们。

在修改前对 Python、tests、scripts、MCP、docs 和 wiring 做了 `rg` + AST 审计，
并额外检查动态 `getattr`、字符串索引、monkeypatch 和 import/re-export。以下旧
Project Page symbols 没有隐藏调用；唯一生产引用均列在表内。

| symbol/file | category | current callers | replacement | compatibility reason | delete_now | risk | evidence |
|---|---|---|---|---|---:|---|---|
| `app.refresh_project_storage` | MISPLACED → extracted | `_open_chain_rest`、`_post_archive_reconcile` 两条 live chain | `ui.project_view_handlers.refresh_project_storage` | 无独立兼容 API；Project View 仍需同一 UI callback | yes（app 定义） | medium | baseline AST/rg 仅命中两条 `.then`；empty/normal/exception 输出逐字测试 |
| `app.refresh_projects_full` | DUPLICATE | `p_refresh.click` 唯一调用 | `catalog_ui.reconcile_project_selector` | 无；薄 wrapper 不属于 public contract | yes | low | baseline 只有定义与按钮引用；candidate 直接接 Catalog authority |
| `app.refresh_p_sel` | DUPLICATE | `_open_chain_rest` 唯一调用 | `catalog_ui.reconcile_project_selector` | 无；薄 wrapper 不属于 public contract | yes | low | baseline 只有定义与打开链引用；candidate 直接接 Catalog authority |
| `app.clear_project_view` | DEAD | 无生产、测试、MCP、脚本或动态 caller | 项目页初始空态 / live open chain | 旧项目页无清除事件，控制仍只保留 Python alias | yes | low | AST 无 `Load` 引用；`project_page.py` 不渲染旧资产控件 |
| `app.open_project_folder` | DUPLICATE | 无 caller | `catalog_handlers.open_selected_directory` | 旧项目页目录按钮已移至 Catalog | yes | low | 无 `.click/.then`、import、getattr 或 monkeypatch 引用；Catalog handler live wiring |
| `app.clear_project_cache` | DUPLICATE | 无 caller | Catalog cleanup (`scan_selected_cleanup` / `execute_selected_cleanup`) | 清理服务和磁盘语义仍由 `ProjectStorageService` 保留 | yes | medium | 无生产/测试/MCP/scripts/dynamic caller；Catalog cleanup 直连同一 Service |
| `app.delete_project` | DUPLICATE | 无 caller | `catalog_handlers.archive_selected` | 旧即时 archive callback 不再是 UI/public contract；底层 archive 未改 | yes | medium | 无 live Gradio wiring、测试契约、MCP/scripts/dynamic caller；项目页旧删除控件不渲染 |
| `ui.project_view_handlers.refresh_project_storage` | live owner | 两条 Project View chain | — | 当前 Project View storage summary 唯一 UI owner | no | low | 只调用 `ProjectStorageService.format_summary`，不触碰 Repository/disk |
| `catalog_ui.reconcile_project_selector` | live authority | `p_refresh`、打开链 selector reconcile | — | Project Catalog selector contract | no | low | AST 输入 `[ss]`、输出 `[p_sel]`；selected/opened isolation tests |

## Caller graph before / after

Storage summary：

```text
before: _open_chain_rest / _post_archive_reconcile
        -> app.refresh_project_storage
        -> ProjectStorageService.format_summary

after:  _open_chain_rest / _post_archive_reconcile
        -> project_view_ui.refresh_project_storage
        -> ProjectStorageService.format_summary
```

Selector reconciliation：

```text
before: p_refresh -> app.refresh_projects_full -> catalog_ui.reconcile_project_selector
        open chain -> app.refresh_p_sel -> catalog_ui.reconcile_project_selector

after:  p_refresh -> catalog_ui.reconcile_project_selector
        open chain -> catalog_ui.reconcile_project_selector
```

`p_sel` 仍只由 opened workflow project reconcile；bookshelf row selection 仍只写
`ss.selected_project`，不写 `p_sel`，也不触发 Project View chain。

## Round 3B change summary

### Confirmed deleted

- `app.py::delete_project`
- `app.py::clear_project_view`
- `app.py::open_project_folder`
- `app.py::clear_project_cache`
- `app.py::refresh_projects_full`
- `app.py::refresh_p_sel`
- `app.py::refresh_project_storage`（实现迁移至 UI-owned handler，非行为删除）
- `app.py` 中仅供 `clear_project_cache` 使用的 `format_size` import。

### Added / moved ownership

- `ui/project_view_handlers.py::refresh_project_storage`，保留原输入、输出、fallback、
  exception logging 和 `ProjectStorageService.format_summary` 调用。
- `_open_chain_rest`、`_post_archive_reconcile` 改接 `project_view_ui.refresh_project_storage`。
- `p_refresh` 与打开链 selector 改为直接接入 `catalog_ui.reconcile_project_selector`。
- 新增 Round 3B AST/行为结构测试；既有 selector/Windows wiring tests 改为验证
  Catalog authority，而不是已删除的 app wrapper。

### Retained compatibility

- `app.select_project_from_bookshelf`：源码明确标记为 old-integration no-op，继续保留。
- `app.hide_project_from_list`、`restore_project_to_list`、`migrate_project_copy`：仍是
  deferred legacy surfaces，语义与 live Catalog archive/Storage v3 不等价。
- `lib/project_manager.py` 未整体删除；尤其保留 `services/synthesis.py -> pm.open_project`
  以及 mutable roots、snapshot、status、synthesis、Voice Cast 等 wrapper。
- `ProjectService.delete_project`、`ProjectRepository.delete_project` 和 MCP public
  `list_projects` contract 未触碰。

### Deferred / frozen

- `app.py` 仍是 HOTSPOT；本轮不做下一轮拆分。
- ProductionRuntime、startup state machine、TaskRepository、watchdog/recovery、IndexTTS、
  engine/prewarm、Voice Cast、Storage v1/v2/v3 resolver/migration、Chapter Merge、Whole
  Book Assembly、QA/Repair、Export、Supplement、Quick TTS、MCP contract 和 dependencies
  均未修改。

### Remaining cleanup candidates

- `hide_project_from_list` / `restore_project_to_list`：需要独立决定 hidden-catalog 磁盘语义
  的正式退役，不可用 Archive/Recycle Bin 冒充等价替代。
- `migrate_project_copy`：copy-to-new-root 与 Storage v3 原位迁移不是同一契约。
- `app.select_project_from_bookshelf`：只有在 old integrations 兼容契约被明确退役后再评估。
- `lib/project_manager.py` 私有兼容 wrappers：需专门 compatibility/deprecation round。

## Round 3B verification

修改前 baseline（以当前 `main` 实测）：

- Full pytest：`1272 passed, 26 skipped, 76 warnings`，50.13s。
- Windows selected workflow 同款集合：`329 passed, 19 warnings`，9.70s。
- `refresh_project_storage` baseline 输出：空态、`SUMMARY:alpha` normal path、以及
  `#### 项目存储\n❌ 无法读取项目目录：baseline boom` exception fallback 已记录并固化。

修改后：

- Targeted Round 3B + related wiring：`51 passed`。
- Full pytest：`1278 passed, 26 skipped, 76 warnings`，51.90s；无 candidate-only failure。
- Windows selected workflow 同款集合：`329 passed, 19 warnings`，8.42s。
- `python -m compileall -q .`（使用仓库 `.venv-test` Python）：pass。
- Ruff changed Python files `--select F`：`All checks passed!`。
- `git diff --check`：pass。

## LOC / scope

- `app.py`：5367 → 5298 行（净减少 69 行；删除 73 行、接线/import 调整增加 4 行）。
- `ui/project_view_handlers.py`：55 → 67 行（增加 12 行，承接 storage summary owner）。
- 生产代码净减少 57 行；新增/更新测试只锁定本轮边界，不改业务语义。
- changed production files 仅 `app.py` 与 `ui/project_view_handlers.py`；无 forbidden module
  或依赖变更。

---

# Repository Entropy Cleanup Round 3C — Voice Asset UI Boundary Extraction

日期：2026-08-23

Baseline：`c70f5edc9a84243da77cc332af2f92fb7c932419`（PR #58 squash merge，当前
`origin/main`）。PR #58 状态为 `MERGED`，其 squash merge commit 已进入当前
`main`；没有要求原始 PR head `14d2bd78bd35df41f8eb695bf037c16cc09717cc`
成为 main ancestor。启动时工作树 clean，Round 3C 分支为
`refactor/voice-asset-boundary-r3c`。

## Baseline evidence and full caller audit

修改前已对 `app.py`、`ui/voice_handlers.py`、`ui/wiring/voice_wiring.py`、
`ui/components/voice_binding.py`、services、repositories、lib、tests、MCP、scripts
和 docs 做 `rg` + AST 审计，并补查 `getattr`、字符串 callback key、monkeypatch、
import/re-export。Baseline full pytest 为 `1278 passed, 26 skipped, 76 warnings`
（50.34s）；Windows selected workflow 同款集合为 `329 passed, 19 warnings`
（9.50s）。Baseline Voice Asset fixture 已记录 role list、role selection、category
choices、voice library rows、browser selection、save 4-tuple 和 playback outputs。

| symbol/file | category | current callers | replacement | compatibility reason | delete_now | risk | evidence |
|---|---|---|---|---|---:|---|---|
| `app.refresh_role_list` | DUPLICATE | nav Voices、overview Voices、create chain（baseline） | `ui.voice_handlers.refresh_role_list` | 无独立 legacy/public API；仍由相同 voice wiring callback key 使用 | yes（app 定义） | low | baseline AST/rg 仅命中上述 live chains；candidate owner tests + A–G fixtures |
| `app.select_role_from_list` | DUPLICATE | `voice_wiring` callback dict（baseline）及旧 direct test | `ui.voice_handlers.select_role_from_list` | callback key 保持不变；7 输出契约保持 | yes（app 定义） | low | callback mapping、AST owner test、bound/unbound/invalid fixture |
| `app.play_lib_voice` | DUPLICATE | `voice_wiring` `v_lib.change` callback（baseline） | `ui.voice_handlers.play_lib_voice` | 播放路径/None fallback 不变 | yes（app 定义） | low | callback mapping + existing/missing path fixture |
| `app._save_category_choices` | DUPLICATE | save/refresh category helpers 与 category tests（baseline） | `ui.voice_handlers._save_category_choices` | “未分类”与“— 新建 —” choices 语义不变 | yes（app 定义） | low | direct output fixture、category dropdown regression tests |
| `app.save_to_lib` | DUPLICATE | `voice_wiring` save callback（baseline） | `ui.voice_handlers.save_to_lib` | `ProjectService.save_to_lib`、消息和四元组 outputs 不变 | yes（app 定义） | medium | ValueError/success fixtures、B10 wiring output test |
| `app.filter_vlib_by_category` | DUPLICATE | `voice_wiring` category change callback（baseline） | `ui.voice_handlers.filter_vlib_by_category` | `voice_lib.voice_names(category)` contract 不变 | yes（app 定义） | low | callback mapping + filter fixture |
| `app.refresh_voice_lib` | DUPLICATE | open/archive/nav/overview chains + browser wiring（baseline） | `ui.voice_handlers.refresh_voice_lib` | Dataframe 4-column schema、style 和 category value 不变 | yes（app 定义） | medium | caller order audit + rows/schema fixture |
| `app.select_voice_from_browser` | DUPLICATE | `voice_wiring` browser select callback（baseline） | `ui.voice_handlers.select_voice_from_browser` | dict/list rows、missing-file fallback 和 selected value 不变 | yes（app 定义） | low | invalid/normal/existing/missing browser fixtures |
| `app.refresh_categories` | DUPLICATE | open/archive chains（baseline） | `ui.voice_handlers.refresh_categories` | 绑定/保存分类 choices/value 不变 | yes（app 定义） | low | chain order test + category regression |
| `app.refresh_voice_filters` | DUPLICATE | nav/overview chains（baseline） | `ui.voice_handlers.refresh_voice_filters` | 三个 dropdown 输出顺序和值不变 | yes（app 定义） | low | module-qualified chain audit + filter fixture |
| `ui.components.voice_binding.format_role_config_title` | LIVE presentation owner | `app.bind_voice`, `ui.voice_handlers.select_role_from_list` | none | pure shared formatter; no compatibility surface | no | low | final AST/rg audit shows one definition and two callers; exact-output fixtures |
| `app._lib_path` | COMPAT / cross-domain state | `bind_voice`, `preview_bound_voice`, QA repair override, Supplement, Quick TTS/Utility | none in this round | shared dynamic voice-library root and runtime/utility callers | no | high | full-repo AST/rg caller graph; explicit retention test |
| `app._lib_voices` | COMPAT / cross-domain state | open-project choices, save/utility choices and production paths | none in this round | existing app-level choice consumers remain live | no | medium | full-repo AST/rg caller graph; explicit retention test |
| `app.bind_voice`, Voice Cast UI/finalization | FROZEN | Voice Cast wiring, persistence/finalization and legacy compatibility | none | explicitly out of scope; state and file contracts cross domains | no | high | Voice Cast tests and app ownership assertions |
| `app.refresh_production_voice_choices` | MISPLACED but retained | production + utility/supplement workflows | future boundary round | crosses review/utility/production choices | no | high | direct live callers and forbidden-scope audit |
| `app.apply_data_dir`, `app.open_data_dir` | deferred residual candidates | settings wiring / legacy settings integration | future Settings round | strong dead/duplicate candidates, but not proven safe in this round | no | medium | recorded for follow-up; no mutation made |

## Caller graph before / after

Low-risk Voice Asset callbacks:

```text
before: nav_voices / ov_voices / voice_create_chain / _open_chain_rest /
        _post_archive_reconcile / voice_wiring
        -> app.<callback>

after:  same event chains and same callback keys
        -> ui.voice_handlers.<callback>
```

Cross-domain state intentionally remains app-owned:

```text
app._lib_path       -> bind_voice / RuntimeTTS preview / QA repair /
                       Supplement / Quick TTS / Utility
app._lib_voices     -> open-project role choices / production and utility choices
ui.components.voice_binding.format_role_config_title -> app.bind_voice / ui.voice_handlers.select_role_from_list
```

`_open_chain_rest` and `_post_archive_reconcile` retain the exact order
`refresh_voice_lib → refresh_categories → refresh_production_voice_choices`; only the
first two callback owners changed to `voice_ui.*`. `selected_project` remains isolated
from opened `p_sel` in both chains. `ui/wiring/voice_wiring.py` keeps its callback-dict
API and event order; only `app.py` injection values now point to the new module.

## Round 3C change summary

### Confirmed moved/deleted from app ownership

- Removed the ten low-risk UI callback definitions listed in the table from `app.py`.
- Added `ui/voice_handlers.py` as their single live implementation owner; it does not
  import `app`, `ProductionRuntime`, `RuntimeTTSService`, or `VoiceCastResolver`.
- Updated nav, overview, create, open/archive reconciliation chains and the Voice Page
  callback map to module-qualified `voice_ui.*` owners.
- No HTML labels, output order, state icons, disk format, storage semantics, runtime,
  Voice Cast, engine, QA, export, utility, MCP, or dependency changes.

### Retained compatibility / frozen code

- `bind_voice`, `refresh_role_summary`, `refresh_voice_cast_ui`,
  `finalize_voice_cast_ui`, `preview_bound_voice`, `refresh_production_voice_choices`.
- `_lib_path` and `_lib_voices` because their caller graphs cross Voice Cast, runtime, QA,
  Supplement, Quick TTS/Utility, or opened-project flows.
- `ui/components/voice_binding.py` remains pure presentation helpers; no wiring moved
  there. `lib/project_manager.py` and all compatibility wrappers remain untouched.

### Deferred / frozen hotspots

- Settings residual candidates `app.apply_data_dir` / `app.open_data_dir` are recorded,
  not deleted.
- `app.py` remains a HOTSPOT; no broad split or formatting pass was performed.
- ProductionRuntime/startup state machine, TaskRepository, Storage v1/v2/v3,
  Chapter Merge, Whole-book Assembly, QA/Repair, Export, Quick TTS, Voice Cast legacy
  compatibility, MCP public contract and dependencies remain frozen.

### Remaining cleanup candidates

- Settings residual candidates require a separate caller/compatibility round.
- Role configuration title formatting is no longer a cleanup candidate: the shared
  `ui.components.voice_binding.format_role_config_title` owner is now used by both callers.
- `_lib_path` / `_lib_voices` require a cross-domain ownership design before any move.

## Behavior-equivalence fixtures and verification

The new `tests/test_voice_asset_boundary.py` locks baseline/candidate outputs for:

- role list: no project, no snapshot, search match/no match, current-role retention;
- role selection: invalid/unbound/bound role, description/name fallback and seven outputs;
- category choices and refresh/filter dropdown value contracts;
- voice-library search/category rows, four-column headers, browser dict/list/invalid rows,
  existing/missing playback paths;
- save `ValueError` and success message/choices/four-output tuple.

Candidate verification:

- Targeted Round 3C + related wiring tests: `86 passed, 20 warnings`.
- Full pytest: `1292 passed, 26 skipped, 76 warnings`，52.74s；coverage
  `18098` statements / `81%`。相对 baseline 增加的通过项来自本轮结构/行为契约，
  无 candidate-only failure。
- Windows selected workflow: `329 passed, 19 warnings`，8.89s；与 baseline 集合
  和 warning 数量一致。
- `python -m compileall -q .`: pass（仓库 `.venv-test` Python）。
- Ruff changed Python files `--select F`: `All checks passed!`。
- `git diff --check`: pass。

## LOC / scope accounting

- `app.py`: `5298` lines at baseline → `5182` lines after extraction (net `-116`).
- New `ui/voice_handlers.py`: `162` lines; this is an ownership extraction, so total
  production LOC changes by `+46` while the app hotspot shrinks by 116 lines.
- Round 3C production files: `app.py`, `ui/voice_handlers.py`; tests only add/update
  boundary contracts and do not alter production behavior.

## Delivery

- Commit: `b3ca249725808262dfd39dd13d8cd00e11f4fdeb`
- Branch: `refactor/voice-asset-boundary-r3c`
- Pull request: [#59](https://github.com/easonwong2026-del/audiobook-studio/pull/59)
- GitHub Actions: Ubuntu Python 3.10 `pass`; Windows Python 3.10 selected workflow
  tests `pass`.
- Final worktree: clean.

## Round 3C final formatter cleanup

The duplicate `_role_config_title` implementations found in the PR review were removed.
`ui/components/voice_binding.py::format_role_config_title` is now the sole presentation
owner. `app.bind_voice` and `ui.voice_handlers.select_role_from_list` call it directly;
the Voice Cast business path is otherwise unchanged. Exact fixtures cover `role=None`,
description-first formatting, name fallback, no metadata, bound, and unbound outputs.
The shared component remains free of Gradio, app, services, VoiceCastResolver, and
RuntimeTTSService dependencies.

---

# Repository Entropy Round 3D — Settings Residual & Wiring Boundary Cleanup

日期：2026-08-23

Baseline：`dd827e65fba613dd11fb7ad617303c7e0c0ceb69`（PR #59 squash merge，当前
`origin/main`）。PR #59 已确认 `MERGED`；没有使用旧 PR head ancestry 作为启动条件。
Round 3D 分支：`refactor/settings-residual-boundary-r3d`。

## Before caller audit

在修改前对 `app.py`、`ui/**`、services、repositories、lib、tests、scripts、MCP 和
docs 做了 `rg` + AST 审计，并检查 import alias、`getattr`、monkeypatch、动态字符串
以及 Gradio `click/change/load/then` wiring。

| symbol | baseline owner | production callers / evidence | category |
|---|---|---|---|
| `app.apply_data_dir` | `app.py` | 只有定义命中；正式按钮 wiring 是 `settings_handlers.apply_data_dir`；无 import/getattr/monkeypatch/script/MCP caller | DEAD / DUPLICATE |
| `app.open_data_dir` | `app.py` | 只有定义命中；正式按钮 wiring 是 `settings_handlers.open_data_dir`；无 production/test/dynamic caller | DEAD / DUPLICATE |
| `settings_handlers.apply_data_dir` | `ui/settings_handlers.py` | `ui/wiring/settings_wiring.py::s_data_apply.click`，以及 data-root/catalog tests | LIVE authority |
| `settings_handlers.open_data_dir` | `ui/settings_handlers.py` | `ui/wiring/settings_wiring.py::s_data_open.click` | LIVE authority |
| `settings_wiring.run_diagnostics_ui` | `ui/wiring/settings_wiring.py` | `s_diagnostics_run.click` 唯一 wiring caller；内部调用 environment diagnostics service | MISPLACED presentation |
| `run_environment_diagnostics` / `diagnostics_table` / `diagnostics_to_markdown` | `services/environment_diagnostics.py` | diagnostics tests、acceptance script 和 wiring-local aggregator | service + presentation inputs |

旧 app data-dir callbacks 与正式 authority 已经语义分叉：旧 `apply_data_dir` 不接
SessionState、空输入返回当前 config 路径且不 reset；旧 `open_data_dir` 仅 Windows
`os.startfile` 并吞掉 OSError。正式 `settings_handlers` 实现负责跨平台打开、HTML
escape、空路径契约和 `reset_for_data_root`，因此不能保留 app trampoline。

## Caller graph before / after

```text
before:
app.apply_data_dir       [zero live caller]
app.open_data_dir       [zero live caller]

settings_wiring
  ├─ settings_handlers.apply_data_dir
  ├─ settings_handlers.open_data_dir
  └─ local run_diagnostics_ui
       └─ run_environment_diagnostics
          diagnostics_table
          diagnostics_to_markdown

after:
app.py                   [no Settings data-dir callbacks]

settings_wiring
  ├─ settings_handlers.apply_data_dir
  ├─ settings_handlers.open_data_dir
  └─ settings_handlers.run_diagnostics_ui
       └─ run_environment_diagnostics
          diagnostics_table
          diagnostics_to_markdown
```

The `s_data_apply → catalog refresh → merge refresh → assembly refresh` chain and its
ordering are unchanged. No selected/opened/catalog-query semantics were modified.

## Round 3D changes

- Deleted `app.apply_data_dir` and `app.open_data_dir`; no compatibility wrapper was
  added because the full caller audit proved zero live or dynamic callers.
- Moved `run_diagnostics_ui` into `ui/settings_handlers.py` without changing its status
  emoji mapping, three-tuple output, report/table/Markdown order, or exception behavior.
- `ui/wiring/settings_wiring.py` now only composes events and directly references
  `settings_handlers.run_diagnostics_ui`; it no longer imports the diagnostics service.
- `ui/settings_handlers.py` continues to directly import `ConfigRepository`,
  `ProjectRepository`, and `TaskRepository`; this remains deferred architecture debt.
- No TTS engine, prewarm, runtime, storage, catalog, Voice Asset, Voice Cast, MCP, or
  dependency changes. `ui/pages/settings_page.py` is unchanged.

## Behavior fixtures and verification

Added `tests/test_settings_residual_boundary.py` covering:

- empty, successful, no-session, exception, reset-for-data-root, and catalog-query
  preservation contracts for `apply_data_dir`;
- Windows/macOS/Linux open-folder commands and escaped failure output;
- diagnostics `ok`/`warning`/`error`/unknown status symbols, shared report identity,
  one-call-per-renderer, and strict three-output order;
- AST ownership and unchanged data-dir refresh-chain composition.

Baseline:

- Full pytest: `1298 passed, 26 skipped, 76 warnings`，45.84s。
- Windows selected workflow: `329 passed, 19 warnings`，10.14s。

Candidate:

- Targeted Settings/data-root/environment/TTS/prewarm/catalog tests: `123 passed, 12 warnings`。
- Full pytest: `1313 passed, 26 skipped, 76 warnings`，47.67s；新增测试无 candidate-only failure。
- Windows selected workflow: `329 passed, 19 warnings`，8.98s。
- `python -m compileall -q .`: pass。
- Ruff changed Python files `--select F`: pass。
- `git diff --check`: pass。

## LOC / deferred findings

- `app.py`: `5173` → `5152` lines（减少 21）。
- `ui/settings_handlers.py`: `605` → `620` lines（承接 diagnostics owner，增加 15）。
- `ui/wiring/settings_wiring.py`: `113` → `98` lines（减少 15）。
- Round 3D production diff：`16 insertions / 37 deletions`，净减少 21 行。
- Deferred：`ui/settings_handlers.py` 直接依赖 `ConfigRepository`、`ProjectRepository`、
  `TaskRepository`；本轮不创建 SettingsService/DiagnosticsService/facade，也不处理
  `_snapshot`/`app._snap`、TTS engine、prewarm、runtime 或 data-root semantics。

## Delivery

- Commit：`c43e677` (`refactor: close settings residual boundary`)。
- PR：[#60](https://github.com/easonwong2026-del/audiobook-studio/pull/60)，标题为
  `refactor: close settings residual boundary (Round 3D)`。
- GitHub CI：Ubuntu Python 3.10 ✅；Windows Python 3.10 selected workflow ✅。
- Final commit diff stat：5 files changed, 305 insertions(+), 37 deletions(-)。其中生产
  ownership cleanup 净减少 21 行；新增测试与审计记录不改变运行时行为。
- Final worktree：clean；分支 `refactor/settings-residual-boundary-r3d` 已推送并跟踪
  `origin/refactor/settings-residual-boundary-r3d`。

---

# Repository Entropy Round 3E — Post-Cleanup Ownership Re-Audit

日期：2026-08-23

Round 3E 以 PR #60 squash merge 后的 `81653416ad970014b4b273f1942eedf1c37e8c08`
为审计基线。复核了 Round 3A–3D 的 ownership 结果、app.py 剩余 callback、UI
modules、services、tests、scripts、MCP 和 docs；没有回退已经完成的 Project View、
Voice Asset 或 Settings ownership。Formal Export 被确认仍是 app.py 中下一块完整
implementation boundary；QA/Review、Voice Cast、Utility、Runtime 和 Storage 均标记
为 deferred/frozen，不在本轮修改。

## Re-audit result

| area | result | Round 3F disposition |
|---|---|---|
| Project Catalog / Project View / Chapter Tree | 唯一 owner 已稳定 | frozen |
| Voice Asset / shared role presentation | 唯一 owner 已稳定 | frozen |
| Settings / diagnostics | handler owner 已稳定，repository imports 仍是 deferred debt | frozen/deferred |
| Formal Export UI observer/callbacks | 仍集中在 app.py，且 wiring 与业务 observer 混合 | selected for Round 3F |
| Supplement / Quick TTS safe-file adaptation | 与 Formal Export 共用 app helper | shared-owner extraction only |
| QA/Review / Voice Cast / Utility business logic / Runtime / Storage | 非本轮 boundary | frozen |

No Round 3E production code was changed; this section records the selection evidence only.

---

# Repository Entropy Round 3F — Formal Export UI Boundary Extraction

日期：2026-08-23

Baseline：`81653416ad970014b4b273f1942eedf1c37e8c08`（PR #60 squash merge，当前
`origin/main`）。分支：`refactor/export-ui-boundary-r3f`。

## Before caller audit and graph

对 baseline 的 `app.py`、`ui/**`、`services/**`、`repositories/**`、`tests/**`、
`scripts/**`、`mcp_server/**`、`docs/**` 和 `qa_verify_export_safe_path.py` 做了 `rg`、
AST、import alias、`getattr`、monkeypatch、动态字符串以及 Gradio
`.click/.change/.tick/.then/.load` 审计。未发现通过 `getattr`、动态 import 或 MCP
字符串调用这些 app callbacks 的兼容契约；测试中的旧 `app.*` 引用属于需要随 owner
迁移的结构/行为契约。

```text
before:
app.py
  ├─ _EXPORT_ACTIVE_STATUSES / _EXPORT_TERMINAL_STATUSES / _EXPORT_STATUS_LABELS
  ├─ _remember_export_ui_state / _export_ui_reset / _resolve_export_ui_artifact
  ├─ _copy_export_ui_artifact / _export_ui_values
  ├─ refresh_export_status / open_export_location / do_export
  ├─ refresh_export_readiness / do_export_subtitles / refresh_export_default_dir
  └─ _safe_path_for_file_component
       ├─ Formal Export (_export_ui_values → download output)
       ├─ do_supplement_export
       └─ do_quick_tts_export

  Gradio Export callbacks (timer/readiness/start/open/subtitle/default-dir)
  └─ bare app-local functions above

after:
app.py
  └─ composition/wiring only
       ├─ export_ui.refresh_export_default_dir
       ├─ export_ui.refresh_export_readiness
       ├─ export_ui.refresh_export_status
       ├─ export_ui.do_export
       ├─ export_ui.open_export_location
       └─ export_ui.do_export_subtitles

ui/export_handlers.py
  └─ all Formal Export UI state, observers, guards, manifest adapter and subtitle callback
       └─ ExportService / ProjectService / QualityService / WorkflowService
          └─ ui.file_component_paths.safe_path_for_file_component

ui/file_component_paths.py
  └─ one shared Gradio File safe-path adapter
       ├─ Formal Export
       ├─ do_supplement_export
       └─ do_quick_tts_export
```

## Ownership and frozen behavior

- All 13 listed Export symbols moved from `app.py` to `ui/export_handlers.py`; no app
  trampoline or duplicate implementation remains.
- `do_export(fmt, bitrate, output_dir, *args)` retains the exact positional compatibility
  contract, including `qa_policy`, `ss`, `active_task_id`, and `active_output_dir`.
- SessionState fields `_export_ui_task_id`, `_export_ui_output_dir`, and
  `_export_ui_project` remain tracking pointers only; durable authority remains
  `ExportService`/`TaskRepository`.
- Pending/running/cancelling/pausing/paused/recovering second-click guard, `EXPORT_ACTIVE`
  race extraction, project-switch isolation, unknown-state polling, and timer transitions
  are unchanged.
- Done still requires ready delivery manifest, matching `export_id`, a relative artifact
  resolved through `project_paths.resolve_relative`, an existing non-empty file, and only
  then shows `✅ 导出成功`.
- Optional user copy remains non-fatal; `open_export_location` still requires done + ready
  manifest and uses `lib.procutil.open_in_folder`.
- `refresh_export_readiness`, subtitle formats (`srt`/`lrc`/`both`/`none`), and default-dir
  fallback/messages are unchanged.
- `app.launch(allowed_paths=[config.get_data_dir()])` is unchanged.
- Supplement and Quick TTS diffs only redirect the shared safe-path call; their output
  directories, naming, bitrate, format, messages, synthesis, runtime, preview and folder
  opening are untouched.
- No changes to `services/export.py` business logic, QA/Review, Voice Cast, Utility
  business logic, ProductionRuntime, Storage, MCP contract, dependencies or user-facing
  copy.

## Safe-path equivalence

The baseline AST body of `app._safe_path_for_file_component` and the candidate body of
`ui.file_component_paths.safe_path_for_file_component` match after owner-name normalization.
The helper preserves:

- `None`/invalid-file passthrough;
- data-dir subtree passthrough;
- external-file copy to `tempfile.gettempdir()` with source retained;
- timestamp suffix for duplicate temp names;
- `commonpath` `ValueError` as external-path handling;
- copy-failure fallback to the original path;
- real absolute/commonpath traversal behavior.

## Tests and fixtures

`tests/test_export_ui_boundary.py` proves unique owners, forbidden app/service imports,
unchanged event graph/timer, exact `do_export` signature, allowed-path wiring, utility-only
redirects, and safe-path internal/external/None/missing/traversal/duplicate/copy-failure/
`commonpath` behavior. Existing `tests/test_export_ux.py` now patches the real
`ui.export_handlers` owner and retains these fixtures:

- start → pending; active second click; `EXPORT_ACTIVE` race;
- project switch isolation; pending → running → done/error/cancelled/interrupted/
  needs_attention/unknown polling;
- done + ready manifest success; done + not-ready manifest no false success;
- open location; legacy v1/v2 artifacts; optional copy success/failure;
- subtitle generation and no-window folder opening.

`tests/qa_allowed_paths_test.py` and `qa_verify_export_safe_path.py` now exercise the real
shared helper owner instead of extracting a function from `app.py`.

## Validation

Baseline:

- Full pytest: `1313 passed, 26 skipped, 76 warnings`，47.55s。
- Windows selected workflow: `329 passed, 19 warnings`，8.70s。

Candidate (serial, authoritative run):

- Full pytest: `1323 passed, 26 skipped, 76 warnings`，45.93s。
- Windows selected workflow: `329 passed, 19 warnings`，8.69s。
- Export targeted set: `54 passed, 1 skipped`。
- Boundary/structural set: `56 passed`。
- Safe-path QA script: pass。
- `python -m compileall -q .`: pass。
- Ruff changed Python files `--select F`: pass。
- `git diff --check`: pass。

An exploratory parallel full+Windows invocation exposed existing process-level runtime/TTS
task contention; its two failures were runtime initialization/wait tests. The required
serial rerun passed with baseline warning count and no candidate-only regression.

## LOC and deferred scope

- `app.py`: `5152` → `4668` lines（减少 `484`）。
- New `ui/export_handlers.py`: `466` lines。
- New `ui/file_component_paths.py`: `39` lines。
- Production ownership net: `+21` lines (implementation moved into two neutral owners while
  the app hotspot shrinks); full commit also includes boundary tests, QA ownership updates,
  docs and this audit section.
- Retained in `app.py`: UI component variables, page construction and event composition only;
  no Formal Export handler definitions remain.
- Deferred: QA/Review generation-fence callbacks, Voice Cast, Utility handler extraction,
  direct repository dependencies elsewhere, Storage migration, Runtime/Prewarm and any
  `export_wiring.py` split.

## Delivery

- Implementation commit：`e4d11bf2c24a770ef2b9dd8bdb1f7a9ee08f51e3`
  (`refactor: extract formal export ui boundary`)；之后提交均为审计文档的 docs-only
  follow-up，最终 head 以 PR #61 当前 head 为准。
- Branch：`refactor/export-ui-boundary-r3f`。
- PR：[#61](https://github.com/easonwong2026-del/audiobook-studio/pull/61)，标题为
  `refactor: extract formal export UI boundary (Round 3F)`。
- GitHub CI：Ubuntu Python 3.10 ✅；Windows Python 3.10 selected workflow ✅。
- Final diff stat：15 files changed, 1040 insertions(+), 635 deletions(-)。其中
  `app.py` 减少 484 行；Formal Export 与 safe-path 实现迁入两个唯一 owner。
- `services/export.py`、ProductionRuntime、TaskRepository、Storage、QA、Voice Cast、
  Utility business logic、MCP contract 和依赖均未修改。
- Final worktree：clean；分支已推送并跟踪
  `origin/refactor/export-ui-boundary-r3f`。

---

# Workbench IA-1 follow-up — Round IA-2 caller audit correction (pre-IA-2A baseline)

日期：2026-08-23

Reference：PR #63 head `e97b84c1104171801bbb31a1cc3bcb6af12ea8ca`。
本节记录 IA-2A 实施前的 caller audit 结论；其 candidate 状态已由文末
Round IA-2A final classification superseded。本节保留作为历史基线，不代表当前
生产代码仍保留这些 sink。

## Corrected Voice wiring conclusion

`ui/wiring/voice_wiring.py::wire_voice_page` 当前只读取以下 context keys：

- `context["callbacks"]`
- `context["session"]`
- `context["production_voice"]`

虽然 `app.py` 仍向 `wire_voice_page` 注入 `"project": p_sel`，但该 key 没有被
`wire_voice_page` 读取，属于 dead compatibility injection。它是 Round IA-2 的优先
删除候选；本轮不删除。

这不等于 `p_sel` 在全局已经 dead：打开链的 selector reconcile、Project View 的
chapter tree / storage mirror、旧 `p_refresh` / `p_open`、创建链和目录刷新仍有真实
兼容消费。当前只能把 Voice wiring 中的 injection 标记为 dead，不能据此删除整个
`p_sel` contract。

```text
current:
app.py::wire_voice_page context
  ├─ callbacks       -> ui/wiring/voice_wiring.py (live)
  ├─ session         -> ui/wiring/voice_wiring.py (live)
  ├─ production_voice -> ui/wiring/voice_wiring.py (live)
  └─ project: p_sel  -> not read (dead compatibility injection)

Round IA-2 candidate:
app.py wire_voice_page context["project"] = p_sel
  -> remove only after a dedicated wiring/caller regression check
```

## Hidden Workbench legacy sink candidates

The following components are intentionally hidden by IA-1 but remain in the Gradio
component graph and/or refresh wiring. They are Round IA-2 audit candidates only:

| hidden sink | current residual wiring | IA-2 action | evidence / guard |
|---|---|---|---|
| `ov_status` | `refresh_overview` / `_open_chain_rest` / `_post_archive_reconcile` still emit the legacy dashboard status output | audit and remove the old dashboard refresh output contract | hidden `grp-workbench-legacy-sink`; opened-project refresh tests |
| `ov_progress` | same legacy dashboard refresh tuple | audit and remove with dashboard tuple | same as above |
| `ov_task` | same legacy dashboard refresh tuple | audit and remove with dashboard tuple | same as above |
| `ov_issues` | same legacy dashboard refresh tuple | audit and remove with dashboard tuple | same as above |
| `ov_open` | hidden quick-action event still routes through the old Project Page open path | audit and retire after visible Inspector open coverage is complete | hidden component; existing open-chain compatibility tests |
| `ov_voices` | hidden quick-action event still routes to Voices | audit and retire after visible navigation/caller coverage | hidden component; existing voice refresh tests |
| `ov_synth` | hidden quick-action event still routes to production | audit and retire after visible navigation/caller coverage | hidden component; production refresh contracts |
| `ov_export` | hidden quick-action event still routes to Delivery | audit and retire after visible navigation/caller coverage | hidden component; Export reconciliation tests |

No hidden sink, old dashboard refresh callback, or quick-action event is deleted in
this follow-up. The IA-2 caller graph is therefore:

```text
Workbench visible UI
  -> selected/opened Catalog + Inspector contracts

hidden legacy sink
  ├─ ov_status / ov_progress / ov_task / ov_issues
  │    -> legacy dashboard refresh outputs
  └─ ov_open / ov_voices / ov_synth / ov_export
       -> hidden compatibility quick-action chains
```

## IA-2 deletion order candidate

1. Remove the dead `wire_voice_page` `"project": p_sel` injection with a focused
   wiring test.
2. Prove all visible Workbench open/voice/production/delivery paths before removing
   hidden quick-action events.
3. Remove the legacy dashboard refresh outputs only after all `refresh_overview`,
   open-chain, archive-reconcile, creation-chain and navigation callers are audited.
4. Re-audit `p_sel` separately; its remaining Project View and open-chain consumers are
   not covered by the dead Voice wiring injection finding.

This section records candidates only. IA-2 implementation is explicitly out of scope
for the IA-1 follow-up.

---

# Round IA-2A — Workbench Legacy Sink Retirement

日期：2026-08-24

Merged baseline：PR #63 已按仓库既有 squash 策略合并；merge commit / new origin/main
为 4393909c985243e4f65fb0a18771e7690d75d540。IA-2A 分支从该 origin/main 建立：
refactor/workbench-legacy-sink-r2a。

本轮只退休 IA-1 遗留的、无用户可见入口的 Workbench dashboard / quick-action sink。
p_sel、Project Page compatibility contract、Runtime/TTS/Production/QA/Repair/Export、
Merge、Assembly、Storage、Voice Cast、MCP 与 dependencies 均未触碰。

## Final caller-audit conclusion

完整审计覆盖：

- 全仓 rg（production、tests、scripts、MCP、docs）；
- production Python AST（排除 .git、.venv-test、__pycache__）；
- imports / reexports；
- getattr、__getattribute__、string callback keys、monkeypatch / setattr；
- tests 与动态 caller 入口。

审计结论：候选 dashboard / quick-action 名称在生产代码中只曾由 app.py、
ui/pages/overview_page.py、ui/components/dashboard.py 及 ui/components/__init__.py
承载；没有 services、repositories、scripts 或 mcp_server 的 caller。完成清理后，
生产 AST 与 rg 不再发现这些 legacy sink；剩余命中仅是测试断言和本审计文档中的
历史 / 当前审计记录。ui/components/__init__.py 已不再 re-export dashboard helper。
没有发现 getattr、__getattribute__、string callback key 或 monkeypatch 对这些
sink 的隐藏消费。

## Before / after caller graph

Before：

    Workbench / open / archive / overview navigation
      ├─ refresh_overview
      │    └─ _dashboard_snapshot -> ov_status / ov_progress / ov_task / ov_issues
      └─ hidden ov_open / ov_voices / ov_synth / ov_export quick-action chains

After：

    Workbench visible Catalog + Inspector
      ├─ bookshelf_open -> open_selected_project -> _open_chain_rest
      │    ├─ downstream refresh contract
      │    └─ Catalog management refresh
      └─ visible nav_voices / nav_synth / nav_export -> existing page refresh chains

    overview navigation / archive reconcile
      └─ live Catalog and workflow outputs only

不存在 dashboard tuple refresh，也不存在 hidden quick-action event wiring。

## Final classification

| candidate | classification | final fact |
|---|---|---|
| ov_status | DEAD | hidden dashboard output 与 refresh_overview 一并删除 |
| ov_progress | DEAD | hidden dashboard output 与 refresh_overview 一并删除 |
| ov_task | DEAD | hidden dashboard output 与 refresh_overview 一并删除 |
| ov_issues | DEAD | hidden dashboard output 与 refresh_overview 一并删除 |
| ov_open | DEAD | hidden open quick action 删除；由 Workbench bookshelf_open 覆盖 |
| ov_voices | DEAD | hidden voice quick action 删除；由 visible nav_voices 覆盖 |
| ov_synth | DEAD | hidden production quick action 删除；由 visible nav_synth 覆盖 |
| ov_export | DEAD | hidden delivery quick action 删除；由 visible nav_export 覆盖 |
| refresh_overview | DEAD | 不再刷新不可见 dashboard tuple；Catalog 由 authority 直接刷新 |
| _dashboard_snapshot | DEAD | 唯一旧 dashboard producer 随 refresh_overview 删除 |
| ui/components/dashboard.py | DEAD | 全仓 production caller / re-export 清零后删除 |
| empty_dashboard_html / project_dashboard_html | DEAD | helper 与 component re-export 删除 |
| wire_voice_page project injection | DEAD | 只删除 dead project: p_sel injection；Voice wiring live keys 不变 |
| ov_bookshelf | LIVE | Workbench visible Catalog/Dataframe 与 selection wiring |
| bookshelf_open | LIVE | Workbench Inspector 唯一可见打开入口 |
| nav_voices | LIVE | visible 角色与声音入口及既有 refresh chain |
| nav_synth | LIVE | visible 生产与质检入口及既有 refresh chain |
| nav_export | LIVE | visible 交付入口及 export reconciliation |
| catalog_ui.refresh_bookshelf_management_view_with_hierarchy | LIVE | Catalog authority；打开、返回、archive 等链路继续使用 |
| grp_project / p_sel / p_open / p_refresh / p_summary / p_storage / p_chapter_tree | DEFERRED | IA-2B 冻结兼容 contract，未删除 |
| create_project_page / project_view_ui Project Page contract | DEFERRED | IA-2B scope，未改动 |

## Deletion list

- app.py dashboard imports、_dashboard_snapshot、refresh_overview 及其不可见输出接线；
- app.py ov_open / ov_voices / ov_synth / ov_export hidden event registrations；
- app.py wire_voice_page context 中 dead project: p_sel injection；
- overview_page.py hidden legacy group 与八个 sink return keys；
- ui/components/dashboard.py 及 components package re-exports；
- theme.py 中仅服务旧 dashboard / quick-action 的 dead CSS selectors；
- 受影响的旧 structural tests，并新增 IA-2A caller / replacement regression tests。

## Retention and visible-path proof

- visible navigation 仍为 工作台 / 角色与声音 / 生产与质检 / 交付 / 设置；
- Workbench 新建项目按钮与既有 create/open chain 保留；
- bookshelf select 仍只更新 selected_project；opened project 仍由 Inspector 显式打开，
  selected != opened invariant 未改变；
- bookshelf_open 仍进入 open_selected_project，再复用 _open_chain_rest；
  Export reconciliation、top status、Voice Cast、Review / QA、Repair observer、
  production queue/task、synthesis scope/preview、voice library、production check、
  export readiness、Catalog、Merge、Assembly 的既有 downstream callbacks 均保留；
- visible nav_voices、nav_synth、nav_export 保留原有 page refresh wiring；
- search / hierarchy / archive / restore 与 Export project-switch isolation 仍走
  Catalog / Session authority；没有引入第二个 Catalog scan 或改变 filter semantics。

## Round IA-2B latest caller audit

本轮没有开始 IA-2B。最新边界结论如下：

- p_sel 仍被 Project Page 的 p_open / p_refresh / summary / chapter tree / storage
  contract 使用；open chain 会先 reconcile selector，再把 p_sel 交给 Project View；
- p_sel 仍作为 project_catalog_wiring 的 compatibility dependency，用于 selector
  与管理输出；创建链、设置页 Catalog refresh 与现有项目页事件仍保留；
- grp_project、create_project_page、project_view_ui 及 compatibility keys 均保留；
- Voice wiring 不读取 context project，也不消费 p_sel；本轮只删 dead injection，
  没有把该结论扩大为 p_sel 全局退休；
- 因而 Project Page / p_sel 正式退休仍属于 IA-2B，未在 IA-2A 实施。

## Validation record

IA-2A targeted structural / Voice / Catalog / open / Export checks：57 passed（初始
legacy-sink 集）；扩展 IA / Catalog / Session / Project Page boundary 集为
116 passed；Voice / Production / Export project-switch / Merge / Assembly 集为
161 passed, 1 skipped。

最终串行验证：

- Full pytest：1340 passed, 26 skipped；
- Windows selected workflow command：329 passed；
- compileall：pass；
- Ruff --select F：pass；
- git diff --check：pass。

PR #63 合并前最终事实：Full pytest 1335 passed / 26 skipped、targeted IA / Catalog
65 passed、Windows selected workflow 329 passed、CI run 32645813009 Ubuntu + Windows
success。

---

# Round IA-2B — Project Page / selected-opened contract retirement

日期：2026-08-24

Merged baseline：PR #64 已按仓库既有 squash 策略合并；新的
`origin/main` baseline 为 `31c76b63ffc03d110607b8c63065adb80c7f4ba5`。
本轮分支：`refactor/project-page-contract-r2b`。

## Latest caller-audit conclusion

本轮重新完成了全仓 `rg`、production AST、imports/reexports、`getattr` /
`__getattribute__`、string callback keys、`setattr` / monkeypatch、tests、scripts、
`mcp_server` 与 docs audit。未发现脚本、MCP、动态反射或 re-export caller 继续消费
`p_sel`、Project Page 或 Project View。`ui/wiring/voice_wiring.py` 仍只读取
`callbacks`、`session`、`production_voice`；本轮保留这三个 live keys。

## Before / after caller graph

Before：

    create_from_json -> [cp_json_result, p_sel]
    p_refresh -> reconcile_project_selector -> [p_sel]
    p_open -> open_project([p_sel, ss]) -> [p_summary, live Voice outputs]
    _open_chain_rest -> reconcile_project_selector -> Project View tree/storage
    Catalog refresh -> [bookshelf, p_sel, trash, selection context]
    settings / app.load -> [bookshelf_search, p_sel, ss]

After：

    create_from_json -> [cp_json_result, cp_json_success]
                     -> require_creation_success
                     -> success only:
                          hydrate_opened_project([ss]) -> six live Voice outputs
                          -> Voices
                          -> _open_chain_rest
    bookshelf_open -> open_selected_project([selected_project, ss])
                   -> open_project -> _open_chain_rest
    _open_chain_rest / _post_archive_reconcile
        -> live export / status / Voice Cast / Review / Repair / queue / synthesis
        -> voice library / production check / export readiness / Catalog
    Catalog refresh -> [bookshelf, trash, selected, selection context, hierarchy]
                     -> [search_query, SessionState]

`SessionState.project` remains the only opened-project truth and
`SessionState.selected_project` remains the only Workbench selection truth. No hidden
Dropdown, Textbox, or project-identity compatibility mirror replaces `p_sel`.
`cp_json_success` is only the boolean result of the current Create operation; it is not
project identity, an opened-project mirror, or a future `p_sel` substitute.

## Final classification

| candidate / contract | classification | final fact |
|---|---|---|
| `p_sel` | DEAD | no production definition, import, output, callback input, or dynamic caller remains |
| `grp_project` | DEAD | hidden Project Page Group removed from app topology |
| `p_refresh`, `p_open`, `p_open_msg`, `p_summary`, `p_storage`, `p_chapter_tree` | DEAD | hidden Project Page components and callbacks removed |
| `create_project_page` | DEAD | page builder removed; `create_create_project_page` is the live JSON create page |
| `project_view_handlers` / `project_view_ui` | DEAD | hidden chapter-tree/storage sink removed after production caller audit |
| selector helpers (`build_project_selector_update`, `reconcile_project_selector`, `refresh_project_catalog`) | DEAD | no remaining live selector contract; management refresh owns Catalog output |
| `create_project` old app wrapper | DEAD | dead four-output compatibility wrapper removed |
| `hide_project_from_list` / `restore_project_to_list` | DEAD | no production caller; selector-dependent dead wrappers removed |
| `select_project_from_bookshelf` | DEAD | no production caller; live selection is `catalog_ui.select_bookshelf_row` |
| `open_project` | LIVE | opened-session loader retained; output contract is six live Voice outputs |
| `hydrate_opened_project` | LIVE | thin create-chain adapter delegates to the existing opened-project loader |
| `bookshelf_open` | LIVE | Workbench Inspector is the only visible project-open path |
| `ov_bookshelf` / Catalog management | LIVE | selected/opened isolation, search, hierarchy, archive/restore remain live |
| `grp_create_project` / `create_create_project_page` | LIVE | Workbench New Project entry remains functional |
| `p_sel` in historical docs/tests | DEFERRED | retained only as historical wording or negative structural assertions; not runtime state |

## Deletion and retention evidence

Deleted production surface:

- hidden Project Page builder and Project View handler module;
- all `p_*` Project Page component construction, outputs, and event wiring;
- selector-dependent Catalog helper APIs and `project_sel` dependency;
- `p_summary` from the `open_project` contract;
- hidden `nav_project` / `nav_create_project` buttons and handlers;
- dead app-level project creation/list-visibility/legacy bookshelf wrappers.

Retained deliberately:

- `SessionState.project`, `SessionState.selected_project`, `p_open`-free live page
  semantics, and selected != opened invariant;
- `ProjectService`, `ProjectCreationService`, Catalog relation status/filter semantics,
  hierarchy/search/archive guards, Runtime/TTS/Production/QA/Repair/Export/Merge/
  Assembly/Storage/Voice Cast/MCP/dependencies;
- `create_create_project_page()` and the visible Workbench New Project action;
- `open_project(name, ss)` and all existing downstream refresh order, with the hidden
  summary output removed because it had no live consumer.

Catalog management output contract is now 24 outputs, or 32 with the eight hierarchy
controls. All management refresh callbacks consume `[bookshelf_search, ss]`; the main
refresh scans the complete Catalog once, filters visible rows from that snapshot, and
uses the same snapshot for Book child counts. The regression case with three Chapters
and one matching search row verifies Parent + matched Chapter visibility, “关联 3 个
章节项目”, selected/opened preservation, and one scan.

Inspector presentation now renders `RELATION_ORPHAN` as
“⚠ 未归属章节 · {relation_message}” and `RELATION_INVALID` as
“⚠ 关系无效 · {relation_message}”; Catalog relation status and business logic are
unchanged.

## Follow-up validation record

- Follow-up head: `97fe339f6c905326fa79af20fb9c4e5358d1792e`;
- Full pytest: **1325 passed, 26 skipped**;
- Windows selected workflow: **329 passed**;
- CI run `32695647298`: Ubuntu success; Windows Python 3.10 selected workflow success;
- compileall: pass;
- Ruff `--select F` on changed Python files: pass;
- `git diff --check`: pass.

No IA-2C or new UI feature work was started. Project Page / `p_sel` retirement is
complete; future work must not reintroduce a hidden opened-project mirror.

---

## Round IA-2B / R3A — `project_manager` compatibility boundary

### Baseline and scope

R3A starts from the merged IA-2B baseline `e141696b4d87cee6d1d1537c9f4fbe465a18f813`
(PR #65, squash merge). This round only audits and narrows the production ownership
of `lib/project_manager.py`. It does not retire `p_sel`, delete Project Page
compatibility, or change Runtime/TTS/Production/QA/Repair/Export/Merge/Assembly,
Catalog, Workbench/Create UX, Voice Cast, MCP, dependencies, or storage semantics.

### Audit method and result

The complete repository audit covered:

- `rg` and qualified-name searches across production code, tests, scripts, MCP,
  docs, and re-exports;
- Python AST call/import inspection for every listed symbol;
- dynamic `getattr` / `setattr`, string callback keys, `import_module`, and
  monkeypatch references;
- root mutation and legacy-root resolution paths.

No hidden production caller was found outside the four migrated read/persistence
paths and the explicit root synchronization in `ProjectService.set_data_dir()`.
There are no `project_manager` imports or calls in `mcp_server` or scripts.

Before R3A:

```text
app.py                    -> lib.project_manager synthesis preference wrappers
services/synthesis.py     -> pm.open_project
lib/progress.py           -> pm.open_project (2 readers)
lib/snapshot.py           -> pm.open_project (stale reload reader)
ProjectService.set_data_dir -> pm.WORKSPACE_ROOT / pm.LEGACY_ROOT sync
lib/project_manager.py   -> ProjectRepository for every disk operation
```

After R3A:

```text
app.py                    -> ProjectService -> ProjectRepository
services/synthesis.py     -> ProjectRepository.load_project
lib/progress.py           -> ProjectRepository.load_project
lib/snapshot.py           -> ProjectRepository.load_project (lazy import)
ProjectService.set_data_dir -> ConfigRepository + ProjectRepository
                              + retained pm root compatibility sync
tests / legacy callers    -> lib.project_manager -> ProjectRepository
```

The canonical ownership rule is now explicit: UI uses `ProjectService`, lower-level
readers use `ProjectRepository` directly where a service layer would introduce a
cycle, and the old module remains a compatibility facade rather than a production
authority.

### Final symbol classification

| Symbol | Classification | Evidence / final handling |
|---|---|---|
| `_repository` | LEGACY_COMPAT | Only compatibility wrappers use it; it synchronizes mutable legacy roots into the repository. |
| `_resolve_dir` | LEGACY_COMPAT | No production caller; retained as a private legacy resolver wrapper. |
| `scan_projects` | TEST_COMPAT | Repository-owned production path; existing tests/legacy imports remain supported. |
| `create_project` | TEST_COMPAT | Repository/creation-service production path; wrapper retained for tests/legacy imports. |
| `open_project` | TEST_COMPAT | All production readers migrated; wrapper retained for tests/legacy imports. |
| `load_snapshot` | TEST_COMPAT | `ProjectService.open_project_as_snapshot()` owns production use; wrapper retained. |
| `delete_project` | TEST_COMPAT | `ProjectService.delete_project()` owns guarded production use; wrapper retained. |
| `get_project_dir` | TEST_COMPAT | Production services use `ProjectRepository`; wrapper retained. |
| `update_segment_status` | TEST_COMPAT | Production mutation paths use service/repository APIs; wrapper retained. |
| `get_remaining` | TEST_COMPAT | No production caller; wrapper retained for legacy/test recovery callers. |
| `_meta_path` | LEGACY_COMPAT | No repository caller; private wrapper retained for compatibility safety. |
| `_load_meta` | LEGACY_COMPAT | No repository caller; private wrapper retained for compatibility safety. |
| `_repair_meta` | LEGACY_COMPAT | No repository caller; private wrapper retained for compatibility safety. |
| `_save_meta` | LEGACY_COMPAT | No repository caller; private wrapper retained for compatibility safety. |
| `get_synthesis_overrides` | TEST_COMPAT | UI now routes through `ProjectService`; wrapper remains for existing tests/legacy callers. |
| `set_synthesis_overrides` | TEST_COMPAT | UI now routes through `ProjectService`; repository remains the persistence owner. |
| `_project_status` | TEST_COMPAT | Catalog/repository owns production derivation; existing style test uses the compatibility name. |
| `get_synthesis_selections` | TEST_COMPAT | UI now routes through `ProjectService`; wrapper remains for existing tests/legacy callers. |
| `set_synthesis_selections` | TEST_COMPAT | UI now routes through `ProjectService`; repository remains the persistence owner. |
| `WORKSPACE_ROOT` | LEGACY_COMPAT_SYNC | `ProjectService.set_data_dir()` keeps the mutable facade root synchronized; `ProjectRepository` remains disk authority. |
| `LEGACY_ROOT` | LEGACY_COMPAT_SYNC | Same explicit facade synchronization; the repository resolver still owns legacy-project fallback. |

No symbol was deleted in this round. The evidence-supported reduction is the
production caller surface: every disk read/write caller now bypasses the facade,
while the wrappers and mutable roots remain covered as a deliberate compatibility
shell. No `DEAD` classification is used for a retained wrapper because the purpose
of this round is to preserve the externally observable compatibility boundary until
a separately authorized retirement round proves it can be removed.

### Root and legacy compatibility proof

`WORKSPACE_ROOT` and `LEGACY_ROOT` remain module-level mutable variables. Existing
monkeypatch and integration assignments continue to flow through `_repository()`;
`ProjectService.set_data_dir()` synchronizes both the canonical repository and the
compatibility module. `ProjectRepository._resolve_dir()` still prefers a workspace
project and falls back to the legacy root. R3A regression tests exercise a mutable
workspace root, a legacy-only project, `ProjectService.open_project()`, progress
readers, and stale snapshot reload against the legacy project.

### R3A validation target

The independent R3A PR must report targeted project-manager/repository/service,
synthesis/progress/snapshot, Production, Runtime selected, Create/Open, Catalog,
Export isolation, Merge, Assembly, full pytest, Windows selected workflow,
compileall, Ruff `--select F`, and `git diff --check`. No IA-2B work is included.

## Round IA-2B / R3B — `project_manager` facade retirement audit

### Baseline and scope

R3B starts from merged PR #66, `720adb8643f6b390d5dcdc47ef7cdb0e5a6b9501`.
The branch is `refactor/project-manager-retirement-r3b`. This round is limited to
the `lib/project_manager.py` compatibility boundary. It does not retire `p_sel`,
Project Page compatibility, Project View compatibility, or change Catalog,
SessionState selected/opened semantics, storage/legacy fallback, Create/Open UX,
Runtime/TTS/Production/QA/Repair/Export/Merge/Assembly/Voice Cast/MCP, config, or
dependencies.

### Complete caller audit

The repository audit used `rg` and qualified-name searches, Python AST inspection,
imports/re-exports, `getattr`/`setattr`, `import_module`, string callback keys,
monkeypatch sites, tests, scripts, MCP, docs, tools, startup, migration, and
recovery paths. No production import or dynamic caller of the facade remains other
than the explicit root synchronization in `ProjectService.set_data_dir()`.

Before R3B, after the R3A production migration:

```text
app.py / services / lib readers -> ProjectService or ProjectRepository
ProjectService.set_data_dir   -> ConfigRepository + ProjectRepository
                                 + pm root synchronization
tests / legacy callers        -> lib.project_manager -> ProjectRepository
```

After R3B:

```text
production disk ownership     -> ProjectService / ProjectRepository
ProjectService.set_data_dir   -> ConfigRepository + ProjectRepository
                                 + explicit pm root compatibility sync
compatibility tests           -> pm public CRUD facade + mutable roots
external/unknown legacy API   -> retained pm public CRUD facade
```

`ProjectRepository` does not read `lib.project_manager` globals. The only reverse
direction is the deliberate `_repository()` synchronization performed when a
caller explicitly invokes the pm facade. R3B regression coverage proves that a
canonical Repository/Service call keeps its own roots when pm globals differ.

### Final classification of every audited symbol

Only the requested R3B categories are used below.

| Symbol | Final classification | Evidence and handling |
|---|---|---|
| `WORKSPACE_ROOT` | `LEGACY_COMPAT` | Module-level mutable legacy root; synchronized by `ProjectService.set_data_dir()` and consumed only when a caller enters the pm facade. |
| `LEGACY_ROOT` | `LEGACY_COMPAT` | Same compatibility contract; Repository still owns workspace-first/legacy-fallback resolution. |
| `_repository` | `COMPAT_INTERNAL` | Internal facade implementation that synchronizes the two mutable roots before delegating to `ProjectRepository`; it is not a public API. |
| `_resolve_dir` | `DEAD` | No production or external evidence in the audit; deleted. Canonical resolver remains `ProjectRepository._resolve_dir`. |
| `scan_projects` | `LEGACY_COMPAT_RETAINED` | Retained CRUD-looking wrapper for compatibility tests and unknown legacy integrations. No production caller or verifiable external consumer was found; production scanning uses `ProjectService`/`ProjectRepository`. |
| `create_project` | `LEGACY_COMPAT_RETAINED` | Retained creation wrapper for compatibility risk only; production creation uses `ProjectCreationService`/`ProjectRepository`. |
| `open_project` | `LEGACY_COMPAT_RETAINED` | Retained load wrapper for compatibility risk only; production open uses `ProjectService`/`ProjectRepository`. |
| `load_snapshot` | `LEGACY_COMPAT_RETAINED` | Retained snapshot wrapper for compatibility risk only; production snapshot ownership is `ProjectRepository`. |
| `delete_project` | `LEGACY_COMPAT_RETAINED` | Retained CRUD-looking wrapper for compatibility risk only; guarded production deletion remains in `ProjectService`. |
| `get_project_dir` | `LEGACY_COMPAT_RETAINED` | Retained path-resolution wrapper for compatibility risk only; production services use `ProjectRepository`. |
| `update_segment_status` | `LEGACY_COMPAT_RETAINED` | Retained status mutation wrapper for compatibility risk only; production mutation paths use Service/Repository APIs. |
| `get_remaining` | `LEGACY_COMPAT_RETAINED` | Retained recovery wrapper for compatibility risk only; canonical recovery logic remains in `ProjectRepository`. |
| `_meta_path` | `DEAD` | No pm wrapper caller; deleted. `ProjectRepository._meta_path` remains canonical and tested. |
| `_load_meta` | `DEAD` | No pm wrapper caller; deleted. Repository metadata loading is unchanged. |
| `_repair_meta` | `DEAD` | No pm wrapper caller; deleted. Repository repair semantics are unchanged. |
| `_save_meta` | `DEAD` | No pm wrapper caller; deleted. Repository atomic metadata writes are unchanged. |
| `get_synthesis_overrides` | `DEAD` | All production and test-only callers migrated to `ProjectService`/`ProjectRepository`; deleted from pm. |
| `set_synthesis_overrides` | `DEAD` | Same; persistence format and atomic-write owner unchanged. |
| `get_synthesis_selections` | `DEAD` | All production and test-only callers migrated to `ProjectService`/`ProjectRepository`; deleted from pm. |
| `set_synthesis_selections` | `DEAD` | Same; synthesis scope semantics unchanged. |
| `_project_status` | `DEAD` | Style/catalog test migrated to `ProjectRepository._project_status`; deleted from pm. |

No retained pm symbol is classified `PRODUCTION` or `TEST_ONLY_LEGACY`: production
callers were migrated and test-only callers were migrated. The repository contains
no verifiable real external consumer. Retaining the public-looking CRUD facade is a
conservative response to unknown legacy-integration risk, not evidence that an
external consumer has been proven. The retained facade is intentionally not deleted
mechanically.

### Test-only migration and compatibility test disposition

Historical fixtures that only created projects, resolved directories, loaded
snapshots, updated progress, or read synthesis selections were moved to the
canonical owner. This includes:

- `tests/test_dataframe_style.py`, `tests/test_o12_cancel_during_pause.py`,
  `tests/test_o12_pause_resume.py`, `tests/test_o5_preview.py`,
  `tests/test_progress.py`, `tests/test_project_service.py`,
  `tests/test_project_snapshot.py`, `tests/test_queue_b7.py`,
  `tests/test_session_snapshot.py`, `tests/test_snapshot_caching.py`,
  `tests/test_synthesis_service.py`, and
  `tests/workflows/test_synthesis_lifecycle.py`;
- root-only fixture imports were removed from the engine, runtime, production,
  startup, MCP, supplement, utility, and self-healing tests, including
  `test_engine_recycle_idempotency.py`, `test_engine_self_healing.py`,
  `test_followup_dual_engine_fixes.py`, `test_hotpath_optimizations.py`,
  `test_mcp_server.py`, `test_partial_production_scope.py`,
  `test_production_jobs.py`, `test_production_runtime.py`, `test_quick_tts.py`,
  `test_runtime_engine_bootstrap.py`, `test_runtime_shutdown.py`,
  `test_runtime_start_fail_fast.py`, `test_startup_phases.py`,
  `test_supplement_dual_engine_regression.py`,
  `test_supplement_progress_terminal.py`, and
  `test_utility_engine_selection.py`;
- the four duplicate synthesis-preference tests in `tests/test_project_manager.py`
  were removed after their invariants were confirmed in Repository/Service tests.

The following are retained as `TESTING_COMPAT_ITSELF`, not historical fixtures:

- `tests/test_project_manager.py` covers the public CRUD/status/recovery facade;
- `tests/test_project_manager_compat_r3a.py` covers mutable roots, legacy fallback,
  and the R3A production-reader boundary;
- `tests/workflows/test_data_dir_switch.py` explicitly verifies the old facade's
  root-switch behavior.

R3B adds `tests/test_project_manager_retirement_r3b.py`, which protects the exact
facade surface, the production import graph, independent pm/repository roots, and
the `ProjectService.set_data_dir()` synchronization contract.

### Final wrapper/root state

Deleted from `lib/project_manager.py`: `_resolve_dir`, `_meta_path`, `_load_meta`,
`_repair_meta`, `_save_meta`, `_project_status`, and all four synthesis preference
wrappers. Retained: the two mutable roots, `_repository()`, and the eight public
CRUD/status/recovery wrappers (`scan_projects`, `create_project`, `open_project`,
`load_snapshot`, `delete_project`, `get_project_dir`, `update_segment_status`, and
`get_remaining`).

`ProjectService.set_data_dir()` still updates `ProjectRepository.WORKSPACE_ROOT` /
`LEGACY_ROOT` and then mirrors those values into pm. This is compatibility root
synchronization only; it does not make pm the storage authority and does not
reintroduce a project selector or a second root state.

### R3B validation record

Local validation passed: full pytest `1330 passed, 26 skipped`; targeted
project-manager/repository/service, storage/migration, snapshot/progress/synthesis,
Create/Open, Catalog/Session, Production, Runtime selected, Export project-switch
isolation, Merge, Assembly, and MCP `335 passed`; Windows selected workflow `329
passed`; compileall, Ruff `--select F` on changed Python, and `git diff --check`
also passed. Final CI run `32723417477` completed with Ubuntu and Windows success. No
IA-2B work beyond this facade boundary is included.

## Post-R3B — 全仓 Code Entropy Audit（2026-08-24）

### Baseline、事实修正与范围

本节追加在 R3B 历史记录之后，不改写前面的阶段结论。

- PR #67 的最终 head 是 `c5193eaef027ec25eaec0aaa8618e277f6c2e75f`。
- PR #67 已按既有 squash 策略合并；merge commit / 当前 `origin/main` 是
  `a75c3e7725e0218cc4fd4467ac5dee2a085892ce`。
- 最终 CI 是 `32726842355`，Ubuntu 与 Windows 均成功。此前 audit 文案中的
  中间 run（包括 `32722861080` / `32723417477`）不是最终事实；本节只引用
  `32726842355`。
- 本轮分支：`audit/code-entropy-post-r3b`。
- 本轮只做审计和文档追加，不删除生产代码，不创建 R4A PR，不开始 R3C，
  不移动或删除 `lib/project_manager.py`，也不触碰 p_sel / Project Page /
  Project View 的正式退休。

审计对象覆盖 `app.py`、`ui/`、`services/`、`repositories/`、`lib/`、
`mcp_server/`、`scripts/`、启动与 launcher、打包/迁移/恢复路径、tests、
docs 与 re-export。仓库当前库存为：`app.py` 1 个、`ui/` 31 个、
`services/` 35 个、`repositories/` 11 个、`lib/` 27 个、
`mcp_server/` 13 个、`scripts/` 6 个、tests 140 个 Python 文件。

### 审计方法与证据边界

本轮使用以下交叉证据，不以单次 grep 命中数直接判定 dead：

- `rg` / qualified-name 搜索：定义、调用、import/from-import、`__all__`、
  re-export、字符串 callback key、Gradio `.click/.change/.submit/.select/.load`
  注册、MCP `_TOOLS` / `_HANDLERS`、scripts/subprocess、docs 和迁移/恢复说明。
- Python AST：函数/类定义、`Name` 与 `Attribute` 引用、Call 节点、import、
  callback 作为参数传递、字符串常量、`getattr/setattr/globals/locals`、
  `importlib` / `__import__`。
- 动态入口：显式检查 `ui/wiring/*` 的 callback dictionary、
  `ui.project_catalog_handlers._OPEN_PROJECT_CALLBACK`、MCP handler map、
  runtime callback / progress callback 和测试 monkeypatch。
- 平台路径：Windows 选中 workflow、`ProjectRepository` 的 workspace-first /
  legacy fallback、TaskRepository legacy JSON fallback、无黑框启动与
  `subprocess` 路径一起纳入 blast-radius 判断。

本仓库无法证明不存在未提交到仓库的真实外部 import；因此“无外部 caller”
只表示没有在 production、tests、scripts、MCP、docs、re-export、动态入口中
找到可验证 consumer。对于看起来像公共 API 的兼容壳，未知 legacy integration
风险不等于已经证明存在外部使用。

### 生产负向审计结果

在 production Python（排除 tests/docs/历史审计文本）中未发现下列已退休的
Workbench sink、dashboard caller 或 p_sel 入口：

```text
p_sel
ov_status       ov_progress     ov_task        ov_issues
ov_open         ov_voices       ov_synth       ov_export
refresh_overview
_dashboard_snapshot
grp-workbench-legacy-sink
ui/components/dashboard.py
```

这些名字当前只在长期 architecture regression tests 的负向断言、历史文档或
测试说明中出现。`ui/pages/overview_page.py` 中的 `assembly_dashboard` 是
Whole-book Assembly 的 live output，不是已删除的 Workbench dashboard sink，
不能误删或误归类。

生产代码中与 `project_manager` 相关的唯一引用仍是
`services/project.py::ProjectService.set_data_dir()` 对
`ProjectRepository.WORKSPACE_ROOT/LEGACY_ROOT` 和兼容模块 roots 的显式同步；
没有重新引入 production CRUD caller。

### Ownership map

| 领域 | canonical owner | 第二层 caller / adapter | 状态与 entropy 判断 |
|---|---|---|---|
| Project lifecycle | `ProjectCreationService` + `ProjectService` + `ProjectRepository` | Create UI、Workbench `bookshelf_open`、MCP project/script adapters | 生产路径已经收口；`lib/project_manager.py` 只是保留的 `LEGACY_COMPAT` facade。 |
| Catalog / hierarchy | `ProjectCatalogService` | `ui/project_catalog_handlers.py`、Catalog wiring、Whole-book Assembly | Catalog 负责关系状态、搜索和 hierarchy；主刷新使用一次 `scan()` snapshot，再 `filter_projects()` 与 `hierarchy_from_summaries()`，不可把 visible subset 当结构统计。 |
| Session / snapshot | `SessionState` 的 `project` / `selected_project`；`ProjectSnapshot` 是 opened asset cache | `app._snap`、Create/Open、Voice、Production、Review、Export | opened / selected 真相源清晰，但 `script` / `bindings` 同时存在于 SessionState 与 Snapshot，是本轮最高风险的重复载荷。 |
| Voice assets / Voice Cast | `VoiceAssetService`、`VoiceCastResolver`、Voice repositories | `ui/wiring/voice_wiring.py`、app legacy-manual fallback、MCP voice adapters、Repair | 新 Voice Cast 与旧 manual project 双轨是有意兼容，不是可由 grep 删除的死代码。隐藏 `v_preview_*` 仍有注册事件和测试覆盖。 |
| Production / Runtime / TTS | `ProductionJobService`、`ProductionRuntime` / `ProductionRuntimeClient`、`TaskRepository` | app observers、UI、MCP production adapters；`SynthesisService` direct/legacy adapter | durable task/runtime 是 owner；`SynthesisService.persist_task` 与 legacy JSON 是兼容 transport，不能在本轮清理。 |
| QA / Repair | `QualityService` + `QualityRepository`；`RepairService` | Review UI、Repair observer、MCP quality adapters | revision、repair history 和 technical QA 由 service/repository 持有；UI 只渲染/发起。 |
| Export / Delivery | `ExportService` + `services.delivery` + `QualityRepository` history | `ui/export_handlers.py`、runtime export worker、MCP export adapters、Workflow | Export A/B project isolation、delivery hash、manifest 和 ownership fence 都是 live contract；任何 alias 清理需另行验证。 |
| Merge / Assembly | Chapter Merge Planner/Executor；Whole-book Assembly Service/Operations | merge/assembly UI handlers、Catalog hierarchy | 两套 planner/executor 是不同业务边界；Assembly 读取 Catalog，但不拥有 Catalog。冻结。 |
| Settings / data-dir | `ConfigRepository` + `lib.config`；data-dir mutation 在 `ProjectService.set_data_dir` | Settings UI、environment resolver、pm root sync | raw config/profile/env 多源是 dual-engine 兼容层；data-dir 切换还负责 Session reset 和 Catalog/Assembly refresh。冻结。 |
| MCP | `mcp_server/server.py` 的显式 `_TOOLS` / `_HANDLERS` | `mcp_server/tools/*.py` thin adapters → services | 无 UI callback、无 p_sel、无 pm facade caller；adapter 只是 JSON-RPC contract transport。 |
| Startup / recovery | `launcher.py`、`lib.environment`、`lib.startup`、`ApplicationLifecycleService`、`ProductionRuntimeClient` | scripts diagnostics、runtime logs、TaskRepository startup fields | 启动阶段、runtime recovery、Windows no-window 行为均为 live reliability surface。 |
| Storage / migration / backup | `ProjectRepository`、`ProjectStorageRepository`、`ProjectStorageService`、`project_paths`、`ProjectBackupService` | Catalog management UI、acceptance/benchmark scripts、recovery paths | v1/v2/v3 resolver、legacy relative paths、backup/rollback、unknown-file preservation 都是合法兼容与恢复职责。 |

### 候选矩阵

下表给出本轮发现的可讨论 candidate。Windows 表示删除/收口后必须覆盖的
Windows 相关风险，不表示当前已存在 Windows bug。

| 优先级 / 分类 | symbol / module | caller graph 与冗余原因 | 删除风险、测试与 Windows 影响 | 建议 |
|---|---|---|---|---|
| P0_BLOCKER | SessionState.script / SessionState.bindings 与 ProjectSnapshot.script / bindings | app.open_project、Create handler 同时 set_project(...) + set_snapshot(...)；Voice bind 先 mutate ss.bindings，再重建 snapshot；页面有的读 ss.script，有的读 _snap(ss).script。 | 高：晚到 callback、snapshot reload、跨页面 refresh 可能暴露两份不同载荷。test_session_snapshot.py、Create/Open、Catalog state、snapshot caching、Windows selected workflow 覆盖当前契约，但没有证明长期 canonicalization 已完成。 | 先保留。下一次涉及它必须先定义“snapshot 是 cache 还是唯一 payload”，并增加 divergence test；不能借此改变 SessionState.project / selected_project。 |
| HIGH_VALUE_CLEANUP | app.py::do_supplement_synth、app.py::do_quick_tts_synth | 当前 utility UI 只接 do_utility_tts_synth；两个旧函数只是 pass-through。do_supplement_synth 仍被 tests/test_supplement.py、tests/test_supplement_progress_terminal.py 和历史设计文档直接引用；do_quick_tts_synth 在仓库内没有直接 caller。 | 中：顶层函数可能被未提交的外部脚本 import；补录 progress/terminal contract 不能丢。现有 supplement/utility tests 与 Windows workflow 可覆盖，项目/声音来源隔离也必须保留。 | 唯一建议的 R4A 候选：只审计并退休这两个 utility compatibility pass-through；不动 shared entrypoint、export、preview、Session 或 TTS service。 |
| MEDIUM_VALUE_CLEANUP | ProjectCatalogService.scan/search_projects/get_summary 与 UI 的 explicit scan → filter | search_projects() = filter_projects(scan(), query)；get_summary() 重新 scan()；render_bookshelf_rows 无 snapshot 时走 convenience API，而主 Workbench refresh 已传递完整 snapshot。重复主要是 in-memory normalization / convenience surface，不是额外磁盘 scan。 | 高：改变 filter_projects 或 hierarchy normalization 会影响 search parent isolation、Book child count、orphan/invalid、Assembly。test_project_catalog*、hierarchy、Catalog state、Windows archive/search 覆盖。 | 记录为 Catalog API consolidation，先不改语义；未来应以显式 Catalog snapshot contract 为前提，不得机械删除 search_projects。 |
| MEDIUM_VALUE_CLEANUP / DEFERRED | services/delivery.py::build_delivery_input_snapshot / build_delivery_input_hash | 两个 descriptive alias 只是绑定到 compute_*；当前 production caller 使用 compute_*，别名只由 __all__ 暴露，没有 repo 内 direct caller。 | 中高：外部 import 风险未知；Export/Workflow freshness hash、manifest 与 A/B project switch 都依赖 canonical computation。test_delivery_freshness.py、Export phase4、project isolation 与 Windows selected workflow 相关。 | 继续保留并冻结到 Export audit；不要在 R4A 触碰。 |
| DEAD / LOW_VALUE_RESIDUE | app.py::migrate_project_copy | AST、qualified rg、Gradio event registration、MCP、scripts、tests、docs 均只有定义；没有 .click / .then / callback dictionary 引用。它只转调仍被测试直接覆盖的 ProjectStorageService.migrate_to_projects_root。 | 删除 blast radius 低，Windows 只涉及未接线的提示 handler；但它可能代表被撤掉的用户入口，不能把 service 误删。 | 可作为独立微清理，排在 utility R4A 之后；本轮不删除。 |
| LOW_VALUE_RESIDUE / LEGIT_ADAPTER | VoiceAssetService.list_voice_assets/get_voice_asset、模块级同名 wrappers | MCP adapter 实际调用 list_assets/get_asset；app、Repair、Voice Cast 也使用 asset_id_for_path/get_record/resolve_path。别名没有仓库内 direct production caller，但通过 __all__ 保留外部发现面。 | 低至中：无 Windows workflow 语义变化，未知 external import 风险仍存在。Voice asset boundary、Voice Cast、partial production tests 覆盖 canonical API。 | 暂归 LEGIT_ADAPTER；需先定义 public service API 才能删除。 |
| LOW_VALUE_RESIDUE / LEGIT_ADAPTER | ui/project_catalog_handlers.reconcile_bookshelf_selection、各 UI module 的 _update | wiring 使用 reconcile_bookshelf_selection_context；旧短名仍被 direct tests 使用。_update 在 Catalog/Create/Merge/Assembly 各自只包装 gr.update，不是业务 owner。 | 低：输出 tuple 长度、Gradio update semantics 和 Windows event payload 容易被机械重构破坏；相关 bookshelf/merge/assembly tests 已覆盖。 | 不合并成“大 UI utility”作为顺手清理；保留兼容短名，未来按页面 contract 分批评估。 |
| LEGIT_COMPAT | lib/project_manager.py roots / _repository / 八个 public-looking wrappers | production 只有 ProjectService.set_data_dir 的 root sync；旧 tests、legacy callers、未知 integrations 仍可经 facade 进入 ProjectRepository。 | 高：workspace-first / legacy fallback、mutable root monkeypatch、Windows 路径和旧脚本都可能受影响。R3A/R3B project-manager tests 与 full CI 已覆盖，但没有 external consumer 证据。 | R3C 冻结：不删 facade、不删文件、不移动、不删除 root sync；保留是未知 legacy 风险的保守决策，不是已证明 external usage。 |
| LEGIT_COMPAT | ProjectRepository / project_paths 的 v1/v2 aliases、relative resolver、TaskRepository._migrate_legacy_json / _legacy_load | 旧项目打开、旧相对路径、项目无 DB 时的 recovery/load path、storage migration、acceptance scripts 和 tests 共同消费。 | 高，尤其 Windows legacy layout、junction/普通目录 fallback、任务恢复和备份 rollback；storage migration / project repo / task repo / Windows selected tests 覆盖。 | DO_NOT_TOUCH，除非有独立迁移/恢复 round 与 fixture matrix。 |
| LEGIT_COMPAT | app legacy-manual Voice Cast、services.delivery / QualityService legacy audio fallback、SynthesisService.persist_task | 没有 Character Roster / Voice Cast 的旧项目仍由 app/status、delivery fingerprint、quality revision 和 direct synthesis callers 识别。 | 高：删除会使旧项目不可读或改变 production readiness；Voice Cast、delivery freshness、quality、supplement/production tests 与 Windows workflow 相关。 | 保留。不能把“当前新 UI 不产生”当成 dead。 |
| DEFERRED | Settings 多源 config / TTS aliases：ui/settings_handlers、ConfigRepository、lib.config、lib.environment | UI 同时读取 raw JSON、profile、env、resolved model dirs，并写 profile + raw keys；这是 dual-engine / rollback compatibility。 | 高：Runtime/TTS engine selection、recycle、startup/prewarm、Windows process behavior；大量 TTS/runtime tests 与 selected CI 覆盖。 | 本轮冻结 Runtime/TTS/Settings semantics；不在 R4A 处理。 |
| TEST_ONLY | tests 中对 WORKSPACE_ROOT / LEGACY_ROOT、Gradio page dict aliases、旧 handler names 的 monkeypatch/direct calls | 这些是 fixture isolation、contract tests 或 compatibility self-tests，不是 production caller。 | 不应以 test-only grep 命中恢复生产代码；删除测试会降低 boundary proof，Windows tests 也使用相同 root isolation pattern。 | 保留测试证据；若未来删除兼容 API，先迁移对应 contract tests，再删 API。 |
| DEAD | ov_* hidden Workbench sinks、refresh_overview、_dashboard_snapshot、grp-workbench-legacy-sink、ui/components/dashboard.py | production AST/rg/wiring/MCP/scripts 中无 caller；长期 test_project_page_contract_r2b.py 负向断言负责防回归。 | 已删除；本轮不重复删除。唯一风险是误把 live assembly_dashboard 归入旧 dashboard。 | 永久负向 invariant 保持；分类为 DEAD，不重新引入。 |

### 重点五项与排序

1. P0_BLOCKER：SessionState 与 ProjectSnapshot 的重复 mutable payload。它不是
   立即删除目标，而是之后所有 Session/页面清理的前置架构决策。
2. HIGH_VALUE_CLEANUP：do_supplement_synth / do_quick_tts_synth 两个
   utility pass-through。当前 visible utility UI 已有统一 owner，清理能减少
   顶层 handler contract；外部 import 风险必须先经过同样的完整 caller audit。
3. MEDIUM_VALUE_CLEANUP：Catalog convenience API 与 snapshot-aware main path
   的边界。可减少重复 normalization，但任何修改都可能触碰 search、hierarchy
   和 selected/opened isolation。
4. MEDIUM_VALUE_CLEANUP / DEFERRED：Delivery hash 的 descriptive aliases。
   熵值很低但 Export freshness / delivery semantics 风险很高，因此不应优先。
5. DEAD / LOW_VALUE_RESIDUE：未接线的 migrate_project_copy。删除很安全，
   但业务收益低，排在 utility contract 清理之后。

### 唯一推荐的下一轮：R4A Utility Compatibility Entry-point Retirement

这是建议，不是本轮实现。R4A 的精确范围只能是：

- app.py::do_supplement_synth；
- app.py::do_quick_tts_synth；
- 这些函数的 direct tests / historical design references 的迁移或删除；
- 证明 do_utility_tts_synth 是唯一 visible utility synth entrypoint。

R4A 明确不包括：migrate_project_copy、ProjectCatalogService API、
lib/project_manager.py、p_sel、Project Page/View、SessionState 字段、
do_supplement_export / do_quick_tts_export、Export/Delivery、Voice Cast、
Production/Runtime/TTS、QA/Repair、Storage/MCP 或 navigation topology。

#### R4A acceptance matrix（仅规划）

| 检查 | 必须证明 |
|---|---|
| Static caller audit | production、tests、scripts、MCP、docs、__all__、dynamic getattr / string callback 中不再需要两个旧 synth entrypoint；visible wiring 仍只接 do_utility_tts_synth。 |
| Project-role behavior | 统一入口仍返回原有补录 WAV / terminal progress / task metadata；旧 do_supplement_synth 的成功、0 成功、异常 progress tests 迁移后全部通过。 |
| Library-voice behavior | Quick TTS 的声音库校验、engine selection、preview/export 与原结果保持一致；不得把 library voice 误接入 opened project。 |
| Session isolation | SessionState.project、selected_project、utility result project marker、Export project-switch isolation 不改变；不新增 mirror。 |
| UI / navigation | visible nav、Voice Cast、Production/QA、Delivery、Workbench Create/Open chain 完全不变；不触碰隐藏 sink 回归断言。 |
| Windows | Windows selected workflow、utility/Gradio callback tests、no-window behavior 通过；不改变 runtime process 或 TTS engine policy。 |
| Repository gates | targeted utility/supplement tests、Catalog/Session/Open、Voice、Production、Export isolation、Merge、Assembly、full pytest、Windows selected workflow、compileall、Ruff --select F、git diff --check。 |

若实施前发现正式 public API 或未提交的 packaging entrypoint 依赖这两个函数，
则 R4A 不应删除它们，应把它们改列为 LEGIT_COMPAT；本轮没有取得这样的
外部证据，也没有授权扩大审计以外的动作。

### 本轮冻结与交付结论

以下结论保持不变：

- SessionState.project 是 opened truth，selected_project 是 selected truth；
  search 不改变 opened，open 只通过 Workbench Inspector bookshelf_open。
- Create success gate、Book child count 完整 Catalog snapshot、orphan/invalid
  presentation、archive revision、hierarchy、Voice Cast、Production/Runtime、
  QA/Repair、Export A/B isolation、Merge、Assembly、storage legacy fallback 与
  MCP contract 均冻结。
- p_sel、Project Page/View、project_manager facade/root sync、Catalog
  filter_projects 语义和所有生产兼容读取路径均未触碰。
- 没有执行 R4A；没有修改生产 Python；本轮只追加本节审计文档。

本轮文档追加后的验证只需 git diff --check 与工作树检查；生产行为验证复用
PR #67 最终 CI 32726842355（Ubuntu + Windows success）及 R3B 已记录的 full
pytest 1330 passed, 26 skipped / Windows selected workflow 329 passed。审计
分支应保持 clean，并作为 R4A 之前的独立证据基线。

## R4A Closure — Utility Compatibility Entry-point Recheck（2026-08-24）

本节只记录 R4A 调查的停止结论，不实现 R4A，也不改变前文历史结论。

### Baseline

- PR #68 merge / `origin/main` baseline：`e08024650306ea00d6de44b7dfa441ae63ffc34a`。
- R4A 调查分支：`refactor/utility-entrypoint-retirement-r4a`。
- visible utility synthesis wiring 仍唯一指向 `app.py::do_utility_tts_synth`。
- targeted supplement / utility / Quick TTS baseline：`58 passed, 1 skipped`。

### Caller recheck

重新执行了 `rg`、AST（definition / Name / Attribute / Call / import）、动态字符串、
`getattr` / `setattr` / `globals` / `locals`、callback dictionary、Gradio event、
MCP、scripts / subprocess、tests / monkeypatch、re-export / `__all__` 审计：

| Symbol | 重新确认的 caller evidence | 分类 |
|---|---|---|
| `do_utility_tts_synth` | `app.py` 定义；`utility_synth.click(...)` 生产 wiring；已有统一 utility tests | `LIVE_PRODUCTION / CANONICAL` |
| `do_supplement_synth` | 仅 `tests/test_supplement.py`、`tests/test_supplement_progress_terminal.py` 与历史设计文档；无生产、MCP、scripts 或动态 caller | `LEGACY_COMPAT_ADAPTER` |
| `do_quick_tts_synth` | 仅自身定义；无仓库内 production、tests、scripts、MCP、docs live example 或动态 caller | `LEGACY_COMPAT_DEFERRED` |

### Contract gate

两个旧入口均不能按 pure pass-through 直接退休：

- `do_supplement_synth` 保留旧的补录参数合同（含 `sup_mode`、JSON role/lines），
  直接转发到 `_synthesize_project_utility`，并返回旧二元组；canonical utility
  project branch 固定映射为 paste mode，并返回四元组。
- `do_quick_tts_synth` 为 canonical library mode 注入固定默认参数，并将 canonical
  四元组压缩为旧二元组。

因此触发 R4A 明确停止条件：

**R4A = `CLOSED_NO_CHANGE / NOT_PURE_PASS_THROUGH`。**

本结论意味着：本轮不迁移测试、不删除两个函数、不改变
`do_utility_tts_synth`、Quick TTS、Supplement、Session 或 TTS service。R4A 不创建
implementation PR；如未来要继续，必须先重新定义这两个旧合同的兼容边界。

## Round R4B — SessionState / ProjectSnapshot Ownership Audit（2026-08-24）

本节是 R4B 的完整 ownership / caller audit。它只追加审计事实与下一轮建议，
没有修改生产 Python、没有删除字段、没有改变 callback / UI contract，也没有执行
R4C。

### Baseline 与审计边界

- latest `origin/main` baseline：`be75f0d52508f913df22ff35143f53ec20ef34e5`。
- 分支：`audit/session-snapshot-ownership-r4b`。
- 分支建立前，R4A 的停止结果已经在上一节单独以 doc-only PR 收口：
  `CLOSED_NO_CHANGE / NOT_PURE_PASS_THROUGH`。
- 冻结 `SessionState.project` = opened、`selected_project` = selected；不引入
  `p_sel`、hidden mirror、第二个 selector 或新的 Snapshot 字段。
- 本轮只检查 ownership、读写路径、身份关系、stale reload、late callback、持久化
  边界与测试缺口；没有把审计建议实施为代码。

### 审计方法与覆盖面

对 production、tests、scripts、MCP、docs 以及 import / re-export surface 执行了
`rg` 与 AST audit，覆盖 `Name`、`Attribute`、`Assign`、`AnnAssign`、`AugAssign`、
`Call`、`Return`，并复核：

- `SessionState.project`、`selected_project`、`script`、`bindings`、
  `project_snapshot` 的所有显式 writer / reader；
- `ProjectSnapshot` 的 `name`、`meta`、`script`、`bindings`、`build`、
  `reload_if_stale`、stale detection 与 project directory identity；
- `set_project`、`set_snapshot`、`ensure_snapshot`、`invalidate_snapshot`、
  `clear_opened`、`reset_for_data_root`、`open_project`、Create hydrate、Voice
  binding、repair / stale reload、Production、Review / QA、Export、archive / delete、
  data-dir、Merge、Assembly；
- `getattr` / `setattr` / `globals` / `locals` / `vars` / `__dict__` / `importlib`、
  string callback keys、Gradio event inputs / outputs、callback dictionaries、MCP
  handlers、tests / monkeypatch / fixtures / fakes、scripts / recovery / migration /
  startup。

结论是：没有发现绕过上述显式路径、以动态字段名重新写入 Session payload 或
ProjectSnapshot payload 的隐藏 writer。`services/__init__.py` 对类型的 re-export
不构成 field access。需要特别纠正命名：当前 `ProjectSnapshot` 没有
`project_name` 字段，实际身份字段是 `name`；`ProjectMeta.project_name` 是持久化
metadata 中的项目名。

### Durable truth 与内存层角色

持久化真相在 `ProjectRepository` 及其项目文件，不在 `SessionState` 或
`ProjectSnapshot`：

- `structured_script.json` / canonical script 的 durable owner 是
  `ProjectRepository` 及创建、合并、存储相关 service；
- `voice_bindings.json` 的 durable owner 是 `ProjectRepository`，Voice Cast / UI / MCP
  通过 service 或 resolver 写回；
- `project.json` 与 status journal 提供 `ProjectMeta` 的持久化来源；
- `SessionState` 是每个 Gradio session 的运行时容器；
- `ProjectSnapshot` 是按 opened project 建立的内存 cache / read view，不是第二个磁盘
  真相源。

当前 Open / Create 的初始路径是：

```text
ProjectRepository.load_snapshot(name)
        -> ProjectSnapshot.build(...)
        -> SessionState.set_project(name, snapshot.script, snapshot.bindings)
        -> SessionState.set_snapshot(snapshot)
```

因此在刚打开或刚创建成功的正常同步路径上，`SessionState.script` 与
`ProjectSnapshot.script`、`SessionState.bindings` 与 `ProjectSnapshot.bindings` 内容相同，
并且当前实现还共享同一份对象引用。这是 `DUPLICATED_BUT_SYNCHRONIZED` 的初始状态，
不是独立 ownership。

`selected_project` 只由 bookshelf selection 写入；选中不会打开项目、不会加载 script、
不会写 `project` 或 payload。archive / catalog reconciliation 在 opened 项目被删除、
归档或从当前 catalog 消失时清理 opened payload；selected ≠ opened 时会保留当前
opened 项目。

### Ownership matrix

| State | Candidate owner | Writers | Readers | Mutable? | Rebuild / source | Current role / classification |
|---|---|---|---|---|---|---|
| `SessionState.project` | Session opened identity | `set_project`、`clear_opened`、Open / Create | 全部需要当前 opened project 的 callbacks、Export / Production / Voice / Catalog | 是 | 显式 Open / Create name；不是从 selected 推导 | `CANONICAL`（opened truth，冻结） |
| `SessionState.selected_project` | Workbench Catalog selection | `set_selected`、archive / catalog handlers | archive、Merge、Assembly confirmation、bookshelf UI | 是 | 当前 bookshelf selection | `CANONICAL`（selected truth，冻结） |
| `SessionState.snapshot` | 不存在此字段；实际字段是 `project_snapshot` | 无 | 无 | 不适用 | 见 `SessionState.project_snapshot` | `NOT_PRESENT`；不新增 alias |
| `SessionState.script` | `ProjectRepository` durable data；Session 仅保留兼容副本 | `set_project`；没有 production script item writer | supplement / utility / legacy production readers、部分 app callbacks | 是；raw dict | Open / Create 时来自 Snapshot；stale reload 不回写 | `COMPAT_MIRROR`；初始为 `DUPLICATED_BUT_SYNCHRONIZED`，stale reload 后进入 `CONFIRMED_DIVERGENCE` |
| `SessionState.bindings` | `ProjectRepository` durable `voice_bindings.json`；Session 仅保留兼容副本 | `set_project`、`bind_voice` 中的 in-place role write、clear；随后部分路径整体替换 | legacy supplement / utility readers、binding presentation、绑定兼容路径 | 是；有直接 in-place mutation | Open / Create 或 bind 后从 Snapshot；`ensure_snapshot` 不回写 | `COMPAT_MIRROR`；初始为 `DUPLICATED_BUT_SYNCHRONIZED`，stale reload 后进入 `CONFIRMED_DIVERGENCE`；另有 alias `STRUCTURAL_RISK` |
| `SessionState.project_snapshot` | Session 内的 opened snapshot handle | `set_snapshot`、`ensure_snapshot`、fallback rebuild、`invalidate_snapshot` | `_snap`、Voice page、现代 Production / QA / Review / status / preview readers | 是；引用可替换 | `ProjectRepository.load_snapshot` 或 `reload_if_stale` | `CACHE`；缺少 name/project identity fence，存在 `STRUCTURAL_RISK` |
| `ProjectSnapshot.project_name` | 不存在此字段 | 无 | 无 | 不适用 | 实际字段 `name`；持久化 metadata 为 `ProjectMeta.project_name` | `NOT_PRESENT`；不新增 duplicate identity field |
| `ProjectSnapshot.name`（不是 `project_name`） | Snapshot identity copied from Open name；durable name 仍来自 Repository | `build` / `reload_if_stale` 创建新对象 | `_snap`、现代 UI / service readers | dataclass 可变，但没有 direct production writer | Open name + `ProjectRepository.load_project(name)` | `CACHE` identity；应与 `SessionState.project` 校验但当前未校验 |
| `ProjectSnapshot.meta` | `project.json` / status journal | `build` / reload；没有 Session mirror | modern status、catalog-adjacent production / QA readers | dataclass 可变；无 direct snapshot writer | `ProjectRepository._load_meta` | `CACHE / DERIVED` |
| `ProjectSnapshot.script` | `structured_script.json` | `build` / reload；没有 direct snapshot item writer | modern Voice / Production / Review / QA / preview readers | dict 可变；当前与初始 Session script alias | `ProjectRepository.load_project` | `CACHE`；与 Session mirror 的 stale divergence 是 `CONFIRMED_BUG` |
| `ProjectSnapshot.bindings` | `voice_bindings.json` | `build` / reload；UI / resolver 先写 durable，再重建 | modern Voice / Voice Cast / Production readers | dict 可变；当前与初始 Session bindings alias | `ProjectRepository.load_project` | `CACHE`；与 Session mirror 的 stale divergence 是 `CONFIRMED_BUG`，alias 为 `STRUCTURAL_RISK` |

这里的 `COMPAT_MIRROR` 不是建议增加的新 mirror，而是记录当前仍被 legacy
supplement / utility 代码读取的既有字段。`ProjectSnapshot.script` / `bindings` 的
`CACHE` 分类也不等于它们拥有 durable truth；它们是现代 opened workflow 的当前读视图。

### Mutation-site classification

| Path | Script operation | Bindings operation | Classification / observation |
|---|---|---|---|
| Open / Create hydrate | `ProjectRepository.load_snapshot` → Snapshot `script` → `set_project` assignment | 同一 Snapshot `bindings` → `set_project` assignment | `RELOAD_FROM_REPOSITORY` + `REPLACE_WHOLE_OBJECT`；初始两边同步且共享引用 |
| `SessionState.set_project` | 替换 `self.script` 引用 | 替换 `self.bindings` 引用 | `REPLACE_WHOLE_OBJECT`；不建立 Snapshot identity contract |
| `SessionState.set_snapshot` | 不触碰 Session script | 不触碰 Session bindings | Snapshot handle `REPLACE_WHOLE_OBJECT`；不是 payload sync |
| `SessionState.ensure_snapshot` / `_snap` / Voice fallback | stale 时 Repository reload 只重建 Snapshot script | stale 时 Repository reload 只重建 Snapshot bindings | `RELOAD_FROM_REPOSITORY`；只更新一边，已触发 `CONFIRMED_DIVERGENCE` |
| `bind_voice` | 不修改 Session script | durable write 后 `ss.bindings[role] = dest`，再整体替换为 fresh Snapshot bindings | `PERSIST_TO_REPOSITORY` + `IN_PLACE_MUTATION` + `RELOAD_FROM_REPOSITORY` + `REPLACE_WHOLE_OBJECT`；中间 alias 有风险 |
| Voice Cast / MCP resolver | 不直接改 Session script | 直接持久化完整 bindings document，无 Session writer | `PERSIST_TO_REPOSITORY`；下一次 stale reload 才进入 Snapshot |
| Production / Voice / Review / QA / Export readers | 不修改 | 不修改 | `READ_ONLY`（现代路径多读 Snapshot；legacy utility/supplement 读 Session mirror） |
| `clear_opened` / data-root reset | 置 `None` | 替换为空 dict | `REPLACE_WHOLE_OBJECT` + `RESET`；可见 data-dir UI 路径受保护 |

因此不能把两套字段描述成“所有 writer 都严格双写”：至少
`ensure_snapshot()` 是合法且现存的只更新 Snapshot 的路径，外部 durable binding writer
也不会直接更新当前 Session。

### Stale reload 与真实 divergence reproducer

`SessionState.ensure_snapshot()` 当前只执行：

```text
fresh = self.project_snapshot.reload_if_stale()
self.project_snapshot = fresh
return fresh
```

它不会同步 `SessionState.project`、`SessionState.script` 或
`SessionState.bindings`，也不会验证 `fresh.name == self.project`。app `_snap()` 与
`ui.voice_handlers._snapshot()` 的 fallback rebuild 同样只 `set_snapshot`，不回写
Session payload。

已用临时目录做真实 reproducer（没有留下仓库文件）：

1. 打开同一个项目，建立 `SessionState` 与 Snapshot 的初始 alias；
2. 从 Repository 外部更新 `structured_script` 标题为 `B`，并更新 durable binding 为
   `new.wav`，使关键文件 mtime 晚于 `loaded_at`；
3. 调用 `ss.ensure_snapshot()`；
4. 观察 Session mirror 与 fresh Snapshot。

结果：

```text
OPEN_IDENTITY True True
STALE_RELOADED True
SESSION_SCRIPT_TITLE A
SNAPSHOT_SCRIPT_TITLE B
SESSION_BINDING None
SNAPSHOT_BINDING new.wav
POST_RELOAD_IDENTITY False False
DIVERGENCE True True
```

这不是普通 Open A → Open B 顺序下的猜测，而是实际 stale reload 后同时出现的
script / bindings 内容分歧。因此本轮对两个 Session payload 字段标记
`CONFIRMED_DIVERGENCE`，最终决策为 `CONFIRMED_BUG`。当前测试没有把这个 reproducer
固化成新测试，因为本轮明确是 audit-only；缺口与 R4C 范围在下文单独列出。

### 关键调用链与风险

#### 1. Voice binding / 外部 durable update（已确认）

`VoiceCastResolver`、MCP voice-cast handler 与 UI 都可以先写
`voice_bindings.json`。现代 Voice / Production 读 Snapshot；legacy supplement / utility
仍读 `SessionState.bindings`。当外部写盘触发 stale reload 时，`ensure_snapshot()` 只替换
Snapshot，旧 Session binding 继续被兼容 reader 使用，形成同一个 opened project 的两种
运行视图。UI `bind_voice` 的正常路径会在写盘后主动重建 Snapshot 并整体替换
`ss.bindings`，但这不能修复外部 writer、stale reload 或异常中断窗口。

此外，Open 初始 alias 使 `ss.bindings[role] = dest` 也可能先改到旧 Snapshot 的同一
dict，再执行 durable write / reload；这是共享可变对象带来的额外 `STRUCTURAL_RISK`。

#### 2. Open A → Open B 与 generic late callback（结构性风险）

正常同步 Open 会先 `set_project` 再 `set_snapshot`，现有 selected/opened 测试覆盖了
常规 A / B 隔离。但 `selection_revision` 只保护 selected / archive confirmation，
不是 opened payload generation；通用 `_open_chain_rest` 也没有像 Export task 或
Review / Repair fence 那样绑定 project / generation。当前没有通用断言保证
`Snapshot.name == SessionState.project`，`_snap()` 在 mismatch 时仍会返回 Snapshot。

因此 late A callback 或并发 Open A/B 可能把 A 的普通 UI 输出带入 B 的 opened workflow，
或者在 `set_project` 与 `set_snapshot` 两步之间观察到混合状态。Export 有明确的
project/task fence，Review / Repair 有 generation/project/repair/task fence；这些局部
保护不能推导为全局 Session/Snapshot fence。该风险尚未在普通单线程生产路径中复现，
分类为 `STRUCTURAL_RISK`，不是第二个 `CONFIRMED_BUG`。

#### 3. data-dir / archive-delete 与 stale identity（UI 有保护，边界仍有风险）

Settings UI 只有在 `ProjectService.set_data_dir()` 成功后才调用
`ss.reset_for_data_root()`，该 reset 清空 selected、opened、script、bindings、
Snapshot、synthesis，同时按 contract 保留 catalog query；因此当前可见 data-dir
workflow 的同名项目隔离是受保护的。

但 `ProjectService.set_data_dir()` 本身不持有 SessionState；若未来脚本、MCP 或其他
调用者直接切 root 而不经过 UI reset，旧 Snapshot 会保留旧 `project_dir`，而
`reload_if_stale()` 通过当前 Repository root 读取数据，Session mirror 也不会同步。
外部 archive / delete 同样可能令 stale detection 后的 reload 抛出 not-found，而不会
自动把 Session 清空。现有 archive handler / catalog reconcile 对可见路径做了清理，
但没有覆盖所有外部 mutation。

三条风险链的最终分类如下：

| Mutation chain | 结果 | 分类 |
|---|---|---|
| 外部 / MCP Voice Cast 写 durable bindings → mtime stale → `ensure_snapshot` 只换 Snapshot → legacy reader 继续读旧 `ss.bindings` | 已实际得到 Snapshot 新值、Session 旧值 | `CONFIRMED_DIVERGENCE / CONFIRMED_BUG` |
| Open A → generic `_open_chain_rest` late callback / 并发 Open B → 普通输出缺少 opened generation fence | 常规同步路径无复现；局部 Export / Review fence 不覆盖 generic chain | `STRUCTURAL_RISK` |
| 直接 data-dir / archive-delete mutation →旧 Session/Snapshot 未 reset → stale reload 使用新 root 或抛 not-found | UI 路径有 reset；非 UI caller 没有 Session fence | `STRUCTURAL_RISK` |

### Domain caller audit 结论

- **Open / Create**：两者都从一次 Snapshot 建立 Session payload；成功路径初始同步，
  但 `set_project` / `set_snapshot` 不是原子 transition，也没有 Snapshot identity check。
  Create failure contract 已有明确 success gate：失败返回统一 `(message, False)`、不改旧
  opened project，后续 hydrate / goto Voices 不执行；R4B 没有触碰这条边界。若在两次
  setter 之间发生异常或并发观察，当前实现仍可能短暂出现新 `project` 配旧 Snapshot，
  这是需要 R4C invariant 覆盖的 transition risk。
- **Voice Binding**：durable write 由 Repository / Voice Cast service 负责；现代 readers
  偏向 Snapshot，legacy readers 仍使用 Session mirror；UI 正常绑定后会主动 reload，
  finalize / repair 主要 invalidate Snapshot，不同步 Session payload。
- **Production / Review / QA / Repair**：现代路径多数从 Snapshot 或 durable task state
  读取；Review / Repair 有自己的 stale-output fence；repair terminal path invalidate
  Snapshot，但不构成 payload synchronization。
- **Export**：Export task 的 project / task tracking 与 callback-current guard 能保护
  A/B project-switch isolation；这是局部安全边界，不是 Session/Snapshot 全局 owner。
- **Archive / delete**：通过 Catalog handler 归档当前 opened 项目时清空 opened；归档
  selected ≠ opened 时保留 opened。外部删除没有同等 Session invalidation contract。
- **Merge / Assembly**：Chapter Merge 在 source/target 等于 opened 时拒绝后台改盘；
  Whole Book Assembly 在目标 Book 已 opened 时产生 blocking conflict，confirmation 也
  绑定 selected/opened scope。它们保护 durable mutation，但不修复一般 late callback。
- **MCP / scripts / startup / recovery**：没有发现直接持有 `SessionState` 或
  `ProjectSnapshot` payload 的 caller；MCP voice-cast 以 project name 走 durable service，
  这正是外部 durable update 能触发本 session stale divergence 的来源之一。
- **Dynamic / hidden callers**：未发现相关 `getattr` / `setattr` / string callback key /
  monkeypatch / importlib 路径重新写入 script 或 bindings；`services.__all__` 只暴露
  类型，不改变 ownership。

### Existing revision / fence inventory

当前已有的版本或 fence 不是统一的 Snapshot revision：

- `SessionState.selection_revision` 只服务 selected / archive confirmation；不代表
  opened payload generation；
- Export 使用 project + task tracking，能拒绝跨项目的旧 export UI callback；
- Review / Repair 使用 generation + project + repair_id + task_id fence；
- Whole Book Assembly / Chapter Merge 将 selected / opened / data-root 纳入 plan 或
  confirmation，并在 opened target / source 时阻止后台改盘；
- `ProjectSnapshot` 没有 revision / generation / content hash；`ensure_snapshot` 只依赖
  关键文件 mtime 与 project directory existence；
- 没有发现统一的 opened-project generation 可以保护普通 `_open_chain_rest`。

因此不能把已有 `selection_revision`、Export task id 或 Review / Repair fence 误当作
Session/Snapshot 的全局 payload consistency mechanism。

### Test coverage 与缺口

本轮只运行审计相关的现有回归集，没有新增测试或修改测试 contract：

```text
134 passed, 22 warnings
```

覆盖 Session / Snapshot、snapshot caching、Project creation、Catalog / data-dir、
Export isolation、Production、Voice Cast、Chapter Merge、Whole Book Assembly 与
Assembly operations。现有 `test_session_snapshot.py` 能证明 stale Snapshot 被替换，但
dirty case 没有用已填充的 Session script / bindings 检查 payload sync；因此不能阻止
本轮已复现的 divergence。还缺少：

- external durable script / binding update 后 Session 与 Snapshot 的 identity / payload
  invariant test；
- Voice Cast / MCP 写盘后同一 session 的 stale reload test；
- Open A → late generic callback → Open B 的 opened generation / output fence test；
- direct data-dir、external delete / restore 与当前 session 的 invalidation test；
- `ProjectSnapshot.build()` 当前 bindings alias 与 Session in-place mutation 的 isolation
  test。

### Recommended durable owner 与 R4C 精确边界

推荐 ownership 保持三层清晰分工：

1. `ProjectRepository` 文件继续是 script、bindings、meta 的唯一 durable authority；
2. `SessionState.project` / `selected_project` 继续分别是 opened / selected identity，
   本轮冻结不动；
3. `ProjectSnapshot` 作为当前 opened project 的 cache / modern read view；
   `SessionState.script` / `bindings` 仅作为现有 legacy reader 的临时兼容 mirror，不能
   再被当成独立真相源。

#### Ownership candidates（只评估，不实施）

| Option | Pros | Risks / migration cost | Affected callers / tests / Windows / compatibility | Decision |
|---|---|---|---|---|
| A. Session 只保留 identity + Snapshot owns opened payload | 最终只有一个 opened payload read view；概念最简单 | 需要迁移或删除所有 Session script / bindings readers；Create / Voice / supplement / utility 兼容面大，可能触碰既有 Gradio / Windows callback 行为 | `refresh_supplement_roles`、`do_supplement_parse_json`、utility、export、`bind_voice` 与大量 tests；需完整 Open / Voice / Production / Windows 回归；旧 integration 若直接读字段会破坏兼容 | 不作为 R4C 首选，属于更后续的字段退休方案 |
| B. Session owns payload，Snapshot 只做 immutable derived cache | legacy readers 迁移成本较低；可把 Session 作为 UI payload owner | durable reload 后必须先更新 Session 再重建 Snapshot；现代 Voice / Production / QA / preview 仍需迁移读源；当前 Snapshot 是 mutable dataclass，冻结与 copy 会扩大 blast radius | 影响 `_snap`、Voice handlers、现代 production readers、Open / Create / stale reload；需新增 immutable / reload / A→B / data-dir tests；兼容性较好但 cache rebuild 复杂 | 不推荐；会把 UI Session 提升为 payload owner，弱化 Repository → cache 的自然方向 |
| C. Repository durable truth + Snapshot cache + 显式受 invariant 保护的 Session compatibility mirror | 保留既有字段和 caller contract；可先修真实 stale divergence；selected / opened 与 Windows UI blast radius 最小 | mirror 仍存在，必须消除 alias、明确同步 / 失效规则，并逐一审计 legacy readers；属于中等 implementation cost | 影响 Session transition、`ensure_snapshot`、Open / Create、Voice write、legacy readers；需 stale / external Voice Cast / late callback / data-dir / delete tests；最能保持现有 compatibility contract | **推荐**，作为 R4C 唯一建议方向 |

推荐 Option C 的理由是：它同时承认 durable repository、现代 Snapshot read view 与现有
legacy Session readers 的事实，不把任何一层误标为第二个 durable truth，也不借本轮
真实 divergence 直接扩大到字段删除或 navigation / project contract 重构。

若继续，R4C 只能是以下精确范围的 implementation round：

- 定义并实现一个明确的 Session ↔ Snapshot payload invariant：同一 opened project 必须
  检查 identity，stale reload / fallback rebuild / Open / Create / Voice write / clear /
  data-root reset 后不得留下旧 script 或 bindings；同时消除 Snapshot 与 Session 的
  隐式可变 dict alias；
- 为 stale script、stale bindings、external Voice Cast update、Open A → B late output、
  data-dir / delete invalidation 增加长期 regression tests；
- 对 `refresh_supplement_roles`、`do_supplement_parse_json`、utility readers、
  `do_supplement_export` 与 `bind_voice` 逐一选择“读取显式 Snapshot view”或“读取受
  invariant 保护的 compatibility mirror”，并完成完整 caller audit；
- 不改变 selected / opened semantics，不删除 `p_sel`、Project Page、任何 Session
  字段，不触碰 Catalog、navigation、Production、QA、Repair、Export、Merge、Assembly、
  Runtime、Storage、Voice Cast、MCP contract。

本段只是 R4C 的边界说明；**本轮没有执行 R4C，也没有提交任何 production fix**。

### R4B final decision

- SessionState script / bindings：初始 Open / Create 时是
  `DUPLICATED_BUT_SYNCHRONIZED` 的既有兼容 mirror；stale reload 后已确认
  `CONFIRMED_DIVERGENCE`。
- ProjectSnapshot script / bindings：`CACHE`，由 Repository 重建；不是 durable owner。
- Durable owner：`ProjectRepository` 项目文件及其 service / resolver 写路径。
- Divergence：**是，已通过真实 reproducer 确认**。
- 最终决策：**`CONFIRMED_BUG`**；但只记录为 R4C candidate，本轮不修。
- R4B 交付：本节审计文档与 audit commit；没有 implementation acceptance，也没有
  扩大到 p_sel / Project Page / Session redesign。

## Round R4C — Session/Snapshot Consistency Bugfix（2026-08-25）

本节记录 R4B 已确认 divergence 的最小 bugfix。它不是字段退休、Session redesign 或
普通 entropy cleanup；R4B 历史审计事实保持不变。

### Baseline、branch 与 scope

- R4B doc-only PR #70 已 squash merge，CI run `32741221931` 的 Ubuntu 与 Windows
  selected workflow 均成功。
- R4C baseline / merge 后 `origin/main`：`ffa497c7a36fb03b7ff752579f3f32e7dfb3fcb0`。
- implementation branch：`fix/session-snapshot-consistency-r4c`。
- 真实字段仍是 `ProjectSnapshot.name` 与 `SessionState.project_snapshot`；没有新增
  `project_name` 或 `snapshot` alias。
- 没有删除 `SessionState.script` / `SessionState.bindings`，没有修改 storage format、
  selected/opened semantics、p_sel、Project Page、Catalog、Production、Runtime/TTS、
  QA/Repair、Export、Merge、Assembly、MCP contract 或 Voice Cast storage。

### Root Cause / Before

R4B reproducer 的路径是：

```text
durable Repository update
  -> ProjectSnapshot.is_stale()
  -> SessionState.ensure_snapshot()
  -> ProjectSnapshot.reload_if_stale()
  -> self.project_snapshot = fresh
```

旧实现只替换 Snapshot，不更新 `SessionState.script` / `SessionState.bindings`，所以同一个
opened project 可以同时出现：

```text
Snapshot script = B       Session script = A
Snapshot binding = new.wav Session binding = None
```

旧 Open / Create / Voice path 还把 `set_project(...)`、`set_snapshot(...)` 与
`ss.bindings[role] = ...` 分开执行，初始 Open 时 Session payload 与 Snapshot payload
甚至共享 mutable dict identity。

### Implementation / After invariant

新增 `SessionState.apply_project_snapshot(snapshot, project=...)` 作为唯一 opened
Snapshot apply boundary：

1. 在任何 Session mutation 前校验 `snapshot.name == opened project`；
2. 一次 transition 更新 `project`、`project_snapshot`、`script`、`bindings`；
3. Session script / bindings 使用 `copy.deepcopy`，内容与 Snapshot 一致但不共享 mutable
   identity；
4. `set_project` 保留为 legacy compatibility setter，并先 invalidate 旧 Snapshot；
5. `set_snapshot` 明确为 low-level cache handle，不再被当作 payload synchronization。

成功 hydrate / reload 后的 invariant：

```text
ss.project == snapshot.name
ss.project_snapshot is snapshot
ss.script == snapshot.script
ss.bindings == snapshot.bindings
ss.script is not snapshot.script
ss.bindings is not snapshot.bindings
```

`ensure_snapshot()` 现在会：

- clean Snapshot 直接返回；
- stale reload 成功时通过 `apply_project_snapshot` 同步 mirrors；
- Snapshot name 与 opened project 不一致时丢弃 cache，不返回错误项目 payload；
- 当前 repository project directory 与 Snapshot directory 不一致时丢弃 cache，防止
  old-root / new-root 混合；
- 外部删除导致 `ProjectNotFoundError` 时清理完整 opened context，不保留 orphan payload；
- 其他 reload failure 至少丢弃 invalid cache，不继续返回旧 Snapshot。

`app._snap()` 与 `ui.voice_handlers._snapshot()` 的 fallback rebuild 均改为同一 apply
boundary。`invalidate_snapshot()` 保持 cache-only contract；但所有生产内部
script/bindings readers 都先经过当前 Snapshot resolver，`clear_opened()` 才负责清空
opened identity、mirrors、Snapshot 与 synthesis。

### Reader migration / ownership result

以下 legacy readers 已改为读取 current Snapshot view：

- `app.open_project` role choices 与 `bind_voice` result presentation；
- `preview_bound_voice`；
- `refresh_supplement_roles`、`do_supplement_parse_json`；
- `_synthesize_project_utility`、`do_supplement_export`；
- `ui.voice_handlers` fallback rebuild。

生产 `app.py` / `ui/**` / `services/**` 中不再存在直接 `ss.script` 或 `ss.bindings`
reader；剩余 `state.script` / `state.bindings` 属于独立 Chapter Merge planner state，
不是 `SessionState`。Session fields 仍保留作为 compatibility mirrors，尚未进入字段
退休 round。

Durable / cache / mirror ownership 没有改变：

```text
ProjectRepository / project files  = durable truth
ProjectSnapshot                    = opened cache / modern read view
SessionState.project               = opened identity
SessionState.selected_project      = selected identity
SessionState.script / bindings     = synchronized compatibility mirrors
```

### Voice binding / external update

UI Voice binding 现在是：

```text
ProjectService / VoiceCast durable write
  -> open_project_as_snapshot
  -> apply_project_snapshot
  -> UI reads fresh Snapshot
```

删除了 `ss.bindings[role] = dest` 的 implicit alias synchronization。外部 durable binding
update 的 R4B reproducer 已转为 permanent regression：stale reload 后 Repository、
Snapshot、Session mirror 三方内容一致，且没有 dict alias。

### Identity、data-dir、archive/delete

- `Session.project = B` + `Snapshot.name = A`：resolver 不返回 A；丢弃错误 cache，
  当前 project 可由 `_snap()` 按 B rebuild。
- 正常 A → B apply transition：B 的 project、Snapshot、script、bindings 全部替换，A
  payload 不残留。
- Settings 既有 `set_data_dir() -> reset_for_data_root()` contract 未改；额外保护了
  direct root mutation，避免旧 project_dir 与新 Repository root 组成 mixed Snapshot。
- archive selected ≠ opened 的隔离未改；archive / external delete 当前 opened project
  后 stale resolver 不再返回旧 Snapshot，删除场景清理 opened payload。

### Late callback conclusion

本轮没有在现有普通 Gradio open chain 中复现第二个 late-output bug，也没有引入全局
generation framework。Export 与 Review / Repair 既有局部 fence 保持不变；generic
opened callback generation 继续记录为 `DEFERRED_STRUCTURAL_RISK`，不扩大 R4C scope。

### Permanent tests and verification

新增 / 更新的长期 regression 覆盖：

- stale script / bindings reload 同步 Session mirror；
- Session / Snapshot script、bindings no-alias；
- external durable Voice binding update；
- Snapshot identity mismatch；
- A → B transition；
- data-dir mixed-root 防护；
- external delete / orphan payload cleanup；
- invalidate cache-only semantics；
- UI Voice binding Repository / Snapshot / Session 三方一致；
- Create failure 保持旧 project / Snapshot / mirrors，Create success 完整 hydrate。

本地最终验证：

```text
Full pytest: 1338 passed, 26 skipped
compileall: passed
Ruff --select F (changed Python): passed
git diff --check: passed
```

Windows selected workflow 与 PR CI 在 implementation PR 建立后记录；本地没有声称真实
Windows UI 已执行。

### R4C acceptance decision

R4C 已把 R4B 的 `CONFIRMED_DIVERGENCE` 收口为 explicit synchronization invariant，
没有把 compatibility mirror 误升格为 durable owner，也没有删除字段。完成 CI / PR
验收后本轮停止；下一轮仍不得自动开始 Session field retirement、Catalog
consolidation、Delivery alias cleanup 或 `migrate_project_copy` cleanup。
