from __future__ import annotations

import pytest

from domain.v4 import ValidationError
from domain.v4.character_extraction import (
    CharacterCandidate,
    CharacterEvidence,
    CharacterExtractionResponse,
)
from repositories.character_candidates_repository import CharacterCandidatesRepository
from repositories.character_extraction_checkpoint_repository import (
    CharacterExtractionCheckpointRepository,
)
from services.character_extraction_service import (
    CharacterExtractionService,
    merge_character_candidates,
)
from services.source_segmenter import SourceSegmenter


def _character(**overrides):
    value = {
        "name": "林晚",
        "aliases": ["小晚"],
        "is_character": True,
        "confidence": 0.95,
        "evidence": [
            {"chapter_id": "chapter_0001", "text": "林晚推开门。"}
        ],
    }
    value.update(overrides)
    return value


def test_character_extraction_protocol_is_strict():
    with pytest.raises(ValidationError, match="schema"):
        CharacterExtractionResponse.from_dict({"characters": []})

    with pytest.raises(ValidationError, match="unknown fields"):
        CharacterExtractionResponse.from_dict(
            {
                "schema_version": "character-extraction-v1",
                "characters": [_character(extra="reject")],
            }
        )

    with pytest.raises(ValidationError, match="confidence"):
        CharacterExtractionResponse.from_dict(
            {
                "schema_version": "character-extraction-v1",
                "characters": [_character(confidence=1.1)],
            }
        )

    with pytest.raises(ValidationError, match="evidence"):
        CharacterExtractionResponse.from_dict(
            {
                "schema_version": "character-extraction-v1",
                "characters": [_character(evidence=[])],
            }
        )


def test_non_character_is_not_a_candidate():
    response = CharacterExtractionResponse.from_dict(
        {
            "schema_version": "character-extraction-v1",
            "characters": [
                _character(
                    name="轻声",
                    aliases=[],
                    is_character=False,
                    confidence=0.99,
                    evidence=[],
                )
            ],
        }
    )
    assert response.characters[0].is_character is False


class _Adapter:
    name = "stub"
    model = "character-v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def extract(self, *, chapter_id, context):
        self.calls.append(chapter_id)
        return self.responses.pop(0)


def test_extraction_is_chapter_scoped_resumable_and_merges_explicit_aliases(tmp_path):
    source = "第一章\n林晚推开门。\n第二章\n小晚抬头。"
    segmented = SourceSegmenter().segment(source)
    adapter = _Adapter(
        [
            {
                "schema_version": "character-extraction-v1",
                "characters": [
                    _character(
                        evidence=[
                            {"chapter_id": "chapter_0001", "text": "林晚推开门。"}
                        ]
                    )
                ],
            },
            {
                "schema_version": "character-extraction-v1",
                "characters": [
                    _character(
                        name="小晚",
                        aliases=[],
                        evidence=[
                            {"chapter_id": "chapter_0002", "text": "小晚抬头。"}
                        ],
                    )
                ],
            },
        ]
    )
    repository = CharacterCandidatesRepository(tmp_path)
    service = CharacterExtractionService(
        adapter,
        CharacterExtractionCheckpointRepository(
            tmp_path / "runtime" / "character_extraction"
        ),
        repository,
    )

    first = service.extract(source, segmented.script)
    assert adapter.calls == ["chapter_0001", "chapter_0002"]
    assert len(first.candidates.candidates) == 1
    candidate = first.candidates.candidates[0]
    assert candidate.display_name == "林晚"
    assert "小晚" in candidate.aliases
    assert len(candidate.evidence) == 2
    assert [item.display_name for item in segmented.speakers.speakers] == ["旁白"]

    resumed = service.extract(source, segmented.script)
    assert adapter.calls == ["chapter_0001", "chapter_0002"]
    assert resumed.failed_chapters == 0


def test_extraction_does_not_merge_similar_names_without_alias_evidence(tmp_path):
    source = "第一章\n林晚推开门。\n第二章\n林夜抬头。"
    segmented = SourceSegmenter().segment(source)
    adapter = _Adapter(
        [
            {
                "schema_version": "character-extraction-v1",
                "characters": [
                    _character(
                        evidence=[
                            {"chapter_id": "chapter_0001", "text": "林晚推开门。"}
                        ]
                    )
                ],
            },
            {
                "schema_version": "character-extraction-v1",
                "characters": [
                    _character(
                        name="林夜",
                        evidence=[
                            {"chapter_id": "chapter_0002", "text": "林夜抬头。"}
                        ],
                    )
                ],
            },
        ]
    )
    result = CharacterExtractionService(
        adapter,
        CharacterExtractionCheckpointRepository(
            tmp_path / "runtime" / "character_extraction"
        ),
        CharacterCandidatesRepository(tmp_path),
    ).extract(source, segmented.script)
    assert {item.display_name for item in result.candidates.candidates} == {"林晚", "林夜"}


def test_source_change_invalidates_candidate_view_and_checkpoints(tmp_path):
    source = "第一章\n林晚推开门。"
    segmented = SourceSegmenter().segment(source)
    adapter = _Adapter(
        [
            {
                "schema_version": "character-extraction-v1",
                "characters": [_character(evidence=[{"chapter_id": "chapter_0001", "text": "林晚推开门。"}])],
            },
            {
                "schema_version": "character-extraction-v1",
                "characters": [],
            },
        ]
    )
    checkpoint_path = tmp_path / "runtime" / "character_extraction"
    service = CharacterExtractionService(
        adapter,
        CharacterExtractionCheckpointRepository(checkpoint_path),
        CharacterCandidatesRepository(tmp_path),
    )
    service.extract(source, segmented.script)
    changed_source = "第一章\n顾川推开门。"
    changed_segmented = SourceSegmenter().segment(changed_source)
    result = service.extract(changed_source, changed_segmented.script)
    assert adapter.calls == ["chapter_0001", "chapter_0001"]
    assert result.candidates.candidates == []


def test_confirmed_candidate_is_not_auto_renamed_or_alias_merged():
    confirmed = CharacterCandidate(
        candidate_id="candidate_late",
        display_name="林晚",
        aliases=["小晚"],
        confidence=1.0,
        evidence=[CharacterEvidence("chapter_0001", "林晚出现。")],
        source="manual",
        status="confirmed",
    )
    incoming = CharacterCandidate(
        candidate_id="candidate_night",
        display_name="晚晚",
        aliases=["林晚"],
        confidence=0.9,
        evidence=[CharacterEvidence("chapter_0002", "晚晚出现。")],
        source="ai",
    )
    merged = merge_character_candidates([confirmed], [incoming])
    assert [item.display_name for item in merged] == ["晚晚", "林晚"]
    assert merged[1].aliases == ["小晚"]
