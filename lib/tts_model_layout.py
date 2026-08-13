"""Pure-Python IndexTTS model config and asset layout helpers.

This module deliberately has no torch, IndexTTS, or network dependency.  The
runtime and environment diagnostics use the same version-specific config
resolution so a local bundle is never validated against a different filename
rule than the native adapter uses.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


VERSION_V2 = "2"
VERSION_V25 = "2.5"

_CONFIG_NAMES = {
    VERSION_V2: ("config.yaml", "config.yml"),
    VERSION_V25: (
        "config_v2_5.yaml",
        "config_v2_5.yml",
        "config.yaml",
        "config.yml",
    ),
}


def normalize_model_version(value: Any) -> str | None:
    raw = str(value or "").strip().lower().replace("_", ".").replace("-", ".")
    if raw in {"2", "2.0", "v2", "v2.0", "indextts2", "indextts.2"}:
        return VERSION_V2
    if raw in {"25", "2.5", "v25", "v2.5", "indextts25", "indextts2.5"}:
        return VERSION_V25
    return None


def model_config_candidates(version: Any, model_dir: str | Path) -> tuple[Path, ...]:
    """Return config candidates in precedence order for one engine version."""
    normalized = normalize_model_version(version)
    if normalized not in _CONFIG_NAMES:
        raise ValueError(f"unsupported IndexTTS version: {version}")
    root = Path(model_dir)
    return tuple(root / name for name in _CONFIG_NAMES[normalized])


def resolve_model_config_path(version: Any, model_dir: str | Path) -> Path | None:
    """Resolve the first existing native config for *version*.

    v2 keeps ``config.yaml`` as its primary name and accepts ``config.yml``.
    v2.5 first accepts the official versioned ``config_v2_5.yaml`` and then
    falls back to the compatible generic names.
    """
    return next(
        (candidate for candidate in model_config_candidates(version, model_dir) if candidate.is_file()),
        None,
    )


def _scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1].strip()
    # The model configs use simple scalar paths.  Ignore an unquoted comment
    # without attempting to implement a full YAML parser in diagnostics.
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def read_model_config_values(config_path: str | Path | None) -> dict[str, str]:
    """Read the small scalar subset needed for local asset validation.

    PyYAML is not an Audiobook Studio runtime dependency.  The official
    IndexTTS configs use ordinary indented scalar keys for these paths, so a
    bounded, dependency-free reader is sufficient and keeps diagnostics
    importable in GPU-free CI.
    """
    if not config_path:
        return {}
    try:
        lines = Path(config_path).read_text(encoding="utf-8", errors="ignore")[:262144].splitlines()
    except OSError:
        return {}

    values: dict[str, str] = {}
    stack: list[tuple[int, str]] = []
    key_pattern = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.-]+)\s*:\s*(?P<value>.*)$")
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = key_pattern.match(line)
        if not match:
            continue
        indent = len(match.group("indent").replace("\t", "    "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = match.group("key")
        raw_value = match.group("value").strip()
        if raw_value in {"", "|", ">"}:
            stack.append((indent, key))
            continue
        path = ".".join([item[1] for item in stack] + [key])
        values[path] = _scalar(raw_value)
        values[key] = values[path]
    return values


def config_value(values: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = str(values.get(key) or "").strip()
        if value:
            return value
    return None


__all__ = [
    "VERSION_V2",
    "VERSION_V25",
    "config_value",
    "model_config_candidates",
    "normalize_model_version",
    "read_model_config_values",
    "resolve_model_config_path",
]
