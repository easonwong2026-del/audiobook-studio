"""Phase 2 演绎导演 contract, separate from source-preserving structure analysis."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ValidationError

ACTING_REQUEST_SCHEMA = "chapter-acting-request-v1"
ACTING_RESPONSE_SCHEMA = "chapter-acting-response-v1"


def _keys(
    value: dict[str, Any], required: set[str], optional: set[str] | None = None
) -> None:
    if not isinstance(value, dict):
        raise ValidationError("acting value must be an object")
    missing = required - set(value)
    unknown = set(value) - required - (optional or set())
    if missing or unknown:
        raise ValidationError(
            f"acting fields invalid; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"acting {label} must be numeric")
    result = float(value)
    if not low <= result <= high:
        raise ValidationError(f"acting {label} is out of range")
    return result


@dataclass(frozen=True)
class ActingSegment:
    index: int
    emotion_strength: float = 0.4
    speed: float = 1.0
    pitch: float = 0.0
    intensity: float = 0.5
    breath: str = "none"
    pause_before: int = 0
    pause_after: int = 600
    performance_note: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ActingSegment:
        _keys(
            value,
            {"index", "emotion_strength", "speed", "pitch", "intensity", "breath", "pause_before", "pause_after"},
            {"performance_note"},
        )
        index = value["index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValidationError("acting segment index is invalid")
        for field_name in ("pause_before", "pause_after"):
            pause = value[field_name]
            if isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= 3000:
                raise ValidationError(f"acting {field_name} is invalid")
        breath = value["breath"]
        if breath not in {"none", "light", "audible"}:
            raise ValidationError("acting breath is invalid")
        note = value.get("performance_note", "")
        if not isinstance(note, str):
            raise ValidationError("acting performance_note is invalid")
        return cls(
            index=index,
            emotion_strength=_number(value["emotion_strength"], "emotion_strength", 0.0, 1.0),
            speed=_number(value["speed"], "speed", 0.5, 1.5),
            pitch=_number(value["pitch"], "pitch", -12.0, 12.0),
            intensity=_number(value["intensity"], "intensity", 0.0, 1.0),
            breath=breath,
            pause_before=value["pause_before"],
            pause_after=value["pause_after"],
            performance_note=note.strip()[:240],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "emotion_strength": self.emotion_strength,
            "speed": self.speed,
            "pitch": self.pitch,
            "intensity": self.intensity,
            "breath": self.breath,
            "pause_before": self.pause_before,
            "pause_after": self.pause_after,
            "performance_note": self.performance_note,
        }


@dataclass(frozen=True)
class ActingResponse:
    chapter_id: str
    segments: list[ActingSegment]
    schema_version: str = ACTING_RESPONSE_SCHEMA

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ActingResponse:
        _keys(value, {"schema_version", "chapter_id", "segments"})
        if value["schema_version"] != ACTING_RESPONSE_SCHEMA:
            raise ValidationError("acting response schema mismatch")
        if not isinstance(value["chapter_id"], str) or not value["chapter_id"]:
            raise ValidationError("acting response chapter_id is invalid")
        if not isinstance(value["segments"], list) or not value["segments"]:
            raise ValidationError("acting response segments cannot be empty")
        segments = [ActingSegment.from_dict(item) for item in value["segments"]]
        if [item.index for item in segments] != list(range(len(segments))):
            raise ValidationError("acting segment indices must be complete and ordered")
        return cls(value["chapter_id"], segments, value["schema_version"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "chapter_id": self.chapter_id,
            "segments": [item.to_dict() for item in self.segments],
        }


__all__ = ["ACTING_REQUEST_SCHEMA", "ACTING_RESPONSE_SCHEMA", "ActingResponse", "ActingSegment"]
