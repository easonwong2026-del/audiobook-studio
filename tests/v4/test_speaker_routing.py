from __future__ import annotations

import json
import sqlite3

import pytest

from ai.speaker_routing import RemoteSpeakerRoutingAdapter
from domain.v4 import Speaker, SpeakersDocument, ValidationError
from domain.v4.models import stable_speaker_id
from domain.v4.routing import SpeakerRoutingResponse
from repositories.routing_checkpoint_repository import RoutingCheckpointRepository
from services.source_segmenter import SourceSegmenter
from services.speaker_routing_service import SpeakerRoutingService


class StubAdapter:
    name = "stub"
    model = "stub-v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def route(self, *, context, segment_ids, allowed_speakers=None):
        self.calls.append(
            {
                "context": context,
                "segment_ids": segment_ids,
                "allowed_speakers": allowed_speakers or [],
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SpeakerRoutingResponse.from_dict(
            response,
            allowed_segment_ids=set(segment_ids),
            allowed_speaker_ids={
                item["speaker_id"]
                for item in (allowed_speakers or [])
            },
        )


def _routing_fixture(text):
    segmented = SourceSegmenter().segment(text)
    speakers = SpeakersDocument(
        speakers=[
            segmented.speakers.speakers[0],
            Speaker(stable_speaker_id("甲"), "甲", "confirmed"),
            Speaker(stable_speaker_id("乙"), "乙", "confirmed"),
        ]
    )
    return segmented.script, speakers


def test_protocol_rejects_duplicate_unknown_and_extra_fields():
    with pytest.raises(ValidationError, match="duplicate"):
        SpeakerRoutingResponse.from_dict(
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [
                    {"segment_id": "s1", "speaker_id": "a", "candidate_name": None, "confidence": 0.9},
                    {"segment_id": "s1", "speaker_id": "b", "candidate_name": None, "confidence": 0.9},
                ],
            },
            allowed_segment_ids={"s1"},
            allowed_speaker_ids={"a", "b"},
        )
    with pytest.raises(ValidationError, match="unknown segment"):
        SpeakerRoutingResponse.from_dict(
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [{"segment_id": "other", "speaker_id": None, "candidate_name": None, "confidence": 0.0}],
            },
            allowed_segment_ids={"s1"},
        )
    with pytest.raises(ValidationError, match="unknown fields"):
        SpeakerRoutingResponse.from_dict(
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [],
                "explanation": "not allowed",
            },
            allowed_segment_ids=set(),
        )

    with pytest.raises(ValidationError, match="unknown speaker_id"):
        SpeakerRoutingResponse.from_dict(
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [
                    {"segment_id": "s1", "speaker_id": "unknown", "candidate_name": None, "confidence": 0.9}
                ],
            },
            allowed_segment_ids={"s1"},
            allowed_speaker_ids={"known"},
        )


def test_remote_adapter_sends_id_context_and_accepts_missing_assignments():
    captured = {}

    def request(system, user):
        captured.update({"system": system, "user": json.loads(user)})
        return {"schema_version": "speaker-routing-v2", "assignments": []}

    adapter = RemoteSpeakerRoutingAdapter("stub", "model", request)
    response = adapter.route(
        context='[s1] “你好”',
        segment_ids=["s1", "s2"],
        allowed_speakers=[{"speaker_id": "speaker_a", "name": "甲", "aliases": ["阿甲"]}],
    )
    assert response.assignments == []
    assert captured["user"]["allowed_segment_ids"] == ["s1", "s2"]
    assert captured["user"]["allowed_speakers"][0]["speaker_id"] == "speaker_a"
    assert "emotion" not in captured["user"]
    assert "TTS" not in captured["user"]


def test_routing_only_unresolved_and_resumes_completed_batches(tmp_path):
    text = "他低声说：“第一句。”\n\n他低声说：“第二句。”"
    script, speakers = _routing_fixture(text)
    ids = [
        item.segment_id
        for item in script.chapters[0].segments
        if item.kind == "dialogue"
    ]
    adapter = StubAdapter(
        [
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [{"segment_id": ids[0], "speaker_id": stable_speaker_id("甲"), "candidate_name": None, "confidence": 0.95}],
            },
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [{"segment_id": ids[1], "speaker_id": stable_speaker_id("乙"), "candidate_name": None, "confidence": 0.95}],
            },
        ]
    )
    checkpoints = RoutingCheckpointRepository(tmp_path / "runtime.db")
    service = SpeakerRoutingService(adapter, checkpoints, batch_size=1)
    first = service.route(text, script, speakers)
    assert first.failed_batches == 0
    assert first.unresolved_segments == 0
    assert len(adapter.calls) == 2

    adapter_again = StubAdapter([])
    resumed = SpeakerRoutingService(
        adapter_again, checkpoints, batch_size=1
    ).route(text, script, speakers)
    assert resumed.unresolved_segments == 0
    assert adapter_again.calls == []


def test_failed_batch_does_not_discard_successful_batch(tmp_path):
    text = "他低声说：“第一句。”他低声说：“第二句。”"
    script, speakers = _routing_fixture(text)
    ids = [
        item.segment_id
        for item in script.chapters[0].segments
        if item.kind == "dialogue"
    ]
    adapter = StubAdapter(
        [
            RuntimeError("temporary failure"),
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [{"segment_id": ids[1], "speaker_id": stable_speaker_id("乙"), "candidate_name": None, "confidence": 0.95}],
            },
        ]
    )
    result = SpeakerRoutingService(
        adapter,
        RoutingCheckpointRepository(tmp_path / "runtime.db"),
        batch_size=1,
    ).route(text, script, speakers)
    assert result.failed_batches == 1
    assert result.completed_batches == 1
    assert result.unresolved_segments == 1


def test_checkpoint_contains_ids_but_not_context_or_api_key(tmp_path):
    text = "上下文。他低声说：“待识别。”"
    script, speakers = _routing_fixture(text)
    dialogue = next(
        item
        for item in script.chapters[0].segments
        if item.kind == "dialogue"
    )
    adapter = StubAdapter(
        [{"schema_version": "speaker-routing-v2", "assignments": []}]
    )
    path = tmp_path / "runtime.db"
    SpeakerRoutingService(
        adapter, RoutingCheckpointRepository(path)
    ).route(text, script, speakers)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT segment_ids_json, assignments_json FROM routing_batches"
        ).fetchone()
    persisted = "".join(row)
    assert dialogue.segment_id in persisted
    assert "上下文" not in persisted
    assert "API" not in persisted


def test_cancelled_checkpoint_is_rechecked_before_each_remote_batch(tmp_path):
    text = "他低声说：“第一句。”他低声说：“第二句。”"
    script, speakers = _routing_fixture(text)
    ids = [
        item.segment_id
        for item in script.chapters[0].segments
        if item.kind == "dialogue"
    ]
    checkpoints = RoutingCheckpointRepository(tmp_path / "runtime.db")

    class CancellingAdapter(StubAdapter):
        def route(self, *, context, segment_ids, allowed_speakers=None):
            result = super().route(
                context=context,
                segment_ids=segment_ids,
                allowed_speakers=allowed_speakers,
            )
            checkpoints.cancel_pending()
            return result

    adapter = CancellingAdapter(
        [
            {
                    "schema_version": "speaker-routing-v2",
                    "assignments": [{"segment_id": ids[0], "speaker_id": stable_speaker_id("甲"), "candidate_name": None, "confidence": 0.95}],
                },
                {
                    "schema_version": "speaker-routing-v2",
                    "assignments": [{"segment_id": ids[1], "speaker_id": stable_speaker_id("乙"), "candidate_name": None, "confidence": 0.95}],
            },
        ]
    )
    result = SpeakerRoutingService(
        adapter, checkpoints, batch_size=1
    ).route(text, script, speakers)
    assert len(adapter.calls) == 1
    assert result.unresolved_segments == 1


def test_null_assignment_stays_unresolved_and_new_name_is_only_a_candidate(tmp_path):
    text = "他低声说：“有人来了。”"
    script, speakers = _routing_fixture(text)
    segment = next(
        item for item in script.chapters[0].segments if item.kind == "dialogue"
    )
    adapter = StubAdapter(
        [
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [
                    {
                        "segment_id": segment.segment_id,
                        "speaker_id": None,
                        "candidate_name": "神秘人",
                        "confidence": 0.4,
                    }
                ],
            }
        ]
    )
    result = SpeakerRoutingService(
        adapter,
        RoutingCheckpointRepository(tmp_path / "runtime.db"),
    ).route(text, script, speakers)
    assert result.unresolved_segments == 1
    assert len(result.speakers.speakers) == 3
    assert result.candidates[0].display_name == "神秘人"


def test_unknown_speaker_id_is_rejected_without_creating_a_formal_speaker(tmp_path):
    text = "他低声说：“无人知道。”"
    script, speakers = _routing_fixture(text)
    segment = next(
        item for item in script.chapters[0].segments if item.kind == "dialogue"
    )
    adapter = StubAdapter(
        [
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [
                    {
                        "segment_id": segment.segment_id,
                        "speaker_id": "speaker_unknown",
                        "candidate_name": None,
                        "confidence": 0.99,
                    }
                ],
            }
        ]
    )
    result = SpeakerRoutingService(
        adapter,
        RoutingCheckpointRepository(tmp_path / "runtime.db"),
    ).route(text, script, speakers)
    assert result.failed_batches == 1
    assert result.unresolved_segments == 1
    assert all(item.display_name != "speaker_unknown" for item in result.speakers.speakers)


def test_router_does_not_change_locked_speaker_metadata(tmp_path):
    text = "他低声说：“继续。”"
    script, speakers = _routing_fixture(text)
    locked = SpeakersDocument(
        speakers=[
            speakers.speakers[0],
            Speaker(
                stable_speaker_id("甲"),
                "甲",
                "confirmed",
                locked=True,
            ),
            speakers.speakers[2],
        ]
    )
    segment = next(
        item for item in script.chapters[0].segments if item.kind == "dialogue"
    )
    adapter = StubAdapter(
        [
            {
                "schema_version": "speaker-routing-v2",
                "assignments": [
                    {
                        "segment_id": segment.segment_id,
                        "speaker_id": stable_speaker_id("甲"),
                        "candidate_name": None,
                        "confidence": 0.95,
                    }
                ],
            }
        ]
    )
    result = SpeakerRoutingService(
        adapter,
        RoutingCheckpointRepository(tmp_path / "runtime.db"),
    ).route(text, script, locked)
    assert next(item for item in result.speakers.speakers if item.display_name == "甲").locked
