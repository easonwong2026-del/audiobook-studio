from __future__ import annotations

import json
from pathlib import Path

from domain.v4 import (
    CharacterBibleDocument,
    ProjectManifest,
    ScriptDirectorBatch,
    SourceMetadata,
)
from domain.v4.ai_first import BibleCharacter, BibleEvidence
from domain.v4.models import source_sha256, stable_speaker_id
from repositories.project_v4_repository import ProjectV4Repository
from services.source_segmenter import SourceSegmenter
from services.v4_project_analysis_pipeline import V4ProjectAnalysisPipeline
from services.v4_project_creation import V4ProjectCreationService


def _entry(name: str, chapter_id: str, evidence: str, aliases=None):
    return BibleCharacter(
        character_id=f"char_{name}",
        canonical_name=name,
        aliases=list(aliases or []),
        description="故事人物",
        importance="major",
        relationships=[],
        first_appearance_chapter=chapter_id,
        evidence=[BibleEvidence(chapter_id, evidence)],
        confidence=0.97,
        speaker_id=stable_speaker_id(name),
    )


class FakeBookUnderstanding:
    name = "fake-book"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def read_chapter(self, **kwargs):
        self.calls.append(kwargs)
        chapter_id = kwargs["chapter_id"]
        values = [_entry("林晚", chapter_id, "林晚")]
        if chapter_id == "chapter_0002":
            values.append(_entry("顾川", chapter_id, "顾川"))
        return CharacterBibleDocument(
            source_sha256=kwargs["source_sha256"],
            characters=values,
            schema_version="character-bible-chapter-v1",
        )

    def finalize(self, *, source_sha256, memory):
        return CharacterBibleDocument.from_dict(memory)


class FakeScriptDirector:
    name = "fake-script"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def analyze_batch(self, **kwargs):
        self.calls.append(kwargs)
        text = kwargs["text"]
        start = kwargs["source_start"]
        end = kwargs["source_end"]
        speaker = None
        for item in kwargs["bible"]["characters"]:
            if item["canonical_name"] in text:
                speaker = item["speaker_id"]
                break
        segment_type = "dialogue" if "说道" in text or "回答" in text else "narration"
        if segment_type == "narration":
            speaker = "narrator"
        return ScriptDirectorBatch.from_dict({
            "schema_version": "ai-script-director-v4",
            "chapter_id": kwargs["chapter_id"],
            "source_start": start,
            "source_end": end,
            "segments": [{
                "source_start": start,
                "source_end": end,
                "segment_type": segment_type,
                "speaker_id": speaker,
                "text": text,
                "confidence": 0.96,
            }],
        })


class FakeReviewer:
    name = "fake-review"
    model = "fake-reasoner"

    def __init__(self):
        self.calls = []

    def review_chapter(self, **_kwargs):
        self.calls.append(_kwargs)
        return {"schema_version": "ai-script-review-v1", "patches": []}


def _project(tmp_path: Path, source: str) -> Path:
    segmented = SourceSegmenter().source_only(source)
    now = "2026-08-02T00:00:00+00:00"
    metadata = SourceMetadata(
        original_filename="book.txt",
        source_format="txt",
        encoding="utf-8",
        normalization="none",
        char_count=len(source),
        sha256=source_sha256(source),
        imported_at=now,
    )
    manifest = ProjectManifest(
        project_id="project_ai_first",
        name="ai-first",
        title="ai-first",
        created_at=now,
        updated_at=now,
    )
    return ProjectV4Repository(tmp_path).create(
        directory_name="ai-first",
        manifest=manifest,
        source_text=source,
        source_metadata=metadata,
        script=segmented.script,
        speakers=segmented.speakers,
    )


def test_v4_creation_is_source_only_before_ai(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章\n林晚说道：‘我们走吧。’", encoding="utf-8")
    result = V4ProjectCreationService(ProjectV4Repository(tmp_path / "projects")).create_from_source(
        source, "book", auto_analyze=False
    )
    speakers = json.loads(
        (result.project_path / "script/speakers.json").read_text(encoding="utf-8")
    )
    script = json.loads(
        (result.project_path / "script/script.json").read_text(encoding="utf-8")
    )
    assert [item["name"] for item in speakers["speakers"]] == ["旁白"]
    assert all(item["speaker_id"] is None for item in script["chapters"][0]["segments"])
    assert all(item["speaker_source"] == "unresolved" for item in script["chapters"][0]["segments"])


def test_ai_first_pipeline_reads_source_builds_script_and_resumes(tmp_path):
    source = "第一章\n林晚说道：‘我们走吧。’\n第二章\n顾川回答：‘我不同意。’"
    project = _project(tmp_path, source)
    book = FakeBookUnderstanding()
    director = FakeScriptDirector()
    reviewer = FakeReviewer()
    pipeline = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    )
    result = pipeline.run()
    assert result.status == "completed"
    assert [item.display_name for item in result.speakers.speakers] == [
        "旁白", "林晚", "顾川"
    ]
    assert result.summary["analysis_mode"] == "ai-first"
    assert result.summary["identified_characters"] == 2
    assert all(
        item.speaker_source in {"ai", "unresolved"}
        for chapter in result.script.chapters
        for item in chapter.segments
    )
    result.script.validate(source)
    assert (project / "runtime/ai_first/book_understanding.json").is_file()
    assert (project / "runtime/ai_first/script_director.json").is_file()
    assert (project / "runtime/ai_first/script_review.json").is_file()
    calls_before = (len(book.calls), len(director.calls))

    resumed = V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    ).run()
    assert resumed.status == "completed"
    assert (len(book.calls), len(director.calls)) == calls_before
    assert len(reviewer.calls) == 2

    V4ProjectAnalysisPipeline(
        project,
        book_understanding_adapter=book,
        script_director_adapter=director,
        script_review_adapter=reviewer,
    ).run(force_reanalysis=True)
    assert len(book.calls) == calls_before[0] + 2
    assert len(director.calls) == calls_before[1] + 2
    assert len(reviewer.calls) == 4
    assert list((project / "revisions").glob("ai-analysis-*/script.json"))
