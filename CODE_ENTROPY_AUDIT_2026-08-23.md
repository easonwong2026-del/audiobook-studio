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

# Workbench IA-1 follow-up — Round IA-2 caller audit correction

日期：2026-08-23

Reference：PR #63 head `e97b84c1104171801bbb31a1cc3bcb6af12ea8ca`。
本节只同步 IA-1 完成后的 caller audit 结论；不执行 Round IA-2 删除。

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
