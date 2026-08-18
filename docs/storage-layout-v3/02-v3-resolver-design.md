# v3 ProjectLayout Resolver 设计

> 目标：`STORAGE_VERSION = 3`，项目根只暴露 4 个用户可理解一级目录；建立**唯一** resolver，业务模块禁止散落路径猜测、禁止新代码 `os.path.join(project_dir, "exports")` 等硬编码。

---

## 1. 逻辑结构契约（用户已定，落成 API）

```
<项目根>/
├── 01_原始资料/
│   ├── 书稿/                      # source_book：源书稿 JSON（原 02_原始文件 + 03_章节文本合并语义）
│   └── 项目音色/                  # project_voices：用户音色拷贝资产（原 04_角色与声音）
├── 02_生成音频/
│   ├── 分段音频/                  # segments
│   ├── 章节音频/                  # chapter_audio
│   ├── 合并音频/                  # merged_audio
│   └── 补录音频/                  # supplement_audio：补录合成 WAV（原 cache/supplement_tasks → 迁移后）
├── 03_导出成品/
│   ├── 正式导出/                  # delivery_official：每任务一个可读目录
│   └── 补录/                      # delivery_supplement：补录导出成品（原 09_导出文件 顶层 supplement_*）
└── 99_系统数据/                   # system_root（用户不关心，但可解释）
    ├── 配置/                      # config：project.json / structured_script.json / voice_bindings.json /
    │                              #        character_roster.json / voice_cast.json / synthesis_*.json / 段状态日志
    ├── 章节数据/                  # chapter_data：单章拆分 JSON（原 03_章节文本）
    ├── 质检/                      # quality：quality_state.json / revisions/ / review_events.jsonl
    ├── 任务/                      # tasks：production_tasks.sqlite3 及 project-local task durable data
    ├── 缓存/                      # cache：supplement_tasks 等可清理缓存
    ├── 日志/                      # logs：项目级日志（当前无写入，保留目录）
    └── 临时/                      # temp：migration / atomic intermediate / .tmp_* / .part 等
```

系统 JSON 归属（**全部进 `99_系统数据/配置/`**）：`project.json`、`structured_script.json`、`voice_bindings.json`、`character_roster.json`、`voice_cast.json`（另 `synthesis_overrides.json`、`synthesis_selections.json` 同目录）。

---

## 2. 文件级 helper（resolver 公共 API）

建议实现位置：**`lib/project_paths.py` 原地扩展**（保持单一导入入口，避免新增模块导致循环依赖）。也可抽 `lib/storage_layout.py`，由 `project_paths` re-export；**优先原地扩展**（改动面最小）。

```python
STORAGE_VERSION: Final[int] = 3

# ── v3 一级/二级目录（用户可见，稳定不可变）──
V3_DIRS: Final[dict[str, str]] = {
    # 一级
    "source_root":       "01_原始资料",
    "generated_root":    "02_生成音频",
    "delivery_root":     "03_导出成品",
    "system_root":       "99_系统数据",
    # 01_原始资料
    "source_book":       "01_原始资料/书稿",
    "project_voices":    "01_原始资料/项目音色",
    # 02_生成音频
    "segments":          "02_生成音频/分段音频",
    "chapter_audio":     "02_生成音频/章节音频",
    "merged_audio":      "02_生成音频/合并音频",
    "supplement_audio":  "02_生成音频/补录音频",
    # 03_导出成品
    "delivery_official":    "03_导出成品/正式导出",
    "delivery_supplement":  "03_导出成品/补录",
    # 99_系统数据
    "config":        "99_系统数据/配置",
    "chapter_data":  "99_系统数据/章节数据",
    "quality":       "99_系统数据/质检",
    "tasks":         "99_系统数据/任务",
    "cache":         "99_系统数据/缓存",
    "logs":          "99_系统数据/日志",
    "temp":          "99_系统数据/临时",
}

# ── v2 canonical（backward read：storage_version == 2）──
V2_DIRS: Final[dict[str, str]] = {
    "config": "01_项目配置", "source": "02_原始文件", "chapter_text": "03_章节文本",
    "voices": "04_角色与声音", "segments": "05_分段音频", "chapter_audio": "06_章节音频",
    "merged_audio": "07_合并音频", "quality": "08_质检记录", "exports": "09_导出文件",
    "cache": "cache", "logs": "logs",
}

# ── v1 legacy（backward read：no manifest / legacy 英文布局）──
V1_DIRS: Final[dict[str, str]] = {
    "source_root": "", "source_book": "", "project_voices": "voices",
    "segments": "segments", "chapter_audio": "", "merged_audio": "",
    "supplement_audio": "", "delivery_official": "output", "delivery_supplement": "",
    "config": "", "chapter_data": "chapters", "quality": "08_质检记录",
    "tasks": "", "cache": "cache", "logs": "logs", "temp": "",
}
```

### 关键函数签名（均在 `lib/project_paths.py`）

```python
def detect_storage_version(project_dir: str) -> int:
    """返回 1/2/3。
    - project.json['storage_version'] >= 3 → 3
    - == 2 → 2
    - 缺失/0/<2 → 1（legacy 英文布局，或 v2 但未写 manifest 的过渡形态）
    """

def directory_map(project_dir: str, *, prefer_version: int | None = None) -> dict[str, str]:
    """返回 {逻辑 key: 绝对路径}。prefer_version=None 时按 detect_storage_version 决定。
    兼容旧调用（v2 的 canonical/legacy 逐 key 解析语义由 v2/v1 表覆盖）。"""

def project_dir(project_dir: str, key: str, *, create: bool = False,
                prefer_version: int | None = None) -> str:
    """返回一个逻辑目录，可选创建（含父目录）。key 不在 V3_DIRS 抛 KeyError。"""

def canonical_project_dirs(project_dir: str) -> dict[str, str]:
    """总是返回 v3 路径（与旧签名语义一致：忽略当前 manifest）。"""

def layout_manifest(project_dir: str) -> dict[str, str]:
    """返回 v3 序列化映射（写入 project.json['directories']）。"""

def ensure_layout(project_dir: str, *, prefer_version: int | None = 3,
                  compatibility: bool = False) -> dict[str, str]:
    """创建 v3 布局（compatibility 默认 False：v3 不建 legacy 空目录/junction）。"""

def resolve_relative(project_dir: str, relative_path: str) -> str:
    """集中式 legacy-relative resolver（迁移后旧相对路径仍可解析）。
    规则：按 detect_storage_version 已知前缀映射（见 §5）；未知/越界 → ValueError。
    所有业务模块读取持久化 relative_path 时必须经此函数。"""

def make_relative(project_dir: str, path: str) -> str:
    """路径 → 当前版本相对路径（v3 项目永远产出 v3 相对路径）。"""
```

### 文件级 helper（`V3_FILES` 表 + 函数）

```python
V3_FILES: Final[dict[str, str]] = {
    "project_meta":       "99_系统数据/配置/project.json",
    "structured_script":  "99_系统数据/配置/structured_script.json",
    "voice_bindings":     "99_系统数据/配置/voice_bindings.json",
    "character_roster":   "99_系统数据/配置/character_roster.json",
    "voice_cast":         "99_系统数据/配置/voice_cast.json",
    "quality_state":      "99_系统数据/质检/quality_state.json",
    "task_db":            "99_系统数据/任务/production_tasks.sqlite3",
    "segment_status_journal": "99_系统数据/配置/segment_status.journal.jsonl",
}

def project_file(project_dir: str, key: str, *, create: bool = False) -> str:
    """返回文件绝对路径；create=True 时创建父目录。key 不在 V3_FILES 抛 KeyError。"""

def project_meta(project_dir: str) -> str:          # → 99_系统数据/配置/project.json
def structured_script(project_dir: str) -> str:     # → 99_系统数据/配置/structured_script.json
def voice_bindings(project_dir: str) -> str:        # → 99_系统数据/配置/voice_bindings.json
def character_roster(project_dir: str) -> str:      # → 99_系统数据/配置/character_roster.json
def voice_cast(project_dir: str) -> str:            # → 99_系统数据/配置/voice_cast.json
def quality_state(project_dir: str) -> str:         # → 99_系统数据/质检/quality_state.json
def task_db(project_dir: str) -> str:               # → 99_系统数据/任务/production_tasks.sqlite3
```

对 v2/v1 的 backward read：以上文件级函数在非 v3 项目上返回对应版本路径（v2：`project.json` 根、`01_项目配置/production_tasks.sqlite3`、`08_质检记录/quality_state.json`；v1：`project.json` 根、无 task DB/quality_state 约定）。

---

## 3. v3 / v2 / v1 相对路径映射表（供 backward read）

| 逻辑 key | v3 相对路径 | v2 相对路径 | v1 legacy 相对路径 |
|---|---|---|---|
| source_root | `01_原始资料` | —（`02_原始文件`+`03_章节文本` 分列） | — |
| source_book | `01_原始资料/书稿` | `02_原始文件` | — |
| project_voices | `01_原始资料/项目音色` | `04_角色与声音` | `voices` |
| segments | `02_生成音频/分段音频` | `05_分段音频` | `segments` |
| chapter_audio | `02_生成音频/章节音频` | `06_章节音频` | — |
| merged_audio | `02_生成音频/合并音频` | `07_合并音频` | — |
| supplement_audio | `02_生成音频/补录音频` | `cache/supplement_tasks`（工作 WAV）；无成品目录 | — |
| delivery_root | `03_导出成品` | — | — |
| delivery_official | `03_导出成品/正式导出` | `09_导出文件/exports/<task_id>`（+顶层旧手工导出） | `output` |
| delivery_supplement | `03_导出成品/补录` | `09_导出文件`（顶层 `supplement_*`） | `output` |
| system_root | `99_系统数据` | — | — |
| config | `99_系统数据/配置` | `01_项目配置` | 根（`project.json` 等在根） |
| chapter_data | `99_系统数据/章节数据` | `03_章节文本` | `chapters` |
| quality | `99_系统数据/质检` | `08_质检记录` | `08_质检记录`（无 legacy 名 → 落 canonical） |
| tasks | `99_系统数据/任务` | `01_项目配置`（`production_tasks.sqlite3` 在其下） | 无项目库（legacy 全局 JSON） |
| cache | `99_系统数据/缓存` | `cache` | `cache` |
| logs | `99_系统数据/日志` | `logs` | `logs` |
| temp | `99_系统数据/临时` | — | — |

文件级 backward 映射：

| 文件 | v3 | v2 | v1 |
|---|---|---|---|
| project.json | `99_系统数据/配置/project.json` | 根 `project.json`（镜像 `01_项目配置/project.json`） | 根 `project.json` |
| structured_script.json | `99_系统数据/配置/structured_script.json` | 根 | 根 |
| voice_bindings.json | `99_系统数据/配置/voice_bindings.json` | 根（镜像 `04_角色与声音/voice_bindings.json`） | 根 |
| character_roster.json | `99_系统数据/配置/character_roster.json` | 根 | 根 |
| voice_cast.json | `99_系统数据/配置/voice_cast.json` | 根 | 根 |
| quality_state.json | `99_系统数据/质检/quality_state.json` | `08_质检记录/quality_state.json` | `08_质检记录/quality_state.json` |
| production_tasks.sqlite3 | `99_系统数据/任务/production_tasks.sqlite3` | `01_项目配置/production_tasks.sqlite3` | （无；legacy 全局 JSON） |
| segment_status.journal.jsonl | `99_系统数据/配置/segment_status.journal.jsonl` | `01_项目配置/segment_status.journal.jsonl` | — |

---

## 4. 读取规则（唯一权威）

```
storage_version >= 3  → 全部走 v3 表
storage_version == 2  → 全部走 v2 表（canonical 01~09）
legacy / no manifest  → 全部走 v1 表（英文布局；quality/config 因无 legacy 名回落到 08_质检记录 / 01_项目配置，保持现状）
```

- `detect_storage_version` 是**唯一**版本判定入口；`is_v2_project()` 保留为兼容别名（`detect_storage_version(project_dir) >= 2`）。
- **打开项目不自动迁移**：版本判定只影响路径解析；迁移必须显式调用 `plan_storage_upgrade` / `upgrade_storage`（见第三部分）。
- 任何业务模块不再直接 `os.path.join(project_dir, "exports"/"segments"/...)`；统一 `project_paths.project_dir(project_dir, key)` 或 `project_paths.project_file(project_dir, file_key)`。

---

## 5. 集中式 legacy-relative resolver（`resolve_relative`）

持久化相对路径在迁移后分两类处理（第三部分详述）：
- **正式重写**：`quality_state.json` 的 `revisions[].relative_path`、`delivery_manifests`/`export_jobs` 的 `outputs[].relative_path`、task DB `options_json.revision_snapshot[].relative_path`、`voice_bindings.json` 的 `bindings`/`role_bindings[].project_voice_path`、`voice_cast.json` 的 `roles[].project_voice_path`、`project.json` 的 `source_file/directories/voice_bindings_path`。
- **resolver 兜底**：对未重写/损坏/旧备份的 relative_path，`resolve_relative()` 按前缀映射：
  - `exports/<task_id>/...` → `03_导出成品/正式导出/<task_id>/...`
  - `output/...` → `03_导出成品/正式导出/...`（旧手工导出，保持原名目录）
  - `05_分段音频/...`（或 `segments/...`）→ `02_生成音频/分段音频/...`
  - `04_角色与声音/...`（或 `voices/...`）→ `01_原始资料/项目音色/...`
  - `03_章节文本/...`（或 `chapters/...`）→ `99_系统数据/章节数据/...`
  - `08_质检记录/...` → `99_系统数据/质检/...`
  - `01_项目配置/...` → `99_系统数据/配置/...`（`production_tasks.sqlite3` 例外 → `99_系统数据/任务/`）
  - `cache/...` → `99_系统数据/缓存/...`
  - `02_原始文件/...` → `01_原始资料/书稿/...`
  - 根级 `project.json/structured_script.json/voice_bindings.json/character_roster.json/voice_cast.json` → 对应 v3 配置路径
- 未知前缀/越界（`..` 等）→ `ValueError`（与 `QualityService._absolute` 现行为一致）。

---

## 6. 业务模块改造清单（禁止散落路径）

| 模块 | 替换为 |
|---|---|
| `ProjectRepository._meta_path` | `project_paths.project_file(project_dir, "project_meta")` |
| `ProjectRepository._status_journal_path` | `project_file(..., "segment_status_journal")` |
| `ProjectRepository._save_meta` 镜像 | 仅 v3 写 `config/project.json`（同文件，无镜像）；v2 行为保留 |
| `ProjectRepository.load_project / _create_project_from_raw / _script_meta / get_synthesis_* / load_bindings/save_bindings` | 根 JSON → `project_file(..., "structured_script"/"voice_bindings"/...)` |
| `VoiceCastRepository._load/_save` | `project_file(..., "character_roster"/"voice_cast")` |
| `TaskRepository.get_database_path / normalize_restored_task_database` | `project_file(..., "task_db")` |
| `QualityRepository.state_path` | `project_file(..., "quality_state")` |
| `project_storage_repo.check_project_integrity` | marker 检查改 `project_file(...)`；`_current_segment_ids` 同 |
| `lib/audio_pipeline.export_book / generate_subtitles / concat_for_preview` | `structured_script` 读 + `project_dir(project_dir,"segments")` |
| `services/export.execute_export_job` | 工作目录 → `99_系统数据/临时/export/<task_id>/`；发布 → `03_导出成品/正式导出/<任务可读目录>/`；`_relative_output` → `make_relative` |
| `services/quality.py` | `_absolute/_project_relative` → `resolve_relative/make_relative`；revision 归档 → `quality` key |
| `services/supplement.build_output_path` | → `03_导出成品/补录/`（删除 legacy `output/` junction 创建逻辑） |
| `services/voice_cast._relative_project_voice_path` | → `01_原始资料/项目音色/<filename>` |
| `app.py do_supplement_synth` | 补录工作 WAV → `02_生成音频/补录音频/<task_id>/`（或 `99_系统数据/临时/`），用户导出 → `03_导出成品/补录/` |
| `services/project.bind_voice` | 拷贝目标 → `project_voices`；JSON → config |
| `services/delivery.py` | `_project_relative/_absolute_project_path` → `make_relative/resolve_relative` |

**Quick TTS 无项目模式**：保持 global utility 数据（`<data_dir>/quick_tts/`、`<data_dir>/runtime/utility_tasks.sqlite3`），不进入任何项目布局。

---

## 7. 正式导出 v3 语义（保持既有机制）

```
99_系统数据/临时/export/<task_id>/      # 工作目录：.part.wav / 中间 wav / 字幕 .part
        │  export_book(..., atomic_publish=True, streaming_postprocess=True)
        ▼
03_导出成品/正式导出/20260817_221530_书名/
        ├── <项目名>.<ext>        # os.replace 原子发布
        └── <项目名>.<srt|lrc>    # 字幕原子发布
```

- 任务可读目录名：`YYYYmmdd_HHMMSS_<书名>`（`chapter_identity.safe_filename` 清洗）；`task_id` 继续保存在系统记录（export_jobs / delivery_manifests / production_tasks），**不作为主要用户目录名**。
- 每次任务独占一个目录；失败时只清理 `99_系统数据/临时/export/<task_id>/`，绝不触碰已发布成品。
- **保留**：export atomic publish（`audio_pipeline.py:177-192`）、ownership fencing（`export.py:736-750`）、delivery manifest（`export.py:873-909`）、hash（sha256 / delivery_input_hash）。
- `delivery_official` 目录下历史 `exports/<task_id>` 迁移为 `03_导出成品/正式导出/<task_id>`（保持原相对路径结构，resolver 兼容）。

---

## 8. 待明确事项（设计假设）

1. `chapter_audio` / `merged_audio` 目前**无写点**（只有统计与 layout 创建）：v3 保留目录，后续 Export UX/补录统一再接入；迁移时若 v2 对应目录为空则只建目录不搬文件。
2. `99_系统数据/日志/` 当前无项目级日志写入者；迁移只搬空目录 + 保留 layout 约定。
3. 补录工作 WAV（`cache/supplement_tasks/<task_id>/`）迁移目标：建议 `02_生成音频/补录音频/<task_id>/`（与“生成音频”语义一致），完成迁移后旧 `cache/supplement_tasks` 可清理；`SupplementService.cleanup_old_tasks`（`supplement.py:461` 扫 `preview/supplement_tasks`）与 app.py 项目内 cache 路径不一致问题顺带对齐。
4. `project.json["directories"]` 序列化在 v3 改为 `V3_DIRS` 的目录级映射；文件级路径不落 `directories`，由 resolver 常量保证。
5. v3 是否移除 `ensure_layout(compatibility=True)` 的 legacy junction 创建：设计为默认关闭（新项目不再建 `voices/segments/chapters/output` 兼容目录），旧项目迁移后删除这些空/junction 兼容目录。
