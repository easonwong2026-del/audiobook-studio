# Changelog

All notable changes to **Audiobook Studio** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [4.1.0] - 2026-08-01

### Added（V4 工作流整合）

- 统一服务层：`V4ProjectService`（V3/V4 混合扫描、格式识别、打开、状态、迁移入口）、
  `V4VoiceService`（音色绑定 + 音频校验 + SHA 指纹 + 自动重生成计划触发局部失效）、
  `V4SynthesisService`（计划 / 后台统一队列 / 暂停 / 继续 / 取消 / 中断恢复，
  运行状态落 `runtime.db` `run_state` 表）、`V4QualityService`（试听 / 重新生成）。
- 角色名规整 `speaker_normalization`：过滤情绪 / 动作 / 语气 / 叙述性后缀与泛指称呼，
  AI 路由与规则切分结果落盘前统一规整；`audio_validation` 提供用户可读的音频校验。
- 项目管理页新增「复制并升级到 V4」迁移入口（原项目不变、含备份、幂等复用）。

### Changed

- 原五步流程全面接入 V4 底层能力：新建项目默认创建 V4（source-first + runtime.db）；
  项目管理 V3/V4 混合列表；角色与声音支持稳定角色 ID、AI 识别、人工指派、合并
  （旧角色保留为别名）、锁定、别名编辑；生产与质检使用 V4 计划、runtime.db、
  cache key、局部失效与真实暂停 / 继续 / 取消；交付使用 V4 章节拼接与
  WAV / MP3 / M4B / 字幕导出。
- 「✨ v4 工作流」从主导航隐藏（开发模式 `AUDIOBOOK_STUDIO_DEV_MODE=1` 可重新显示）；
  `v4_workspace_page.py` 与全部 handler 保留作为调试入口。
- 章节解析：纯题名页（书名 / 作者短行）并入第一章旁白，不再产生「前言」伪章节；
  说话动词正则收紧（排除「名叫 / 道谢」误判、后置动词需紧跟标点）。

### Fixed

- 未选择项目 / 章节时的 `WindowsPath` / `NoneType` 空态异常。
- 自动角色识别不再产生「她自言自语 / 顾川急 / 轻声说 / 笑着问」等噪音角色。

---

## [3.3.3] - 2026-07-29

### Added

- 上传 TXT、DOCX、EPUB 或 JSON 后，从原始文件名自动填写项目名称/作品名，并即时检查名称槽位。
- 设置页模型改为可编辑下拉框，支持调用当前 Provider `/models`、恢复唯一默认模型并显示模型来源。
- 数据设置增加异常与残留项目列表、打开目录和显式移动到 `.trash/projects` 的恢复入口。

### Changed

- 远程导演默认单批输入从 50,000 降至 12,000 字符；优先按章节、自然段和中文句末标点拆分。
- AI 输出改用 `audiobook-script-batch-v3.1`，同章多批按来源章节合并。

### Fixed

- DeepSeek `finish_reason=length`、OpenAI Responses incomplete 和末尾截断 JSON 现在会触发当前批次的有界拆分重试（最多 3 层、最小叶片最多 2 次）。
- 正式项目仅在所有批次完成协议、枚举、范围、顺序和原文覆盖检查后原子创建。
- 项目名称占用现在区分完整、不完整、损坏、临时和 Legacy 目录；空 Legacy 根不再误读当前目录。

### Security

- 模型刷新、连接检查和 AI 失败摘要不记录 API Key、完整书稿或完整 Provider 原始响应。
- 残留清理只在用户明确点击后归档；合法项目和 Legacy 项目不会经此接口移动。

## [3.3.2] - 2026-07-29

### Fixed

- 启动器统一使用已解析的 Python 执行 `requirements.txt`，自动补齐 `keyring`，并保持 Gradio `<6` 约束。
- 修复设置页被创建在主工作区外导致内容裁切的问题，补充中等窗口响应式布局。
- 角色与声音页删除导演试听、试听反馈及对应死接线；确认绑定改为正常底部操作区。

### Quality

- 增加启动器依赖安装、设置页结构和导演试听清理回归测试。

## [3.3.1] - 2026-07-28

### Added

- AI 设置页状态加载，以及 OpenAI / DeepSeek / Local Provider 配置。
- 通过系统 Keyring 安全存储 API Key，支持清除密钥并显示 Keyring / 环境变量来源状态。
- 从 TXT、DOCX、EPUB 原稿经 AI 剧本导演分析、人工校正后直接创建项目。
- 导演试听、反馈调整，以及 v3 导演停顿进入正式合成链；v2 固定停顿兼容逻辑保持不变。
- 不依赖 GPU 推理的结构化环境诊断、设置页诊断报告和 `scripts/acceptance_check.py`。
- 长篇剧本一致性检查：重复 ID、角色、语速、情绪、停顿、文本长度及疑似别名提示。

### Fixed

- 项目创建改为临时目录 + 原子替换，失败时清理临时产物。
- 项目扫描增加完整性检查，排除并清理残留 `.tmp_` 项目目录。
- AI 剧本导演工作流重构后，设置加载、密钥清除和异常信息均不再泄露密钥。

### Quality

- 覆盖环境诊断、验收脚本、剧本一致性、AI 设置、原子创建和文档一致性的自动测试。
- macOS / Python 3.12 / Gradio 5.50 验证结果：545 passed、25 skipped，`lib` /
  `services` / `repositories` 合计覆盖率 84%；CI 不运行真实 GPU 推理。
- Gradio 继续固定在 `>=5.50,<6`。

## [3.2.1] - 2026-07-27

### Fixed

- 运行时版本更新为 v3.2.1；`lib.__version__` 继续作为唯一来源。
- Logo 改为独立品牌组件和侧边栏专用高清资源，深色背景不再受裁切或 CSS filter 影响。
- 工作台聚焦当前项目状态、进度、待处理问题和下一步操作，移除重复产品介绍。
- 右侧页面标题改为任务名称；音色分类前移到音色列表之前。
- 增加角色声音“选择角色 → 选择声音 → 试听 → 确认绑定”引导，并让试听使用当前候选音频。
- 在“生产与质检”内增加合成中心、试听质检、角色补录的阶段导航和合成前状态检查。
- 增加 `structured_script.json` 示例下载、质量参数中文标签和动态默认导出目录提示。
- 统一角色描述格式与 Windows 数据路径展示，修复 WorkBuddy 验收中的重复 UX 问题。
- 调整浅色画布、深森林侧栏、按钮和表单标签的对比度，避免黑块和低对比文字。
- 收紧页面最大宽度、侧栏高度和音色绑定卡片布局，减少无意义留白。
- 提供 256×256 PNG 与包含 7 种尺寸的 ICO，方便快捷方式和安装包复用。

### Changed

- 音色库和音频处理模块改为按需加载，减少启动期目录扫描和 NumPy/SciPy 初始化。
- 移除未使用的 `pydub` 运行依赖，并合并重复的音色分类刷新逻辑。
- 将 Gradio 限定为项目原生兼容的 5.50–5.x 范围，避免安装 Gradio 6 导致 UI 参数不兼容。

## [3.2.0] - 2026-07-26

### Added

- 生产工作台 UI/UX：以工作台、项目、角色与声音、生产与质检、交付组织完整生产流程。
- 工作台项目状态、角色绑定进度、生产进度与待处理问题摘要。
- 角色与声音页面将绑定流程明确为选择角色、选择声音、试听、确认绑定四步。

### Changed

- 侧边栏品牌标记与启动器图标统一为 Audiobook Studio v3.2.0 视觉资产。
- 保持 `lib.__version__` 为运行时唯一版本来源，核心服务、推理逻辑和数据协议不变。

## [3.1.1] - 2026-07-25

### Fixed

- **launcher.py 编码修复**：UTF-8 编码声明、U+FFFD 替换字符清理、中文注释和输出提示恢复。
- **Python 解释器回退逻辑重构**：优先级 `AUDIOBOOK_STUDIO_PYTHON`（存在性校验）> 同级 `index-tts/.venv`（跨平台）> PATH；寻找失败时给出清晰错误说明。
- **`start.bat` 简化**：解释器探测集中到 `launcher.py`，`start.bat` 仅负责调用。
- **恢复路径安全验证**：`tests/qa_allowed_paths_test.py` 和 `qa_verify_export_safe_path.py` 恢复为有意义的测试，适配外置数据目录架构，消除硬编码绝对路径。
- **清理空壳文件**：删除 `REFACTOR_PROGRESS.md`（从未有实质内容）；恢复 `REVIEW.md` 的完整架构评审内容。
- **统一版本来源**：`lib/__init__.py` 为单一版本权威值 `"3.1.1"`；所有运行时标题从该值读取。
- **README 乱码修复**：修复目录树字符 "──"、中文"推荐""缺失"等 7 处 U+FFFD 替换字符。
- **app.py 描述更正**：从"导航"改为"Gradio 应用入口、事件接线及部分 UI callback 编排"。
- **全项目编码扫描**：清理 16 个文件中的 U+FFFD 替换字符（修复关键文件，其余记录于未解决问题）。

## [3.1.0] - 2026-07-25

### Added

- **Supplement synthesis (补录合成)**: re-synthesise individual role lines after the main pass without re-running the entire book.
- **UI module split**: `app.py` handlers decomposed into `ui/pages/` (overview, project, voice, synthesis, review, export, supplement).
- **Repository persistence layer** (`repositories/`): clean `Repository` pattern over JSON file storage with `ConfigRepository`, `ProjectRepository`, `BindingRepository`, `TaskRepository`.
- **ProjectSnapshot**: immutable snapshot of project state at a point in time, with caching for fast UI refreshes.
- **Atomic JSON writes** (`repositories/_atomic.py`): crash-safe config/project file writes via write-to-temp + rename.
- **Pause / resume synthesis** (O12 state machine): proper cancellation state and state-machine-constrained transitions.
- **Resumable synthesis (断点续跑)**: interrupted synthesis progress is persisted so a session can pick up where it left off.
- **OOM degradation parameters** (`test_tts_oom_numbeams.py`): reduced num_beams fallback path for low-VRAM scenarios.
- **Audio loudness normalisation** (LUFS-16 via `lib/postprocess.py` + `pyloudnorm`).
- **Export subtitles** (`.srt` / `.vtt` chapter markers).
- **Workflow integration tests**: `tests/workflows/` covering project lifecycle, synthesis lifecycle, export/mixed audio, and data-directory switching.

### Changed

- **`app.py` modularised**: verbose inline handler blocks moved into dedicated page modules under `ui/pages/`; main file reduced from ~1800 to ~1260 lines of wiring + orchestration only.
- **Data directory externalised**: all project data, synthesis output, voice library and exports now default to `~/AudiobookStudio/` instead of living inside the repository; configurable via `AUDIOBOOK_STUDIO_DATA_DIR`, `config.json`, or the in-app settings panel.
- **Python interpreter discovery** (`launcher.py`): environment variable `AUDIOBOOK_STUDIO_PYTHON` takes precedence, then fallback to `../index-tts/.venv` — zero hardcoded absolute paths.
- **Unified error handling**: domain-level `OperationResult` replaces ad-hoc `(success, message, data)` tuples throughout services and pages.
- **Logging system**: structured logging with automatic rotation (`lib/logging_setup.py`), replacing scattered `print()` calls.
- **Environment-variable based configuration**: `AUDIOBOOK_STUDIO_MODEL_DIR`, `AUDIOBOOK_STUDIO_DATA_DIR`, `AUDIOBOOK_STUDIO_FFMPEG`, `AUDIOBOOK_STUDIO_PYTHON`, `AUDIOBOOK_STUDIO_LEGACY_DIR` — all discoverable, no hardcoded host-specific paths.
- **`start.bat`**: rewritten with pure ASCII content and `%~dp0`-based paths; passes the repo's own encoding conformance test.
- **`config.json`**: now `.gitignore`-d (always had a machine-specific data-dir path); all paths derived at runtime from env / defaults.

### Fixed

- **Start.bat encoding**: repaired non-ASCII REM comments that caused `UnicodeDecodeError` on the repo's ASCII-conformance test.
- **`test_d5_docs`**: corrected `ARCHITECTURE.md` path lookup from `os.path.dirname(PROJECT_ROOT)` to `PROJECT_ROOT` (the repository root *is* the project root in a standard checkout).
- **`更新日志.txt`**: brought under version control so `test_wave3` / `test_d5_docs` pass on CI.
- **All personal absolute paths scrubbed**: no `C:\Users\rakliang`, `D:\AudiobookStudio`, or workspace-specific paths remain in tracked files.
- **JSON data-contract validation**: stricter checks prevent silently corrupt project/binding files.
- **Supplement cache isolation**: supplement synthesis no longer interferes with main synthesis cache.
- **Cancel state machine**: race conditions around rapid pause/cancel/resume handled correctly.
- **Active voice / style prompt threading**: multi-role script with `voice_prompt` / `style_prompt` now correctly applies the active character's parameters during synthesis.
- **FFmpeg absence warning**: `launcher.py` prints a prominent warning when `ffmpeg` is not on `PATH` before the app starts.

### Tests

- **测试数字更新**：399 passing, 20 skipped, 3 failed（3 个 TTS OOM 测试需要 IndexTTS2 模型环境，非 CI 可执行）。
- All 19 skips are intentional: 18 parametrised path-integrity skips (only `launcher.py` is checked; others are dimension-tested once), 1 ffmpeg-present skip for a \"missing ffmpeg should warn\" case.
- Added workflow integration tests: `test_data_dir_switch`, `test_export_mixed_audio`, `test_project_lifecycle`, `test_synthesis_lifecycle`.
- Added CI workflow (`.github/workflows/tests.yml`): Ubuntu + Python 3.10, system `ffmpeg` binary, no torch/CUDA/model download.

### Known limitations

- **GPU / IndexTTS2 required for real synthesis**: the TTS engine lazily imports `torch` and `indextts` at call time; CI uses mock/stub audio and never loads the model. Real synthesis requires a CUDA-capable GPU with sufficient VRAM (12 GB+ recommended).
- **Windows-only for end-user operation**: the Gradio app is cross-platform in principle, but `start.bat`, `os.startfile()` calls, and IndexTTS2 assumptions make Windows the only supported runtime today.
- **FFmpeg required for MP3/M4B export**: WAV export works without it; the launcher warns on missing `ffmpeg`.
- **No model bundling**: this repository does not ship IndexTTS2 weights, torch, or CUDA runtimes. Users must source them separately (see `docs/releases/v3.1.0.md` or README).
- **No multi-GPU / distributed synthesis**: single-GPU only.
- **Manual voice library setup**: voice bindings JSON and reference audio must be prepared externally; no auto-clone from raw audio is built in.

---

## [3.0.0] - 2026-06

(Initial V3 rewrite with Gradio 5.x, ProjectService, data-dir externalisation. Not formally published as a separate release — folded into the V3.1 codebase. See V3.1 for the refactored, test-covered version.)

---

*Older versions are not tracked in this changelog.*
