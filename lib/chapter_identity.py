"""Stable chapter numbering and display helpers.

The source JSON may use arbitrary chapter IDs (or IDs that restart at one in
different files).  Chapter order is nevertheless deterministic: it is the
order in the JSON ``chapters`` array.  This module keeps the original ``id``
for segment/cache compatibility and adds a sequential, zero-padded number for
human-facing labels and file names.
"""
from __future__ import annotations

import copy
import re
from typing import Any


CHAPTER_NUMBER_KEY = "chapter_number"
CHAPTER_CODE_KEY = "chapter_code"


def _chapters(raw: dict[str, Any]) -> tuple[str, list]:
    """Return the canonical chapter key and list without mutating ``raw``."""
    if not isinstance(raw, dict):
        return "chapters", []
    chapters = raw.get("chapters")
    if isinstance(chapters, list):
        return "chapters", chapters
    for alias in ("sections", "episodes", "scenes"):
        chapters = raw.get(alias)
        if isinstance(chapters, list):
            return alias, chapters
    return "chapters", []


def chapter_width(total: int) -> int:
    """Return the minimum width used by chapter file/list codes.

    Three digits keep ordinary books stable as ``001``…``100``; very large
    books grow naturally instead of truncating their order.
    """
    return max(3, len(str(max(1, int(total or 0)))))


def chapter_code(number: int, total: int | None = None) -> str:
    """Format a one-based chapter number as a zero-padded code."""
    width = chapter_width(total if total is not None else number)
    return str(max(1, int(number))).zfill(width)


def chapter_number(chapter: dict[str, Any] | None, index: int = 0) -> int:
    """Return the stored sequence number, falling back to array order."""
    if isinstance(chapter, dict):
        value = chapter.get(CHAPTER_NUMBER_KEY)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return index + 1


def chapter_code_for(chapter: dict[str, Any] | None, index: int = 0, total: int | None = None) -> str:
    """Return a stable display/file code for a chapter."""
    if isinstance(chapter, dict):
        value = chapter.get(CHAPTER_CODE_KEY)
        if isinstance(value, str) and value.isdigit() and value:
            return value
    return chapter_code(chapter_number(chapter, index), total)


def safe_filename(value: Any, fallback: str = "未命名") -> str:
    """Make a cross-platform filename component without losing Chinese text."""
    text = str(value or "").strip()
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def chapter_label(chapter: dict[str, Any] | None, index: int = 0, total: int | None = None) -> str:
    """Build the user-facing chapter label used by all chapter selectors."""
    chapter = chapter or {}
    number = chapter_number(chapter, index)
    code = chapter_code_for(chapter, index, total)
    title = str(chapter.get("title") or chapter.get("id") or "未命名章节").strip()
    return f"第 {number} 章 · {code} · {title}"


def chapter_file_stem(chapter: dict[str, Any] | None, index: int = 0, total: int | None = None) -> str:
    """Build a padded, readable chapter filename stem."""
    chapter = chapter or {}
    code = chapter_code_for(chapter, index, total)
    title = safe_filename(chapter.get("title") or chapter.get("id"), "未命名章节")
    return f"{code}_{title}"


def normalize_script_for_project(raw: dict[str, Any]) -> dict[str, Any]:
    """Copy a script and materialize sequential chapter metadata.

    Original chapter/segment IDs are intentionally untouched.  This makes the
    change safe for already generated segment audio while giving every project
    a stable sequence for display and storage.
    """
    normalized = copy.deepcopy(raw) if isinstance(raw, dict) else raw
    if not isinstance(normalized, dict):
        return normalized

    key, chapters = _chapters(normalized)
    if key != "chapters" and "chapters" not in normalized:
        normalized["chapters"] = chapters
    chapters = normalized.get("chapters")
    if not isinstance(chapters, list):
        return normalized

    total = len(chapters)
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            continue
        number = index + 1
        chapter[CHAPTER_NUMBER_KEY] = number
        chapter[CHAPTER_CODE_KEY] = chapter_code(number, total)
    return normalized


__all__ = [
    "CHAPTER_CODE_KEY",
    "CHAPTER_NUMBER_KEY",
    "chapter_code",
    "chapter_code_for",
    "chapter_file_stem",
    "chapter_label",
    "chapter_number",
    "chapter_width",
    "normalize_script_for_project",
    "safe_filename",
]
