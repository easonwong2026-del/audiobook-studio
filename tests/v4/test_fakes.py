from domain.v4 import FakeSpeakerRouter, FakeTtsAdapter
from services.source_segmenter import SourceSegmenter


def test_fake_router_accepts_only_known_unique_ids_and_speakers():
    segmented = SourceSegmenter().segment("张三说：“你好。”")
    dialogue = next(
        item
        for item in segmented.script.chapters[0].segments
        if item.kind == "dialogue"
    )
    router = FakeSpeakerRouter(
        [
            {"segment_id": dialogue.segment_id, "speaker_id": dialogue.speaker_id},
            {"segment_id": dialogue.segment_id, "speaker_id": dialogue.speaker_id},
            {"segment_id": "unknown", "speaker_id": None},
        ]
    )
    result = router.route(
        segmented.script.to_dict(), segmented.speakers.to_dict()
    )
    assert result == {
        "schema_version": "speaker-routing-v1",
        "assignments": [
            {
                "segment_id": dialogue.segment_id,
                "speaker_id": dialogue.speaker_id,
            }
        ],
    }


def test_fake_tts_records_no_source_text():
    adapter = FakeTtsAdapter()
    output = adapter.synthesize(
        {"task_id": "task_1", "actual_text": "敏感原文"},
        {"profile_id": "test"},
    )
    assert output == "audio/chunks/task_1.wav"
    assert adapter.calls == [{"task_id": "task_1", "profile_id": "test"}]
