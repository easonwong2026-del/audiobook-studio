from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.v4 import (
    ProjectManifest,
    SourceMetadata,
    Speaker,
    SpeakersDocument,
    ValidationError,
)
from domain.v4.character_consolidation import CharacterConsolidationResponse
from domain.v4.character_extraction import (
    CharacterCandidate,
    CharacterCandidatesDocument,
    CharacterEvidence,
)
from domain.v4.models import source_sha256, stable_speaker_id
from repositories.project_v4_repository import ProjectV4Repository
from services.character_consolidation_service import CharacterConsolidationService
from services.source_segmenter import SourceSegmenter
from services.v4_accuracy_evaluation import evaluate_v4_accuracy
from services.v4_project_analysis_pipeline import V4ProjectAnalysisPipeline


def _project(tmp_path: Path, source: str) -> Path:
    segmented = SourceSegmenter().segment(source)
    now = "2026-08-02T00:00:00+00:00"
    metadata = SourceMetadata(
        original_filename="book.txt",
        source_format="txt",
        encoding="utf-8",
        normalization="none",
        char_count=len(source),
        sha256=source_sha256(source),
        imported_at=now,
        source_origin="test",
        source_fidelity="full-text",
    )
    manifest = ProjectManifest(
        project_id="project_pipeline",
        name="pipeline",
        title="pipeline",
        author="",
        created_at=now,
        updated_at=now,
    )
    return ProjectV4Repository(tmp_path).create(
        directory_name="pipeline",
        manifest=manifest,
        source_text=source,
        source_metadata=metadata,
        script=segmented.script,
        speakers=segmented.speakers,
    )


class ExtractionAdapter:
    name = "stub"
    model = "extract-v1"

    def __init__(self):
        self.calls: list[str] = []

    def extract(self, *, chapter_id: str, context: str):
        self.calls.append(chapter_id)
        if chapter_id == "chapter_0001":
            return {
                "schema_version": "character-extraction-v1",
                "characters": [{
                    "name": "周建国",
                    "aliases": ["周队"],
                    "is_character": True,
                    "confidence": 0.96,
                    "evidence": [{"chapter_id": chapter_id, "text": "周建国推门进来。"}],
                }],
            }
        return {
            "schema_version": "character-extraction-v1",
            "characters": [{
                "name": "老周",
                "aliases": ["周队长"],
                "is_character": True,
                "confidence": 0.89,
                "evidence": [{"chapter_id": chapter_id, "text": "老周看了他一眼。"}],
            }],
        }


class ConsolidationAdapter:
    name = "stub"
    model = "consolidate-v1"

    def __init__(self):
        self.calls = 0

    def consolidate(self, request):
        self.calls += 1
        ids = [item.candidate_id for item in request.candidates]
        return {
            "schema_version": "character-consolidation-v1",
            "characters": [{
                "canonical_name": "周建国",
                "aliases": ["周队", "周队长", "老周"],
                "candidate_ids": ids,
                "confidence": 0.96,
                "importance": "major",
                "reason": "身份和两章证据一致",
            }],
            "unresolved_groups": [],
        }


class RoutingAdapter:
    name = "stub"
    model = "route-v2"

    def __init__(self):
        self.calls = []

    def route(self, *, context, segment_ids, allowed_speakers):
        self.calls.append(context)
        speaker_id = next(
            item["speaker_id"]
            for item in allowed_speakers
            if item["name"] == "周建国"
        )
        return {
            "schema_version": "speaker-routing-v2",
            "assignments": [
                {
                    "segment_id": segment_ids[0],
                    "speaker_id": speaker_id,
                    "candidate_name": None,
                    "confidence": 0.94,
                }
            ],
        }


def test_pipeline_consolidates_auto_confirms_routes_and_resumes(tmp_path):
    source = (
        "第一章\n周建国推门进来。\n"
        "第二章\n老周看了他一眼。\n他说：“我们走。”"
    )
    project = _project(tmp_path, source)
    extraction = ExtractionAdapter()
    consolidation = ConsolidationAdapter()
    routing = RoutingAdapter()
    pipeline = V4ProjectAnalysisPipeline(
        project,
        character_extraction_adapter=extraction,
        character_consolidation_adapter=consolidation,
        speaker_routing_adapter=routing,
    )

    result = pipeline.run()
    assert result.status == "completed"
    assert extraction.calls == ["chapter_0001", "chapter_0002"]
    assert consolidation.calls == 1
    assert len(routing.calls) == 1
    assert "continuous dialogue group" in routing.calls[0]
    assert "周建国" in routing.calls[0]
    assert any(item.display_name == "周建国" for item in result.speakers.speakers)
    character = next(item for item in result.speakers.speakers if item.display_name == "周建国")
    assert set(character.aliases) >= {"老周", "周队长"}
    assert result.summary["auto_confirmed_characters"] == 1
    assert result.summary["dialogue_auto_routed"] == 1
    assert (project / "runtime/analysis.json").is_file()
    assert (project / "runtime/character_consistency.json").is_file()

    resumed = V4ProjectAnalysisPipeline(
        project,
        character_extraction_adapter=extraction,
        character_consolidation_adapter=consolidation,
        speaker_routing_adapter=routing,
    ).run()
    assert resumed.status == "completed"
    assert extraction.calls == ["chapter_0001", "chapter_0002"]
    assert consolidation.calls == 1
    assert len(routing.calls) == 1


def test_consolidation_rejects_unknown_candidate_ids_and_keeps_low_confidence_pending():
    with pytest.raises(ValidationError, match="unknown candidate_id"):
        CharacterConsolidationResponse.from_dict(
            {
                "schema_version": "character-consolidation-v1",
                "characters": [{
                    "canonical_name": "周建国",
                    "aliases": [],
                    "candidate_ids": ["candidate_unknown"],
                    "confidence": 0.99,
                    "importance": "major",
                    "reason": "证据",
                }],
                "unresolved_groups": [],
            },
            allowed_candidate_ids={"candidate_real"},
        )


def test_similar_names_without_identity_evidence_are_not_merged(tmp_path):
    source = "第一章\n林晚出现。\n第二章\n林夜出现。"
    segmented = SourceSegmenter().segment(source)
    candidates = CharacterCandidatesDocument(
        source_sha256(source),
        [
            CharacterCandidate(
                "candidate_late",
                "林晚",
                [],
                0.96,
                [CharacterEvidence("chapter_0001", "林晚出现。")],
                "ai",
            ),
            CharacterCandidate(
                "candidate_night",
                "林夜",
                [],
                0.96,
                [CharacterEvidence("chapter_0002", "林夜出现。")],
                "ai",
            ),
        ],
    )
    result = CharacterConsolidationService().consolidate(
        source_sha256(source),
        candidates,
        segmented.speakers,
        response={
            "schema_version": "character-consolidation-v1",
            "characters": [{
                "canonical_name": "林晚",
                "aliases": [],
                "candidate_ids": ["candidate_late", "candidate_night"],
                "confidence": 0.98,
                "importance": "major",
                "reason": "名称相似",
            }],
            "unresolved_groups": [],
        },
    )
    assert [item.display_name for item in result.speakers.speakers] == ["旁白"]
    assert all(item.status == "candidate" for item in result.candidates.candidates)


def test_locked_speaker_is_not_renamed_or_auto_extended(tmp_path):
    source = "第一章\n周建国出现。"
    candidate = CharacterCandidate(
        candidate_id="candidate_a",
        display_name="周建国",
        aliases=["老周"],
        confidence=0.96,
        evidence=[CharacterEvidence("chapter_0001", "周建国出现。")],
        source="ai",
    )
    candidates = CharacterCandidatesDocument(source_sha256(source), [candidate])
    locked = SpeakersDocument(
        speakers=[
            Speaker("narrator", "旁白", "confirmed", speaker_type="narrator", locked=True),
            Speaker(stable_speaker_id("周建国"), "周建国", "confirmed", locked=True),
        ]
    )
    service = CharacterConsolidationService(auto_confirm_threshold=0.90)
    result = service.consolidate(
        source_sha256(source), candidates, locked,
        response={
            "schema_version": "character-consolidation-v1",
            "characters": [{
                "canonical_name": "周建国",
                "aliases": ["老周"],
                "candidate_ids": ["candidate_a"],
                "confidence": 0.96,
                "importance": "major",
                "reason": "已有人工锁定角色",
            }],
            "unresolved_groups": [],
        },
    )
    speaker = next(item for item in result.speakers.speakers if item.display_name == "周建国")
    assert speaker.locked
    assert speaker.aliases == []
    assert result.candidates.candidates[0].status == "confirmed"


def test_accuracy_evaluator_reports_precision_and_coverage():
    source = "第一章\n甲说：“好。”"
    segmented = SourceSegmenter().segment(source)
    dialogue = next(
        item
        for chapter in segmented.script.chapters
        for item in chapter.segments
        if item.kind == "dialogue"
    )
    metrics = evaluate_v4_accuracy(
        segmented.speakers,
        segmented.script,
        {
            "characters": ["甲"],
            "dialogue": {dialogue.segment_id: "甲"},
        },
    )
    assert metrics.role_accuracy == 1.0
    assert metrics.dialogue_accuracy == 1.0
    assert metrics.auto_coverage == 1.0


def test_failed_ai_stage_keeps_project_openable_and_marks_partial(tmp_path):
    class FailingExtraction:
        name = "stub"
        model = "failure-v1"

        def extract(self, *, chapter_id, context):
            raise RuntimeError("temporary provider failure")

    source = "第一章\n林晚推开门。"
    project = _project(tmp_path, source)
    result = V4ProjectAnalysisPipeline(
        project,
        character_extraction_adapter=FailingExtraction(),
    ).run()
    assert result.status == "partial"
    assert result.errors
    assert (project / "source/source.txt").read_text(encoding="utf-8") == source
    assert (project / "script/script.json").is_file()
    state = json.loads((project / "runtime/analysis.json").read_text(encoding="utf-8"))
    assert state["status"] == "partial"
