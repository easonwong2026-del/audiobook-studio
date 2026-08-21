# Seven-Layer Release Readiness Final Report

Date: 2026-08-21 (Asia/Shanghai)

This document is a release/integration readiness audit. It does not add product
features, merge branches, or claim Windows validation.

## Top Candidate

| Item | Value |
| --- | --- |
| Repository | `easonwong2026-del/audiobook-studio` |
| Top branch | `feat-assembly-operations-closure` |
| Top SHA | `35f75d5c5cb4b7869f3e5978469a03c858e4cf62` |
| Remote SHA | `35f75d5c5cb4b7869f3e5978469a03c858e4cf62` |
| Base parent | `011ace579760db45abce1589120abf7074d2638b` |
| Worktree at audit start | clean |
| Windows Combined Final Gate | DEFERRED |
| Release Sign-off | NO |
| Merged to `main` | NO |

The readiness report is being prepared on `chore-seven-layer-release-readiness`,
created from the exact top candidate. No production code is changed by this
readiness iteration.

## Layer Matrix

All seven remote branches were fetched. Each remote ref matches its frozen SHA,
each layer is exactly one commit ahead of its expected base, and no extra commit
was found in the layer range.

| Layer | Feature | Branch | Expected / actual SHA | Base | Ahead / behind base | Ancestry | Remote exactness |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Bookshelf Management | `fix-bookshelf-management-ux` | `5fc6fbeba1e044eeae2c577e25aceb00eeffcc20` | `main` / `88f0a9a991cf0f06aec7b2a4b0d8dc790d574c66` | 1 / 0 | PASS | EXACT |
| 2 | Book→Chapter Catalog Hierarchy | `feat-book-chapter-catalog-hierarchy` | `8747e7d1d604b13c93e8f6138a937a7eb30c1368` | Layer 1 | 1 / 0 | PASS | EXACT |
| 3 | Hierarchy Management Closure | `feat-hierarchy-management-closure` | `69d35e1449f7c7261b90e1adf6ac180dac7b370a` | Layer 2 | 1 / 0 | PASS | EXACT |
| 4 | Chapter Merge Planner / Dry Run | `feat-chapter-merge-planner` | `07f80a55f15862d28f8787890f63a49a5c03f651` | Layer 3 | 1 / 0 | PASS | EXACT |
| 5 | Transactional Chapter→Book Merge | `feat-chapter-book-merge-complete` | `2899a1e346e73853ee5c619f52e1977c5977791a` | Layer 4 | 1 / 0 | PASS | EXACT |
| 6 | Whole-book Assembly | `feat-whole-book-assembly` | `011ace579760db45abce1589120abf7074d2638b` | Layer 5 | 1 / 0 | PASS | EXACT |
| 7 | Assembly Operations Closure | `feat-assembly-operations-closure` | `35f75d5c5cb4b7869f3e5978469a03c858e4cf62` | Layer 6 | 1 / 0 | PASS | EXACT |

Explicit ancestry checks:

- L1 → L2: PASS
- L2 → L3: PASS
- L3 → L4: PASS
- L4 → L5: PASS
- L5 → L6: PASS
- L6 → L7: PASS

## Current Main

`origin/main` is `88f0a9a991cf0f06aec7b2a4b0d8dc790d574c66`, dated
2026-08-18 22:49:58 +0800, with commit `fix: keep Gradio controls readable in
dark preference (#52)`.

There are no commits, file changes, or ahead/behind drift between the Layer 1
base and current `origin/main`:

```text
git rev-list --left-right --count 88f0a9a...origin/main
0 0
```

Main drift classification: **NONE**. Current conflict risk from main drift is
LOW, although the eventual merge will touch historical hotspots such as
`app.py`, `services/project_catalog.py`, `services/project_storage.py`,
`repositories/project_repo.py`, `ui/pages/overview_page.py`, and
`ui/wiring/*`.

No rebase or main merge was performed.

## Per-Layer Delta Audit

The following are exact adjacent-range statistics from the frozen commits.
The listed paths were inspected; no data, log, IDE, or unrelated cleanup files
were present.

### Layer 1 — Bookshelf Management

`12 files changed, 1142 insertions(+), 205 deletions(-)`.

Paths: `app.py`, `services/project_catalog.py`, `services/session.py`,
`tests/test_app_glue.py`, `tests/test_bookshelf_management_closure.py`,
`tests/test_catalog_refresh_integration.py`, `tests/test_storage_layout_v3.py`,
`ui/pages/overview_page.py`, `ui/project_catalog_handlers.py`,
`ui/settings_handlers.py`, `ui/wiring/project_catalog_wiring.py`, and
`ui/wiring/settings_wiring.py`.

Scope: Catalog initialization/refresh, selection/opened separation, `p_sel`,
action-state safety, archive confirmation revision protection, cleanup/storage
transient invalidation, data-directory reset, and regression coverage.

### Layer 2 — Book→Chapter Catalog Hierarchy

`11 files changed, 1241 insertions(+), 90 deletions(-)`.

Paths: `app.py`, `lib/types.py`, `repositories/project_repo.py`,
`services/project_catalog.py`, `services/project_storage.py`,
`tests/test_book_chapter_catalog_hierarchy.py`,
`tests/test_bookshelf_management_closure.py`,
`tests/test_catalog_refresh_integration.py`, `ui/pages/overview_page.py`,
`ui/project_catalog_handlers.py`, and `ui/wiring/project_catalog_wiring.py`.

Scope: logical `Book`/`Chapter` metadata and Catalog relationship resolution.
Physical project directories remain flat siblings.

### Layer 3 — Hierarchy Management Closure

`7 files changed, 684 insertions(+), 36 deletions(-)`.

Paths: `repositories/project_repo.py`, `services/project_catalog.py`,
`tests/test_book_chapter_catalog_hierarchy.py`,
`tests/test_hierarchy_management_closure.py`, `ui/pages/overview_page.py`,
`ui/project_catalog_handlers.py`, and `ui/wiring/project_catalog_wiring.py`.

Scope: explicit bind/reassign/title/order/unbind/orphan repair/archive safety,
duplicate-ID diagnostics, and additive UI output contract.

### Layer 4 — Chapter Merge Planner

`7 files changed, 2377 insertions(+), 12 deletions(-)`.

Paths: `app.py`, `services/chapter_merge_planner.py`,
`tests/test_chapter_merge_planner.py`, `ui/chapter_merge_handlers.py`,
`ui/pages/overview_page.py`, `ui/wiring/project_catalog_wiring.py`, and
`ui/wiring/settings_wiring.py`.

Scope: read-only inventory, deterministic ordering, conflict and provenance
inspection, plan token invalidation, and explicit blocking policy.

### Layer 5 — Transactional Chapter→Book Merge

`7 files changed, 2492 insertions(+), 17 deletions(-)`.

Paths: `app.py`, `services/chapter_merge_executor.py`,
`services/chapter_merge_planner.py`, `tests/test_chapter_merge_executor.py`,
`ui/chapter_merge_handlers.py`, `ui/pages/overview_page.py`, and
`ui/wiring/project_catalog_wiring.py`.

Scope: single-Chapter target backup, shadow staging, journaled commit,
integrity verification, rollback, merge provenance, idempotency, and explicit
source-preservation behavior.

### Layer 6 — Whole-book Assembly

`10 files changed, 2693 insertions(+), 24 deletions(-)`.

Paths: `app.py`, `services/__init__.py`,
`services/chapter_merge_executor.py`, `services/whole_book_assembly.py`,
`tests/test_whole_book_assembly.py`, `ui/pages/overview_page.py`,
`ui/whole_book_assembly_handlers.py`, `ui/wiring/project_catalog_wiring.py`,
`ui/wiring/settings_wiring.py`, and
`ui/wiring/whole_book_assembly_wiring.py`.

Scope: sequential orchestration over the existing Planner/Executor, fresh
per-Chapter replanning, partial-stop behavior, source preservation, and
assembly-level result reporting.

### Layer 7 — Assembly Operations Closure

`9 files changed, 1976 insertions(+), 21 deletions(-)`.

Paths: `services/__init__.py`, `services/chapter_merge_executor.py`,
`services/whole_book_assembly.py`,
`services/whole_book_assembly_operations.py`,
`tests/test_whole_book_assembly.py`,
`tests/test_assembly_operations_closure.py`, `ui/pages/overview_page.py`,
`ui/whole_book_assembly_handlers.py`, and
`ui/wiring/whole_book_assembly_wiring.py`.

Scope: restart-safe operational derivation, minimal run history, transaction
diagnostics, integrity-aware completion, fresh resume, and dedicated Dashboard
state. It does not add new merge semantics.

## PR Topology

The intended topology is:

| PR | Base | Head | Current GitHub state | CI |
| --- | --- | --- | --- | --- |
| PR 1 | `main` | `fix-bookshelf-management-ux` | PR #53 OPEN, CLEAN | Ubuntu Python 3.10 SUCCESS; Windows selected workflow SUCCESS |
| PR 2 | `fix-bookshelf-management-ux` | `feat-book-chapter-catalog-hierarchy` | No PR currently exists | N/A |
| PR 3 | `feat-book-chapter-catalog-hierarchy` | `feat-hierarchy-management-closure` | No PR currently exists | N/A |
| PR 4 | `feat-hierarchy-management-closure` | `feat-chapter-merge-planner` | No PR currently exists | N/A |
| PR 5 | `feat-chapter-merge-planner` | `feat-chapter-book-merge-complete` | No PR currently exists | N/A |
| PR 6 | `feat-chapter-book-merge-complete` | `feat-whole-book-assembly` | No PR currently exists | N/A |
| PR 7 | `feat-whole-book-assembly` | `feat-assembly-operations-closure` | No PR currently exists | N/A |

PR #53: [fix(projects): finish bookshelf management UX and state safety](https://github.com/easonwong2026-del/audiobook-studio/pull/53).

The absence of PRs 2–7 is an integration-process blocker, not branch drift.
They should be created only when integration work is authorized. The exact
base/head pairs above must be preserved.

## Recommended Integration Strategy

Recommendation: **sequential stacked merge**.

- It preserves the seven audited SHAs and their one-commit-per-layer scope.
- Each layer remains independently reviewable and attributable.
- A failure can be isolated to the owning layer or a new corrective branch.
- CI can be rerun after every base transition.
- The final top candidate remains reproducible until Windows validation.

Alternatives:

- A single collapsed PR is simpler to merge but loses layer-level review and
  rollback boundaries, and makes conflicts harder to attribute.
- Rebuilding from current `main` is technically possible because main has no
  drift, but rewrites the known passing SHAs and weakens CI traceability.

Do not execute any collapse, squash, rebase, or main merge during this audit.

## Persistent Data Changes

| Field/file | Layer | Location | Compatibility/read when absent | Write trigger | Rollback/recovery |
| --- | --- | --- | --- | --- | --- |
| `project_id` | 2 | `project.json` metadata | Missing is readable as `None`; Catalog scan does not write it | New project creation or explicit relationship operation; legacy IDs are lazy | Relationship writes are atomic; participant rollback restores prior metadata |
| `project_kind` | 2/3 | `project.json` metadata | Missing defaults to `book` | New project, bind/unbind | Same metadata rollback |
| `parent_project_id` | 2/3 | `project.json` metadata | Missing means standalone/orphan-compatible legacy state | Explicit bind/reassign/unbind | Same metadata rollback |
| `chapter_title` | 2/3 | `project.json` metadata | Missing falls back to project title | Explicit bind/title update | Same metadata rollback |
| `chapter_order` | 2/3 | `project.json` metadata | Missing means Catalog fallback ordering | Explicit bind/order update | Same metadata rollback |
| `parent_project_name`, `relation_status`, `relation_message` | 2/3 | Derived `ProjectSummary` fields | Derived at scan time | No direct persistence | No rollback artifact |
| `merge_history.json` | 5 | Existing project system/config directory (`99_系统数据/配置` for v3) | Missing means no prior merge history | Successful Chapter→Book transaction writes a new record | Included in project backup; deleting loses idempotency/provenance |
| `source_state_fingerprint` and target pre-merge fingerprint | 5 | Merge history and plan/confirmation payloads | Missing history is treated as no history; missing fingerprint cannot prove idempotency | Planner/executor provenance | Backup/history must be retained for recovery |
| Transaction journal | 5 | `<data-root>/runtime/merge_transactions/<transaction-id>.json` | Missing journal means no diagnostic record; unreadable journal is surfaced | Created before validation and updated at every stage | External recovery artifact; not part of project content tree |
| Backup archive | 5 | `<data-root>/backups/*.audiobook-project.zip` | No archive means no external backup recovery | Mandatory before target mutation | Primary manual recovery artifact |
| Shadow snapshot/stage | 5 | `<data-root>/runtime/merge_transactions/<transaction-id>/` | Not used outside its transaction | Created during execution | Safe to remove only after terminal state and recovery policy permits |
| Assembly run history | 7 | `<data-root>/runtime/assembly_runs/<assembly-id>.json` | Missing means no run audit; content truth is still reconstructed | Assembly execution progress/finish | Audit only; not a content authority |
| `assembly_id`, current Chapter, minimal result rows | 6/7 | Transient result plus run history | Old sessions are not required for reconstruction | Assembly execution | Fresh resume uses durable merge history, not old token |

Assembly run history deliberately does not persist the old plan, confirmation
token, source content, or WAV inventory as a second truth source.

## Legacy Compatibility Matrix

Legend: `S` = SUPPORTED, `S+ID` = SUPPORTED WITH LAZY ID or explicit relation,
`RO` = READ-ONLY/diagnostic, `B` = BLOCKED, `N/A` = NOT APPLICABLE.

| Case | READ | OPEN | CATALOG | PLAN | MERGE | ASSEMBLY |
| --- | --- | --- | --- | --- | --- | --- |
| Legacy project with no `project_id`/hierarchy fields | S | S | S as standalone Book | S+ID after explicit relation; otherwise N/A/B | S+ID only after a valid explicit Chapter→Book relation | S+ID when it is a Book with valid current children |
| Legacy Storage v1/v2 | S | S | S through version resolver | S for readable content and valid references | S when current plan/backup/integrity checks pass | S when valid hierarchy exists |
| Storage v3 project created before hierarchy metadata | S | S | S as standalone Book | S+ID after explicit relationship | S+ID after explicit relationship | S+ID with valid children |
| Book with no Chapters | S | S | S | S, empty/`NOT_STARTED` | N/A | S, no-op/`NOT_STARTED`; not `COMPLETE` |
| Chapter with valid parent | S | S | S | S if content policy passes | S if fresh confirmation and conflicts pass | S as part of ordered assembly |
| Orphan Chapter | S | S | S with orphan diagnostic | B | B | B until explicit repair |
| Duplicate project ID | S by name | S by name | S with ambiguity diagnostic | B | B | B |
| Old Book already containing production data | S | S | S | S with inventory/conflict diagnostics | B on segment/audio collision; never overwrite existing data | S only for conflict-free current children; otherwise B/partial-stop |
| Chapter with partial audio | S | S | S | S with `PARTIAL_AUDIO` warning | S with warning under current policy | S with warning/partial result under current policy |
| Chapter with no audio | S | S | S | B (`SOURCE_AUDIO_MISSING`) | B | B for that Chapter |
| Book with historical merge state | S | S | S | `ALREADY_MERGED` or source-changed blocking state | B for duplicate/source-changed re-merge | S; already-merged Chapter is skipped from fresh resume |
| Assembly with interrupted journal | S | S | S | Fresh plan can be read, but operations state is `DEGRADED` | B until manual recovery | B; diagnostics remain visible |

No global metadata migration is performed by Catalog scanning. Explicit
relationship operations may lazily materialize IDs only for their participants.

## Storage / Runtime Files

Storage Layout v3 remains unchanged:

```text
01_原始资料/
02_生成音频/
03_导出成品/
99_系统数据/
```

Book and Chapter projects remain flat sibling directories. Logical hierarchy is
metadata only; no physical folder nesting was introduced.

| Runtime/system artifact | Owner | Lifetime | Safe deletion | Recovery/backup behavior |
| --- | --- | --- | --- | --- |
| `99_系统数据/配置/project.json` | Project repository/Catalog | Project lifetime | No | Included in project backup; required metadata |
| `99_系统数据/配置/merge_history.json` | Merge executor/planner | Target provenance lifetime | No, unless intentionally abandoning idempotency history | Included in project backup; required to reconstruct prior merges |
| `<data-root>/runtime/merge_transactions/<id>.json` | Chapter merge executor | Audit/recovery lifetime | Only after terminal state and retention decision | Outside project archive; preserve separately for incident recovery |
| `<data-root>/runtime/merge_transactions/<id>/snapshot` | Executor rollback boundary | Transaction lifetime | Only after recovery decision | Outside project archive; useful for rollback diagnosis |
| `<data-root>/runtime/merge_transactions/<id>/stage` | Executor shadow tree | Transaction lifetime | Only after terminal state and retention decision | Outside project archive; not user content |
| `<data-root>/backups/*.audiobook-project.zip` | Backup service/executor | Until recovery confidence is established | No before release/recovery sign-off | External target recovery artifact |
| `<data-root>/runtime/assembly_runs/<assembly-id>.json` | Assembly operations layer | Audit lifetime | Audit policy only; never for content correction | Outside project archive; content remains governed by merge history |

The project backup archive covers the project tree, including v3 system data,
but does not automatically include external data-root runtime journals,
snapshots, stages, run history, or the separate backup directory.

## Transaction Safety Statement

The current merge system is **not** a global filesystem ACID transaction.

The accurate guarantee is:

```text
mandatory target backup
+ shadow staging
+ external transaction journal
+ per-file atomic replacement/write
+ post-commit integrity verification
+ rollback to the pre-merge shadow snapshot when mutation has started
```

Validation failures do not mutate the target. Backup/staging failures stop before
target commit. Commit or verification failures after mutation attempt rollback.
Rollback failure leaves the target potentially uncertain, preserves the external
backup reference, and is surfaced as a critical/degraded state.

## Backup / Rollback Matrix

| Failure boundary | Journal | Target | Source | Backup | Automatic behavior | Future execution |
| --- | --- | --- | --- | --- | --- | --- |
| Before backup: stale plan/confirmation/validation | `VALIDATION_FAILED` | Unchanged | Unchanged | None required | Stop and report | Fresh plan/confirmation required; not globally blocked |
| Snapshot/backup preparation | `BACKUP_FAILED` | Expected unchanged; side effects guarded | Unchanged | May be absent/partial | Restore guarded backup side effects and stop | Retryable after fresh analysis if no degraded diagnostic remains |
| During external backup | `BACKUP_FAILED` | Expected unchanged | Unchanged | May be available; journal records path when known | Stop; retain diagnostic | Retryable after fresh analysis unless manual review is needed |
| During staging | `STAGE_FAILED` | Original target remains unpublished | Unchanged | Available | Discard/retain stage according to recovery policy; no commit | Retryable after fresh analysis |
| During commit without mutation | `COMMIT_FAILED` | Expected unchanged | Unchanged | Available | Stop | Retryable after fresh analysis |
| During commit after mutation | `ROLLED_BACK` or `ROLLBACK_FAILED` | Restored if rollback succeeds; uncertain if it fails | Unchanged | Available | Roll back or surface critical failure | `ROLLED_BACK` is retryable; `ROLLBACK_FAILED` blocks |
| During verification | `ROLLED_BACK` or `ROLLBACK_FAILED` | Restored if rollback succeeds; uncertain if it fails | Unchanged | Available | Roll back or surface critical failure | Same as commit failure |
| During rollback | `ROLLBACK_FAILED` | Potentially partial/uncertain | Unchanged | Available | No automatic repair | `DEGRADED`; manual recovery required |
| Process crash between stages | Active stage journal | May be original, staged, or partially committed | No source write path | Depends on crash point | No silent repair; restart detects active journal | Blocked/degraded until manual recovery |
| Crash between Chapters | Assembly run may remain `IN_PROGRESS`; per-Chapter journals/history are durable | Prior successful Chapters remain; current Chapter follows its journal | Unchanged | Per successful Chapter | Restart reconstructs current state | Fresh resume if no active/degraded transaction |
| Crash after Chapter success before Assembly report update | Merge journal/history show success; run audit may be incomplete | Successful target content remains | Unchanged | Recorded in merge history/journal | Re-analysis prefers durable merge state | Fresh resume skips the already-merged Chapter |

## Source Preservation Audit

Source preservation is supported by both code and tests:

- The executor reads source metadata, script, audio, Voice Cast, and inventories;
  its commit target is the Book tree.
- Merge history records `source_unchanged: true`.
- The executor has no source archive/delete/unbind/directory-move step.
- Layer 5 tests cover source preservation and rollback.
- Layer 6 tests cover all source Chapters remaining intact during whole-book
  assembly.
- Layer 7 restart/resume tests verify that re-analysis does not clean up source
  content.

No automatic source archive, delete, unbind, or physical move is performed.

## Idempotency Audit

| Situation | Planner/operations state | Execution behavior |
| --- | --- | --- |
| No matching target history and no blocking conflict | READY | Fresh confirmation and merge may proceed |
| Same source/target and same source fingerprint already in history | `ALREADY_MERGED` | Duplicate import blocked; Assembly skips it |
| Same source/target but source fingerprint changed | `SOURCE_CHANGED_AFTER_PREVIOUS_MERGE` | Incremental sync is blocked |
| Target already contains conflicting segment/audio path | `TARGET_AUDIO_PATH_CONFLICT` | Execution blocked; no overwrite/remap |
| Restart after partial Assembly | Durable per-Chapter history is re-read | Fresh resume skips completed Chapters and replans pending ones |

No duplicate Chapter import is performed by restart/resume.

## Operational Status Truth Audit

Layer 7 derives current state from:

1. Catalog hierarchy and current membership/order.
2. Fresh Chapter Merge Planner output.
3. Target `merge_history.json` and source fingerprints.
4. Transaction journals and active/terminal stages.
5. Read-only target integrity report.
6. Minimal Assembly run history for audit/progress only.

Assembly run history is not a second content truth source. Important invariants:

- Past `COMPLETE` + current source changed = current state is not `COMPLETE`.
- Past `COMPLETE` + new Chapter attached = current state is not `COMPLETE`.
- Rollback failure = `DEGRADED`.
- `COMPLETE` requires all current Chapters merged and final integrity `PASS`.
- Active or unreadable transaction diagnostics prevent unsafe resume.

## Gradio Contract Inventory

| Area | Handler/contract | Count | Major callers |
| --- | --- | ---: | --- |
| Bookshelf base refresh | `bookshelf_management_outputs(..., include_hierarchy=False)` | 25 | Catalog refresh/search/archive/storage/settings chains |
| Hierarchy composite refresh | `hierarchy_outputs` additive suffix | 8 appended; 33 total with bookshelf | Bind/reassign/title/order/unbind/archive refresh chains |
| Chapter Merge Planner refresh | `refresh_merge_planner_controls` | 5 | Planner refresh and Catalog refresh subscription |
| Chapter Planner + Executor workflow refresh | `refresh_merge_workflow_controls` | 11 | Planner state refresh paths |
| Chapter Merge Executor prepare/invalidate | `clear/prepare/invalidate_merge_execution_controls` | 6 | Planner analyze/input/resolution changes |
| Chapter Merge Executor execute | `execute_merge_plan` | 5 | Merge Execute click |
| Whole-book Assembly workflow | `assembly_workflow_outputs` | 12 | Assembly page and Catalog/settings refresh subscriptions |
| Whole-book Assembly execution controls | `assembly_execution_outputs` | 6 | Analyze/target/resolution/confirmation chains |
| Assembly Operations Dashboard refresh | `refresh_assembly_dashboard` | 2 | Assembly wiring after state changes |
| Assembly analyze | `analyze_assembly` | 3 | Assembly Analyze click |
| Assembly resume | `resume_assembly_plan` | 7 | “继续未完成章节” click |

The Assembly/Operations outputs remain separate from the 25/33 bookshelf
contracts. Existing contract tests pass; no callback refactor was performed in
this readiness iteration.

## Automated Test Inventory

Key test modules and test-function counts:

| Category | Key files | Functions |
| --- | --- | ---: |
| Bookshelf state | `tests/test_bookshelf_management_closure.py`, `tests/test_project_catalog.py` | 28 |
| Catalog hierarchy | `tests/test_book_chapter_catalog_hierarchy.py`, `tests/test_project_catalog.py` | 26 |
| Hierarchy management | `tests/test_hierarchy_management_closure.py` | 13 |
| Merge Planner | `tests/test_chapter_merge_planner.py` | 16 |
| Merge Executor | `tests/test_chapter_merge_executor.py` | 9 |
| Rollback/fault injection | `tests/test_chapter_merge_executor.py`, `tests/test_storage_layout_v3.py` | covered |
| Whole-book Assembly | `tests/test_whole_book_assembly.py` | 15 |
| Resume/idempotency | `tests/test_chapter_merge_executor.py`, `tests/test_whole_book_assembly.py` | covered |
| Operations/restart reconstruction | `tests/test_assembly_operations_closure.py` | 11 |
| Data-directory | `tests/test_bookshelf_management_closure.py`, `tests/test_book_chapter_catalog_hierarchy.py` | covered |
| Gradio contracts | `tests/test_project_catalog_handlers.py`, `tests/test_catalog_refresh_integration.py`, `tests/test_bookshelf_management_closure.py`, `tests/test_hierarchy_management_closure.py`, `tests/test_chapter_merge_planner.py`, `tests/test_chapter_merge_executor.py`, `tests/test_whole_book_assembly.py`, `tests/test_assembly_operations_closure.py` | covered |
| Storage compatibility | `tests/test_storage_layout_v3.py` | 18 |

Meaningful remaining coverage gap: native Windows GUI behavior, Explorer
console behavior, real process crashes, real file locks/permissions, and final
main post-merge smoke are intentionally reserved for the Master Windows Gate.

Known automated baseline at the frozen top candidate:

```text
pytest: 1207 passed, 0 failed, 26 skipped, 61 warnings
Candidate Ruff: PASS
git diff --check: PASS
compileall: PASS
application import: PASS
Gradio smoke: PASS
```

The warnings are existing Gradio deprecation warnings, not candidate failures.

## Release Blockers

### P0 — Must fix before any merge

None confirmed by this audit.

### P1 — Must fix before Windows sign-off/integration

- Windows Combined Final Gate has not run on the exact top SHA.
- PRs 2–7 do not yet exist on GitHub, so their CI and merge-base transitions
  are not yet observable.
- Final sequential merge, post-transition CI, fresh-main regression, and final
  main Windows smoke remain pending.

### P2 — Documented supported-scope limitations

- Segment ID collision remap is unsupported and remains BLOCKED.
- Changed-source incremental sync is unsupported and remains BLOCKED.
- Duplicate project ID auto-repair is unsupported and remains diagnostic/BLOCKED.
- Reverse merge/remove is unsupported.
- Source Chapter auto-archive/delete is unsupported.
- No global filesystem ACID guarantee exists.
- Parallel and multi-book batch execution are unsupported.

### P3 — Future maintenance/enhancement

- Historical Ruff debt outside the candidate delta.
- Existing Gradio deprecation warnings.
- Future packaging/export redesign and broader schema evolution.

## Master Windows Final Gate

Run only against:

```text
Repository: easonwong2026-del/audiobook-studio
Branch: feat-assembly-operations-closure
Candidate SHA: 35f75d5c5cb4b7869f3e5978469a03c858e4cf62
```

The Windows machine must report `git rev-parse HEAD` and prove it equals the
candidate SHA. Use disposable data only; do not use real user data.

### Section 1 — Startup / Bookshelf

- Studio starts normally and the first-screen Catalog is visible.
- Prewarm does not block Catalog initialization.
- No selection disables actions.
- `selected != opened` semantics remain correct; opened B and selected A are
  distinguishable.
- `p_sel` follows the opened/current project contract.
- Search, filter, refresh, and reconciliation do not resurrect stale selection.
- Data-directory success switches state; failure preserves the old context.
- Explorer actions produce no black console window.

### Section 2 — Hierarchy

- Legacy project appears as a Book.
- Bind, reassign, edit Chapter title/order, and unbind work explicitly.
- Orphan is visible; orphan repair is explicit.
- Invalid relation and duplicate-ID ambiguity are diagnostic and do not attach
  randomly.
- Parent archive is blocked while children exist.
- Chapter archive does not archive or mutate its parent.

### Section 3 — Merge Planner

- Select a Chapter and confirm the target Book.
- Analyze is read-only and shows inventory, conflicts, provenance, and token.
- Source/target changes invalidate the plan token.
- Selected/opened context remains unchanged.

### Section 4 — Single Chapter Merge

With disposable data:

- READY plan requires explicit confirmation and executes.
- Target backup and transaction journal exist.
- Source remains intact.
- Expected target script/audio appears and target integrity passes.
- Stale plan/confirmation is refused.
- Opened target follows the existing block policy.
- Forced failure triggers rollback and exposes the result.
- Restart keeps the merged target readable.
- The same source state cannot merge twice.

### Section 5 — Whole-book Assembly

- Catalog order is the assembly order.
- Already-merged Chapters are skipped.
- Pending Chapters are freshly replanned sequentially.
- Target evolves correctly and prior successful Chapters remain.
- A later blocking Chapter stops safely; not-attempted Chapters are visible.
- All source Chapters remain intact.
- Final target integrity passes for success.
- Partial-success status is understandable.

### Section 6 — Restart / Resume

- Close Studio after partial Assembly.
- Restart and confirm Dashboard reconstruction without old memory state.
- Merged, pending, and blocked states are correct.
- “重新分析” performs no mutation.
- “继续未完成章节” skips completed Chapters.
- Resume succeeds from durable history when safe.
- Old confirmation is not restored.

### Section 7 — Degraded / Recovery

- Rollback-success failure is visible and retryable.
- Rollback-failed state becomes `DEGRADED` and disables Resume.
- Interrupted journal is visible.
- Transaction ID and backup reference are readable.
- Integrity `FAIL`/`UNKNOWN` prevents false `COMPLETE`.

### Section 8 — Completion

- All current Chapters are merged.
- Final integrity is `PASS`.
- Dashboard shows `COMPLETE`.
- Restart retains `COMPLETE`.
- Changing one source Chapter invalidates `COMPLETE`.
- Attaching a new Chapter invalidates `COMPLETE`.
- Source projects remain present and unchanged.

## Windows Test Dataset

Create a disposable data root, for example a new temporary directory selected
through the Studio Settings UI. Do not point the test at a production data root.

Minimum dataset:

- Book A: target for assembly.
- Book B: unrelated opened/selected-context control.
- Chapter A1: clean, complete audio.
- Chapter A2: partial audio.
- Chapter A3: Voice Cast conflict.
- Chapter A4: orphan or repair case.
- Legacy Book L1: no hierarchy metadata.
- Optional v1/v2 project: backward-read verification.
- Separate data-root B: data-directory switch verification.

Use short synthetic scripts and small WAV fixtures. Generate/copy them under
the disposable root, verify the root before testing, and delete the disposable
root only after collecting logs/screenshots and preserving any failure journal.

## Windows Fault-Injection Plan

| Fault | Expected executor state | Expected rollback | Expected Dashboard state |
| --- | --- | --- | --- |
| Destination file locked | `COMMIT_FAILED` or `VERIFY_FAILED` | `ROLLED_BACK` when mutation started | Failed/rolled-back and retryable |
| Backup target unavailable | `BACKUP_FAILED` | Target unchanged; no merge commit | Diagnostic failure; fresh retry only |
| Staging write denied | `STAGE_FAILED` | Target unchanged | Diagnostic failure; retryable after fresh analysis |
| Target opened by another process | Validation/commit block according to observed boundary | No unsafe overwrite | Blocked or degraded; no Resume if journal is active |
| Integrity failure on disposable copy | `VERIFY_FAILED` then `ROLLED_BACK`, or rollback failure | Restore snapshot or surface uncertainty | `DEGRADED` when rollback fails |

Do not induce these failures against real user data.

## Release Notes

The seven-layer stack provides:

1. Bookshelf selection, refresh, action-state, cleanup/storage safety, and
   selected/opened separation.
2. Logical Book/Chapter hierarchy while keeping physical project folders flat.
3. Explicit relationship management, ordering, orphan diagnostics, and archive
   protection.
4. Read-only Chapter→Book merge analysis with inventories, conflicts, tokens,
   and provenance checks.
5. Safe single Chapter→Book merge with target backup, staging, journal,
   integrity verification, rollback, source preservation, and idempotency.
6. Sequential whole-book Assembly with fresh per-Chapter planning and safe
   partial-stop behavior.
7. Restart reconstruction, fresh re-analysis, safe resume, operational status,
   transaction diagnostics, backup visibility, and completion invalidation.

Current limitations:

- Windows final validation has not yet run.
- Segment collision remap remains BLOCKED.
- Changed-source incremental sync is unsupported.
- Source Chapter auto-archive/delete is unsupported.
- Reverse merge/remove is unsupported.
- Duplicate-ID auto repair is unsupported.
- There is no global filesystem ACID guarantee.

## Upgrade / Compatibility Notes

- Existing v1/v2/v3 projects remain readable through the storage resolver.
- Catalog scans do not globally rewrite legacy metadata.
- New projects receive a stable `project_id` and default `project_kind=book`.
- Legacy IDs are materialized lazily only by explicit relationship operations.
- No physical folder nesting is introduced.
- Storage Layout remains v3.
- There is no mandatory global project migration.
- Merge history lives in the existing project system/config area.
- Merge journals, Assembly run history, snapshots, stages, and backups live in
  runtime/data-root areas outside the project folder.
- Back up the data root, project directories, and external runtime/backup areas
  before first production use if recovery forensics are required.

## Final Merge Order

Do not execute now. After Windows passes on the exact top SHA:

1. Merge PR 1: `main` ← `fix-bookshelf-management-ux`.
2. Verify `main`, then prepare PR 2: `main` ←
   `feat-book-chapter-catalog-hierarchy`.
3. Verify `main`, then prepare PR 3: `main` ←
   `feat-hierarchy-management-closure`.
4. Verify `main`, then prepare PR 4: `main` ←
   `feat-chapter-merge-planner`.
5. Verify `main`, then prepare PR 5: `main` ←
   `feat-chapter-book-merge-complete`.
6. Verify `main`, then prepare PR 6: `main` ← `feat-whole-book-assembly`.
7. Verify `main`, then prepare PR 7: `main` ←
   `feat-assembly-operations-closure`.
8. After every merge: fetch, verify main SHA, rerun required CI, and confirm
   the next diff contains only its owning layer.
9. Clone fresh from final `main`, rerun full QA, and run final Windows smoke.

GitHub may retain the original stacked base branch, but automatic retargeting is
not guaranteed. Manually retarget the next PR to `main` after its lower layer
merges, then verify that only the next layer's delta remains. Do not force-push
the frozen feature branches.

## Conflict Resolution Procedure

Likely hotspots are `app.py`, `services/project_catalog.py`,
`services/project_storage.py`, `repositories/project_repo.py`,
`ui/pages/overview_page.py`, `ui/project_catalog_handlers.py`,
`ui/wiring/*`, and the merge/assembly handlers.

If an upper branch conflicts after a lower layer merges:

1. Do not mechanically accept “ours” or “theirs”.
2. Preserve the latest merged lower-layer behavior.
3. Preserve the upper layer's documented conceptual boundary.
4. Resolve only the minimal conflict.
5. Run the owning layer regression suite plus full pytest.
6. Compare the resulting diff with the original frozen layer delta.
7. Produce a new candidate SHA if any code changed; do not silently redefine the
   frozen candidate.

## Post-Merge Verification

On a fresh clone with no old worktree assumptions:

```text
git fetch origin
git switch main
git rev-parse HEAD
pytest
candidate-only Ruff / project lint checks
git diff --check
```

Then launch Studio and perform:

- first-screen Catalog smoke;
- hierarchy bind/reassign/unbind smoke;
- read-only merge Planner smoke;
- one disposable single-Chapter merge;
- whole-book partial/resume smoke;
- final target integrity and source-preservation checks;
- final Windows Explorer/console smoke.

## Files Changed During This Readiness Iteration

Production source code changed: **NO**.

The only intended artifact is this report:

`docs/releases/seven_layer_release_readiness_2026-08.md`

No PR was created, edited, closed, or merged during this iteration.

## QA

The frozen top candidate was re-audited without production edits. Baseline:

- pytest: `1207 passed`, `0 failed`, `26 skipped`, `61 warnings`;
- candidate Ruff: PASS;
- `git diff --check`: PASS;
- compileall: PASS;
- application import: PASS;
- Gradio smoke: PASS.

After this documentation-only change, full pytest was rerun before the
readiness commit and produced the same passing baseline. No historical lint
cleanup was performed.

## Candidate Status

```text
Seven-layer ancestry: PASS
Frozen branch integrity: PASS
Per-layer scope audit: PASS
Compatibility audit: PASS
Release documentation: COMPLETE
Master Windows Gate: READY
Windows Combined Final Gate: DEFERRED
Production code changed: NO
Release Sign-off: NO
Merged to main: NO
```

## Release Sign-off Criteria

Release Sign-off remains `NO` until all of the following are true:

1. Frozen layer ancestry is valid.
2. Remote SHAs match the approved candidate set.
3. No unexpected branch drift exists.
4. Top candidate automated regression passes.
5. No P0 release blocker remains.
6. The Master Windows Final Gate passes on the exact top SHA.
7. Windows merge, rollback, restart, resume, and source-preservation scenarios
   pass.
8. Explorer black-console regression passes.
9. Final target integrity behavior passes.
10. `COMPLETE` invalidation behavior passes.
11. Stacked PRs merge in controlled order with CI after every transition.
12. A fresh final-main clone passes regression and final Windows smoke.

Central principle: this iteration makes the seven-layer candidate
understandable, auditable, mergeable, recoverable, and testable. It does not
make the product more powerful and it does not claim Windows PASS.
