"""UI-ready synthesis-plan preview without executing TTS."""
from __future__ import annotations

from collections import Counter

from domain.v4.production import SynthesisPlan


def synthesis_plan_summary(plan: SynthesisPlan) -> dict:
    by_chapter = Counter(item.chapter_id for item in plan.tasks)
    by_speaker = Counter(item.speaker_id for item in plan.tasks)
    return {
        "revision": plan.revision,
        "task_count": len(plan.tasks),
        "total_measured_length": sum(item.text_length for item in plan.tasks),
        "chapter_tasks": dict(sorted(by_chapter.items())),
        "speaker_tasks": dict(sorted(by_speaker.items())),
        "continuation_tasks": sum(item.continuation for item in plan.tasks),
    }


def synthesis_plan_rows(plan: SynthesisPlan) -> list[list]:
    return [
        [
            item.task_id,
            item.chapter_id,
            item.speaker_id,
            item.voice_id,
            ",".join(item.source_segments),
            item.text_length,
            item.continuation,
            item.pause_after_ms,
        ]
        for item in plan.tasks
    ]
