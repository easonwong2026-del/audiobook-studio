"""Model-independent TTS engine selection and task snapshot helpers.

The structured script contract deliberately does not know about a concrete
TTS release.  This module is the small boundary where an application setting
is resolved into a runtime profile.  A profile contains the private model
directory used by the runtime and a path-free public identity used by task
snapshots, caches, and status responses.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .tts_model_layout import (
    VERSION_V2 as MODEL_VERSION_V2,
    VERSION_V25 as MODEL_VERSION_V25,
    config_value,
    model_config_candidates,
    read_model_config_values,
    resolve_model_config_path,
)


ENGINE_BACKEND = "indextts"
VERSION_V2 = "2"
VERSION_V25 = "2.5"
SUPPORTED_VERSIONS = (VERSION_V2, VERSION_V25)
DEFAULT_PRECISION = {VERSION_V2: "FP16", VERSION_V25: "BF16"}

ENV_BACKEND = "AUDIOBOOK_STUDIO_TTS_BACKEND"
ENV_ENGINE = "AUDIOBOOK_STUDIO_ENGINE"
ENV_VERSION = "AUDIOBOOK_STUDIO_TTS_VERSION"
ENV_ENGINE_VERSION = "AUDIOBOOK_STUDIO_ENGINE_VERSION"
ENV_MODEL_DIR_V2 = "AUDIOBOOK_STUDIO_MODEL_DIR_V2"
ENV_MODEL_DIR_V25 = "AUDIOBOOK_STUDIO_MODEL_DIR_V25"
ENV_MODEL_DIR_V25_ALT = "AUDIOBOOK_STUDIO_MODEL_DIR_2_5"
ENV_MODEL_DIR = "AUDIOBOOK_STUDIO_MODEL_DIR"


def normalize_version(value: Any, default: str | None = None) -> str | None:
    raw = str(value or "").strip().lower().replace("_", ".").replace("-", ".")
    if raw in {
        "2", "2.0", "v2", "v2.0", "indextts2", "indextts.2",
        "legacy", "indextts2legacy", "indextts.2.legacy",
    }:
        return VERSION_V2
    if raw in {"25", "2.5", "v25", "v2.5", "indextts25", "indextts2.5"}:
        return VERSION_V25
    return default


def normalize_backend(value: Any, default: str = ENGINE_BACKEND) -> str:
    raw = str(value or "").strip().lower().replace("_", "-").replace(" ", "")
    if raw in {"indextts", "index-tts", "indextts2", "indextts25", "index-tts-2.5"}:
        return ENGINE_BACKEND
    return default


def engine_identity(version: Any, backend: Any = ENGINE_BACKEND) -> str:
    normalized = normalize_version(version, VERSION_V2) or VERSION_V2
    return f"{normalize_backend(backend)}:{normalized}"


def cache_identity(profile: Mapping[str, Any] | None) -> str:
    """Identity used in audio cache keys, including model and precision."""
    resolved = resolve_profile(profile or {})
    return "|".join(
        str(resolved.get(key) or "")
        for key in ("engine_identity", "model_identity", "precision")
    )


def _raw_config() -> dict[str, Any]:
    # Lazy import avoids making config.py -> tts_profile -> config.py import
    # cycles during application bootstrap.
    try:
        from . import config

        value = config._read_config()
    except Exception:  # pragma: no cover - configuration is best effort
        return {}
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _nested(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if data.get(key) not in (None, ""):
            return data.get(key)
    for name in ("tts", "engine", "engines"):
        value = data.get(name)
        if isinstance(value, Mapping):
            for key in keys:
                if value.get(key) not in (None, ""):
                    return value.get(key)
    return None


def _auto_model_dir(version: str) -> str | None:
    root = Path(__file__).resolve().parents[1].parent / "index-tts"
    candidates = (
        (
            root / "checkpoints-v2.5",
            root / "checkpoints_v2.5",
            root / "checkpoints-2.5",
            root / "checkpoints" / "v2.5",
            root.parent / "index-tts-2.5" / "checkpoints",
        ) if version == VERSION_V25 else (root / "checkpoints",)
    )
    return next((str(path) for path in candidates if path.is_dir()), None)


def _default_model_dir(version: str) -> str:
    root = Path(__file__).resolve().parents[1].parent / "index-tts"
    return str(root / ("checkpoints-v2.5" if version == VERSION_V25 else "checkpoints"))


def _configured_model_dir(data: Mapping[str, Any], version: str) -> Any:
    if version == VERSION_V25:
        return _nested(
            data,
            "model_dir_v25", "model_dir_2_5", "model_dir_25",
            "indextts25_model_dir", "model_dir_v2.5", "v2.5", "v25",
        )
    return _nested(
        data,
        "model_dir_v2", "model_dir_2", "indextts2_model_dir",
        "legacy_model_dir", "model_dir_legacy", "model_dir",
    )


def _legacy_config_model_dir(data: Mapping[str, Any], environ: Mapping[str, Any]) -> Any:
    """Read the old model_dir resolver as the v2 compatibility source.

    ``config.get_model_dir()`` has a built-in path fallback.  That fallback is
    not an explicit legacy deployment by itself; otherwise every clean install
    would be classified as v2 before the v2.5 recommendation is considered.
    """
    value = _first(environ.get(ENV_MODEL_DIR), data.get("model_dir"))
    if value not in (None, ""):
        return value
    try:
        from . import config

        value = config.get_model_dir()
        default = _default_model_dir(VERSION_V2)
        normalized = os.path.abspath(os.path.expanduser(str(value or "")))
        if normalized and normalized != os.path.abspath(default):
            return value
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return None


def _env_model_dir(environ: Mapping[str, Any], version: str) -> Any:
    if version == VERSION_V25:
        return _first(
            environ.get(ENV_MODEL_DIR_V25),
            environ.get(ENV_MODEL_DIR_V25_ALT),
            environ.get("AUDIOBOOK_STUDIO_MODEL_DIR_25"),
            environ.get("AUDIOBOOK_STUDIO_INDEXTTS25_MODEL_DIR"),
        )
    return _first(
        environ.get(ENV_MODEL_DIR_V2),
        environ.get("AUDIOBOOK_STUDIO_MODEL_DIR_LEGACY"),
        environ.get(ENV_MODEL_DIR),
    )


def _resolve_model_dir(
    version: str,
    data: Mapping[str, Any],
    environ: Mapping[str, Any],
    auto_model_dirs: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    configured = _env_model_dir(environ, version)
    source = "environment" if configured not in (None, "") else "config"
    if configured in (None, ""):
        configured = _configured_model_dir(data, version)
    if configured not in (None, ""):
        return os.path.abspath(os.path.expanduser(str(configured))), source
    auto = (auto_model_dirs or {}).get(version)
    if auto in (None, ""):
        auto = _auto_model_dir(version)
    if auto not in (None, ""):
        return os.path.abspath(os.path.expanduser(str(auto))), "auto"
    return os.path.abspath(os.path.expanduser(_default_model_dir(version))), "default"


def engine_version_for(value: Any, default: str | None = None) -> str | None:
    """Map engine/version aliases to the canonical profile version."""
    return normalize_version(value, default)


def model_fingerprint(model_dir: Any, version: Any = None) -> str:
    """Return a stable, path-free local model identity.

    The fingerprint uses the engine version, selected config name/content,
    config-referenced checkpoint names, and shallow file sizes.  It does not
    include an absolute path or mtime and never reads large checkpoint
    contents, so copying one bundle to another directory keeps its identity.
    """
    path = Path(str(model_dir or "")).expanduser()
    normalized_version = normalize_version(version)
    if normalized_version is None:
        v25_config = resolve_model_config_path(MODEL_VERSION_V25, path)
        hint_values = read_model_config_values(v25_config)
        hinted_version = normalize_version(config_value(
            hint_values, "version", "model_version", "indextts_version"
        ))
        normalized_version = hinted_version or (
            MODEL_VERSION_V25 if (path / "config_v2_5.yaml").is_file() else MODEL_VERSION_V2
        )
    config_path = resolve_model_config_path(normalized_version, path)
    config_values = read_model_config_values(config_path)
    entries: list[dict[str, Any]] = []
    candidate_names = {
        candidate.name for candidate in model_config_candidates(normalized_version, path)
    }
    for key in (
        "gpt_checkpoint", "s2mel_checkpoint", "w2v_stat", "spk_matrix",
        "emo_matrix", "dataset.bpe_model", "bpe_model",
    ):
        value = config_value(config_values, key)
        if value:
            candidate_names.add(Path(value).name)
    candidate_names.update({
        "codec.pth",
        "feat1.pt", "feat2.pt", "gpt.pth", "s2mel.pth",
        "wav2vec2bert_stats.pt", "bpe.model",
        "multilingual_zh_ja_yue_char_del.tiktoken",
    })
    try:
        if path.is_dir():
            for name in sorted(candidate_names):
                child = path / name
                if child.is_file():
                    stat = child.stat()
                    entries.append({"name": name, "size": stat.st_size})
    except OSError:
        entries = []

    config_bytes = b""
    if config_path is not None:
        try:
            config_bytes = config_path.read_bytes()[:262144]
        except OSError:
            config_bytes = b""
    payload = {
        "engine_version": normalized_version,
        "config_name": config_path.name if config_path else next(
            (candidate.name for candidate in model_config_candidates(normalized_version, path)),
            "",
        ),
        "config_hash": hashlib.sha256(config_bytes).hexdigest()[:16],
        "entries": entries,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _precision(version: str, value: Any = None) -> str:
    raw = str(value or "").strip().upper()
    if raw in {"FP16", "BF16", "FP32"}:
        return raw
    return DEFAULT_PRECISION[version]


def resolve_profile(
    value: Mapping[str, Any] | str | None = None,
    *,
    config_data: Mapping[str, Any] | None = None,
    environ: Mapping[str, Any] | None = None,
    auto_model_dirs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve global settings or an explicit task profile into JSON data.

    Explicit task values win over environment/config values.  A legacy
    ``model_dir`` without a version intentionally resolves to v2 so existing
    installations remain rollback-compatible.  A clean installation with no
    legacy setting defaults to the recommended v2.5.
    """
    data = dict(config_data) if isinstance(config_data, Mapping) else _raw_config()
    env = environ if isinstance(environ, Mapping) else os.environ
    explicit: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    if isinstance(value, str) and value:
        explicit = {"engine_version": value}

    explicit_version = _first(
        explicit.get("engine_version"), explicit.get("version"),
    )
    explicit_engine = _first(
        explicit.get("engine_backend"), explicit.get("backend"), explicit.get("engine"),
    )
    env_version = _first(env.get(ENV_ENGINE_VERSION), env.get(ENV_VERSION))
    env_engine = _first(env.get(ENV_BACKEND), env.get(ENV_ENGINE))
    config_version = _nested(data, "engine_version", "tts_version", "version")
    config_engine = _nested(data, "engine_backend", "backend", "engine", "tts_engine", "active_tts_engine")

    version = normalize_version(explicit_version)
    version_source = "explicit" if version else ""
    if version is None and explicit.get("model_dir") not in (None, ""):
        version = VERSION_V2
        version_source = "explicit_legacy_model_dir"
    if version is None:
        version = normalize_version(explicit_engine)
        version_source = "explicit" if version else version_source
    if version is None:
        version = normalize_version(env_version)
        version_source = "environment" if version else version_source
    if version is None:
        version = normalize_version(env_engine)
        version_source = "environment" if version else version_source
    if version is None:
        version = normalize_version(config_version)
        version_source = "config" if version else version_source
    if version is None:
        version = normalize_version(config_engine)
        version_source = "config" if version else version_source

    v2_config = _first(_env_model_dir(env, VERSION_V2), _configured_model_dir(data, VERSION_V2))
    v25_config = _first(_env_model_dir(env, VERSION_V25), _configured_model_dir(data, VERSION_V25))
    legacy_config = _first(explicit.get("model_dir"), _legacy_config_model_dir(data, env))
    if version is None and v2_config not in (None, "") and v25_config in (None, ""):
        version, version_source = VERSION_V2, "version_specific_model"
    elif version is None and v25_config not in (None, ""):
        version, version_source = VERSION_V25, "version_specific_model"
    elif version is None and legacy_config not in (None, ""):
        version, version_source = VERSION_V2, "legacy_model_dir"
    if version is None:
        auto = auto_model_dirs or {
            VERSION_V2: _auto_model_dir(VERSION_V2),
            VERSION_V25: _auto_model_dir(VERSION_V25),
        }
        if auto.get(VERSION_V25):
            version, version_source = VERSION_V25, "auto"
        elif auto.get(VERSION_V2):
            version, version_source = VERSION_V2, "auto"
    if version is None:
        version, version_source = VERSION_V25, "default"

    backend = normalize_backend(_first(explicit_engine, env_engine, config_engine))
    model_dir = (
        os.path.abspath(os.path.expanduser(str(explicit.get("model_dir"))))
        if explicit.get("model_dir") not in (None, "")
        else (
            _resolve_model_dir(version, data, env, auto_model_dirs)[0]
            if version != VERSION_V2
            or _env_model_dir(env, VERSION_V2)
            or _configured_model_dir(data, VERSION_V2)
            else os.path.abspath(
                os.path.expanduser(
                    str(_legacy_config_model_dir(data, env) or _default_model_dir(version))
                )
            )
        )
    )
    precision = _precision(version, _first(explicit.get("precision"), explicit.get("tts_precision"), _nested(data, "tts_precision", "precision")))
    identity = str(explicit.get("engine_identity") or "").strip() or engine_identity(version, backend)
    # Never trust a caller-provided identity to select a different adapter.
    if ":" in identity:
        identity = engine_identity(version, identity.split(":", 1)[0])
    resolved = {
        "engine_backend": backend,
        "engine_version": version,
        "engine_identity": identity,
        "model_dir": model_dir,
        "model_identity": str(explicit.get("model_identity") or model_fingerprint(model_dir, version)),
        "precision": precision,
        "device": str(explicit.get("device") or "auto"),
        "selection_engine_source": "explicit" if explicit_engine else (
            "environment" if env_engine else "config" if config_engine else "default"
        ),
        "selection_version_source": version_source,
    }
    resolved["cache_identity"] = "|".join(
        str(resolved.get(key) or "")
        for key in ("engine_identity", "model_identity", "precision")
    )
    return resolved


def resolve_model_dir(
    version: Any,
    *,
    config_data: Mapping[str, Any] | None = None,
    environ: Mapping[str, Any] | None = None,
    auto_model_dirs: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    normalized = normalize_version(version)
    if normalized not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported engine version: {version}")
    data = dict(config_data) if isinstance(config_data, Mapping) else _raw_config()
    env = environ if isinstance(environ, Mapping) else os.environ
    if normalized == VERSION_V2 and not _env_model_dir(env, VERSION_V2) and not _configured_model_dir(data, VERSION_V2):
        legacy = _legacy_config_model_dir(data, env)
        if legacy not in (None, ""):
            return {
                "path": os.path.abspath(os.path.expanduser(str(legacy))),
                "source": "legacy_config_or_default",
            }
    path, source = _resolve_model_dir(normalized, data, env, auto_model_dirs)
    return {"path": path, "source": source}


def public_profile(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return task/status-safe identity data without absolute model paths."""
    resolved = resolve_profile(profile or {})
    return {
        key: resolved.get(key)
        for key in (
            "engine_backend", "engine_version", "engine_identity",
            "model_identity", "precision", "device", "cache_identity",
        )
    }


def profile_matches(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    if not left or not right:
        return False
    a = resolve_profile(left)
    b = resolve_profile(right)
    return (a["engine_identity"], a["model_identity"], a["precision"]) == (
        b["engine_identity"], b["model_identity"], b["precision"]
    )


__all__ = [
    "ENGINE_BACKEND", "VERSION_V2", "VERSION_V25", "SUPPORTED_VERSIONS",
    "engine_identity", "engine_version_for", "model_fingerprint", "normalize_backend", "normalize_version",
    "resolve_model_dir",
    "cache_identity", "profile_matches", "public_profile", "resolve_profile",
]
