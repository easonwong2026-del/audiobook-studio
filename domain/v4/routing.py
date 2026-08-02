"""Strict, role-table-constrained speaker-routing-v2 protocol models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ValidationError

ROUTING_SCHEMA = "speaker-routing-v2"
LEGACY_ROUTING_SCHEMA = "speaker-routing-v1"


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("routing confidence must be a number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValidationError("routing confidence must be between 0 and 1")
    return result


@dataclass(frozen=True)
class SpeakerAssignment:
    segment_id: str
    speaker_id: str | None
    candidate_name: str | None = None
    confidence: float = 0.0
    legacy_speaker: str | None = None

    @property
    def speaker(self) -> str | None:
        """Compatibility view for callers that only inspected the old protocol."""
        return self.legacy_speaker or self.speaker_id

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        legacy: bool = False,
    ) -> SpeakerAssignment:
        if not isinstance(data, dict):
            raise ValidationError("routing assignment must be an object")
        if legacy:
            if set(data) != {"segment_id", "speaker"}:
                raise ValidationError("routing assignment contains unknown fields")
            segment_id = data.get("segment_id")
            speaker = data.get("speaker")
            if not isinstance(segment_id, str) or not segment_id:
                raise ValidationError("routing assignment needs segment_id")
            if speaker is not None and (
                not isinstance(speaker, str) or not speaker.strip()
            ):
                raise ValidationError("routing speaker must be a name or null")
            return cls(
                segment_id=segment_id,
                speaker_id=None,
                candidate_name=None,
                confidence=1.0 if speaker else 0.0,
                legacy_speaker=speaker.strip() if speaker else None,
            )
        expected = {"segment_id", "speaker_id", "candidate_name", "confidence"}
        if set(data) != expected:
            raise ValidationError("routing assignment contains unknown fields")
        segment_id = data.get("segment_id")
        speaker_id = data.get("speaker_id")
        candidate_name = data.get("candidate_name")
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValidationError("routing assignment needs segment_id")
        if speaker_id is not None and (
            not isinstance(speaker_id, str) or not speaker_id.strip()
        ):
            raise ValidationError("routing speaker_id must be a non-empty string or null")
        if candidate_name is not None and (
            not isinstance(candidate_name, str) or not candidate_name.strip()
        ):
            raise ValidationError("routing candidate_name must be a non-empty string or null")
        if speaker_id is not None and candidate_name is not None:
            raise ValidationError("routing assignment cannot contain speaker_id and candidate_name")
        return cls(
            segment_id=segment_id.strip(),
            speaker_id=speaker_id.strip() if speaker_id else None,
            candidate_name=candidate_name.strip() if candidate_name else None,
            confidence=_confidence(data.get("confidence")),
        )

    def to_dict(self) -> dict[str, Any]:
        if self.legacy_speaker is not None:
            return {"segment_id": self.segment_id, "speaker": self.legacy_speaker}
        return {
            "segment_id": self.segment_id,
            "speaker_id": self.speaker_id,
            "candidate_name": self.candidate_name,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class SpeakerRoutingResponse:
    assignments: list[SpeakerAssignment]
    schema_version: str = ROUTING_SCHEMA

    @property
    def is_legacy(self) -> bool:
        return self.schema_version == LEGACY_ROUTING_SCHEMA

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        allowed_segment_ids: set[str],
        allowed_speaker_ids: set[str] | None = None,
        allow_legacy: bool = False,
    ) -> SpeakerRoutingResponse:
        if not isinstance(data, dict):
            raise ValidationError("routing response must be an object")
        schema_version = data.get("schema_version")
        if schema_version == LEGACY_ROUTING_SCHEMA and not allow_legacy:
            raise ValidationError("legacy speaker routing protocol is not accepted")
        if schema_version not in {ROUTING_SCHEMA, LEGACY_ROUTING_SCHEMA}:
            raise ValidationError("speaker routing schema mismatch")
        if set(data) != {"schema_version", "assignments"}:
            raise ValidationError("routing response contains unknown fields")
        raw = data.get("assignments")
        if not isinstance(raw, list):
            raise ValidationError("routing assignments must be a list")
        legacy = schema_version == LEGACY_ROUTING_SCHEMA
        assignments = [SpeakerAssignment.from_dict(item, legacy=legacy) for item in raw]
        identifiers = [item.segment_id for item in assignments]
        if len(identifiers) != len(set(identifiers)):
            raise ValidationError("routing response contains duplicate segment_id")
        unknown = set(identifiers) - allowed_segment_ids
        if unknown:
            raise ValidationError(
                f"routing response contains unknown segment_id: {min(unknown)}"
            )
        if allowed_speaker_ids is not None and not legacy:
            unknown_speakers = {
                item.speaker_id
                for item in assignments
                if item.speaker_id is not None
            } - allowed_speaker_ids
            if unknown_speakers:
                raise ValidationError(
                    f"routing response contains unknown speaker_id: {min(unknown_speakers)}"
                )
        return cls(assignments=assignments, schema_version=schema_version)

    def validate_allowed_speakers(self, allowed_speaker_ids: set[str]) -> None:
        if self.is_legacy:
            return
        unknown = {
            item.speaker_id
            for item in self.assignments
            if item.speaker_id is not None
        } - allowed_speaker_ids
        if unknown:
            raise ValidationError(
                f"routing response contains unknown speaker_id: {min(unknown)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "assignments": [item.to_dict() for item in self.assignments],
        }
