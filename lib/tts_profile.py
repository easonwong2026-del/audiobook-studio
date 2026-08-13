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
    if raw in {"2", "2.0", "v2", "v2.0", "indextts2", "indextts.2"}:
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


def _model_dir(version: str, data: Mapping[str, Any], explicit: Any = None) -> str:
    if explicit not in (None, ""):
        return os.path.abspath(os.path.expanduser(str(explicit)))
    if version == VERSION_V25:
        value = _first(
            os.environ.get(ENV_MODEL_DIR_V25),
            os.environ.get(ENV_MODEL_DIR_V25_ALT),
            os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR_25"),
            os.environ.get("AUDIOBOOK_STUDIO_INDEXTTS25_MODEL_DIR"),
            _nested(data, "model_dir_v25", "model_dir_2_5", "model_dir_25", "indextts25_model_dir"),
        )
        if value in (None, ""):
            value = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "index-tts", "checkpoints-v2.5")
    else:
        value = _first(
            os.environ.get(ENV_MODEL_DIR_V2),
            os.environ.get(ENV_MODEL_DIR),
            _nested(data, "model_dir_v2", "model_dir"),
        )
        if value in (None, ""):
            value = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "index-tts", "checkpoints")
    return os.path.abspath(os.path.expanduser(str(value)))


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


def resolve_profile(value: Mapping[str, Any] | str | None = None) -> dict[str, Any]:
    """Resolve global settings or an explicit task profile into JSON data.

    Explicit task values win over environment/config values.  A legacy
    ``model_dir`` without a version intentionally resolves to v2 so existing
    installations remain rollback-compatible.  A clean installation with no
    legacy setting defaults to the recommended v2.5.
    """
    data = _raw_config()
    explicit: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    if isinstance(value, str) and value:
        explicit = {"engine_version": value}

    raw_version = _first(
        explicit.get("engine_version"), explicit.get("version"),
        os.environ.get(ENV_ENGINE_VERSION), os.environ.get(ENV_VERSION),
        _nested(data, "engine_version", "tts_version", "version"),
    )
    raw_engine = _first(
        explicit.get("engine_backend"), explicit.get("backend"), explicit.get("engine"),
        os.environ.get(ENV_BACKEND), os.environ.get(ENV_ENGINE),
        _nested(data, "engine_backend", "backend", "engine"),
    )
    version = normalize_version(raw_version)
    if version is None:
        # UI aliases are explicit version selections even when no separate
        # engine_version key was persisted.
        alias = str(raw_engine or "").strip().lower().replace(" ", "")
        version = VERSION_V25 if alias in {"indextts25", "indextts2.5", "v2.5", "2.5"} else None
    if version is None:
        # Existing config.json/model-dir deployments are v2 by definition.
        has_legacy = _first(
            explicit.get("model_dir"), os.environ.get(ENV_MODEL_DIR), data.get("model_dir")
        ) not in (None, "")
        version = VERSION_V2 if has_legacy else VERSION_V25
    backend = normalize_backend(raw_engine)
    model_dir = _model_dir(version, data, explicit.get("model_dir"))
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
    }
    resolved["cache_identity"] = "|".join(
        str(resolved.get(key) or "")
        for key in ("engine_identity", "model_identity", "precision")
    )
    return resolved


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
    "engine_identity", "model_fingerprint", "normalize_backend", "normalize_version",
    "cache_identity", "profile_matches", "public_profile", "resolve_profile",
]
