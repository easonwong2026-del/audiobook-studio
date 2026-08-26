# Storage Path Inventory（v3 前置核实报告）

> 文档状态：历史 v3 前置核实报告；当前实现以 `lib/project_paths.py`、`services/project_storage.py` 和 `ARCHITECTURE.md` 为准。
> 核实基准：worktree `D:/AudiobookStudio/project/audiobook-studio-v3`，HEAD `a0cfb24`（含 #45/#46）。
> 全部结论均有代码行证据。路径均为相对 worktree 根。

---

## 0. 当前布局版本（问题 A 核实）

| 项 | 现状 | 证据 |
|---|---|---|
| `STORAGE_VERSION` | **`2`** | `lib/project_paths.py:14` `STORAGE_VERSION: Final[int] = 2` |
| v2 canonical 目录清单 | `01_项目配置/ 02_原始文件/ 03_章节文本/ 04_角色与声音/ 05_分段音频/ 06_章节音频/ 07_合并音频/ 08_质检记录/ 09_导出文件/ cache/ logs/` | `lib/project_paths.py:18-30` `CANONICAL_DIRS` |
| v1 legacy 目录清单 | `voices/ segments/ chapters/ output/ cache/ logs/` | `lib/project_paths.py:34-41` `LEGACY_DIRS` |
| manifest 判定 | `project.json["storage_version"] >= STORAGE_VERSION` 才视为 v2 | `lib/project_paths.py:54-56` `is_v2_project()` |
| 目录解析 | 逐 key 判断：canonical 存在或 legacy 不存在 → canonical；否则 legacy | `lib/project_paths.py:59-76` `directory_map()` |
| `ensure_layout` 签名 | `ensure_layout(project_dir, *, prefer_canonical=True, compatibility=True) -> dict[str, str]` | `lib/project_paths.py:99` |

**A 结论（逐条）**：

1. `ensure_layout` 会创建 `CANONICAL_DIRS` 全部目录（含 `voices/segments/chapters/output/cache/logs`）：
   - `lib/project_paths.py:106-109` `os.makedirs(project_dir, exist_ok=True)` + 对 `directory_map()` 每个 path `os.makedirs(path, exist_ok=True)`。
2. Windows 无 symlink 时创建普通空目录：`lib/project_paths.py:119-124`：
   ```python
   try:
       os.symlink(os.path.basename(canonical), legacy, target_is_directory=True)
   except OSError:
       os.makedirs(legacy, exist_ok=True)   # ← 无权限时的普通空目录兜底
   ```
   注意：兼容名（`voices/segments/chapters/output`）在 v2 项目上只是 junction/空目录；`cache/ logs/` legacy 名与 canonical 名相同（`LEGACY_DIRS` 与 `CANONICAL_DIRS` 同名），无需链接。
3. 创建项目时在 `.tmp_<name>_<uuid>` 下 `ensure_layout(tmp_dir, prefer_canonical=True, compatibility=True)` 再原子 `os.replace` 发布：`repositories/project_repo.py:716-765`。

---

## 1. 逻辑资产 × 当前读写位置 × 归属

| 逻辑资产 | 当前写入位置 | 当前读取位置 | user-facing | internal | legacy compat | 涉及文件/函数 |
|---|---|---|---|---|---|---|
| `project.json`（权威 meta） | 项目根 `project.json` | 根；另 v2 起镜像到 `01_项目配置/project.json` | ✅（项目标志/书稿元数据） | ✅（段状态/计数） | — | 写：`repositories/project_repo.py:200-201(_meta_path)`、`315-344(_save_meta)`；镜像：`338-344`。读：`lib/project_paths.py:45`、`repositories/project_storage_repo.py:615`、`services/project_backup.py:190`、`repositories/task_repo.py:420` |
| `structured_script.json` | 项目根 | 根 | ✅（书稿正文/章节结构） | ✅（段 ID 源） | — | 写：`project_repo.py:720-721`。读：`project_repo.py:270,646,1016`、`project_storage_repo.py:483,616`、`lib/audio_pipeline.py:89,431,589`、`services/export.py:628`、`services/delivery.py:382`、`lib/snapshot.py:61` |
| `voice_bindings.json` | 项目根（权威） | 根；**另镜像到 `04_角色与声音/voice_bindings.json`** | ✅ | ✅ | — | 写：`project_repo.py:1182-1193(save_bindings)`、`services/project.py:346-347`；镜像：`project_repo.py:750-753`、`services/project.py:346-347`。读：`project_repo.py:1161-1179`、`repositories/binding_repo.py:114,130,148`、`project_storage_repo.py:617,676-680`、`lib/audio_pipeline.py:381`、`services/voice_cast.py:826`、`services/delivery.py:382` |
| `character_roster.json` | 项目根 | 根 | ✅（角色清单） | ✅ | — | 写/读：`repositories/voice_cast_repo.py:19,23-53`（`ROSTER_FILE`）；存在性检查：`app.py:602,684` |
| `voice_cast.json` | 项目根 | 根 | ✅（演员表） | ✅ | — | 写/读：`repositories/voice_cast_repo.py:20,23-53`（`CAST_FILE`）；存在性检查：`lib/queue.py:303`、`services/quality.py:155,206,995`、`lib/audio_pipeline.py:376`、`services/review_audio.py:114`、`services/voice_cast.py:587,736`、`services/delivery.py:240` |
| `synthesis_overrides.json` | 项目根 | 根 | — | ✅ | — | `project_repo.py:1093,1116` |
| `synthesis_selections.json` | 项目根 | 根 | — | ✅ | — | `project_repo.py:1132,1155` |
| 章节拆分 JSON（单章文件） | `03_章节文本/`（v2 canonical；legacy `chapters/`） | 同上 | ✅（书稿章节） | — | ✅ `chapters/` | 写：`project_repo.py:735-743`；读：`project_paths.directory_map` key `chapter_text` |
| 原始书稿拷贝 | `02_原始文件/`（v2 canonical） | 同上 | ✅ | — | — | 写：`project_repo.py:727-734`；`meta.source_file` 持久化为相对路径 |
| 用户音色拷贝（绑定参考音频） | `04_角色与声音/`（v2 canonical；legacy `voices/`） | 同上 | ✅（项目音色资产） | — | ✅ `voices/` | 写：`services/project.py:330` `project_paths.project_dir(d,"voices",create=True)`；`services/voice_cast.py:783`；持久化 `project_voice_path` 统一为 `voices/<file>` 逻辑路径（见 §3.2） |
| 分段音频 WAV | `05_分段音频/`（v2 canonical；legacy `segments/`） | 同上 | ✅ | — | ✅ `segments/` | 写/读：`lib/segment_cache.py:84-100`、`services/quality.py:187,209,994`、`lib/audio_pipeline.py:93,437,604`、`repositories/project_repo.py:312`、`app.py:166,2405` |
| 章节音频 | `06_章节音频/` | 同上 | ✅ | — | — | 无写点（O13 合并试听写 preview），目录由 layout 创建；`project_storage_repo.py:290` 统计 |
| 合并音频 | `07_合并音频/` | 同上 | ✅ | — | — | 无写点（同上） |
| 正式导出产物 | `09_导出文件/exports/<task_id>/` | 同上 | ✅（交付物） | — | ✅ `output/`（legacy） | 写：`services/export.py:802-805`；失败清理 `931`；`supplement.py:491`；`app.py:2141,2169,2178,2908` |
| 补录导出产物 | `09_导出文件/supplement_*.{wav,mp3,m4b}` | 同上 | ✅ | — | — | `services/supplement.py:477-505`（`build_output_path`，legacy `output/` junction 兼容）、`app.py:2141-2158` |
| 补录合成 WAV（项目内临时） | `cache/supplement_tasks/<task_id>/`（= `05_分段音频` 外的 `cache/`） | 同上（随项目备份） | — | ✅ | — | `app.py:1978-1983`；`services/runtime_tts.py:335-340`（`_artifact_dir`：`cache/<group>/<task_id>/`）；`services/production_runtime.py:1633-1646`（artifact_dir 必须在项目内） |
| `quality_state.json` | `08_质检记录/quality_state.json` | 同上 | — | ✅ | — | `repositories/quality_repo.py:25,74-77`（`state_path` 用 key `quality`） |
| revision 归档音频 | `08_质检记录/revisions/<seg_id>/<revision_id>_<basename>.wav` | 同上 | — | ✅ | — | `services/quality.py:342-347,380-388` |
| 质检事件日志 | `08_质检记录/review_events.jsonl` | 同上 | — | ✅ | — | `services/review_audio.py:329-332` |
| `production_tasks.sqlite3` | `01_项目配置/production_tasks.sqlite3` | 同上 | — | ✅ | — | `repositories/task_repo.py:30,423-424`（`get_database_path` 用 key `config`） |
| 段状态恢复日志 | `01_项目配置/segment_status.journal.jsonl` | 同上 | — | ✅ | — | `project_repo.py:214-216,258-265` |
| 项目内 `cache/` | `cache/`（canonical 与 legacy 同名） | 同上 | — | ✅ | — | `app.py:1979`、`runtime_tts.py:337`、`lib/queue.py` 等 |
| 项目内 `logs/` | `logs/`（canonical 与 legacy 同名） | 同上 | — | ✅ | — | **仅 layout 创建**（`project_paths.py:29,40`）；项目级日志实际无写入（全局日志在 `data_dir/logs` 与程序目录 `BASE/logs`：`app.py:3803`、`production_runtime.py:90,939,2361,2579`、`runtime_engine.py:71`） |
| Quick TTS（无项目） | `<data_dir>/quick_tts/cache/<task_id>/` 与 `<data_dir>/quick_tts/exports/` | 同上 | ✅ | ✅ | — | `services/quick_tts.py:62-82,150`；任务行 `<data_dir>/runtime/utility_tasks.sqlite3`（`task_repo.py:44-45,393-405`） |
| 试听预览缓存 | `<data_dir>/preview/<project>/`（全局，非项目内） | 同上 | — | ✅ | — | `project_storage_repo.py:198-205`；`config.py:120-123` |
| 全局日志 | `<data_dir>/logs/` | 同上 | — | ✅ | — | `production_runtime.py:90,2361,2579`；`runtime_engine.py:71` |
| 备份 ZIP | `<data_dir>/backups/`（默认） | 同上 | ✅ | — | — | `services/project_backup.py:77` |
| 回收站 | `<data_dir>/.trash/projects/` | 同上 | ✅ | — | — | `project_storage_repo.py:207-217` |
| 项目列表隐藏记录 | `<data_dir>/.project_catalog.json` | 同上 | — | ✅ | — | `project_repo.py:370-382`、`project_storage_repo.py:420-425` |

---

## 2. 问题 B 核实：正式导出路径

**结论：确认**。`services/export.py:802-805`：

```python
export_dir = os.path.join(project_dir, "exports", str(record.task_id))   # ← project_dir/"exports"/task_id
os.makedirs(export_dir, exist_ok=True)
```

- 导出文件最终写入 `export_dir`，文件名 `<项目名>.<ext>`：`lib/audio_pipeline.py:177-192`（`export_book(..., output_dir=export_dir, atomic_publish=True)` 由 `services/export.py:818-826` 调用）。
- 失败处理：`services/export.py:927-931` `shutil.rmtree(export_dir, ignore_errors=True)`（整目录删除，无半成品残留的意图，但**在发布点之前 `.part` 文件也在该目录**，见 `audio_pipeline.py:178-181,146-149`）。
- artifact 的 `relative_path` 以项目根为基准持久化：`services/export.py:488-489` `_relative_output` → `QualityService._project_relative`（`services/quality.py:53-62`），产物形如 `exports/<task_id>/<书名>.<ext>`。
- 字幕也写 `export_dir`：`services/export.py:837-844`。
- 交付 manifest 的 `outputs[].relative_path` 同上：`services/export.py:864-871`。

---

## 3. 问题 C 核实：项目根散落 JSON

| 文件 | 写者 | 读者 | 说明 |
|---|---|---|---|
| `project.json` | `ProjectRepository._save_meta`（`project_repo.py:316-344`） | `project_paths._manifest`、`ProjectRepository._load_meta`、`project_storage_repo`、`task_repo`、`project_backup`、`project_catalog` 等 | 权威 meta；v2 起同时在 `01_项目配置/project.json` 存镜像 |
| `structured_script.json` | `ProjectRepository._create_project_from_raw`（`project_repo.py:720-721`） | 全链路（见 §1） | 项目三标志之一（`project_repo.py:31-35`） |
| `voice_bindings.json` | `ProjectRepository.save_bindings`（`project_repo.py:1182-1193`）、`ProjectService.bind_voice`（`project.py:330-347`）、`services/voice_cast.py:826` | `ProjectRepository.load_bindings`、`binding_repo`、`quality.py`、`audio_pipeline.py`、`delivery.py` | 项目三标志之一 |
| `character_roster.json` | `VoiceCastRepository.save_roster`（`voice_cast_repo.py:44-45`） | `VoiceCastRepository.load_roster`、`delivery.py:241`、`app.py:602,684` | 有 roster 才进入 Voice Cast 模式 |
| `voice_cast.json` | `VoiceCastRepository.save_cast`（`voice_cast_repo.py:52-53`） | `VoiceCastRepository.load_cast`、`delivery.py:240`、`quality.py`、`audio_pipeline.py:376`、`queue.py:303`、`review_audio.py:114` | 存在即 cast_active |
| `synthesis_overrides.json` / `synthesis_selections.json` | `ProjectRepository.set_*`（`project_repo.py:1105-1117,1144-1156`） | `get_*`（同文件） | 非项目标志 |

另注意：`_PROJECT_MARKERS = ("project.json","structured_script.json","voice_bindings.json")`（`project_repo.py:31-35`）——项目合法性判定依赖这三个**根级**文件，v3 迁移后此判定必须同步改为解析器定位（根目录不再有这些 JSON）。

---

## 4. 问题 D 核实：relative_path 持久化完整 inventory（最高风险）

> 统一约定：所有相对路径均以**项目根**为基准、使用 `/` 分隔（`QualityService._project_relative`、`delivery._project_relative`）。迁移后这些旧相对路径必须仍可解析（见 v3 设计 legacy-relative resolver / 正式重写）。

### 4.1 project.json（`ProjectMeta` 字段）
| 字段 | 内容示例 | 位置 |
|---|---|---|
| `directories` | `{"config":"01_项目配置",...}`（=`layout_manifest()`= `CANONICAL_DIRS` 的序列化） | 写：`project_repo.py:760,331`；读：`ProjectMeta.directories`（`lib/types.py`） |
| `source_file` | `02_原始文件/<name>.json`（`os.path.relpath(source_target, tmp_dir)`） | 写：`project_repo.py:761,332` |
| `voice_bindings_path` | 默认 `voice_bindings.json` | 写：`project_repo.py:329`；默认 `lib/types.py:61` |

### 4.2 voice_bindings.json 内部路径
| 字段 | 内容示例 | 位置 |
|---|---|---|
| `bindings[role]`（legacy 键） | 相对或绝对路径（相对即项目根基准）；读时 `not abs → join(project_dir, path)` | 写：`services/project.py:330-347`；读：`binding_repo.py:130`、`project_storage_repo.py:676-680`、`quality.py:116-117` |
| `role_bindings[role_id].project_voice_path` | 统一为 `voices/<filename>`（**逻辑路径，非真实目录名**，见下） | 写：`services/voice_cast.py:239-242`（`_relative_project_voice_path`）；读：`voice_cast.py:260,314,612,757,846,1082,1301`、`quality.py:109,117`、`audio_pipeline.py:394-396`、`delivery.py:258-276,293-323` |
| `role_bindings[role_id].voice_sha256` | 内容哈希（路径无关，无需迁移） | `voice_cast.py` / `delivery.py` |

### 4.3 character_roster.json / voice_cast.json 内部路径
| 字段 | 内容示例 | 位置 |
|---|---|---|
| `voice_cast.roles[role_id].project_voice_path` | `voices/<filename>`（相对） | 读：`delivery.py:258-276`；写由 `VoiceCastResolver.bind_cast_role` 链路（`voice_cast.py`） |
| `voice_sha256` | 内容哈希（无需迁移） | `voice_cast.py`、`delivery.py:264` |

### 4.4 quality_state.json（`QualityRepository`）
| 集合 | 字段 | 内容示例 | 位置 |
|---|---|---|---|
| `revisions[rev_id]` | `relative_path` | `08_质检记录/revisions/<seg_id>/<rev_id>_<file>.wav` | 写：`quality_repo.py:171`（create_revision）、`212`（bootstrap）、`254`（update_revision 允许字段）；**活动/归档音频由 `services/quality.py:342-347,380-388` 决定** |
| `active_revisions` | `{seg_id: rev_id}` | 段 → 当前 revision | 写：`quality_repo.py:224,240`；读：`delivery.py:191-192`、`quality.py:288-289` 等 |
| `revisions[].params.engine_snapshot` | 引擎快照（含 `model_dir` 等路径，经 `public_profile` 清洗后无本地路径） | — | 写：`quality_repo.py:174`；读：`quality.py`、`delivery.py` |
| `technical_qa[rev_id]` | QA 结果（无路径） | — | `quality_repo.py:312-372` |
| `human_reviews[rev_id]` | Review 结果（无路径） | — | `quality_repo.py:374-415` |
| `repair_history[id]` | `{repair_id, task_id, status, ...}`（task_id 非路径） | — | `quality_repo.py:417-495`；活动判定：`services/export.py:208-224` |
| `export_jobs[id]` | `{task_id, outputs:[{relative_path,...}], manifest_id, ...}` | `exports/<task_id>/<书名>.mp3` | 写：`export.py:688-725,911-920`；`outputs[].relative_path`：`export.py:864-871` |
| `delivery_manifests[id]` | `{export_id, outputs:[{relative_path,...}], ...}` | 同上 | 写：`export.py:873-909` |

### 4.5 production_tasks.sqlite3（`TaskRepository`）
| 列/字段 | 内容示例 | 位置 |
|---|---|---|
| `artifact_dir` | 绝对路径（项目内），运行时产物目录（补录/预览/导出工作目录） | 列：`task_repo.py:530`；运行时约束“必须在项目目录内”：`production_runtime.py:1641-1646`；**terminal 后不删除（历史记录）** |
| `options_json` | 含 `revision_snapshot[].relative_path`、`delivery_input_snapshot`（内部含 `active_revisions[].relative_path`、`voice_cast.roles[].project_voice_path`）、`engine_snapshot` | 写：`export.py:656-677`（`_task_options`）；读：`export.py:806-812`（segment_paths 重建）、`757-777` |
| `startup_json` | 启动阶段字段（无路径） | `task_repo.py:1218-1302` |
| `scope_json` / `progress_json` / `failed_segment_ids_json` / `log_lines_json` | 无路径 | — |

### 4.6 其它持久化路径
| 资产 | 字段 | 位置 |
|---|---|---|
| `01_项目配置/segment_status.journal.jsonl` | 崩溃恢复日志（无路径） | `project_repo.py:214-216` |
| `08_质检记录/review_events.jsonl` | 事件日志（无路径） | `review_audio.py:329-332` |
| 预览缓存 | `<data_dir>/preview/<project>`（全局，非项目内；不随迁移） | `project_storage_repo.py:198-205` |
| 任务目录 legacy JSON | `<data_dir>/preview/task_records/*.json`（全局 legacy 后端） | `task_repo.py:367-371` |

---

## 5. 迁移面清单（谁必须感知 v3）

| 模块 | 需要改动点 |
|---|---|
| `lib/project_paths.py` | 升级 v3 布局表 + resolver（核心） |
| `repositories/project_repo.py` | 根 JSON 读写全部改走 resolver；`_meta_path`、`_status_journal_path`、`_save_meta` 镜像、`_create_project_from_raw`、`source_file` |
| `repositories/voice_cast_repo.py` | `_load/_save` 根 JSON → config |
| `repositories/task_repo.py` | `get_database_path`（config→tasks）、`normalize_restored_task_database` |
| `repositories/quality_repo.py` | `state_path`（quality key） |
| `repositories/project_storage_repo.py` | `check_project_integrity` 根 marker 校验、`_current_segment_ids`、类别统计 |
| `services/export.py` | `execute_export_job` 导出目录 → v3 临时/正式目录；`_relative_output` 走 resolver；manifest outputs |
| `services/quality.py` | `_absolute/_project_relative` 走 resolver；revision 归档目录 |
| `services/supplement.py` | `build_output_path` 补录导出 → `03_导出成品/补录/`；legacy `output/` 兼容 |
| `services/voice_cast.py` | `_relative_project_voice_path` → v3 `01_原始资料/项目音色/`；根 JSON 读写 |
| `services/audio_pipeline.py` | `structured_script.json` 读、`segments/` 解析、补录导出 |
| `services/delivery.py` | `_project_relative/_absolute_project_path` 走 resolver；`_voice_cast_snapshot` |
| `lib/segment_cache.py` | segments 目录由调用方传（无需改核心逻辑） |
| `app.py` / `ui/*` | 目录打开按钮、Storage summary 显示、`do_supplement_synth` 的 cache 路径、Quick TTS 不变 |
| `services/project_backup.py` | 备份/恢复 marker 校验（根 JSON 已移动，恢复后需重写 manifest 字段或按 resolver 读） |
| `services/project.py` | `bind_voice` 拷贝目标目录、`ensure_project_mutation_allowed`（迁移复用） |
| `services/project_storage.py` | `repair_integrity`（v2→v3 目录补建逻辑）、新增 `plan_storage_upgrade/upgrade_storage` |

---

## 6. 关键事实速查（供 v3 设计与迁移器使用）

1. v2 `directory_map` 的 legacy 解析是**逐 key 独立**的：`config/source/chapter_audio/merged_audio/quality` 无 legacy 名 → legacy 项目也会落到中文 canonical 目录（`project_paths.py:72-75` 中 `not legacy` 分支）。因此 v1 legacy 项目上 `quality_state.json`/`production_tasks.sqlite3` 实际可能在 `08_质检记录/`、`01_项目配置/`。
2. `ensure_layout(compatibility=True)` 只对 `voices/segments/chapters/output` 建 legacy 兼容目录（`project_paths.py:111-124`）；`cache/logs` legacy 名=canonical 名。
3. 项目合法性 = 根三文件（`project_repo.py:31-35`）——v3 必须改为“按 resolver 找到的配置 JSON”。
4. `meta.directories` 只存**目录名映射**（`CANONICAL_DIRS` 全量），不存文件级路径；文件级路径散落在各 repo 的 `os.path.join(project_dir, <file>)`。
5. #46（`709b401`）只修复了 runtime utility 任务 stale/live 语义（`RuntimeTTSService._runtime_lost`、`runtime_engine` 状态探针），**未触碰** storage layout；v3 不得修改 `ProductionRuntime`。
6. 项目内 `logs/` 目录当前无实际写入（全局日志在 `data_dir/logs`）；v3 迁移只需处理目录本身。
