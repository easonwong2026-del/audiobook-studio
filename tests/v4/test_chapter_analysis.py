from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai.providers.exceptions import ProviderOutputInvalidJsonError
from domain.v4 import Speaker, SpeakersDocument
from domain.v4.chapter_analysis import CHAPTER_ANALYSIS_RESPONSE_SCHEMA
from domain.v4.models import stable_speaker_id
from repositories.project_v4_repository import ProjectV4Repository
from services.chapter_analysis_service import ChapterAnalysisService
from services.v4_project_creation import V4ProjectCreationService


def _response(chapter_id: str, segments: list[dict], updates=None) -> dict:
    return {
        "schema_version": CHAPTER_ANALYSIS_RESPONSE_SCHEMA,
        "chapter_id": chapter_id,
        "character_updates": list(updates or []),
        "segments": segments,
    }


def _segment(index: int, text: str, *, kind="narration", speaker_id=None) -> dict:
    return {
        "index": index,
        "segment_type": kind,
        "speaker_id": speaker_id,
        "text": text,
        "emotion": "neutral",
        "confidence": 0.95,
    }


class FakeChapterAdapter:
    name = "fake"
    model = "chapter-test-v1"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def analyze_chapter(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def _project(tmp_path: Path, source_text: str):
    source = tmp_path / "chapter.txt"
    source.write_text(source_text, encoding="utf-8")
    return V4ProjectCreationService(
        ProjectV4Repository(tmp_path / "projects")
    ).create_from_source(source, "chapter", auto_analyze=False).project_path


def test_fast_path_treats_uploaded_book_as_one_chapter(tmp_path):
    project = _project(tmp_path, "第一章\n第二章\n仍是同一份当前章节输入。")
    import json

    script = json.loads(
        (project / "script/script.json").read_text(encoding="utf-8")
    )
    assert len(script["chapters"]) == 1


def test_normal_dialogue_creates_stable_new_role_and_absolute_coordinates(tmp_path):
    source = "林晚说：“走吧。”\n夜色渐深。"
    project = _project(tmp_path, source)
    response = _response(
        "chapter_0001",
        [
            _segment(0, "林晚说：“走吧。”", kind="dialogue", speaker_id="new:林晚"),
            _segment(1, "\n夜色渐深。"),
        ],
        updates=[
            {
                "character_id": None,
                "canonical_name": "林晚",
                "aliases": [],
                "is_new": True,
                "confidence": 0.96,
            }
        ],
    )
    adapter = FakeChapterAdapter([response])

    result = ChapterAnalysisService(project, adapter).analyze()

    assert result.status == "analyzed"
    assert len(adapter.calls) == 1
    dialogue = result.script.chapters[0].segments[0]
    assert source[dialogue.start:dialogue.end] == "林晚说：“走吧。”"
    assert dialogue.speaker_id == stable_speaker_id("林晚")
    assert dialogue.speaker_source == "ai"
    assert (project / "runtime/chapter_analysis/chapter_0001.json").is_file()


def test_existing_role_is_reused_and_null_speaker_stays_unresolved(tmp_path):
    source = "林晚说：“好。”\n有人敲门。"
    project = _project(tmp_path, source)
    import json

    from domain.v4 import ScriptDocument

    script = ScriptDocument.from_dict(
        json.loads((project / "script/script.json").read_text(encoding="utf-8")),
        source,
    )
    role_id = stable_speaker_id("林晚")
    speakers = SpeakersDocument(
        speakers=[
            Speaker("narrator", "旁白", "confirmed", "narrator", locked=True),
            Speaker(role_id, "林晚", "confirmed", aliases=["小晚"]),
        ],
        revision=2,
    )
    ProjectV4Repository(project.parent).save_script_and_speakers(
        project, source, replace(script, revision=2), speakers
    )
    response = _response(
        "chapter_0001",
        [
            _segment(0, "林晚说：“好。”", kind="dialogue", speaker_id=role_id),
            _segment(1, "\n有人敲门。", kind="dialogue", speaker_id=None),
        ],
        updates=[
            {
                "character_id": role_id,
                "canonical_name": "林晚",
                "aliases": ["小晚"],
                "is_new": False,
                "confidence": 0.99,
            }
        ],
    )

    result = ChapterAnalysisService(project, FakeChapterAdapter([response])).analyze()

    assert result.script.chapters[0].segments[0].speaker_id == role_id
    assert result.script.chapters[0].segments[1].status == "unresolved"
    assert any(item.speaker_id == role_id for item in result.speakers.speakers)


def test_omission_is_repaired_once_and_counts_requests(tmp_path):
    source = "旁白开场。随后继续。"
    project = _project(tmp_path, source)
    invalid = _response("chapter_0001", [_segment(0, "旁白开场。")])
    valid = _response("chapter_0001", [_segment(0, source)])
    adapter = FakeChapterAdapter([invalid, valid])

    result = ChapterAnalysisService(project, adapter).analyze()

    assert result.status == "analyzed"
    assert len(adapter.calls) == 2
    assert adapter.calls[1]["previous_response"] == invalid
    assert result.state["ai_requests"] == 2
    assert result.state["retries"] == 1


def test_rewrite_or_invalid_json_never_becomes_success(tmp_path):
    source = "原文不能被改写。"
    project = _project(tmp_path, source)
    invalid_json = ProviderOutputInvalidJsonError("invalid JSON")
    adapter = FakeChapterAdapter([invalid_json, _response("chapter_0001", [_segment(0, "改写了")])])

    result = ChapterAnalysisService(project, adapter).analyze()

    assert result.status == "needs_attention"
    assert result.state["reason_code"] == "chapter_analysis_invalid"
    assert len(adapter.calls) == 2


def test_timeout_is_visible_and_does_not_retry_as_repair(tmp_path):
    project = _project(tmp_path, "网络超时也不能写入半成品。")
    adapter = FakeChapterAdapter([TimeoutError("request timeout")])

    result = ChapterAnalysisService(project, adapter).analyze()

    assert result.status == "failed"
    assert result.state["reason_code"] == "chapter_analysis_timeout"
    assert result.state["ai_requests"] == 1
    assert len(adapter.calls) == 1


def test_old_full_pipeline_cache_is_not_used_by_fast_state(tmp_path):
    project = _project(tmp_path, "旧缓存不应被章节模式误读。")
    runtime = project / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "analysis.json").write_text(
        '{"schema_version":"v4-analysis-state-v1","status":"completed"}',
        encoding="utf-8",
    )
    adapter = FakeChapterAdapter(
        [_response("chapter_0001", [_segment(0, "旧缓存不应被章节模式误读。")])]
    )

    result = ChapterAnalysisService(project, adapter).analyze()

    assert len(adapter.calls) == 1
    assert result.state["analysis_mode"] == "chapter-fast"
