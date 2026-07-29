from __future__ import annotations

from dataclasses import replace

import pytest

from domain.v4 import (
    ProjectManifest,
    ScriptDocument,
    SourceMetadata,
    SpeakersDocument,
    ValidationError,
)
from services.source_segmenter import SourceSegmenter


def test_documents_round_trip():
    text = "他说：“你好。”"
    result = SourceSegmenter().segment(text)
    script = ScriptDocument.from_dict(result.script.to_dict(), text)
    speakers = SpeakersDocument.from_dict(result.speakers.to_dict())
    assert script == result.script
    assert speakers == result.speakers


def test_schema_mismatch_rejected():
    with pytest.raises(ValidationError, match="schema mismatch"):
        ProjectManifest.from_dict({"schema_version": "old"})


def test_source_hash_mismatch_rejected():
    metadata = SourceMetadata(
        original_filename="a.txt",
        source_format="txt",
        encoding="utf-8",
        normalization="audiobook-normalization-v1",
        char_count=1,
        sha256="0" * 64,
        imported_at="now",
    )
    with pytest.raises(ValidationError, match="sha256"):
        metadata.validate("字")


def test_source_metadata_uses_prompt_field_names_and_round_trips():
    metadata = SourceMetadata(
        original_filename="a.txt",
        source_format="txt",
        encoding="utf-8",
        normalization="audiobook-normalization-v1",
        char_count=1,
        sha256="0" * 64,
        imported_at="now",
    )
    persisted = metadata.to_dict()
    assert set(persisted) == {
        "schema_version",
        "original_filename",
        "original_format",
        "encoding",
        "normalization_version",
        "character_count",
        "sha256",
        "imported_at",
        "source_origin",
        "source_fidelity",
    }
    assert SourceMetadata.from_dict(persisted) == metadata


def test_overlap_and_bounds_rejected():
    text = "旁白“对话”"
    result = SourceSegmenter().segment(text)
    chapter = result.script.chapters[0]
    overlapping = replace(chapter.segments[1], start=chapter.segments[0].start)
    broken = replace(
        result.script,
        chapters=[replace(chapter, segments=[chapter.segments[0], overlapping])],
    )
    with pytest.raises(ValidationError, match="overlap"):
        broken.validate(text)


def test_unresolved_is_valid_but_locked_unresolved_speaker_is_not():
    text = "“未知角色。”"
    result = SourceSegmenter().segment(text)
    result.script.validate(text)
    bad = result.speakers.speakers[0]
    with pytest.raises(ValidationError):
        replace(bad, status="unresolved").validate()


def test_segment_out_of_bounds_is_rejected():
    text = "旁白"
    result = SourceSegmenter().segment(text)
    chapter = result.script.chapters[0]
    broken_segment = replace(chapter.segments[0], end=len(text) + 1)
    broken = replace(
        result.script,
        chapters=[replace(chapter, segments=[broken_segment])],
    )
    with pytest.raises(ValidationError, match="bounds"):
        broken.validate(text)


def test_manual_character_lock_and_aliases_are_valid():
    result = SourceSegmenter().segment("张三说：“你好。”")
    character = result.speakers.speakers[1]
    locked = replace(character, aliases=["老张"], locked=True)
    document = replace(
        result.speakers,
        revision=2,
        speakers=[result.speakers.speakers[0], locked],
    )
    document.validate()
    restored = SpeakersDocument.from_dict(document.to_dict())
    assert restored.speakers[1].aliases == ["老张"]
    assert restored.speakers[1].locked is True
