"""Strict contracts for the AI-first V4 reading and directing pipeline.

The contracts intentionally keep semantic decisions out of ``SourceSegmenter``:
the source service supplies text and coordinates, while these responses are the
only place where a model may classify an interval or identify a person.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ValidationError

CHARACTER_BIBLE_CHAPTER_SCHEMA = "character-bible-chapter-v1"
CHARACTER_BIBLE_FINAL_SCHEMA = "character-bible-final-v1"
SCRIPT_DIRECTOR_SCHEMA = "ai-script-director-v4"
SCRIPT_REVIEW_SCHEMA = "ai-script-review-v1"

CHARACTER_IMPORTANCE = {"major", "minor", "unknown"}
DIRECTOR_TYPES = {
    "narration",
    "dialogue",
    "inner_monologue",
    "quotation",
    "stage_direction",
}
REVIEW_ACTIONS = {"reassign", "unresolve", "reclassify"}


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise ValidationError(f"{label} must be a non-empty string")
    return result


def _confidence(value: Any, label: str = "confidence") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValidationError(f"{label} must be between 0 and 1")
    return result


def _list_of_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be a list")
    return [_string(item, label.removesuffix("s")) for item in value]


@dataclass(frozen=True)
class BibleEvidence:
    chapter_id: str
    text: str
    source_start: int | None = None
    source_end: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BibleEvidence:
        if not isinstance(data, dict):
            raise ValidationError("bible evidence must be an object")
        allowed = {"chapter_id", "text", "source_start", "source_end"}
        if set(data) - allowed:
            raise ValidationError("bible evidence contains unknown fields")
        start = data.get("source_start")
        end = data.get("source_end")
        if (start is None) != (end is None):
            raise ValidationError("bible evidence coordinates must be both present or absent")
        if start is not None and (
            isinstance(start, bool) or not isinstance(start, int) or start < 0
        ):
            raise ValidationError("bible evidence source_start is invalid")
        if end is not None and (
            isinstance(end, bool) or not isinstance(end, int) or end <= 0
        ):
            raise ValidationError("bible evidence source_end is invalid")
        if start is not None and end is not None and start >= end:
            raise ValidationError("bible evidence coordinates are invalid")
        raw_text = data.get("text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValidationError("evidence text must be a non-empty string")
        return cls(
            chapter_id=_string(data.get("chapter_id"), "evidence chapter_id"),
            # Evidence is copied from the immutable source. Preserve its
            # whitespace so coordinate validation can compare it exactly.
            text=raw_text,
            source_start=start,
            source_end=end,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "text": self.text,
            "source_start": self.source_start,
            "source_end": self.source_end,
        }


@dataclass(frozen=True)
class BibleRelationship:
    character_id: str
    relation: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BibleRelationship:
        if not isinstance(data, dict) or set(data) != {"character_id", "relation"}:
            raise ValidationError("bible relationship has an invalid shape")
        return cls(
            character_id=_string(data.get("character_id"), "relationship character_id"),
            relation=_string(data.get("relation"), "relationship relation"),
        )

    def to_dict(self) -> dict[str, str]:
        return {"character_id": self.character_id, "relation": self.relation}


@dataclass(frozen=True)
class BibleCharacter:
    character_id: str
    canonical_name: str
    aliases: list[str]
    description: str
    importance: str
    relationships: list[BibleRelationship]
    first_appearance_chapter: str
    evidence: list[BibleEvidence]
    confidence: float
    speaker_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BibleCharacter:
        if not isinstance(data, dict):
            raise ValidationError("bible character must be an object")
        required = {
            "character_id", "canonical_name", "aliases", "description",
            "importance", "relationships", "first_appearance_chapter",
            "evidence", "confidence",
        }
        missing = required - set(data)
        if missing:
            raise ValidationError(f"bible character missing field: {min(missing)}")
        allowed = required | {"speaker_id"}
        if set(data) - allowed:
            raise ValidationError("bible character contains unknown fields")
        aliases = _list_of_strings(data.get("aliases"), "aliases")
        if len(set(aliases)) != len(aliases):
            raise ValidationError("bible character aliases must be unique")
        name = _string(data.get("canonical_name"), "canonical_name")
        if name in aliases:
            raise ValidationError("bible character aliases repeat canonical_name")
        relationships = data.get("relationships")
        evidence = data.get("evidence")
        if not isinstance(relationships, list) or not isinstance(evidence, list):
            raise ValidationError("bible relationships and evidence must be lists")
        if not evidence:
            raise ValidationError("bible character needs source evidence")
        importance = _string(data.get("importance"), "importance")
        if importance not in CHARACTER_IMPORTANCE:
            raise ValidationError("bible character importance is invalid")
        return cls(
            character_id=_string(data.get("character_id"), "character_id"),
            canonical_name=name,
            aliases=aliases,
            description=_string(data.get("description"), "description", allow_empty=True),
            importance=importance,
            relationships=[BibleRelationship.from_dict(item) for item in relationships],
            first_appearance_chapter=_string(
                data.get("first_appearance_chapter"), "first_appearance_chapter"
            ),
            evidence=[BibleEvidence.from_dict(item) for item in evidence],
            confidence=_confidence(data.get("confidence")),
            speaker_id=_string(data.get("speaker_id", ""), "speaker_id", allow_empty=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "description": self.description,
            "importance": self.importance,
            "relationships": [item.to_dict() for item in self.relationships],
            "first_appearance_chapter": self.first_appearance_chapter,
            "evidence": [item.to_dict() for item in self.evidence],
            "confidence": self.confidence,
            "speaker_id": self.speaker_id,
        }


@dataclass(frozen=True)
class CharacterBibleDocument:
    source_sha256: str
    characters: list[BibleCharacter] = field(default_factory=list)
    uncertain_entities: list[dict[str, Any]] = field(default_factory=list)
    revision: int = 1
    schema_version: str = CHARACTER_BIBLE_FINAL_SCHEMA

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterBibleDocument:
        if not isinstance(data, dict):
            raise ValidationError("character bible must be an object")
        required = {"schema_version", "source_sha256", "characters", "uncertain_entities"}
        if not required.issubset(data):
            raise ValidationError("character bible is missing required fields")
        if set(data) - (required | {"revision", "chapter_id"}):
            raise ValidationError("character bible contains unknown fields")
        if data.get("schema_version") not in {
            CHARACTER_BIBLE_CHAPTER_SCHEMA, CHARACTER_BIBLE_FINAL_SCHEMA
        }:
            raise ValidationError("character bible schema mismatch")
        raw_characters = data.get("characters")
        uncertain = data.get("uncertain_entities")
        if not isinstance(raw_characters, list) or not isinstance(uncertain, list):
            raise ValidationError("character bible characters and uncertain_entities must be lists")
        if any(not isinstance(item, dict) for item in uncertain):
            raise ValidationError("uncertain_entities must contain objects")
        source = _string(data.get("source_sha256"), "bible source_sha256")
        if len(source) != 64:
            raise ValidationError("bible source_sha256 is invalid")
        value = cls(
            source_sha256=source,
            characters=[BibleCharacter.from_dict(item) for item in raw_characters],
            uncertain_entities=list(uncertain),
            revision=int(data.get("revision", 1)),
            schema_version=data["schema_version"],
        )
        value.validate()
        return value

    def validate(self) -> None:
        if self.schema_version not in {
            CHARACTER_BIBLE_CHAPTER_SCHEMA, CHARACTER_BIBLE_FINAL_SCHEMA
        }:
            raise ValidationError("character bible schema mismatch")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise ValidationError("bible source_sha256 is invalid")
        if self.revision < 1:
            raise ValidationError("bible revision must be positive")
        ids: set[str] = set()
        names: set[str] = set()
        for character in self.characters:
            if character.character_id in ids:
                raise ValidationError("duplicate bible character_id")
            ids.add(character.character_id)
            for name in [character.canonical_name, *character.aliases]:
                if name in names:
                    raise ValidationError("bible names and aliases must be unique")
                names.add(name)
            character.from_dict(character.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "revision": self.revision,
            "characters": [item.to_dict() for item in self.characters],
            "uncertain_entities": list(self.uncertain_entities),
        }


@dataclass(frozen=True)
class ScriptDirectorSegment:
    source_start: int
    source_end: int
    segment_type: str
    speaker_id: str | None
    text: str
    confidence: float
    emotion: str = "neutral"
    emotion_strength: float = 0.4
    delivery: dict[str, Any] = field(default_factory=dict)
    pause_before: int = 0
    pause_after: int = 600
    pauses: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScriptDirectorSegment:
        if not isinstance(data, dict):
            raise ValidationError("script director segment must be an object")
        required = {
            "source_start", "source_end", "segment_type", "speaker_id",
            "text", "confidence",
        }
        if not required.issubset(data):
            raise ValidationError("script director segment is missing required fields")
        allowed = required | {
            "emotion", "emotion_strength", "delivery", "pause_before", "pause_after", "pauses"
        }
        if set(data) - allowed:
            raise ValidationError("script director segment contains unknown fields")
        start = data.get("source_start")
        end = data.get("source_end")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)):
            raise ValidationError("script director segment coordinates must be integers")
        if start < 0 or end <= start:
            raise ValidationError("script director segment coordinates are invalid")
        segment_type = _string(data.get("segment_type"), "segment_type")
        if segment_type not in DIRECTOR_TYPES:
            raise ValidationError("script director segment_type is invalid")
        speaker_id = data.get("speaker_id")
        if speaker_id is not None:
            speaker_id = _string(speaker_id, "speaker_id")
        delivery = data.get("delivery", {})
        if not isinstance(delivery, dict):
            raise ValidationError("script director delivery must be an object")
        pauses = data.get("pauses", [])
        if not isinstance(pauses, list):
            raise ValidationError("script director pauses must be a list")
        if any(not isinstance(item, dict) for item in pauses):
            raise ValidationError("script director pauses must contain objects")
        pause_before = data.get("pause_before", 0)
        pause_after = data.get("pause_after", 600)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (pause_before, pause_after)
        ):
            raise ValidationError("script director pause values must be integers")
        raw_text = data.get("text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValidationError("segment text must be a non-empty string")
        return cls(
            source_start=start,
            source_end=end,
            segment_type=segment_type,
            speaker_id=speaker_id,
            # Do not strip this field: exact source equality is validated by
            # the director service and whitespace is part of the coordinate.
            text=raw_text,
            confidence=_confidence(data.get("confidence")),
            emotion=_string(data.get("emotion", "neutral"), "emotion"),
            emotion_strength=_confidence(data.get("emotion_strength", 0.4), "emotion_strength"),
            delivery=dict(delivery),
            pause_before=pause_before,
            pause_after=pause_after,
            pauses=list(pauses),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_start": self.source_start,
            "source_end": self.source_end,
            "segment_type": self.segment_type,
            "speaker_id": self.speaker_id,
            "text": self.text,
            "confidence": self.confidence,
            "emotion": self.emotion,
            "emotion_strength": self.emotion_strength,
            "delivery": dict(self.delivery),
            "pause_before": self.pause_before,
            "pause_after": self.pause_after,
            "pauses": list(self.pauses),
        }


@dataclass(frozen=True)
class ScriptDirectorBatch:
    chapter_id: str
    source_start: int
    source_end: int
    segments: list[ScriptDirectorSegment]
    schema_version: str = SCRIPT_DIRECTOR_SCHEMA

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScriptDirectorBatch:
        if not isinstance(data, dict):
            raise ValidationError("script director response must be an object")
        if data.get("schema_version") != SCRIPT_DIRECTOR_SCHEMA:
            raise ValidationError("script director schema mismatch")
        allowed = {
            "schema_version", "chapter_id", "source_start", "source_end", "segments"
        }
        if set(data) - allowed:
            raise ValidationError("script director response contains unknown fields")
        raw = data.get("segments")
        if not isinstance(raw, list) or not raw:
            raise ValidationError("script director segments must be non-empty")
        start = data.get("source_start")
        end = data.get("source_end")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)):
            raise ValidationError("script director batch coordinates must be integers")
        value = cls(
            chapter_id=_string(data.get("chapter_id"), "chapter_id"),
            source_start=start,
            source_end=end,
            segments=[ScriptDirectorSegment.from_dict(item) for item in raw],
        )
        if value.source_start < 0 or value.source_end <= value.source_start:
            raise ValidationError("script director batch coordinates are invalid")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "chapter_id": self.chapter_id,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "segments": [item.to_dict() for item in self.segments],
        }


@dataclass(frozen=True)
class ReviewPatch:
    segment_id: str
    action: str
    speaker_id: str | None
    segment_type: str | None
    confidence: float
    reason: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewPatch:
        if not isinstance(data, dict):
            raise ValidationError("review patch must be an object")
        required = {"segment_id", "action", "speaker_id", "segment_type", "confidence", "reason"}
        if set(data) != required:
            raise ValidationError("review patch contains unknown or missing fields")
        action = _string(data.get("action"), "review action")
        if action not in REVIEW_ACTIONS:
            raise ValidationError("review action is invalid")
        speaker_id = data.get("speaker_id")
        if speaker_id is not None:
            speaker_id = _string(speaker_id, "review speaker_id")
        segment_type = data.get("segment_type")
        if segment_type is not None:
            segment_type = _string(segment_type, "review segment_type")
            if segment_type not in DIRECTOR_TYPES:
                raise ValidationError("review segment_type is invalid")
        return cls(
            segment_id=_string(data.get("segment_id"), "review segment_id"),
            action=action,
            speaker_id=speaker_id,
            segment_type=segment_type,
            confidence=_confidence(data.get("confidence")),
            reason=_string(data.get("reason"), "review reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "action": self.action,
            "speaker_id": self.speaker_id,
            "segment_type": self.segment_type,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ScriptReviewResponse:
    patches: list[ReviewPatch]
    schema_version: str = SCRIPT_REVIEW_SCHEMA

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScriptReviewResponse:
        if not isinstance(data, dict) or data.get("schema_version") != SCRIPT_REVIEW_SCHEMA:
            raise ValidationError("script review schema mismatch")
        raw = data.get("patches")
        if not isinstance(raw, list):
            raise ValidationError("script review patches must be a list")
        value = cls(patches=[ReviewPatch.from_dict(item) for item in raw])
        ids = [item.segment_id for item in value.patches]
        if len(ids) != len(set(ids)):
            raise ValidationError("script review contains duplicate segment_id")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "patches": [item.to_dict() for item in self.patches],
        }
