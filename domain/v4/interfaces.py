"""Ports reserved for later routing and synthesis phases."""
from __future__ import annotations

from typing import Any, Protocol


class SpeakerRouter(Protocol):
    def route(self, script: dict[str, Any], speakers: dict[str, Any]) -> dict[str, Any]:
        """Return speaker-routing-v1 assignments without text or TTS settings."""


class TtsAdapter(Protocol):
    def synthesize(self, task: dict[str, Any], profile: dict[str, Any]) -> str:
        """Run one planned synthesis task and return a relative audio path."""
