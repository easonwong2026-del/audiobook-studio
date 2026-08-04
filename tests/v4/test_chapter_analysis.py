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


def _segment(
    index: int,
    text: str,
    *,
    kind="narration",
    speaker_id=None,
    confidence=0.95,
    speaker_evidence=None,
    uncertainty_reason=None,
) -> dict:
    return {
        "index": index,
        "segment_type": kind,
        "speaker_id": speaker_id,
        "text": text,
        "emotion": "neutral",
        "confidence": confidence,
        **({"speaker_evidence": speaker_evidence} if speaker_evidence else {}),
        **(
            {"uncertainty_reason": uncertainty_reason}
            if uncertainty_reason
            else {}
        ),
    }


class FakeChapterAdapter:
    name = "fake"
    model = "chapter-test-v1"

    def __init__(self, outputs, acting_output=None):
        self.outputs = list(outputs)
        self.acting_output = acting_output
        self.calls: list[dict] = []

    def analyze_chapter(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output

    def act_chapter(self, **kwargs):
        self.calls.append({"acting": True, **kwargs})
        return self.acting_output


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


def test_low_confidence_attribution_stays_as_candidate_until_confirmation(tmp_path):
    source = "林晚低声说：“也许吧。”"
    project = _project(tmp_path, source)
    response = _response(
        "chapter_0001",
        [
            _segment(
                0,
                source,
                kind="dialogue",
                speaker_id="new:林晚",
                confidence=0.42,
                speaker_evidence=["低声说"],
                uncertainty_reason="原文没有明确的说话人提示",
            )
        ],
        updates=[
            {
                "character_id": None,
                "canonical_name": "林晚",
                "aliases": ["小晚"],
                "is_new": True,
                "confidence": 0.42,
                "evidence": ["低声说"],
                "uncertainty_reason": "原文没有明确的说话人提示",
            }
        ],
    )

    result = ChapterAnalysisService(project, FakeChapterAdapter([response])).analyze()

    role_id = stable_speaker_id("林晚")
    segment = result.script.chapters[0].segments[0]
    role = next(item for item in result.speakers.speakers if item.speaker_id == role_id)
    assert result.status == "analyzed"
    assert segment.speaker_id is None
    assert segment.candidate_speaker_id == role_id
    assert segment.candidate_speaker_name == "林晚"
    assert segment.uncertainty_reason == "原文没有明确的说话人提示"
    assert role.status == "unresolved"
    assert role.review_status == "candidate"
    assert result.state["trace"]
    assert all("reasoning_content" not in item for item in result.state["trace"])

    confirmed_script, confirmed_speakers, attached = ChapterAnalysisService.confirm_candidate(
        project, role_id
    )
    assert attached == 1
    assert confirmed_script.chapters[0].segments[0].speaker_id == role_id
    assert confirmed_script.chapters[0].segments[0].speaker_source == "manual"
    assert next(
        item for item in confirmed_speakers.speakers if item.speaker_id == role_id
    ).review_status == "confirmed"


def test_rejecting_candidate_keeps_segment_unknown_not_narrator(tmp_path):
    source = "有人问：“你是谁？”"
    project = _project(tmp_path, source)
    response = _response(
        "chapter_0001",
        [_segment(0, source, kind="dialogue", speaker_id="new:陌生人", confidence=0.3)],
        updates=[
            {
                "character_id": None,
                "canonical_name": "陌生人",
                "aliases": [],
                "is_new": True,
                "confidence": 0.3,
            }
        ],
    )
    ChapterAnalysisService(project, FakeChapterAdapter([response])).analyze()
    role_id = stable_speaker_id("陌生人")

    rejected_script, rejected_speakers, cleared = ChapterAnalysisService.reject_candidate(
        project, role_id
    )
    segment = rejected_script.chapters[0].segments[0]
    assert cleared == 1
    assert segment.speaker_id is None
    assert segment.speaker_source == "unresolved"
    assert segment.candidate_speaker_id is None
    assert next(
        item for item in rejected_speakers.speakers if item.speaker_id == role_id
    ).review_status == "rejected"


def test_standard_depth_uses_separate_acting_contract(tmp_path):
    source = "雨停了。"
    project = _project(tmp_path, source)
    response = _response("chapter_0001", [_segment(0, source)])
    acting = {
        "schema_version": "chapter-acting-response-v1",
        "chapter_id": "chapter_0001",
        "segments": [
            {
                "index": 0,
                "emotion_strength": 0.8,
                "speed": 0.9,
                "pitch": 1.0,
                "intensity": 0.6,
                "breath": "light",
                "pause_before": 120,
                "pause_after": 700,
                "performance_note": "轻声收尾",
            }
        ],
    }
    adapter = FakeChapterAdapter([response], acting_output=acting)

    result = ChapterAnalysisService(
        project, adapter, analysis_depth="standard", reasoning_mode="high"
    ).analyze()

    segment = result.script.chapters[0].segments[0]
    assert segment.delivery["speed"] == 0.9
    assert segment.pause_before == 120
    assert result.state["phase2"]["status"] == "completed"
    assert result.state["stats"]["phase2_status"] == "completed"
    assert any(item.get("stage") == "phase2_acting" for item in result.state["trace"])
