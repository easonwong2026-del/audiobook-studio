# Changelog

All notable changes to **Audiobook Studio** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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

- **399 passing, 19 skipped, 0 failing** (Python 3.13, no torch, no IndexTTS2 model, no GPU).
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
