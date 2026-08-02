"""Strict character-extraction and review-candidate contracts for v4."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .models import ValidationError, source_sha256

CHARACTER_EXTRACTION_SCHEMA = "character-extraction-v1"
CHARACTER_CANDIDATES_SCHEMA = "audiobook-character-candidates-v1"


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _require_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("confidence must be a number")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise ValidationError("confidence must be between 0 and 1")
    return confidence


def stable_candidate_id(name: str) -> str:
    normalized = _require_string(name, "candidate name")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"candidate_{digest}"


@dataclass(frozen=True)
class CharacterEvidence:
    chapter_id: str
    text: str

    def validate(self) -> None:
        _require_string(self.chapter_id, "evidence chapter_id")
        _require_string(self.text, "evidence text")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterEvidence:
        data = _require_mapping(data, "evidence")
        if set(data) != {"chapter_id", "text"}:
            raise ValidationError("evidence contains unknown fields")
        value = cls(
            chapter_id=_require_string(data.get("chapter_id"), "evidence chapter_id"),
            text=_require_string(data.get("text"), "evidence text"),
        )
        value.validate()
        return value

    def to_dict(self) -> dict[str, str]:
        return {"chapter_id": self.chapter_id, "text": self.text}


@dataclass(frozen=True)
class ExtractedCharacter:
    """One strictly validated item returned by the chapter AI call."""

    name: str
    aliases: list[str]
    is_character: bool
    confidence: float
    evidence: list[CharacterEvidence]

    def validate(self) -> None:
        _require_string(self.name, "character name")
        if not isinstance(self.is_character, bool):
            raise ValidationError("is_character must be a boolean")
        _require_confidence(self.confidence)
        if len(set(self.aliases)) != len(self.aliases):
            raise ValidationError("character aliases must be unique")
        if self.name in self.aliases:
            raise ValidationError("character aliases cannot repeat the name")
        if any(not isinstance(alias, str) or not alias.strip() for alias in self.aliases):
            raise ValidationError("character aliases must be non-empty strings")
        for evidence in self.evidence:
            evidence.validate()
        if self.is_character and not self.evidence:
            raise ValidationError("character evidence cannot be empty")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractedCharacter:
        data = _require_mapping(data, "character")
        expected = {"name", "aliases", "is_character", "confidence", "evidence"}
        if set(data) != expected:
            raise ValidationError("character contains unknown fields")
        aliases = data.get("aliases")
        evidence = data.get("evidence")
        if not isinstance(aliases, list):
            raise ValidationError("character aliases must be a list")
        if not isinstance(evidence, list):
            raise ValidationError("character evidence must be a list")
        if not isinstance(data.get("is_character"), bool):
            raise ValidationError("is_character must be a boolean")
        value = cls(
            name=_require_string(data.get("name"), "character name"),
            aliases=[_require_string(item, "character alias") for item in aliases],
            is_character=data["is_character"],
            confidence=_require_confidence(data.get("confidence")),
            evidence=[CharacterEvidence.from_dict(item) for item in evidence],
        )
        value.validate()
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "is_character": self.is_character,
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class CharacterExtractionResponse:
    characters: list[ExtractedCharacter]
    schema_version: str = CHARACTER_EXTRACTION_SCHEMA

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        allowed_chapter_id: str | None = None,
        chapter_text: str | None = None,
    ) -> CharacterExtractionResponse:
        data = _require_mapping(data, "character extraction response")
        if data.get("schema_version") != CHARACTER_EXTRACTION_SCHEMA:
            raise ValidationError("character extraction schema mismatch")
        if set(data) != {"schema_version", "characters"}:
            raise ValidationError("character extraction response contains unknown fields")
        raw = data.get("characters")
        if not isinstance(raw, list):
            raise ValidationError("characters must be a list")
        value = cls(
            characters=[ExtractedCharacter.from_dict(item) for item in raw],
            schema_version=data["schema_version"],
        )
        value.validate(
            allowed_chapter_id=allowed_chapter_id,
            chapter_text=chapter_text,
        )
        return value

    def validate(
        self,
        *,
        allowed_chapter_id: str | None = None,
        chapter_text: str | None = None,
    ) -> None:
        if self.schema_version != CHARACTER_EXTRACTION_SCHEMA:
            raise ValidationError("character extraction schema mismatch")
        for item in self.characters:
            item.validate()
            if not item.is_character:
                continue
            for evidence in item.evidence:
                if allowed_chapter_id is not None and evidence.chapter_id != allowed_chapter_id:
                    raise ValidationError("character evidence chapter_id is not allowed")
                if chapter_text is not None and evidence.text not in chapter_text:
                    raise ValidationError("character evidence is not in source chapter")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "characters": [item.to_dict() for item in self.characters],
        }


@dataclass(frozen=True)
class CharacterCandidate:
    """A review-only candidate; it is not a formal Speaker."""

    candidate_id: str
    display_name: str
    aliases: list[str]
    confidence: float
    evidence: list[CharacterEvidence]
    source: str
    status: str = "candidate"

    def validate(self) -> None:
        _require_string(self.candidate_id, "candidate_id")
        _require_string(self.display_name, "candidate display_name")
        _require_confidence(self.confidence)
        if self.source not in {"rule", "ai", "manual"}:
            raise ValidationError("invalid candidate source")
        if self.status not in {"candidate", "confirmed", "rejected"}:
            raise ValidationError("invalid candidate status")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValidationError("candidate aliases must be unique")
        if self.display_name in self.aliases:
            raise ValidationError("candidate aliases cannot repeat display_name")
        if not self.evidence:
            raise ValidationError("candidate evidence cannot be empty")
        for evidence in self.evidence:
            evidence.validate()

    @classmethod
    def from_extracted(
        cls,
        item: ExtractedCharacter,
        *,
        source: str = "ai",
    ) -> CharacterCandidate | None:
        if not item.is_character:
            return None
        value = cls(
            candidate_id=stable_candidate_id(item.name),
            display_name=item.name,
            aliases=list(item.aliases),
            confidence=item.confidence,
            evidence=list(item.evidence),
            source=source,
        )
        value.validate()
        return value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterCandidate:
        data = _require_mapping(data, "character candidate")
        expected = {
            "candidate_id",
            "display_name",
            "aliases",
            "confidence",
            "evidence",
            "source",
            "status",
        }
        if set(data) != expected:
            raise ValidationError("character candidate contains unknown fields")
        aliases = data.get("aliases")
        evidence = data.get("evidence")
        if not isinstance(aliases, list) or not isinstance(evidence, list):
            raise ValidationError("candidate aliases and evidence must be lists")
        value = cls(
            candidate_id=_require_string(data.get("candidate_id"), "candidate_id"),
            display_name=_require_string(data.get("display_name"), "candidate display_name"),
            aliases=[_require_string(item, "candidate alias") for item in aliases],
            confidence=_require_confidence(data.get("confidence")),
            evidence=[CharacterEvidence.from_dict(item) for item in evidence],
            source=_require_string(data.get("source"), "candidate source"),
            status=_require_string(data.get("status"), "candidate status"),
        )
        value.validate()
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
            "source": self.source,
            "status": self.status,
        }


@dataclass(frozen=True)
class CharacterCandidatesDocument:
    source_sha256: str
    candidates: list[CharacterCandidate] = field(default_factory=list)
    revision: int = 1
    schema_version: str = CHARACTER_CANDIDATES_SCHEMA

    @classmethod
    def empty(cls, source_text_or_sha: str) -> CharacterCandidatesDocument:
        value = source_text_or_sha
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            value = source_sha256(value)
        return cls(source_sha256=value)

    def validate(self) -> None:
        if self.schema_version != CHARACTER_CANDIDATES_SCHEMA:
            raise ValidationError("invalid character candidates schema")
        if (
            not isinstance(self.source_sha256, str)
            or len(self.source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.source_sha256)
        ):
            raise ValidationError("invalid character candidates source hash")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValidationError("character candidates revision must be positive")
        identifiers: set[str] = set()
        for candidate in self.candidates:
            candidate.validate()
            if candidate.candidate_id in identifiers:
                raise ValidationError("duplicate character candidate_id")
            identifiers.add(candidate.candidate_id)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterCandidatesDocument:
        data = _require_mapping(data, "character candidates document")
        expected = {"schema_version", "source_sha256", "revision", "candidates"}
        if set(data) != expected:
            raise ValidationError("character candidates document contains unknown fields")
        raw = data.get("candidates")
        if not isinstance(raw, list):
            raise ValidationError("character candidates must be a list")
        value = cls(
            source_sha256=_require_string(data.get("source_sha256"), "source_sha256"),
            candidates=[CharacterCandidate.from_dict(item) for item in raw],
            revision=data.get("revision"),
            schema_version=data["schema_version"],
        )
        value.validate()
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "revision": self.revision,
            "candidates": [item.to_dict() for item in self.candidates],
        }
