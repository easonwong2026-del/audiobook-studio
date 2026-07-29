from __future__ import annotations

import json
import sqlite3

import pytest

from ai.speaker_routing import RemoteSpeakerRoutingAdapter
from domain.v4 import ValidationError
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

    def route(self, *, context, segment_ids):
        self.calls.append({"context": context, "segment_ids": segment_ids})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SpeakerRoutingResponse.from_dict(
            response, allowed_segment_ids=set(segment_ids)
        )


def test_protocol_rejects_duplicate_unknown_and_extra_fields():
    with pytest.raises(ValidationError, match="duplicate"):
        SpeakerRoutingResponse.from_dict(
            {
                "schema_version": "speaker-routing-v1",
                "assignments": [
                    {"segment_id": "s1", "speaker": "甲"},
                    {"segment_id": "s1", "speaker": "乙"},
                ],
            },
            allowed_segment_ids={"s1"},
        )
    with pytest.raises(ValidationError, match="unknown segment"):
        SpeakerRoutingResponse.from_dict(
            {
                "schema_version": "speaker-routing-v1",
                "assignments": [{"segment_id": "other", "speaker": None}],
            },
            allowed_segment_ids={"s1"},
        )
    with pytest.raises(ValidationError, match="unknown fields"):
        SpeakerRoutingResponse.from_dict(
            {
                "schema_version": "speaker-routing-v1",
                "assignments": [],
                "explanation": "not allowed",
            },
            allowed_segment_ids=set(),
        )


def test_remote_adapter_sends_id_context_and_accepts_missing_assignments():
    captured = {}

    def request(system, user):
        captured.update({"system": system, "user": json.loads(user)})
        return {"schema_version": "speaker-routing-v1", "assignments": []}

    adapter = RemoteSpeakerRoutingAdapter("stub", "model", request)
    response = adapter.route(context='[s1] “你好”', segment_ids=["s1", "s2"])
    assert response.assignments == []
    assert captured["user"]["allowed_segment_ids"] == ["s1", "s2"]
    assert "emotion" not in captured["user"]
    assert "TTS" not in captured["user"]


def test_routing_only_unresolved_and_resumes_completed_batches(tmp_path):
    text = "“第一句。”\n\n“第二句。”"
    segmented = SourceSegmenter().segment(text)
    ids = [
        item.segment_id
        for item in segmented.script.chapters[0].segments
        if item.kind == "dialogue"
    ]
    adapter = StubAdapter(
        [
            {
                "schema_version": "speaker-routing-v1",
                "assignments": [{"segment_id": ids[0], "speaker": "甲"}],
            },
            {
                "schema_version": "speaker-routing-v1",
                "assignments": [{"segment_id": ids[1], "speaker": "乙"}],
            },
        ]
    )
    checkpoints = RoutingCheckpointRepository(tmp_path / "runtime.db")
    service = SpeakerRoutingService(adapter, checkpoints, batch_size=1)
    first = service.route(text, segmented.script, segmented.speakers)
    assert first.failed_batches == 0
    assert first.unresolved_segments == 0
    assert len(adapter.calls) == 2

    adapter_again = StubAdapter([])
    resumed = SpeakerRoutingService(
        adapter_again, checkpoints, batch_size=1
    ).route(text, segmented.script, segmented.speakers)
    assert resumed.unresolved_segments == 0
    assert adapter_again.calls == []


def test_failed_batch_does_not_discard_successful_batch(tmp_path):
    text = "“第一句。”“第二句。”"
    segmented = SourceSegmenter().segment(text)
    ids = [
        item.segment_id
        for item in segmented.script.chapters[0].segments
        if item.kind == "dialogue"
    ]
    adapter = StubAdapter(
        [
            RuntimeError("temporary failure"),
            {
                "schema_version": "speaker-routing-v1",
                "assignments": [{"segment_id": ids[1], "speaker": "乙"}],
            },
        ]
    )
    result = SpeakerRoutingService(
        adapter,
        RoutingCheckpointRepository(tmp_path / "runtime.db"),
        batch_size=1,
    ).route(text, segmented.script, segmented.speakers)
    assert result.failed_batches == 1
    assert result.completed_batches == 1
    assert result.unresolved_segments == 1


def test_checkpoint_contains_ids_but_not_context_or_api_key(tmp_path):
    text = "上下文。“待识别。”"
    segmented = SourceSegmenter().segment(text)
    dialogue = next(
        item
        for item in segmented.script.chapters[0].segments
        if item.kind == "dialogue"
    )
    adapter = StubAdapter(
        [{"schema_version": "speaker-routing-v1", "assignments": []}]
    )
    path = tmp_path / "runtime.db"
    SpeakerRoutingService(
        adapter, RoutingCheckpointRepository(path)
    ).route(text, segmented.script, segmented.speakers)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT segment_ids_json, assignments_json FROM routing_batches"
        ).fetchone()
    persisted = "".join(row)
    assert dialogue.segment_id in persisted
    assert "上下文" not in persisted
    assert "API" not in persisted


def test_cancelled_checkpoint_is_rechecked_before_each_remote_batch(tmp_path):
    text = "“第一句。”“第二句。”"
    segmented = SourceSegmenter().segment(text)
    ids = [
        item.segment_id
        for item in segmented.script.chapters[0].segments
        if item.kind == "dialogue"
    ]
    checkpoints = RoutingCheckpointRepository(tmp_path / "runtime.db")

    class CancellingAdapter(StubAdapter):
        def route(self, *, context, segment_ids):
            result = super().route(context=context, segment_ids=segment_ids)
            checkpoints.cancel_pending()
            return result

    adapter = CancellingAdapter(
        [
            {
                "schema_version": "speaker-routing-v1",
                "assignments": [{"segment_id": ids[0], "speaker": "甲"}],
            },
            {
                "schema_version": "speaker-routing-v1",
                "assignments": [{"segment_id": ids[1], "speaker": "乙"}],
            },
        ]
    )
    result = SpeakerRoutingService(
        adapter, checkpoints, batch_size=1
    ).route(text, segmented.script, segmented.speakers)
    assert len(adapter.calls) == 1
    assert result.unresolved_segments == 1
