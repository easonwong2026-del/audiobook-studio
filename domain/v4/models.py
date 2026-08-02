"""Strict, dependency-free models for the v4 project format."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

PROJECT_SCHEMA = "audiobook-project-v4"
SOURCE_SCHEMA = "audiobook-source-v1"
SCRIPT_SCHEMA = "audiobook-script-v4"
SPEAKERS_SCHEMA = "audiobook-speakers-v1"


class ValidationError(ValueError):
    """A persisted v4 document violates its declared contract."""


def _expect_schema(data: dict[str, Any], expected: str) -> None:
    if data.get("schema_version") != expected:
        raise ValidationError(
            f"schema mismatch: expected {expected!r}, "
            f"got {data.get('schema_version')!r}"
        )


def source_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_speaker_id(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValidationError("speaker name cannot be empty")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"speaker_{digest}"


@dataclass(frozen=True)
class SourceMetadata:
    original_filename: str
    source_format: str
    encoding: str
    normalization: str
    char_count: int
    sha256: str
    imported_at: str
    source_origin: str = "original-upload"
    source_fidelity: str = "normalized-source"
    schema_version: str = SOURCE_SCHEMA

    def validate(self, source_text: str | None = None) -> None:
        if self.schema_version != SOURCE_SCHEMA:
            raise ValidationError("invalid source metadata schema")
        if self.char_count < 1 or len(self.sha256) != 64:
            raise ValidationError("invalid source metadata size or hash")
        if source_text is not None:
            if self.char_count != len(source_text):
                raise ValidationError("source char_count mismatch")
            if self.sha256 != source_sha256(source_text):
                raise ValidationError("source sha256 mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "original_filename": self.original_filename,
            "original_format": self.source_format,
            "encoding": self.encoding,
            "normalization_version": self.normalization,
            "character_count": self.char_count,
            "sha256": self.sha256,
            "imported_at": self.imported_at,
            "source_origin": self.source_origin,
            "source_fidelity": self.source_fidelity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceMetadata:
        _expect_schema(data, SOURCE_SCHEMA)
        try:
            value = cls(
                original_filename=data["original_filename"],
                source_format=data["original_format"],
                encoding=data["encoding"],
                normalization=data["normalization_version"],
                char_count=data["character_count"],
                sha256=data["sha256"],
                imported_at=data["imported_at"],
                source_origin=data.get("source_origin", "original-upload"),
                source_fidelity=data.get(
                    "source_fidelity", "normalized-source"
                ),
                schema_version=data["schema_version"],
            )
        except (TypeError, KeyError) as exc:
            raise ValidationError(f"invalid source metadata: {exc}") from exc
        value.validate()
        return value


@dataclass(frozen=True)
class SemanticSegment:
    segment_id: str
    chapter_id: str
    start: int
    end: int
    kind: str
    speaker_id: str | None
    speaker_source: str
    status: str
    text_override: str | None = None
    dialogue_type: str = "dialogue"

    def __post_init__(self) -> None:
        # Older in-memory callers only supplied ``kind``.  Preserve that
        # constructor shape while serializing the explicit classification.
        if self.kind == "narration" and self.dialogue_type == "dialogue":
            object.__setattr__(self, "dialogue_type", "narration")

    def validate(self, source_length: int) -> None:
        if not self.segment_id or not self.chapter_id:
            raise ValidationError("segment identifiers cannot be empty")
        if not 0 <= self.start < self.end <= source_length:
            raise ValidationError(f"segment {self.segment_id} has invalid bounds")
        if self.kind not in {"narration", "dialogue"}:
            raise ValidationError(f"segment {self.segment_id} has invalid kind")
        if self.dialogue_type not in {
            "narration",
            "dialogue",
            "suspected_dialogue",
            "quotation",
        }:
            raise ValidationError(f"segment {self.segment_id} has invalid dialogue_type")
        if self.kind == "narration" and self.dialogue_type != "narration":
            raise ValidationError(
                f"narration segment {self.segment_id} needs dialogue_type=narration"
            )
        if self.kind == "dialogue" and self.dialogue_type == "narration":
            raise ValidationError(
                f"dialogue segment {self.segment_id} cannot be narration type"
            )
        if self.speaker_source not in {"rule", "router", "manual", "unresolved"}:
            raise ValidationError(f"segment {self.segment_id} has invalid speaker_source")
        if self.status not in {"confirmed", "unresolved"}:
            raise ValidationError(f"segment {self.segment_id} has invalid status")
        if self.status == "confirmed" and not self.speaker_id:
            raise ValidationError(f"confirmed segment {self.segment_id} needs speaker_id")
        if self.status == "unresolved" and self.speaker_source != "unresolved":
            raise ValidationError(
                f"unresolved segment {self.segment_id} needs unresolved source"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticSegment:
        try:
            return cls(
                segment_id=data["id"],
                chapter_id=data["chapter_id"],
                start=data["source_start"],
                end=data["source_end"],
                kind=data["kind"],
                speaker_id=data["speaker_id"],
                speaker_source=data["speaker_source"],
                status=data["status"],
                text_override=data.get("text_override"),
                dialogue_type=data.get(
                    "dialogue_type",
                    "narration" if data.get("kind") == "narration" else "dialogue",
                ),
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid segment: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.segment_id,
            "chapter_id": self.chapter_id,
            "source_start": self.start,
            "source_end": self.end,
            "kind": self.kind,
            "speaker_id": self.speaker_id,
            "speaker_source": self.speaker_source,
            "status": self.status,
            "text_override": self.text_override,
            "dialogue_type": self.dialogue_type,
        }


@dataclass(frozen=True)
class ChapterScript:
    chapter_id: str
    title: str
    start: int
    end: int
    segments: list[SemanticSegment] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChapterScript:
        try:
            raw_segments = data["segments"]
            return cls(
                chapter_id=data["chapter_id"],
                title=data["title"],
                start=data["source_start"],
                end=data["source_end"],
                segments=[SemanticSegment.from_dict(item) for item in raw_segments],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid chapter: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "title": self.title,
            "source_start": self.start,
            "source_end": self.end,
            "segments": [item.to_dict() for item in self.segments],
        }


@dataclass(frozen=True)
class ScriptDocument:
    source_sha256: str
    chapters: list[ChapterScript]
    revision: int = 1
    schema_version: str = SCRIPT_SCHEMA

    def validate(self, source_text: str) -> None:
        if self.schema_version != SCRIPT_SCHEMA:
            raise ValidationError("invalid script schema")
        if self.revision < 1:
            raise ValidationError("script revision must be positive")
        if self.source_sha256 != source_sha256(source_text):
            raise ValidationError("script source hash mismatch")
        previous_end = 0
        seen: set[str] = set()
        for chapter in self.chapters:
            if not 0 <= chapter.start < chapter.end <= len(source_text):
                raise ValidationError(f"chapter {chapter.chapter_id} has invalid bounds")
            if chapter.start < previous_end:
                raise ValidationError("chapters overlap or are out of order")
            if source_text[previous_end:chapter.start].strip():
                raise ValidationError("non-whitespace source gap before chapter")
            cursor = chapter.start
            for segment in chapter.segments:
                segment.validate(len(source_text))
                if segment.chapter_id != chapter.chapter_id:
                    raise ValidationError("segment chapter reference mismatch")
                if segment.segment_id in seen:
                    raise ValidationError("duplicate segment_id")
                seen.add(segment.segment_id)
                if segment.start < cursor:
                    raise ValidationError("segments overlap or are out of order")
                if segment.end > chapter.end:
                    raise ValidationError("segment extends beyond chapter")
                if source_text[cursor:segment.start].strip():
                    raise ValidationError("non-whitespace source gap between segments")
                cursor = segment.end
            if source_text[cursor:chapter.end].strip():
                raise ValidationError("non-whitespace source gap after segments")
            previous_end = chapter.end
        if source_text[previous_end:].strip():
            raise ValidationError("non-whitespace source tail is uncovered")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "revision": self.revision,
            "chapters": [item.to_dict() for item in self.chapters],
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], source_text: str | None = None
    ) -> ScriptDocument:
        _expect_schema(data, SCRIPT_SCHEMA)
        try:
            value = cls(
                source_sha256=data["source_sha256"],
                chapters=[ChapterScript.from_dict(item) for item in data["chapters"]],
                revision=data["revision"],
                schema_version=data["schema_version"],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid script: {exc}") from exc
        if source_text is not None:
            value.validate(source_text)
        return value


@dataclass(frozen=True)
class Speaker:
    speaker_id: str
    display_name: str
    status: str
    speaker_type: str = "character"
    aliases: list[str] = field(default_factory=list)
    locked: bool = False

    def validate(self) -> None:
        if not self.speaker_id or not self.display_name:
            raise ValidationError("speaker identifiers and names cannot be empty")
        if self.status not in {"confirmed", "unresolved"}:
            raise ValidationError("invalid speaker status")
        if self.speaker_type not in {"narrator", "character"}:
            raise ValidationError("invalid speaker type")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValidationError("speaker aliases must be unique")
        if self.display_name in self.aliases:
            raise ValidationError("speaker aliases cannot repeat the display name")
        if self.locked and self.status != "confirmed":
            raise ValidationError("locked speaker must be confirmed")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Speaker:
        try:
            value = cls(
                speaker_id=data["id"],
                display_name=data["name"],
                status=data["status"],
                speaker_type=data["type"],
                aliases=list(data["aliases"]),
                locked=data["locked"],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid speaker: {exc}") from exc
        value.validate()
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.speaker_id,
            "name": self.display_name,
            "type": self.speaker_type,
            "aliases": self.aliases,
            "status": self.status,
            "locked": self.locked,
        }


@dataclass(frozen=True)
class SpeakersDocument:
    speakers: list[Speaker]
    revision: int = 1
    schema_version: str = SPEAKERS_SCHEMA

    def validate(self) -> None:
        if self.schema_version != SPEAKERS_SCHEMA:
            raise ValidationError("invalid speakers schema")
        if self.revision < 1:
            raise ValidationError("speakers revision must be positive")
        identifiers: set[str] = set()
        names_and_aliases: set[str] = set()
        narrator = None
        for speaker in self.speakers:
            speaker.validate()
            if speaker.speaker_id in identifiers:
                raise ValidationError("duplicate speaker_id")
            identifiers.add(speaker.speaker_id)
            for name in [speaker.display_name, *speaker.aliases]:
                if name in names_and_aliases:
                    raise ValidationError("speaker names and aliases must be unique")
                names_and_aliases.add(name)
            if speaker.speaker_id == "narrator":
                narrator = speaker
        if (
            narrator is None
            or not narrator.locked
            or narrator.speaker_type != "narrator"
        ):
            raise ValidationError("locked narrator speaker is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "speakers": [item.to_dict() for item in self.speakers],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpeakersDocument:
        _expect_schema(data, SPEAKERS_SCHEMA)
        try:
            value = cls(
                speakers=[Speaker.from_dict(item) for item in data["speakers"]],
                revision=data["revision"],
                schema_version=data["schema_version"],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid speakers document: {exc}") from exc
        value.validate()
        return value


@dataclass(frozen=True)
class ProjectManifest:
    project_id: str
    name: str
    created_at: str
    title: str = ""
    author: str = ""
    updated_at: str = ""
    source_path: str = "source/source.txt"
    source_meta_path: str = "source/source.meta.json"
    script_path: str = "script/script.json"
    speakers_path: str = "script/speakers.json"
    runtime_db_path: str = "runtime/runtime.db"
    schema_version: str = PROJECT_SCHEMA

    def validate(self) -> None:
        if self.schema_version != PROJECT_SCHEMA:
            raise ValidationError("invalid project schema")
        if not self.project_id or not self.name:
            raise ValidationError("project id and name are required")
        if not self.updated_at:
            raise ValidationError("project updated_at is required")
        for path in (
            self.source_path,
            self.source_meta_path,
            self.script_path,
            self.speakers_path,
            self.runtime_db_path,
        ):
            if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
                raise ValidationError("project paths must be safe relative paths")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "name": self.name,
            "title": self.title,
            "author": self.author,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source_path,
            "source_meta": self.source_meta_path,
            "script": self.script_path,
            "speakers": self.speakers_path,
            "runtime_db": self.runtime_db_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectManifest:
        _expect_schema(data, PROJECT_SCHEMA)
        try:
            value = cls(
                project_id=data["project_id"],
                name=data["name"],
                title=data["title"],
                author=data["author"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                source_path=data["source"],
                source_meta_path=data["source_meta"],
                script_path=data["script"],
                speakers_path=data["speakers"],
                runtime_db_path=data["runtime_db"],
                schema_version=data["schema_version"],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid project manifest: {exc}") from exc
        value.validate()
        return value
