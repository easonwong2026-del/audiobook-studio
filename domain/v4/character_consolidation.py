"""Strict contracts for the book-wide character-consolidation stage.

Chapter extraction deliberately produces review material only.  This module
defines the second, book-scoped boundary where the model may say that several
chapter observations refer to one person.  The application service still
applies the result conservatively; a valid response is not, by itself,
permission to mutate the formal speaker table.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .character_extraction import CharacterEvidence
from .models import ValidationError

CONSOLIDATION_REQUEST_SCHEMA = "character-consolidation-request-v1"
CONSOLIDATION_SCHEMA = "character-consolidation-v1"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("consolidation confidence must be a number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValidationError("consolidation confidence must be between 0 and 1")
    return result


@dataclass(frozen=True)
class ConsolidationCandidate:
    """The compact candidate record sent to the book-wide model."""

    candidate_id: str
    name: str
    aliases: list[str]
    confidence: float
    evidence: list[CharacterEvidence]
    source: str
    status: str

    def validate(self) -> None:
        _string(self.candidate_id, "candidate_id")
        _string(self.name, "candidate name")
        _confidence(self.confidence)
        if self.source not in {"rule", "ai", "manual"}:
            raise ValidationError("invalid consolidation candidate source")
        if self.status not in {"candidate", "confirmed", "rejected"}:
            raise ValidationError("invalid consolidation candidate status")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValidationError("consolidation candidate aliases must be unique")
        if self.name in self.aliases:
            raise ValidationError("consolidation candidate aliases repeat the name")
        if not self.evidence:
            raise ValidationError("consolidation candidate evidence cannot be empty")
        for item in self.evidence:
            item.validate()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidationCandidate:
        data = _mapping(data, "consolidation candidate")
        expected = {
            "candidate_id", "name", "aliases", "confidence", "evidence",
            "source", "status",
        }
        if set(data) != expected:
            raise ValidationError("consolidation candidate contains unknown fields")
        aliases = data.get("aliases")
        evidence = data.get("evidence")
        if not isinstance(aliases, list) or not isinstance(evidence, list):
            raise ValidationError("consolidation candidate aliases and evidence must be lists")
        value = cls(
            candidate_id=_string(data.get("candidate_id"), "candidate_id"),
            name=_string(data.get("name"), "candidate name"),
            aliases=[_string(item, "candidate alias") for item in aliases],
            confidence=_confidence(data.get("confidence")),
            evidence=[CharacterEvidence.from_dict(item) for item in evidence],
            source=_string(data.get("source"), "candidate source"),
            status=_string(data.get("status"), "candidate status"),
        )
        value.validate()
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "aliases": list(self.aliases),
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
            "source": self.source,
            "status": self.status,
        }


@dataclass(frozen=True)
class CharacterConsolidationRequest:
    candidates: list[ConsolidationCandidate]
    existing_speakers: list[dict[str, Any]]
    source_sha256: str
    schema_version: str = CONSOLIDATION_REQUEST_SCHEMA

    def validate(self) -> None:
        if self.schema_version != CONSOLIDATION_REQUEST_SCHEMA:
            raise ValidationError("character consolidation request schema mismatch")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise ValidationError("invalid consolidation source_sha256")
        identifiers: set[str] = set()
        for item in self.candidates:
            item.validate()
            if item.candidate_id in identifiers:
                raise ValidationError("duplicate consolidation candidate_id")
            identifiers.add(item.candidate_id)
        if not isinstance(self.existing_speakers, list):
            raise ValidationError("existing_speakers must be a list")
        for item in self.existing_speakers:
            value = _mapping(item, "existing speaker")
            if set(value) != {"speaker_id", "name", "aliases", "locked"}:
                raise ValidationError("existing speaker contains unknown fields")
            _string(value.get("speaker_id"), "existing speaker_id")
            _string(value.get("name"), "existing speaker name")
            aliases = value.get("aliases")
            if not isinstance(aliases, list):
                raise ValidationError("existing speaker aliases must be a list")
            if any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
                raise ValidationError("existing speaker aliases must be strings")
            if not isinstance(value.get("locked"), bool):
                raise ValidationError("existing speaker locked must be a boolean")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterConsolidationRequest:
        data = _mapping(data, "character consolidation request")
        expected = {
            "schema_version", "source_sha256", "chapter_candidates", "existing_speakers"
        }
        if set(data) != expected:
            raise ValidationError("character consolidation request contains unknown fields")
        raw_candidates = data.get("chapter_candidates")
        raw_speakers = data.get("existing_speakers")
        if not isinstance(raw_candidates, list) or not isinstance(raw_speakers, list):
            raise ValidationError("chapter_candidates and existing_speakers must be lists")
        value = cls(
            candidates=[ConsolidationCandidate.from_dict(item) for item in raw_candidates],
            existing_speakers=[_mapping(item, "existing speaker") for item in raw_speakers],
            source_sha256=_string(data.get("source_sha256"), "source_sha256"),
            schema_version=_string(data.get("schema_version"), "schema_version"),
        )
        value.validate()
        return value

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "chapter_candidates": [item.to_dict() for item in self.candidates],
            "existing_speakers": [dict(item) for item in self.existing_speakers],
        }

    def fingerprint(self) -> str:
        # Review status is an application decision, not a new identity input.
        # Ignoring it lets a completed consolidation be replayed safely after
        # auto-confirm/reject bookkeeping; ``apply`` still honours those frozen
        # decisions and new candidate IDs still invalidate the fingerprint.
        candidate_payload = [
            {
                key: value
                for key, value in item.to_dict().items()
                if key != "status"
            }
            for item in self.candidates
        ]
        payload = json.dumps(
            {
                "schema_version": self.schema_version,
                "source_sha256": self.source_sha256,
                "chapter_candidates": candidate_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConsolidatedCharacter:
    canonical_name: str
    aliases: list[str]
    candidate_ids: list[str]
    confidence: float
    importance: str
    reason: str

    def validate(self, allowed_candidate_ids: set[str]) -> None:
        _string(self.canonical_name, "canonical_name")
        _confidence(self.confidence)
        if self.importance not in {"major", "minor"}:
            raise ValidationError("consolidated importance must be major or minor")
        _string(self.reason, "consolidated reason")
        if not self.candidate_ids:
            raise ValidationError("consolidated character needs candidate_ids")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValidationError("consolidated candidate_ids must be unique")
        unknown = set(self.candidate_ids) - allowed_candidate_ids
        if unknown:
            raise ValidationError(
                f"consolidation contains unknown candidate_id: {min(unknown)}"
            )
        if len(set(self.aliases)) != len(self.aliases):
            raise ValidationError("consolidated aliases must be unique")
        if self.canonical_name in self.aliases:
            raise ValidationError("consolidated aliases repeat canonical_name")
        if any(not isinstance(alias, str) or not alias.strip() for alias in self.aliases):
            raise ValidationError("consolidated aliases must be strings")

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], allowed_candidate_ids: set[str]
    ) -> ConsolidatedCharacter:
        data = _mapping(data, "consolidated character")
        expected = {
            "canonical_name", "aliases", "candidate_ids", "confidence",
            "importance", "reason",
        }
        if set(data) != expected:
            raise ValidationError("consolidated character contains unknown fields")
        aliases = data.get("aliases")
        candidate_ids = data.get("candidate_ids")
        if not isinstance(aliases, list) or not isinstance(candidate_ids, list):
            raise ValidationError("consolidated aliases and candidate_ids must be lists")
        value = cls(
            canonical_name=_string(data.get("canonical_name"), "canonical_name"),
            aliases=[_string(item, "consolidated alias") for item in aliases],
            candidate_ids=[_string(item, "candidate_id") for item in candidate_ids],
            confidence=_confidence(data.get("confidence")),
            importance=_string(data.get("importance"), "importance"),
            reason=_string(data.get("reason"), "reason"),
        )
        value.validate(allowed_candidate_ids)
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "candidate_ids": list(self.candidate_ids),
            "confidence": self.confidence,
            "importance": self.importance,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class UnresolvedCharacterGroup:
    candidate_ids: list[str]
    reason: str

    def validate(self, allowed_candidate_ids: set[str]) -> None:
        if not self.candidate_ids:
            raise ValidationError("unresolved group needs candidate_ids")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValidationError("unresolved group candidate_ids must be unique")
        unknown = set(self.candidate_ids) - allowed_candidate_ids
        if unknown:
            raise ValidationError(
                f"unresolved group contains unknown candidate_id: {min(unknown)}"
            )
        _string(self.reason, "unresolved group reason")

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], allowed_candidate_ids: set[str]
    ) -> UnresolvedCharacterGroup:
        data = _mapping(data, "unresolved character group")
        if set(data) != {"candidate_ids", "reason"}:
            raise ValidationError("unresolved group contains unknown fields")
        candidate_ids = data.get("candidate_ids")
        if not isinstance(candidate_ids, list):
            raise ValidationError("unresolved group candidate_ids must be a list")
        value = cls(
            candidate_ids=[_string(item, "candidate_id") for item in candidate_ids],
            reason=_string(data.get("reason"), "unresolved group reason"),
        )
        value.validate(allowed_candidate_ids)
        return value

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_ids": list(self.candidate_ids), "reason": self.reason}


@dataclass(frozen=True)
class CharacterConsolidationResponse:
    characters: list[ConsolidatedCharacter]
    unresolved_groups: list[UnresolvedCharacterGroup]
    schema_version: str = CONSOLIDATION_SCHEMA

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, allowed_candidate_ids: set[str]
    ) -> CharacterConsolidationResponse:
        data = _mapping(data, "character consolidation response")
        if data.get("schema_version") != CONSOLIDATION_SCHEMA:
            raise ValidationError("character consolidation schema mismatch")
        if set(data) != {"schema_version", "characters", "unresolved_groups"}:
            raise ValidationError("character consolidation response contains unknown fields")
        characters = data.get("characters")
        unresolved = data.get("unresolved_groups")
        if not isinstance(characters, list) or not isinstance(unresolved, list):
            raise ValidationError("consolidation characters and unresolved_groups must be lists")
        value = cls(
            characters=[
                ConsolidatedCharacter.from_dict(item, allowed_candidate_ids)
                for item in characters
            ],
            unresolved_groups=[
                UnresolvedCharacterGroup.from_dict(item, allowed_candidate_ids)
                for item in unresolved
            ],
        )
        value.validate(allowed_candidate_ids)
        return value

    def validate(self, allowed_candidate_ids: set[str]) -> None:
        if self.schema_version != CONSOLIDATION_SCHEMA:
            raise ValidationError("character consolidation schema mismatch")
        seen: set[str] = set()
        for item in self.characters:
            item.validate(allowed_candidate_ids)
            overlap = seen & set(item.candidate_ids)
            if overlap:
                raise ValidationError(
                    f"candidate_id appears in multiple consolidation groups: {min(overlap)}"
                )
            seen.update(item.candidate_ids)
        for item in self.unresolved_groups:
            item.validate(allowed_candidate_ids)
            overlap = seen & set(item.candidate_ids)
            if overlap:
                raise ValidationError(
                    f"candidate_id appears in multiple consolidation groups: {min(overlap)}"
                )
            seen.update(item.candidate_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "characters": [item.to_dict() for item in self.characters],
            "unresolved_groups": [item.to_dict() for item in self.unresolved_groups],
        }
