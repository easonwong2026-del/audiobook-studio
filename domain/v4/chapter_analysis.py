"""Strict request/response contracts for the default fast chapter flow."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .models import ValidationError

CHAPTER_ANALYSIS_REQUEST_SCHEMA = "chapter-analysis-request-v1"
CHAPTER_ANALYSIS_RESPONSE_SCHEMA = "chapter-analysis-response-v1"

CHAPTER_SEGMENT_TYPES = frozenset(
    {"narration", "dialogue", "inner_monologue", "quotation", "stage_direction"}
)
CHAPTER_EMOTIONS = frozenset(
    {
        "neutral",
        "calm",
        "happy",
        "sad",
        "angry",
        "fearful",
        "surprised",
        "tense",
        "excited",
        "tender",
        "urgent",
        "cold",
        "confident",
        "hesitant",
    }
)


def _strict_keys(
    value: dict[str, Any], *, required: set[str], optional: set[str] | None = None
) -> None:
    if not isinstance(value, dict):
        raise ValidationError("chapter analysis JSON value must be an object")
    expected = required | (optional or set())
    missing = required - set(value)
    unknown = set(value) - expected
    if missing:
        raise ValidationError(f"chapter analysis missing fields: {sorted(missing)}")
    if unknown:
        raise ValidationError(
            f"chapter analysis contains unsupported fields: {sorted(unknown)}"
        )


def _confidence(value: Any, *, required: bool = True) -> float | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("chapter analysis confidence must be numeric")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValidationError("chapter analysis confidence must be between 0 and 1")
    return value


@dataclass(frozen=True)
class ChapterAnalysisRequest:
    chapter_id: str
    chapter_title: str
    known_characters: list[dict[str, Any]]
    chapter_text: str
    schema_version: str = CHAPTER_ANALYSIS_REQUEST_SCHEMA

    def validate(self) -> None:
        if self.schema_version != CHAPTER_ANALYSIS_REQUEST_SCHEMA:
            raise ValidationError("invalid chapter analysis request schema")
        if not self.chapter_id or not self.chapter_title:
            raise ValidationError("chapter analysis request needs chapter identity")
        if not isinstance(self.chapter_text, str) or not self.chapter_text.strip():
            raise ValidationError("chapter analysis request text cannot be empty")
        for character in self.known_characters:
            _strict_keys(
                character,
                required={"character_id", "name", "aliases", "voice_bound"},
            )
            if not character["character_id"] or not character["name"]:
                raise ValidationError("known character needs id and name")
            if not isinstance(character["aliases"], list):
                raise ValidationError("known character aliases must be a list")
            if not isinstance(character["voice_bound"], bool):
                raise ValidationError("known character voice_bound must be boolean")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "known_characters": self.known_characters,
            "chapter_text": self.chapter_text,
        }


@dataclass(frozen=True)
class ChapterCharacterUpdate:
    character_id: str | None
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    is_new: bool = False
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    uncertainty_reason: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ChapterCharacterUpdate:
        _strict_keys(
            value,
            required={"character_id", "canonical_name", "aliases", "is_new"},
            optional={"confidence", "evidence", "uncertainty_reason"},
        )
        if value["character_id"] is not None and not isinstance(value["character_id"], str):
            raise ValidationError("character_update character_id must be string or null")
        if not isinstance(value["canonical_name"], str) or not value["canonical_name"].strip():
            raise ValidationError("character_update canonical_name cannot be empty")
        if not isinstance(value["aliases"], list) or any(
            not isinstance(item, str) or not item.strip() for item in value["aliases"]
        ):
            raise ValidationError("character_update aliases must be non-empty strings")
        if not isinstance(value["is_new"], bool):
            raise ValidationError("character_update is_new must be boolean")
        confidence = _confidence(value.get("confidence", 0.0))
        evidence = value.get("evidence", [])
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence
        ):
            raise ValidationError("character_update evidence must be text strings")
        uncertainty_reason = value.get("uncertainty_reason")
        if uncertainty_reason is not None and (
            not isinstance(uncertainty_reason, str) or not uncertainty_reason.strip()
        ):
            raise ValidationError("character_update uncertainty_reason is invalid")
        return cls(
            character_id=value["character_id"],
            canonical_name=value["canonical_name"].strip(),
            aliases=list(dict.fromkeys(item.strip() for item in value["aliases"])),
            is_new=value["is_new"],
            confidence=float(confidence),
            evidence=list(dict.fromkeys(item.strip() for item in evidence)),
            uncertainty_reason=uncertainty_reason.strip()
            if uncertainty_reason
            else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "canonical_name": self.canonical_name,
            "aliases": self.aliases,
            "is_new": self.is_new,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "uncertainty_reason": self.uncertainty_reason,
        }


@dataclass(frozen=True)
class ChapterAnalysisSegment:
    index: int
    segment_type: str
    speaker_id: str | None
    text: str
    emotion: str = "neutral"
    confidence: float | None = None
    speaker_name: str | None = None
    speaker_evidence: list[str] = field(default_factory=list)
    uncertainty_reason: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ChapterAnalysisSegment:
        _strict_keys(
            value,
            required={"segment_type", "speaker_id", "text", "emotion"},
            optional={
                "index",
                "confidence",
                "speaker_name",
                "speaker_evidence",
                "uncertainty_reason",
            },
        )
        index = value.get("index", 0)
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValidationError("chapter segment index must be integer")
        if value["segment_type"] not in CHAPTER_SEGMENT_TYPES:
            raise ValidationError(f"unsupported chapter segment type: {value['segment_type']!r}")
        speaker_id = value["speaker_id"]
        if speaker_id is not None and not isinstance(speaker_id, str):
            raise ValidationError("chapter segment speaker_id must be string or null")
        if not isinstance(value["text"], str) or not value["text"].strip():
            raise ValidationError("chapter segment text cannot be empty")
        if value["emotion"] not in CHAPTER_EMOTIONS:
            raise ValidationError(f"unsupported chapter emotion: {value['emotion']!r}")
        speaker_name = value.get("speaker_name")
        if speaker_name is not None and (
            not isinstance(speaker_name, str) or not speaker_name.strip()
        ):
            raise ValidationError("chapter segment speaker_name must be non-empty")
        speaker_evidence = value.get("speaker_evidence", [])
        if not isinstance(speaker_evidence, list) or any(
            not isinstance(item, str) or not item.strip() for item in speaker_evidence
        ):
            raise ValidationError("chapter segment speaker_evidence must be text strings")
        uncertainty_reason = value.get("uncertainty_reason")
        if uncertainty_reason is not None and (
            not isinstance(uncertainty_reason, str) or not uncertainty_reason.strip()
        ):
            raise ValidationError("chapter segment uncertainty_reason is invalid")
        return cls(
            index=index,
            segment_type=value["segment_type"],
            speaker_id=speaker_id,
            text=value["text"],
            emotion=value["emotion"],
            confidence=_confidence(value.get("confidence"), required=False),
            speaker_name=speaker_name.strip() if speaker_name else None,
            speaker_evidence=list(dict.fromkeys(item.strip() for item in speaker_evidence)),
            uncertainty_reason=uncertainty_reason.strip()
            if uncertainty_reason
            else None,
        )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "index": self.index,
            "segment_type": self.segment_type,
            "speaker_id": self.speaker_id,
            "text": self.text,
            "emotion": self.emotion,
            "confidence": self.confidence,
        }
        if self.speaker_name:
            value["speaker_name"] = self.speaker_name
        if self.speaker_evidence:
            value["speaker_evidence"] = self.speaker_evidence
        if self.uncertainty_reason:
            value["uncertainty_reason"] = self.uncertainty_reason
        return value


@dataclass(frozen=True)
class ChapterAnalysisResponse:
    chapter_id: str
    character_updates: list[ChapterCharacterUpdate]
    segments: list[ChapterAnalysisSegment]
    schema_version: str = CHAPTER_ANALYSIS_RESPONSE_SCHEMA

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ChapterAnalysisResponse:
        _strict_keys(
            value,
            required={"schema_version", "chapter_id", "character_updates", "segments"},
        )
        if value["schema_version"] != CHAPTER_ANALYSIS_RESPONSE_SCHEMA:
            raise ValidationError(
                f"schema mismatch: expected {CHAPTER_ANALYSIS_RESPONSE_SCHEMA!r}"
            )
        if not isinstance(value["chapter_id"], str) or not value["chapter_id"]:
            raise ValidationError("chapter analysis response needs chapter_id")
        if not isinstance(value["character_updates"], list):
            raise ValidationError("character_updates must be a list")
        if not isinstance(value["segments"], list) or not value["segments"]:
            raise ValidationError("chapter analysis segments must be non-empty")
        return cls(
            chapter_id=value["chapter_id"],
            character_updates=[ChapterCharacterUpdate.from_dict(item) for item in value["character_updates"]],
            segments=[
                replace(
                    ChapterAnalysisSegment.from_dict(item),
                    index=item.get("index", index),
                )
                for index, item in enumerate(value["segments"])
            ],
            schema_version=value["schema_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "chapter_id": self.chapter_id,
            "character_updates": [item.to_dict() for item in self.character_updates],
            "segments": [item.to_dict() for item in self.segments],
        }
