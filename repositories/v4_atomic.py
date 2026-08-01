"""Durable atomic primitives used only by the v4 repository."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _short_tmp() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def _filesystem_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    resolved = path.resolve() if not path.is_absolute() else path
    raw = str(resolved)
    if raw.startswith("\\\\?\\"):
        return resolved
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC" + raw[1:])
    return Path("\\\\?\\" + raw)


def atomic_write_text(path: Path, text: str) -> None:
    parent = _filesystem_path(path.parent)
    parent.mkdir(parents=True, exist_ok=True)
    tmp_name = f".{_short_tmp()}.tmp"
    temporary = parent / tmp_name
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _filesystem_path(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
