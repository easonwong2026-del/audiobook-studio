"""Atomic persistence for declarative production inputs and derived plans."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from domain.v4.production import (
    PerformanceOverrides,
    PronunciationRules,
    SynthesisPlan,
    TtsProfile,
    VoiceBindings,
)
from repositories.v4_atomic import atomic_write_json

T = TypeVar("T")


class ProductionRepository:
    def __init__(self, project_path: str | Path):
        self.project = Path(project_path)
        self.production = self.project / "production"
        self.revisions = self.project / "revisions"

    def initialize(self, profile: TtsProfile) -> None:
        profile.validate()
        self.production.mkdir(parents=True, exist_ok=True)
        defaults = {
            "voices.json": VoiceBindings({}).to_dict(),
            "performance.json": PerformanceOverrides().to_dict(),
            "pronunciation.json": PronunciationRules().to_dict(),
            "tts_profile.json": profile.to_dict(),
        }
        for filename, value in defaults.items():
            path = self.production / filename
            if not path.exists():
                atomic_write_json(path, value)

    def load_inputs(
        self,
    ) -> tuple[VoiceBindings, PerformanceOverrides, PronunciationRules, TtsProfile]:
        return (
            VoiceBindings.from_dict(self._read("voices.json")),
            PerformanceOverrides.from_dict(self._read("performance.json")),
            PronunciationRules.from_dict(self._read("pronunciation.json")),
            TtsProfile.from_dict(self._read("tts_profile.json")),
        )

    def load_plan(self) -> SynthesisPlan | None:
        path = self.production / "synthesis_plan.json"
        return SynthesisPlan.from_dict(self._read(path.name)) if path.exists() else None

    def save_document(self, filename: str, value: dict[str, Any]) -> None:
        if filename not in {
            "voices.json",
            "performance.json",
            "pronunciation.json",
            "tts_profile.json",
        }:
            raise ValueError("unsupported production document")
        self._snapshot(filename)
        atomic_write_json(self.production / filename, value)

    def save_plan(self, plan: SynthesisPlan) -> None:
        if plan.revision < 1:
            raise ValueError("plan revision must be positive")
        self._snapshot("synthesis_plan.json")
        atomic_write_json(
            self.production / "synthesis_plan.json", plan.to_dict()
        )

    def _snapshot(self, filename: str) -> None:
        current = self.production / filename
        if not current.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        target = self.revisions / f"production-{stamp}" / filename
        atomic_write_json(target, self._read(filename))

    def _read(self, filename: str) -> dict[str, Any]:
        with (self.production / filename).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise TypeError(f"{filename} must contain a JSON object")
        return value
