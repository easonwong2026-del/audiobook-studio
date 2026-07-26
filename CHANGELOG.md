# Changelog

All notable changes to **Audiobook Studio** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [3.2.1] - Unreleased

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
