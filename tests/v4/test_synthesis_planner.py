from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from domain.v4.production import (
    PerformanceOverrides,
    PronunciationRules,
    TextLimits,
    TtsProfile,
    VoiceBinding,
    VoiceBindings,
)
from repositories.runtime_repository import RuntimeRepository
from services.invalidation_service import InvalidationService
from services.plan_preview import synthesis_plan_rows, synthesis_plan_summary
from services.source_segmenter import SourceSegmenter
from services.speaker_review_service import SpeakerReviewService
from services.synthesis_planner import SynthesisPlanner
from tts.text_measurement import CharacterMeasurer, ConservativeTokenMeasurer


def _profile(**limits):
    values = {
        "preferred": 8,
        "maximum": 10,
        "absolute": 12,
        "minimum": 3,
        "metric": "characters",
    }
    values.update(limits)
    return TtsProfile(
        profile_id="test",
        engine="fake",
        limits=TextLimits(**values),
    )


def _inputs(text, profile=None):
    segmented = SourceSegmenter().segment(text)
    voices = VoiceBindings(
        {
            item.speaker_id: VoiceBinding(
                voice_id=f"voice_{item.speaker_id}",
                fingerprint=f"fp_{item.speaker_id}",
            )
            for item in segmented.speakers.speakers
        }
    )
    return (
        segmented,
        voices,
        PerformanceOverrides(),
        PronunciationRules(),
        profile or _profile(),
    )


def test_long_segment_splits_at_sentence_boundaries_without_loss():
    text = "第一句。第二句。第三句。第四句。"
    segmented, voices, performance, pronunciation, profile = _inputs(text)
    result = SynthesisPlanner(CharacterMeasurer()).plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        performance,
        pronunciation,
        profile,
    )
    assert len(result.plan.tasks) > 1
    assert all(item.text_length <= 10 for item in result.plan.tasks)
    assert "".join(item.actual_text for item in result.plan.tasks) == text
    assert result.plan.tasks[1].continuation is True
    assert result.plan.tasks[1].pause_after_ms == 120


def test_short_same_speaker_segments_merge_and_keep_whitespace_gap():
    text = "“甲。”\n\n“乙。”"
    segmented, voices, performance, pronunciation, _ = _inputs(text)
    ids = [
        item.segment_id
        for item in segmented.script.chapters[0].segments
        if item.kind == "dialogue"
    ]
    script, speakers = SpeakerReviewService.assign(
        segmented.script,
        segmented.speakers,
        segment_ids=ids,
        speaker_id="narrator",
    )
    profile = _profile(minimum=8, maximum=20, absolute=24, preferred=12)
    result = SynthesisPlanner(CharacterMeasurer()).plan(
        text, script, speakers, voices, performance, pronunciation, profile
    )
    assert len(result.plan.tasks) == 1
    assert result.plan.tasks[0].source_segments == ids
    assert result.plan.tasks[0].actual_text == text


def test_short_segments_with_different_performance_do_not_merge():
    text = "“甲。”“乙。”"
    segmented, voices, _, pronunciation, _ = _inputs(text)
    ids = [
        item.segment_id
        for item in segmented.script.chapters[0].segments
        if item.kind == "dialogue"
    ]
    script, speakers = SpeakerReviewService.assign(
        segmented.script,
        segmented.speakers,
        segment_ids=ids,
        speaker_id="narrator",
    )
    performance = PerformanceOverrides(
        overrides={
            ids[0]: {"emotion_mode": "manual", "emotion": "sad"}
        }
    )
    profile = _profile(minimum=8, maximum=20, absolute=24, preferred=12)
    result = SynthesisPlanner(CharacterMeasurer()).plan(
        text, script, speakers, voices, performance, pronunciation, profile
    )
    assert len(result.plan.tasks) == 2


def test_unresolved_and_unbound_are_reported_not_planned():
    text = "“未知。”旁白。"
    segmented, voices, performance, pronunciation, profile = _inputs(text)
    voices = VoiceBindings({})
    result = SynthesisPlanner(CharacterMeasurer()).plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        performance,
        pronunciation,
        profile,
    )
    assert result.unresolved_segments
    assert result.unbound_speakers == ["narrator"]
    assert result.plan.tasks == []


def test_profile_metric_must_match_pluggable_measurer():
    text = "正文。"
    segmented, voices, performance, pronunciation, profile = _inputs(text)
    with pytest.raises(ValueError, match="metric"):
        SynthesisPlanner(ConservativeTokenMeasurer()).plan(
            text,
            segmented.script,
            segmented.speakers,
            voices,
            performance,
            pronunciation,
            profile,
        )


def test_voice_change_only_invalidates_affected_speaker_tasks():
    text = "张三说：“甲。”李四说：“乙。”"
    segmented, voices, performance, pronunciation, profile = _inputs(
        text, _profile(maximum=20, absolute=24, preferred=12)
    )
    planner = SynthesisPlanner(CharacterMeasurer())
    first = planner.plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        performance,
        pronunciation,
        profile,
    ).plan
    character = segmented.speakers.speakers[1]
    changed_bindings = dict(voices.bindings)
    changed_bindings[character.speaker_id] = replace(
        changed_bindings[character.speaker_id],
        voice_id="new_voice",
        fingerprint="new_fingerprint",
    )
    second = planner.plan(
        text,
        segmented.script,
        segmented.speakers,
        replace(voices, bindings=changed_bindings, revision=2),
        performance,
        pronunciation,
        profile,
        previous_plan=first,
    ).plan
    diff = InvalidationService.compare(first, second)
    assert diff.stale_task_ids
    assert diff.reusable_task_ids
    stale_speakers = {
        item.speaker_id
        for item in first.tasks
        if item.task_id in diff.stale_task_ids
    }
    assert stale_speakers == {character.speaker_id}


def test_text_override_and_performance_only_change_related_fingerprint():
    text = "第一段。第二段。"
    segmented, voices, performance, pronunciation, profile = _inputs(
        text, _profile(maximum=20, absolute=24, preferred=12)
    )
    planner = SynthesisPlanner(CharacterMeasurer())
    first = planner.plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        performance,
        pronunciation,
        profile,
    ).plan
    segment = segmented.script.chapters[0].segments[0]
    changed_script = replace(
        segmented.script,
        revision=2,
        chapters=[
            replace(
                segmented.script.chapters[0],
                segments=[replace(segment, text_override="替换文本。")],
            )
        ],
    )
    changed_performance = PerformanceOverrides(
        overrides={
            segment.segment_id: {
                "emotion_mode": "manual",
                "emotion": "sad",
            }
        },
        revision=2,
    )
    second = planner.plan(
        text,
        changed_script,
        segmented.speakers,
        voices,
        changed_performance,
        pronunciation,
        profile,
        previous_plan=first,
    ).plan
    diff = InvalidationService.compare(first, second)
    assert diff.stale_task_ids
    assert first.dependencies.source_sha256 == second.dependencies.source_sha256


def test_plan_preview_contains_no_execution_side_effects():
    text = "正文。"
    segmented, voices, performance, pronunciation, profile = _inputs(text)
    plan = SynthesisPlanner(CharacterMeasurer()).plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        performance,
        pronunciation,
        profile,
    ).plan
    assert synthesis_plan_summary(plan)["task_count"] == 1
    assert synthesis_plan_rows(plan)[0][0] == plan.tasks[0].task_id


def test_larger_split_limit_reuses_tasks_whose_boundaries_do_not_change():
    text = "短句。"
    segmented, voices, performance, pronunciation, profile = _inputs(text)
    planner = SynthesisPlanner(CharacterMeasurer())
    first = planner.plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        performance,
        pronunciation,
        profile,
    ).plan
    larger = replace(
        profile,
        revision=2,
        limits=replace(
            profile.limits,
            preferred=10,
            maximum=12,
            absolute=16,
        ),
    )
    second = planner.plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        performance,
        pronunciation,
        larger,
        previous_plan=first,
    ).plan
    diff = InvalidationService.compare(first, second)
    assert diff.reusable_task_ids == [first.tasks[0].task_id]
    assert diff.stale_task_ids == []


def test_runtime_sync_preserves_reusable_completion_and_stales_only_changed(tmp_path):
    text = "张三说：“甲。”李四说：“乙。”"
    segmented, voices, performance, pronunciation, profile = _inputs(
        text, _profile(maximum=20, absolute=24, preferred=12)
    )
    planner = SynthesisPlanner(CharacterMeasurer())
    first = planner.plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        performance,
        pronunciation,
        profile,
    ).plan
    runtime = RuntimeRepository(tmp_path / "runtime.db")
    runtime.initialize()
    InvalidationService.sync_runtime(runtime, None, first)
    with sqlite3.connect(runtime.path) as connection:
        connection.execute(
            "UPDATE synthesis_tasks SET status = 'completed'"
        )
        connection.commit()
    character = segmented.speakers.speakers[1]
    bindings = dict(voices.bindings)
    bindings[character.speaker_id] = replace(
        bindings[character.speaker_id],
        voice_id="changed",
        fingerprint="changed",
    )
    second = planner.plan(
        text,
        segmented.script,
        segmented.speakers,
        replace(voices, bindings=bindings, revision=2),
        performance,
        pronunciation,
        profile,
        previous_plan=first,
    ).plan
    diff = InvalidationService.sync_runtime(runtime, first, second)
    with sqlite3.connect(runtime.path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT task_id, status FROM synthesis_tasks"
            ).fetchall()
        )
    assert all(statuses[item] == "stale" for item in diff.stale_task_ids)
    assert all(statuses[item] == "completed" for item in diff.reusable_task_ids)
