"""Pluggable text measurement; planning is not hard-coded to characters."""
from __future__ import annotations

import re
from typing import Protocol


class TextMeasurer(Protocol):
    metric: str

    def measure(self, text: str) -> int: ...


class CharacterMeasurer:
    metric = "characters"

    def measure(self, text: str) -> int:
        return len(text)


class ConservativeTokenMeasurer:
    """Dependency-free upper-bound token estimate for mixed Chinese/English text."""

    metric = "tokens"
    _TOKEN_RE = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9]+|[^\s]")

    def measure(self, text: str) -> int:
        return len(self._TOKEN_RE.findall(text))
