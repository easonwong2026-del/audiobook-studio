"""运行环境检查：路径全部配置化，给出可操作诊断（无 GUI 依赖，可单测）。

路径优先级统一为：环境变量 > config.json > 自动探测 > UI 首次配置。
本模块集中实现「模型 / ffmpeg / Python / CUDA」四项检查，供启动器 / app 在
启动期给出明确提示，避免模型缺失时崩溃或 ffmpeg 缺失时静默改格式。
"""
from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as _cfg
from .tts_model_layout import (
    config_value,
    read_model_config_values,
    resolve_model_config_path,
)

logger = logging.getLogger(__name__)
PROGRAM_DIR = Path(__file__).resolve().parents[1]

# These values are deliberately kept in this dependency-free module.  The
# diagnostics/UI can understand the two model layouts without importing an
# IndexTTS adapter (or torch), while the existing runtime keeps using the
# legacy ``config.get_model_dir()`` path until an adapter explicitly opts in.
ENGINE_INDEXTTS = "indextts"
VERSION_V2 = "v2"
VERSION_V25 = "v2.5"
SUPPORTED_ENGINE_VERSIONS = (VERSION_V2, VERSION_V25)

ENV_ENGINE = "AUDIOBOOK_STUDIO_ENGINE"
ENV_ENGINE_VERSION = "AUDIOBOOK_STUDIO_ENGINE_VERSION"
ENV_VERSION = "AUDIOBOOK_STUDIO_VERSION"  # short alias used by older launchers
ENV_TTS_BACKEND = "AUDIOBOOK_STUDIO_TTS_BACKEND"
ENV_TTS_VERSION = "AUDIOBOOK_STUDIO_TTS_VERSION"
ENV_MODEL_DIR_V2 = "AUDIOBOOK_STUDIO_MODEL_DIR_V2"
ENV_MODEL_DIR_LEGACY = "AUDIOBOOK_STUDIO_MODEL_DIR_LEGACY"
ENV_MODEL_DIR_V25 = "AUDIOBOOK_STUDIO_MODEL_DIR_V25"
ENV_MODEL_DIR_V25_ALIAS = "AUDIOBOOK_STUDIO_MODEL_DIR_2_5"
ENV_MODEL_DIR_V25_LEGACY_ALIAS = "AUDIOBOOK_STUDIO_MODEL_DIR_25"
ENV_MODEL_DIR_V25_PACKAGE_ALIAS = "AUDIOBOOK_STUDIO_INDEXTTS25_MODEL_DIR"

# The v2 and v2.5 readiness checks are both derived from the selected native
# config plus the adapter's unconditionally-loaded fixed assets.  The official
# ``indextts.infer_v2`` adapter (2026-08 main) loads gpt/s2mel/w2v/spk/emo/BPE
# from the config and always loads campplus + semantic codec + w2v-bert +
# BigVGAN from ``hf_cache/`` via ``ensure_models_available``.  It does not use
# ``dvae.pth`` or ``campplus.onnx``, so those legacy 1.x filenames must not be
# required.  This tuple keeps the directory-missing placeholder label list in
# sync with those real requirements; the per-file existence check itself is
# config-driven inside ``model_checkpoint_state``.
_V2_CONFIG_REQUIRED_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gpt checkpoint (config)", ("gpt_checkpoint",)),
    ("s2mel checkpoint (config)", ("s2mel_checkpoint",)),
    ("wav2vec2bert stats (config)", ("w2v_stat",)),
    ("feat1/spk matrix (config)", ("spk_matrix",)),
    ("feat2/emo matrix (config)", ("emo_matrix",)),
    ("tokenizer/BPE resource (config)", ("dataset.bpe_model", "bpe_model")),
)
_V2_REQUIRED_ASSETS: tuple[tuple[str, str], ...] = (
    ("hf_cache/campplus_cn_common.bin", "file"),
    ("hf_cache/semantic_codec_model.safetensors", "file"),
    ("hf_cache/w2v-bert-2.0", "dir"),
    ("hf_cache/bigvgan", "dir"),
)

# Public for callers/tests that previously inspected this constant.  The
# per-file checks are generated from the config in ``model_checkpoint_state``;
# these entries provide the stable label list for a missing directory and for
# the required assets that the native adapter always loads.
MODEL_REQUIRED_FILE_GROUPS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    VERSION_V2: tuple(
        (label, aliases) for label, aliases in _V2_CONFIG_REQUIRED_KEYS
    ) + tuple((label, ()) for label, _kind in _V2_REQUIRED_ASSETS),
    VERSION_V25: (),
}

MODEL_OPTIONAL_FILES: tuple[str, ...] = ()


@dataclass(frozen=True)
class EngineSelection:
    """Resolved engine/version selection with its independent sources."""

    engine: str
    version: str
    engine_source: str
    version_source: str
    warnings: tuple[str, ...] = ()


def _read_environment_config() -> dict[str, Any]:
    """Read config.json through the existing config reader, without writes."""
    reader = getattr(_cfg, "_read_config", None)
    if not callable(reader):
        return {}
    try:
        value = reader()
    except Exception:  # noqa: BLE001 - diagnostics must degrade gracefully
        return {}
    return value if isinstance(value, dict) else {}


def _nested_value(data: Mapping[str, Any], *keys: str) -> Any:
    """Return the first non-empty value from flat or common nested config."""
    for key in keys:
        value = data.get(key)
        if value not in (None, "") and not isinstance(value, Mapping):
            return value

    for container_name in (
        "engine",
        "engines",
        "model_dirs",
        "models",
        "tts_model_dirs",
    ):
        container = data.get(container_name)
        if not isinstance(container, Mapping):
            continue
        for key in keys:
            value = container.get(key)
            if value not in (None, "") and not isinstance(value, Mapping):
                return value
        index_tts = next(
            (
                container.get(name)
                for name in ("indextts", "index-tts", "IndexTTS", "IndexTTS2")
                if isinstance(container.get(name), Mapping)
            ),
            None,
        )
        if isinstance(index_tts, Mapping):
            for key in keys:
                value = index_tts.get(key)
                if value not in (None, "") and not isinstance(value, Mapping):
                    return value
    return None


def normalize_engine(value: Any) -> str | None:
    """Normalize supported IndexTTS engine spellings, or return ``None``."""
    if value in (None, ""):
        return None
    raw = str(value).strip().lower().replace("_", "-").replace(" ", "")
    if raw in {
        "indextts",
        "index-tts",
        "legacy",
        "indextts2",
        "indextts2-legacy",
        "indextts2.5",
        "index-tts2",
        "index-tts-2",
        "index-tts25",
        "index-tts2.5",
        "indextts25",
        "index-tts-2.5",
    }:
        return ENGINE_INDEXTTS
    return None


def normalize_version(value: Any) -> str | None:
    """Normalize v2/v2.5 aliases without guessing arbitrary versions."""
    if value in (None, ""):
        return None
    raw = str(value).strip().lower().replace("_", ".").replace("-", ".")
    if raw in {"v2", "v2.0", "2", "2.0", "indextts2", "index.tts.2"}:
        return VERSION_V2
    if raw in {"25", "v2.5", "v25", "2.5", "indextts25", "index.tts.2.5"}:
        return VERSION_V25
    return None


def _engine_version_hint(value: Any) -> str | None:
    raw = str(value or "").strip().lower().replace("_", "-")
    return VERSION_V25 if "25" in raw or "2.5" in raw else None


def _auto_model_dir(version: str) -> Path | None:
    """Find an existing sibling checkpoint directory, never creating it."""
    sibling = PROGRAM_DIR.parent / "index-tts"
    if version == VERSION_V25:
        candidates = (
            sibling / "checkpoints-v2.5",
            sibling / "checkpoints_v2.5",
            sibling / "checkpoints-2.5",
            sibling / "checkpoints" / "v2.5",
            PROGRAM_DIR.parent / "index-tts-2.5" / "checkpoints",
        )
    else:
        candidates = (sibling / "checkpoints",)
    return next((candidate for candidate in candidates if candidate.is_dir()), None)


def _default_model_dir(version: str) -> Path:
    sibling = PROGRAM_DIR.parent / "index-tts"
    if version == VERSION_V25:
        return sibling / "checkpoints-v2.5"
    return sibling / "checkpoints"


def _as_absolute_path(value: Any) -> str:
    return os.path.abspath(os.path.expanduser(os.fspath(value)))


def resolve_engine_selection() -> EngineSelection:
    """Resolve engine/version using env, config, then local auto-detection.

    This function only reads environment/configuration and checks directory
    existence.  It does not import a model package or start a subprocess.
    """
    data = _read_environment_config()
    warnings: list[str] = []

    raw_engine = os.environ.get(ENV_ENGINE) or os.environ.get(ENV_TTS_BACKEND)
    engine_source = "environment" if raw_engine else "config"
    if not raw_engine:
        raw_engine = _nested_value(
            data,
            "engine",
            "engine_backend",
            "tts_engine",
            "engine_name",
            "backend",
        )
    engine = normalize_engine(raw_engine) or ENGINE_INDEXTTS
    if raw_engine and normalize_engine(raw_engine) is None:
        warnings.append("未识别的 engine，已回退到 IndexTTS。")
        engine_source = "fallback"
    if raw_engine is None:
        engine_source = "default"

    raw_version = (
        os.environ.get(ENV_ENGINE_VERSION)
        or os.environ.get(ENV_TTS_VERSION)
        or os.environ.get(ENV_VERSION)
    )
    version_source = "environment" if raw_version else "config"
    if not raw_version:
        raw_version = _nested_value(
            data,
            "engine_version",
            "tts_version",
            "version",
            "indextts_version",
        )
    version = normalize_version(raw_version)
    if raw_version and version is None:
        warnings.append("未识别的 engine version，已按本地模型目录自动选择。")
        version_source = "auto"
    if version is None and raw_engine:
        version = _engine_version_hint(raw_engine)
        if version:
            version_source = "engine_alias"
    if version is None:
        v25 = _auto_model_dir(VERSION_V25)
        v2 = _auto_model_dir(VERSION_V2)
        # A pre-dual-engine installation may only expose its old directory
        # through the legacy resolver (or a launcher monkeypatch); preserve
        # that deployment instead of making the diagnostic report a missing
        # v2.5 directory as an error.
        legacy_runtime_dir = ""
        try:
            legacy_runtime_dir = str(_cfg.get_model_dir() or "")
        except (OSError, RuntimeError, TypeError, ValueError):
            legacy_runtime_dir = ""
        if legacy_runtime_dir and os.path.isdir(legacy_runtime_dir) and not v25:
            version = VERSION_V2
            version_source = "legacy_model_dir"
        elif v2 and not v25:
            version = VERSION_V2
            version_source = "auto"
        elif v25 or v2:
            # Both installed (or only the recommended one installed): choose
            # the recommended release unless an explicit legacy model_dir
            # already made this installation a rollback deployment.
            version = VERSION_V25
            version_source = "auto"
        else:
            legacy_configured = (
                os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR")
                or _nested_value(data, "model_dir", "model_dir_v2", "legacy_model_dir")
            )
            version = VERSION_V2 if legacy_configured else VERSION_V25
            version_source = "legacy_config" if legacy_configured else "default"

    return EngineSelection(
        engine=engine,
        version=version,
        engine_source=engine_source,
        version_source=version_source,
        warnings=tuple(warnings),
    )


def _configured_model_dir(data: Mapping[str, Any], version: str) -> Any:
    if version == VERSION_V25:
        return _nested_value(
            data,
            "model_dir_v25",
            "model_dir_v2_5",
            "model_dir_25",
            "indextts25_model_dir",
            "indextts25",
            "v2.5",
            "v25",
        )
    return _nested_value(
        data,
        "model_dir_v2",
        "model_dir_2",
        "indextts2_model_dir",
        "legacy_model_dir",
        "model_dir_legacy",
        "legacy",
        "v2",
        "2",
    )


def resolve_model_directories() -> dict[str, dict[str, str]]:
    """Resolve both model dirs and report only their source, not model files.

    ``AUDIOBOOK_STUDIO_MODEL_DIR`` and config ``model_dir`` remain v2 aliases
    for backwards compatibility.  Version-specific values take precedence for
    their own version.  Missing directories are returned as paths for the
    caller to inspect; this function never creates, downloads, or repairs them.
    """
    data = _read_environment_config()
    result: dict[str, dict[str, str]] = {}
    for version, env_name in (
        (VERSION_V2, ENV_MODEL_DIR_V2),
        (VERSION_V25, ENV_MODEL_DIR_V25),
    ):
        configured = os.environ.get(env_name)
        if not configured and version == VERSION_V25:
            configured = (
                os.environ.get(ENV_MODEL_DIR_V25_ALIAS)
                or os.environ.get(ENV_MODEL_DIR_V25_LEGACY_ALIAS)
                or os.environ.get(ENV_MODEL_DIR_V25_PACKAGE_ALIAS)
            )
        source = "environment"
        if not configured and version == VERSION_V2:
            configured = (
                os.environ.get(ENV_MODEL_DIR_LEGACY)
                or os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR")
            )
            source = "legacy_environment" if configured else source
        if not configured:
            configured = _configured_model_dir(data, version)
            source = "config" if configured else source
        if not configured and version == VERSION_V2:
            # Preserve the old config resolver, including its default sibling
            # path and any legacy config.json model_dir value.
            configured = _cfg.get_model_dir()
            source = "legacy_config_or_default"
        if not configured:
            auto = _auto_model_dir(version)
            if auto:
                configured = str(auto)
                source = "auto"
        if not configured:
            configured = str(_default_model_dir(version))
            source = "default"
        result[version] = {"path": _as_absolute_path(configured), "source": source}
    return result


def model_checkpoint_state(version: str, model_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Best-effort local checkpoint inspection for one supported version.

    Both v2 and v2.5 derive checkpoint filenames from the selected native
    config and validate the fixed assets the adapter unconditionally loads.
    v2 additionally requires the local auxiliary bundle (campplus / semantic
    codec / w2v-bert / BigVGAN) that ``indextts.infer_v2`` loads from
    ``hf_cache/``.  No recursive scan, import, network access, or download is
    performed.  The diagnostic layer receives only a display-safe path label.
    """
    normalized = normalize_version(version)
    if normalized not in SUPPORTED_ENGINE_VERSIONS:
        raise ValueError(f"unsupported IndexTTS version: {version}")
    path = Path(model_dir)
    if not path.is_dir():
        if normalized == VERSION_V25:
            return {
                "exists": False,
                "directory": False,
                "config_path": None,
                "config_name": None,
                "missing_required": [
                    "config_v2_5.yaml",
                    "gpt checkpoint (config)",
                    "s2mel checkpoint (config)",
                    "codec.pth",
                    "feat1/spk matrix (config)",
                    "feat2/emo matrix (config)",
                    "wav2vec2bert stats (config)",
                    "tokenizer/BPE resource (config)",
                    "hf_cache/w2v-bert-2.0",
                    "hf_cache/campplus_cn_common.bin",
                    "hf_cache/bigvgan",
                ],
                "present_required": [],
                "optional_present": [],
            }
        return {
            "exists": False,
            "directory": False,
            "missing_required": ["config.yaml", *[name for name, _ in MODEL_REQUIRED_FILE_GROUPS[normalized]]],
            "present_required": [],
            "optional_present": [],
        }

    config_path = resolve_model_config_path(normalized, path)
    if normalized == VERSION_V25:
        values = read_model_config_values(config_path)
        required: list[tuple[str, tuple[Path, ...], str]] = []

        def configured_file(label: str, *keys: str) -> None:
            value = config_value(values, *keys)
            required.append((label, (path / value,) if value else (), "file"))

        def configured_dir(label: str, *keys: str) -> None:
            value = config_value(values, *keys)
            required.append((label, (path / value,) if value else (), "dir"))

        def any_file(label: str, *candidates: Path) -> None:
            required.append((label, tuple(candidates), "file"))

        configured_file("gpt checkpoint (config)", "gpt_checkpoint")
        configured_file("s2mel checkpoint (config)", "s2mel_checkpoint")
        configured_file("feat1/spk matrix (config)", "spk_matrix")
        configured_file("feat2/emo matrix (config)", "emo_matrix")
        configured_file("wav2vec2bert stats (config)", "w2v_stat")
        bpe_value = config_value(values, "dataset.bpe_model", "bpe_model")
        bpe_candidates = ((path / bpe_value,) if bpe_value else ()) + (
            path / "multilingual_zh_ja_yue_char_del.tiktoken",
        )
        any_file("tokenizer/BPE resource", *bpe_candidates)
        configured_dir("qwen emotion directory", "qwen_emo_path")
        required.extend([
            ("codec.pth", (path / "codec.pth",), "file"),
            ("hf_cache/w2v-bert-2.0", (path / "hf_cache" / "w2v-bert-2.0",), "dir"),
            ("hf_cache/campplus_cn_common.bin", (path / "hf_cache" / "campplus_cn_common.bin",), "file"),
            ("hf_cache/bigvgan", (path / "hf_cache" / "bigvgan",), "dir"),
        ])
        if config_path is None:
            required.insert(0, ("config_v2_5.yaml", (), "file"))

        def exists(candidates: tuple[Path, ...], kind: str) -> bool:
            if kind == "dir":
                return any(
                    candidate.is_dir() and any(candidate.iterdir())
                    for candidate in candidates
                )
            return any(candidate.is_file() for candidate in candidates)

        present_required = [label for label, candidates, kind in required if exists(candidates, kind)]
        missing_required = [label for label, candidates, kind in required if not exists(candidates, kind)]
    else:
        values = read_model_config_values(config_path)
        required: list[tuple[str, tuple[Path, ...], str]] = []

        def v2_configured_file(label: str, *keys: str) -> None:
            value = config_value(values, *keys)
            candidates = (path / value,) if value else ()
            # The native adapter only reads these from the config; a missing
            # config entry is reported the same way as a missing file.
            required.append((label, candidates, "file"))

        for label, keys in _V2_CONFIG_REQUIRED_KEYS:
            v2_configured_file(label, *keys)
        for label, kind in _V2_REQUIRED_ASSETS:
            if kind == "dir":
                required.append((label, (path / label,), "dir"))
            else:
                required.append((label, (path / label,), "file"))
        if config_path is None:
            required.insert(0, ("config.yaml", (), "file"))

        def v2_exists(candidates: tuple[Path, ...], kind: str) -> bool:
            if kind == "dir":
                return any(
                    candidate.is_dir() and any(candidate.iterdir())
                    for candidate in candidates
                )
            return any(candidate.is_file() for candidate in candidates)

        present_required = [label for label, candidates, kind in required if v2_exists(candidates, kind)]
        missing_required = [label for label, candidates, kind in required if not v2_exists(candidates, kind)]
    optional_present = [name for name in MODEL_OPTIONAL_FILES if (path / name).is_file()]
    return {
        "exists": True,
        "directory": True,
        "config_path": str(config_path) if config_path else None,
        "config_name": config_path.name if config_path else None,
        "missing_required": missing_required,
        "present_required": present_required,
        "optional_present": optional_present,
    }


def detect_model_version(model_dir: str | os.PathLike[str]) -> str | None:
    """Read a small local config hint, if present; never infer from weights."""
    path = Path(model_dir)
    config_path = resolve_model_config_path(VERSION_V25, path)
    if config_path is None:
        config_path = resolve_model_config_path(VERSION_V2, path)
    if config_path is None:
        return None
    try:
        text = config_path.read_text(encoding="utf-8", errors="ignore")[:65536]
    except OSError:
        return None

    import re

    match = re.search(
        r"(?im)^\s*(?:version|model_version|model-version|indextts_version)\s*:\s*[\"']?([^\"'\s#]+)",
        text,
    )
    return normalize_version(match.group(1)) if match else None


def get_model_dir_for_version(version: str) -> str:
    """Return the resolved v2/v2.5 directory for explicit diagnostic callers."""
    normalized = normalize_version(version)
    if normalized not in SUPPORTED_ENGINE_VERSIONS:
        raise ValueError(f"unsupported IndexTTS version: {version}")
    return resolve_model_directories()[normalized]["path"]


def get_selected_model_dir() -> str:
    """Return the selected version's model dir without loading the model."""
    selection = resolve_engine_selection()
    return get_model_dir_for_version(selection.version)


def display_path(path: str | os.PathLike[str] | None, kind: str = "path") -> str:
    """Return a useful path label without exposing parent directories."""
    if not path:
        return ""
    try:
        normalized = os.path.normpath(os.fspath(path))
        name = os.path.basename(normalized)
    except (TypeError, ValueError):
        return f"<{kind}>"
    return f"<{kind}>/{name}" if name else f"<{kind}>"


@dataclass(frozen=True)
class PythonResolution:
    """无副作用的 Python 解释器解析结果。"""

    executable: str | None
    source: str
    warnings: list[str]


def resolve_python_interpreter() -> PythonResolution:
    """按 launcher 顺序解析解释器，不打印、不退出、不启动进程。"""
    warnings: list[str] = []
    configured = os.environ.get("AUDIOBOOK_STUDIO_PYTHON")
    if configured:
        if os.path.isfile(configured):
            return PythonResolution(configured, "environment", warnings)
        warnings.append(f"AUDIOBOOK_STUDIO_PYTHON 指向的文件不存在：{configured}")

    venv = PROGRAM_DIR.parent / "index-tts" / ".venv"
    for candidate in (
        venv / "Scripts" / "python.exe",
        venv / "bin" / "python",
    ):
        if candidate.is_file():
            return PythonResolution(str(candidate), "sibling_venv", warnings)

    for command in ("python", "python3"):
        found = shutil.which(command)
        if found:
            return PythonResolution(found, "path", warnings)
    return PythonResolution(None, "missing", warnings)


@dataclass
class EnvironmentCheckResult:
    """运行环境检查结果（方案 §5.7）。

    Attributes:
        python_ok: Python 解释器可用。
        model_ok: 选中 IndexTTS 模型目录及其版本配置可用。
        ffmpeg_ok: ffmpeg 系统二进制可用（mp3 / m4b 导出需要）。
        cuda_ok: CUDA 可用（GPU 推理）。
        messages: 可读诊断信息列表（供 UI / 启动器展示）。
    """

    python_ok: bool
    model_ok: bool
    ffmpeg_ok: bool
    cuda_ok: bool
    messages: list[str]


def _check_python() -> bool:
    """Python 解释器总是可用（本进程即 Python），返回 True。"""
    return True


def _check_model() -> bool:
    """选中版本的模型目录和版本专用配置可用才算就绪。"""
    selection = resolve_engine_selection()
    model_dir = resolve_model_directories()[selection.version]["path"]
    return os.path.isdir(model_dir) and resolve_model_config_path(selection.version, model_dir) is not None


def _check_ffmpeg() -> bool:
    """ffmpeg 在 PATH 或环境变量指定路径可用。"""
    return shutil.which(_cfg.get_ffmpeg_path()) is not None


def _check_cuda() -> bool:
    """CUDA 可用性（无 torch 时视为 False，不报错）。"""
    try:
        import torch
    except Exception:  # noqa: BLE001 - missing torch is a normal diagnostic result
        return False
    try:
        return bool(getattr(torch.cuda, "is_available", lambda: False)())
    except Exception:  # noqa: BLE001 - broken torch CUDA probe is non-fatal
        return False


def check_environment() -> EnvironmentCheckResult:
    """运行完整环境检查，返回结构化结果（含可读提示）。"""
    python_ok = _check_python()
    model_ok = _check_model()
    ffmpeg_ok = _check_ffmpeg()
    cuda_ok = _check_cuda()

    messages: list[str] = []
    if not python_ok:
        messages.append("Python 解释器不可用（不应发生）。")
    if model_ok:
        messages.append(
            f"✅ IndexTTS 模型目录就绪：{display_path(get_selected_model_dir(), 'model-dir')}"
        )
    else:
        messages.append(
            "⚠ 模型目录未找到。请设置环境变量 AUDIOBOOK_STUDIO_MODEL_DIR、"
            "config.json 的 model_dir，或在 UI 中首次配置模型路径。"
        )
    if ffmpeg_ok:
        messages.append(
            f"✅ ffmpeg 就绪：{display_path(_cfg.get_ffmpeg_path(), 'ffmpeg')}"
        )
    else:
        messages.append(
            "⚠ 未检测到 ffmpeg（mp3/m4b 导出需要）。请安装并加入 PATH，"
            "或设置环境变量 AUDIOBOOK_STUDIO_FFMPEG。WAV 导出不受影响。"
        )
    if cuda_ok:
        messages.append("✅ CUDA 可用（GPU 推理）。")
    else:
        messages.append("⚠ 未检测到 CUDA（将不可用 GPU 推理；仅检查，不影响启动）。")
    return EnvironmentCheckResult(
        python_ok=python_ok,
        model_ok=model_ok,
        ffmpeg_ok=ffmpeg_ok,
        cuda_ok=cuda_ok,
        messages=messages,
    )
