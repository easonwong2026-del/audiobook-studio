# v2→v3 迁移器设计 + 任务分解

---

## 1. 迁移器设计（`services/project_storage.py` 扩展）

### 1.1 三阶段协议

```python
# ── 阶段一：dry-run plan（只读，无副作用）──
@staticmethod
def plan_storage_upgrade(project_name: str) -> dict[str, Any]:
    """返回 v2/v1 → v3 迁移计划（JSON-safe）。不移动任何文件。"""

# ── 阶段二：confirmation token（由 plan 内容派生，防 stale）──
# token = sha256(json.dumps([project_name, source_paths, target_paths,
#                            file_count, total_bytes, conflicts, unknown_paths],
#                           sort_keys=True))

# ── 阶段三：execute（幂等：token 过期即拒绝）──
@staticmethod
def upgrade_storage(project_name: str, token: str) -> dict[str, Any]:
    """执行迁移。返回 MigrationResult。"""
```

### 1.2 MigrationPlan 字段（plan 至少返回）

```python
{
    "from_version": int,          # detect_storage_version(project_dir)（1 或 2）
    "to_version": 3,
    "project": project_name,
    "source_paths": {key: abs_path},      # 每个逻辑 key 当前解析路径
    "target_paths": {key: abs_path},      # 每个逻辑 key 的 v3 路径
    "file_count": int,                    # 项目内将移动的文件数（不含 preview/全局）
    "total_bytes": int,
    "conflicts": [                        # v3 目标已存在非空目录 → 冲突清单
        {"target": str, "exists": bool, "non_empty": bool, "action": "merge|overwrite|block"}
    ],
    "unknown_paths": [                    # 不属于任何已知 key 的根级条目 → 默认 preserve
        {"path": str, "kind": "file|dir", "size": int, "action": "preserve_to_migration_keep"}
    ],
    "relative_path_records": [            # 将被重写的持久化 relative path 统计
        {"document": "quality_state.json", "field": "revisions[].relative_path", "count": int},
        {"document": "quality_state.json", "field": "delivery_manifests[].outputs[].relative_path", "count": int},
        {"document": "quality_state.json", "field": "export_jobs[].outputs[].relative_path", "count": int},
        {"document": "voice_bindings.json", "field": "bindings[*]", "count": int},
        {"document": "voice_bindings.json", "field": "role_bindings[].project_voice_path", "count": int},
        {"document": "voice_cast.json", "field": "roles[].project_voice_path", "count": int},
        {"document": "project.json", "field": "source_file", "count": 1},
        {"document": "project.json", "field": "voice_bindings_path", "count": 1},
        {"document": "production_tasks.sqlite3", "field": "options_json.revision_snapshot[].relative_path", "count": int},
    ],
    "backup_required": True,
    "backup_target": "<data_dir>/backups/<name>_<ts>_<id>.audiobook-project.zip",
    "blockers": [                         # 非空 → 拒绝迁移
        {"code": "PROJECT_NOT_FOUND", ...},
        {"code": "LIVE_PRODUCTION_TASK", "task_id": ..., "status": ...},
        {"code": "LIVE_SUPPLEMENT_TASK", ...},
        {"code": "LIVE_REPAIR", "repair_id": ..., "status": ...},
        {"code": "LIVE_EXPORT_TASK", ...},
        {"code": "MIGRATION_ALREADY_DONE", ...},
    ],
    "token": str,                         # 阶段二
}
```

### 1.3 安全条件（迁移前置 blocker）

**存在任何真正 live 的 production / supplement / repair / export 任务 → 拒绝迁移。**

```python
# 1) 复用正式 guard：services/project.py:58-90 ensure_project_mutation_allowed(project, "storage_upgrade")
#    （覆盖 synthesis + export + 带 idempotency_key 的 supplement/voice_preview）
# 2) 补 repair 检查（repair 记录在 quality_state.json，不在 production_tasks）：
repairs = QualityRepository.list_history(project, "repair_history")
live_repair = [r for r in repairs if r.get("status") in {
    "preparing", "submitting", "pending", "running", "pausing", "paused", "cancelling"}]
# 3) Quick TTS / voice_preview（无 idempotency_key 的 utility）不阻塞项目迁移：
#    Quick TTS 数据在全局 <data_dir>/quick_tts，与项目无关；#46 已修 stale/live 语义，
#    迁移器**不得修改 ProductionRuntime / RuntimeTTSService**，只读取 list_tasks 判定。
```

实现要点：`TaskRepository.list_tasks(project=project)` 取全部任务行，按 `record.status in _ACTIVE_STATES` 且 task_type ∈ {synthesis, supplement, export} 判定（与 `export.py:181-237 _active_blockers` 同语义）；再叠加 repair_history 活动记录。**只读**，绝不写任务表。

### 1.4 执行顺序（upgrade_storage）

```
1. token 校验（与 plan 重新计算比对，过期/不匹配 → 拒绝）
2. blocker 校验（§1.3）
3. backup：ProjectBackupService.create_backup(project_name, target_dir=None)
   - 成功 → 记录 backup_path 到 result；失败 → 立即中止，不移动任何文件
4. 创建 v3 布局（ensure_layout(project_dir, prefer_version=3, compatibility=False)）
5. 文件移动（先 move 后 rewrite，全部同盘 os.replace / shutil.move）：
   a. 根系统 JSON 5 件 → 99_系统数据/配置/
   b. 单章 JSON（v2: 03_章节文本/; v1: chapters/）→ 99_系统数据/章节数据/
   c. 音频目录：segments → 02_生成音频/分段音频
      chapter_audio → 02_生成音频/章节音频；merged_audio → 02_生成音频/合并音频
      voices/04_角色与声音 内容 → 01_原始资料/项目音色（含 voice_bindings.json 镜像）
      cache/supplement_tasks → 02_生成音频/补录音频/（保留 task_id 子目录）
   d. 导出：09_导出文件/exports/<task_id>/ → 03_导出成品/正式导出/<task_id>/
      09_导出文件 顶层 supplement_* / 其它手工成品 → 03_导出成品/补录/（supplement_*）
      + 03_导出成品/正式导出/legacy_output/（其余旧 output 内容）
   e. 质检：08_质检记录/* → 99_系统数据/质检/
   f. 配置：01_项目配置/* → 99_系统数据/配置/（production_tasks.sqlite3 → 99_系统数据/任务/）
   g. cache → 99_系统数据/缓存/；logs → 99_系统数据/日志/
   h. unknown_paths → 99_系统数据/迁移保留/<原名>/（默认 preserve，禁止删除；移动失败则原位保留并报告）
6. 删除 v1/v2 遗留空目录与 legacy junction（voices/segments/chapters/output 等），
   确认对应数据已移动且目标非空；只删“空目录/符号链接”，绝不删用户文件
7. 重写持久化 relative_path（§1.5）
8. 更新 project.json：storage_version=3、directories=V3 映射、source_file/voice_bindings_path 新值
9. 校验：check_project_integrity(project) 全绿；quality_state / task DB 可读
10. 返回 MigrationResult（含 backup_path、moved 统计、rewritten 统计、unknown 报告）
```

**失败回滚**：迁移过程中任一步失败 → 停止；已移动条目反向移动回原位置（按 moved log 逆序）；已重写 JSON 从 backup ZIP 恢复（backup 已含全部原文件）；不删除 backup。backup 路径记录在 migration result，永不自动删除。

### 1.5 relative-path 迁移策略

**目标**：v3 新写永远写 v3 relative path；旧记录可靠读取。采用「**正式重写 + 集中式 resolver 兜底**」双保险：

1. **重写**（`resolve_relative` 的逆向，`make_relative` 应用于旧相对路径，再写回）：
   - `quality_state.json`：`revisions[].relative_path`、`delivery_manifests[].outputs[].relative_path`、`export_jobs[].outputs[].relative_path`（文件已移动到新位置，路径前缀同步替换；找不到目标文件 → 保留旧值并记 warning，由 resolver 兜底）。
   - `voice_bindings.json`：`bindings[*]`（相对值）与 `role_bindings[].project_voice_path`。
   - `voice_cast.json`：`roles[].project_voice_path`。
   - `project.json`：`source_file`、`voice_bindings_path`、`directories`。
   - `production_tasks.sqlite3`：`options_json` 内 `revision_snapshot[].relative_path`、`delivery_input_snapshot.active_revisions[].relative_path`（若需要保持 re-export 可读；历史 hash 不变，不改 hash 字段）。
   - **重写只针对 JSON 内容字段，不修改 delivery_input_hash / revision_snapshot_hash**（历史语义保持；若路径变化导致 hash 失效属预期，读取侧按 resolver 兜底）。
2. **兜底**：所有读取相对路径的入口统一走 `project_paths.resolve_relative(project_dir, path)`（§5 of part 2），未重写/损坏记录仍可解析旧前缀。

### 1.6 unknown / needs_preservation 文件

- 默认 **preserve，禁止删除**。
- 优先移入 `99_系统数据/迁移保留/`（保留原名，重名加后缀）；若移动失败 → 原位保留并在 result.unknown_paths 报告。
- `unknown_paths` 计入 plan 供用户确认（与 cleanup 两步交互同模式）。

---

## 2. Supplement / Export / Quick TTS 在 v3 的归属（迁移目标态）

| 流程 | v3 写入位置 |
|---|---|
| 补录合成 WAV（工作） | `02_生成音频/补录音频/<task_id>/`（迁移旧 `cache/supplement_tasks/<task_id>/`） |
| 补录用户导出 | `03_导出成品/补录/<name>.<ext>`（`SupplementService.build_output_path` 改此；不再创建 `output/` junction） |
| 正式导出（工作/最终） | 工作 `99_系统数据/临时/export/<task_id>/`；发布 `03_导出成品/正式导出/<YYYYmmdd_HHMMSS_书名>/` |
| Quick TTS（无项目） | 保持 global utility：`<data_dir>/quick_tts/cache|exports/`、`<data_dir>/runtime/utility_tasks.sqlite3`（不迁移、不纳入项目布局） |
| 用户音色复制资产 | `01_原始资料/项目音色/`；JSON（voice_bindings/character_roster/voice_cast）→ `99_系统数据/配置/` |

---

## 3. UI 入口（Bookshelf / 项目管理）

| 入口 | 行为 |
|---|---|
| 打开项目目录 | 复用 `ProjectStorageService.open_directory`（`lib.procutil.open_in_folder`，Windows 无黑框） |
| 打开生成音频 | 新增 `ProjectStorageService.open_directory(name, key="segments")`（或 v3 `02_生成音频/分段音频`） |
| 打开导出成品 | 新增 `open_directory(name, key="delivery_official")`（v3 `03_导出成品/正式导出`） |
| Storage summary | 显示 `Storage Layout: v1/v2/v3`（`detect_storage_version`）；v1/v2 显示“旧版项目目录，可整理为新版目录”+「扫描整理方案 / 确认整理」两步交互（`plan_storage_upgrade` → token → `upgrade_storage`），复用 cleanup 的确认模式（`ui/project_catalog_handlers.py:196-261` 的 scan→token→confirm 结构） |

新增 UI handler（`ui/project_catalog_handlers.py`）：
```python
def scan_selected_storage_upgrade(project_name: str) -> tuple[str, str, dict]:
    """plan_storage_upgrade → 预览（旧版→新版路径、文件数、字节、conflicts、unknown）→ (markdown, token, 确认按钮可见性)"""
def execute_selected_storage_upgrade(project_name: str, token: str) -> tuple[str, str, dict]:
    """upgrade_storage(project_name, token)；返回 result 摘要（backup_path / moved / rewritten）"""
def cancel_selected_storage_upgrade() -> tuple[str, str, dict]:
    """取消：不改变任何文件"""
```

---

## 4. 任务分解（≤5 任务，按依赖排序）

### 文件列表（相对 worktree 根）

```
lib/project_paths.py                       # 核心：v3 表 + resolver + resolve_relative/make_relative
lib/types.py                               # ProjectMeta 注释/默认值对齐（可选）
repositories/project_repo.py               # 根 JSON → resolver；_meta_path/_status_journal/_save_meta 镜像/创建
repositories/voice_cast_repo.py            # 根 JSON → config
repositories/quality_repo.py               # state_path → quality file
repositories/task_repo.py                  # get_database_path → tasks file；normalize_restored_task_database
repositories/project_storage_repo.py       # marker/check_integrity/_current_segment_ids → resolver
services/project_storage.py                # plan_storage_upgrade / upgrade_storage / open_directory(key) / summary 显示
services/project.py                        # bind_voice 目标目录 → project_voices；guard 复用
services/project_backup.py                 # 恢复后按 resolver 校验 marker（v3 项目）
services/export.py                         # execute_export_job → 临时/正式导出目录；_relative_output → make_relative
services/quality.py                        # _absolute/_project_relative → resolve_relative/make_relative；归档目录
services/supplement.py                     # build_output_path → 03_导出成品/补录/
services/voice_cast.py                     # _relative_project_voice_path → v3；根 JSON → config
services/delivery.py                       # _project_relative/_absolute_project_path → resolver
services/audio_pipeline.py                 # 读 structured_script / segments 改 resolver（export_book/generate_subtitles/concat_for_preview）
lib/segment_cache.py                       # 无核心改动（segments 目录由调用方传入）
app.py                                     # do_supplement_synth 路径；do_export 路径；目录打开/Storage summary 接线
ui/project_catalog_handlers.py             # 新增 storage upgrade 三步 handler + 目录打开 handler
ui/pages/overview_page.py                  # 书架按钮：打开生成音频/打开导出成品/整理目录
ui/pages/project_page.py / export_page.py / supplement_page.py  # 文案/保存位置提示对齐 v3
services/production_runtime.py             # ❌ 不改（#46 语义保持）
```

### T01 项目基础设施：v3 resolver（P0）

- **Source Files**：`lib/project_paths.py`、`lib/types.py`、`repositories/project_repo.py`（仅 marker/`_meta_path`/`_status_journal_path`/`_save_meta` 镜像的最小适配）
- **Dependencies**：无（基础设施，其余任务都依赖）
- **内容**：`STORAGE_VERSION=3`；`V3_DIRS/V2_DIRS/V1_DIRS/V3_FILES` 表；`detect_storage_version/directory_map/project_dir/canonical_project_dirs/layout_manifest/ensure_layout(compatibility=False 默认)`；文件级 helper（`project_meta/structured_script/voice_bindings/character_roster/voice_cast/quality_state/task_db/segment_status_journal`）；`resolve_relative/make_relative`（legacy 前缀映射）；`is_v2_project` 兼容别名。配套单测：三版本路径解析表、`resolve_relative` 前缀映射、`detect_storage_version`。

### T02 数据层改造：repos/services 全量走 resolver（P0）

- **Source Files**：`repositories/project_repo.py`、`repositories/voice_cast_repo.py`、`repositories/quality_repo.py`、`repositories/task_repo.py`、`repositories/project_storage_repo.py`、`services/project.py`、`services/voice_cast.py`、`services/quality.py`、`services/delivery.py`、`services/audio_pipeline.py`、`services/export.py`（路径读取侧）、`lib/segment_cache.py`（保持）
- **Dependencies**：T01
- **内容**：所有根 JSON 读写、quality_state、task DB、segments/voices/exports 目录解析改走 T01 resolver；`QualityService._absolute/_project_relative`、`delivery._project_relative/_absolute_project_path`、`voice_cast._relative_project_voice_path` 改用 resolver；`ProjectService.bind_voice` 拷贝到 `project_voices`；新增项目创建即 v3（`_create_project_from_raw` 走 v3 ensure_layout，写 v3 相对路径）。配套单测：v3 项目创建后根目录仅 4 个一级目录、JSON 全在 config、quality/task DB 新位置可读写。

### T03 正式导出 + 补录 + 目录打开（业务语义迁移）（P0）

- **Source Files**：`services/export.py`、`lib/audio_pipeline.py`、`services/supplement.py`、`app.py`、`ui/project_catalog_handlers.py`、`ui/pages/overview_page.py`、`ui/pages/export_page.py`、`ui/pages/supplement_page.py`、`ui/pages/project_page.py`
- **Dependencies**：T02
- **内容**：正式导出工作目录 `99_系统数据/临时/export/<task_id>/`、发布目录 `03_导出成品/正式导出/<YYYYmmdd_HHMMSS_书名>/`（保留 atomic publish/ownership/manifest/hash）；补录 WAV → `02_生成音频/补录音频/<task_id>/`、补录导出 → `03_导出成品/补录/`；Quick TTS 保持全局；Bookshelf 增加「打开生成音频 / 打开导出成品」（`open_directory(name, key)` 复用 `lib.procutil.open_in_folder`）；Storage summary 显示 `Storage Layout: v1/v2/v3`。配套单测：导出产物只落在正式导出目录且失败不留半成品、补录导出路径、目录打开 handler。

### T04 迁移器：plan → token → execute（P0）

- **Source Files**：`services/project_storage.py`、`services/project.py`（guard 复用）、`services/project_backup.py`、`repositories/project_storage_repo.py`、`ui/project_catalog_handlers.py`
- **Dependencies**：T01、T03
- **内容**：`plan_storage_upgrade`（三阶段协议、plan 字段、live 任务 blocker、repair 检查、unknown/conflicts、relative_path_records 统计、token）；`upgrade_storage`（backup → 移动 → legacy 目录清理 → 重写 relative_path → project.json 更新 → integrity 校验 → result）；`open_directory(name, key)` 扩展；UI「扫描整理方案 / 确认整理」两步交互。配套单测：v2→v3 全量迁移（含根 JSON、quality、task DB、导出目录、unknown 保留）、live 任务拒绝、token 过期拒绝、backup 失败中止。

### T05 兼容回归 + 收尾（P1）

- **Source Files**：`services/project_backup.py`、`repositories/project_storage_repo.py`、`lib/project_paths.py`、`app.py`、`ui/project_catalog_handlers.py`、`tests/*`（回归）
- **Dependencies**：T04
- **内容**：v1 legacy 打开回归（无 manifest → 英文布局；quality/config 落 canonical 现状保持）；备份/恢复在 v3 项目上按 resolver 校验 marker；`check_project_integrity` v3 版全绿；旧 `output/` junction 清理策略；`qa_verify_export_safe_path.py` 等既有脚本适配；CHANGELOG/更新日志。**不修改 ProductionRuntime**（#46 语义保持）。

### 依赖关系

```
T01 ──► T02 ──► T03 ──► T04 ──► T05
        │        └──────────┘
        └───────────────────►（T05 回归覆盖 T01-T04）
```

---

## 5. 依赖包列表

**无新增依赖**。全部使用现有标准库与已有第三方（gradio / numpy / mutagen 等），`requirements.txt` / `requirements-dev.txt` 不变。

---

## 6. 共享知识（跨文件约定）

- 版本判定唯一入口：`lib/project_paths.detect_storage_version(project_dir)`；禁止模块自行读 `project.json["storage_version"]`。
- 相对路径统一以项目根为基准、`/` 分隔；写路径用 `project_paths.make_relative`，读路径用 `project_paths.resolve_relative`；禁止新代码 `os.path.join(project_dir, "exports"/"segments"/...)`。
- 根 JSON 读写必须走 `project_paths.project_file(project_dir, <file_key>)`；目录读写必须走 `project_paths.project_dir(project_dir, <key>)`。
- 迁移只允许在**无 live 任务**时执行：复用 `services/project.ensure_project_mutation_allowed` + repair_history 活动检查；迁移器只读任务表，不改 ProductionRuntime（#46 stale/live 语义保持）。
- 迁移必须先 backup（`ProjectBackupService.create_backup`），backup 路径写入 migration result；backup 失败即中止。
- unknown 文件默认 preserve（`99_系统数据/迁移保留/`），禁止删除。
- 正式导出：task_id 继续存系统记录，不作为主要用户目录名；每次任务独占一个可读目录；失败只清理临时工作目录，不触碰已发布成品。
- Quick TTS 无项目模式保持 global utility 数据，不纳入项目布局。
- 打开项目不得自动迁移；迁移必须显式调用 plan → token → execute。
- 所有 API/服务返回保持 `{code, data, message}` 风格与既有异常类型（`ExportPlanError`、`ProjectMutationBlockedError` 等）不变。

---

## 7. 待明确事项

1. `chapter_audio/merged_audio` 当前无写点：v3 保留目录，待 Export UX 接入；迁移时仅建目录。
2. `99_系统数据/日志/` 无项目级写入者：仅建目录 + 约定。
3. 补录工作 WAV 最终放 `02_生成音频/补录音频/<task_id>/` 还是 `99_系统数据/临时/supplement/<task_id>/`：本设计取前者（“生成音频”语义更直观），实现前请主理人确认。
4. 旧 `09_导出文件` 顶层非 `exports/<task_id>` 的手工产物（旧版单文件导出）归类：设计为 `03_导出成品/正式导出/legacy_output/`（保名不冲突），如用户更希望放 `补录/` 需确认。
5. `project.json["directories"]` 在 v3 是否仍序列化全量目录映射（含空目录）：本设计保留（与现行为一致，向前兼容）。
