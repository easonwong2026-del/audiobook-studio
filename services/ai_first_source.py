"""Lossless source-range batching used by the V4 AI-first services."""
from __future__ import annotations

import re


def split_source_range(
    source_text: str, start: int, end: int, max_chars: int
) -> list[tuple[int, int]]:
    """Split only for transport; never classify or rewrite the source."""
    if start < 0 or end <= start or end > len(source_text):
        raise ValueError("invalid source range")
    limit = max(200, int(max_chars))
    if end - start <= limit:
        return [(start, end)]
    ranges: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > limit:
        window = source_text[cursor:cursor + limit]
        boundaries = [
            window.rfind(mark)
            for mark in ("\n\n", "。", "！", "？", "；", "\n", "”", "」", "』")
        ]
        boundary = max(boundaries, default=-1)
        cut = boundary + 1 if boundary >= limit // 3 else limit
        cut = max(1, min(cut, limit))
        ranges.append((cursor, cursor + cut))
        cursor += cut
    if cursor < end:
        ranges.append((cursor, end))
    return ranges


def normalized_source(value: str) -> str:
    """Normalize only whitespace for coverage comparison, never persistence."""
    return re.sub(r"\s+", "", str(value or ""))
