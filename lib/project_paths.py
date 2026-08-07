"""Project-local storage layout with legacy path compatibility.

New projects use the explicit, user-facing directory names requested for the
3.3.3 workflow.  Existing projects and test fixtures that only have the old
English directories continue to resolve to those directories.
"""
from __future__ import annotations

import json
import os
from typing import Final


STORAGE_VERSION: Final[int] = 2

# Key -> new project-local directory.  Keep these names stable: they are part
# of the project format and are also shown to users when opening a project.
CANONICAL_DIRS: Final[dict[str, str]] = {
    "config": "01_项目配置",
    "source": "02_原始文件",
    "chapter_text": "03_章节文本",
    "voices": "04_角色与声音",
    "segments": "05_分段音频",
    "chapter_audio": "06_章节音频",
    "merged_audio": "07_合并音频",
    "quality": "08_质检记录",
    "exports": "09_导出文件",
    "cache": "cache",
    "logs": "logs",
}

# Existing code and pre-3.3 projects use these names.  They are only selected
# when the project has no v2 manifest; new writes always use CANONICAL_DIRS.
LEGACY_DIRS: Final[dict[str, str]] = {
    "voices": "voices",
    "segments": "segments",
    "chapter_text": "chapters",
    "exports": "output",
    "cache": "cache",
    "logs": "logs",
}


def _manifest(project_dir: str) -> dict:
    path = os.path.join(project_dir, "project.json")
    try:
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def is_v2_project(project_dir: str) -> bool:
    """Whether a project explicitly opts into the canonical layout."""
    return _manifest(project_dir).get("storage_version", 0) >= STORAGE_VERSION


def directory_map(project_dir: str, *, prefer_canonical: bool | None = None) -> dict[str, str]:
    """Resolve all logical directories for a project.

    ``prefer_canonical`` is useful during atomic project creation, before the
    root project manifest has been written.
    """
    if prefer_canonical is None:
        prefer_canonical = is_v2_project(project_dir)
    result: dict[str, str] = {}
    for key, canonical_name in CANONICAL_DIRS.items():
        canonical = os.path.join(project_dir, canonical_name)
        legacy_name = LEGACY_DIRS.get(key)
        legacy = os.path.join(project_dir, legacy_name) if legacy_name else None
        if prefer_canonical or os.path.isdir(canonical) or not legacy or not os.path.isdir(legacy):
            result[key] = canonical
        else:
            result[key] = legacy
    return result


def project_dir(project_dir: str, key: str, *, create: bool = False, prefer_canonical: bool | None = None) -> str:
    """Return one logical project directory, optionally creating it."""
    if key not in CANONICAL_DIRS:
        raise KeyError(f"未知项目目录类型: {key}")
    path = directory_map(project_dir, prefer_canonical=prefer_canonical)[key]
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def canonical_project_dirs(project_dir: str) -> dict[str, str]:
    """Return paths from the canonical map regardless of current manifest."""
    return {key: os.path.join(project_dir, name) for key, name in CANONICAL_DIRS.items()}


def layout_manifest(project_dir: str) -> dict[str, str]:
    """Return the serializable logical-to-relative directory mapping."""
    return dict(CANONICAL_DIRS)


def ensure_layout(project_dir: str, *, prefer_canonical: bool = True, compatibility: bool = True) -> dict[str, str]:
    """Create the canonical layout and optional old-name compatibility dirs.

    Windows machines may not permit directory symlinks.  The application never
    writes through the compatibility names for v2 projects, so a plain empty
    compatibility directory is a safe fallback when a link cannot be made.
    """
    os.makedirs(project_dir, exist_ok=True)
    paths = directory_map(project_dir, prefer_canonical=prefer_canonical)
    for path in paths.values():
        os.makedirs(path, exist_ok=True)

    if compatibility and prefer_canonical:
        for key, legacy_name in LEGACY_DIRS.items():
            canonical = os.path.join(project_dir, CANONICAL_DIRS[key])
            legacy = os.path.join(project_dir, legacy_name)
            if os.path.abspath(canonical) == os.path.abspath(legacy) or os.path.lexists(legacy):
                continue
            # A junction/symlink keeps old tools working without duplicating
            # audio.  Fall back to a directory for restricted Windows setups.
            try:
                # Use a relative target because the whole project is first
                # assembled under ``.tmp_*`` and then atomically renamed.
                os.symlink(os.path.basename(canonical), legacy, target_is_directory=True)
            except OSError:
                os.makedirs(legacy, exist_ok=True)
    return paths


__all__ = [
    "CANONICAL_DIRS",
    "LEGACY_DIRS",
    "STORAGE_VERSION",
    "canonical_project_dirs",
    "directory_map",
    "ensure_layout",
    "is_v2_project",
    "layout_manifest",
    "project_dir",
]
