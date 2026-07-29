"""Strict speaker-routing-v1 protocol models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ValidationError

ROUTING_SCHEMA = "speaker-routing-v1"


@dataclass(frozen=True)
class SpeakerAssignment:
    segment_id: str
    speaker: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpeakerAssignment:
        if set(data) != {"segment_id", "speaker"}:
            raise ValidationError("routing assignment contains unknown fields")
        segment_id = data.get("segment_id")
        speaker = data.get("speaker")
        if not isinstance(segment_id, str) or not segment_id:
            raise ValidationError("routing assignment needs segment_id")
        if speaker is not None and (not isinstance(speaker, str) or not speaker.strip()):
            raise ValidationError("routing speaker must be a name or null")
        return cls(segment_id=segment_id, speaker=speaker.strip() if speaker else None)

    def to_dict(self) -> dict[str, str | None]:
        return {"segment_id": self.segment_id, "speaker": self.speaker}


@dataclass(frozen=True)
class SpeakerRoutingResponse:
    assignments: list[SpeakerAssignment]
    schema_version: str = ROUTING_SCHEMA

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        allowed_segment_ids: set[str],
    ) -> SpeakerRoutingResponse:
        if set(data) != {"schema_version", "assignments"}:
            raise ValidationError("routing response contains unknown fields")
        if data.get("schema_version") != ROUTING_SCHEMA:
            raise ValidationError("speaker routing schema mismatch")
        raw = data.get("assignments")
        if not isinstance(raw, list):
            raise ValidationError("routing assignments must be a list")
        assignments = [SpeakerAssignment.from_dict(item) for item in raw]
        identifiers = [item.segment_id for item in assignments]
        if len(identifiers) != len(set(identifiers)):
            raise ValidationError("routing response contains duplicate segment_id")
        unknown = set(identifiers) - allowed_segment_ids
        if unknown:
            raise ValidationError(
                f"routing response contains unknown segment_id: {min(unknown)}"
            )
        return cls(assignments=assignments, schema_version=data["schema_version"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "assignments": [item.to_dict() for item in self.assignments],
        }
