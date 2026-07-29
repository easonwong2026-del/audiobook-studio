"""Compute local plan invalidation without touching unrelated tasks."""
from __future__ import annotations

from dataclasses import dataclass

from domain.v4.production import SynthesisPlan
from repositories.runtime_repository import RuntimeRepository


@dataclass(frozen=True)
class PlanInvalidation:
    reusable_task_ids: list[str]
    stale_task_ids: list[str]
    new_task_ids: list[str]
    stale_chapter_ids: list[str]


class InvalidationService:
    @staticmethod
    def compare(
        previous: SynthesisPlan | None,
        current: SynthesisPlan,
    ) -> PlanInvalidation:
        if previous is None:
            return PlanInvalidation(
                reusable_task_ids=[],
                stale_task_ids=[],
                new_task_ids=[item.task_id for item in current.tasks],
                stale_chapter_ids=[],
            )
        old = {item.task_id: item for item in previous.tasks}
        new = {item.task_id: item for item in current.tasks}
        reusable = sorted(
            task_id
            for task_id in old.keys() & new.keys()
            if old[task_id].input_fingerprint == new[task_id].input_fingerprint
        )
        changed_common = {
            task_id
            for task_id in old.keys() & new.keys()
            if old[task_id].input_fingerprint != new[task_id].input_fingerprint
        }
        removed = old.keys() - new.keys()
        stale = sorted(changed_common | removed)
        added_or_changed = sorted((new.keys() - old.keys()) | changed_common)
        chapters = sorted({old[item].chapter_id for item in stale})
        return PlanInvalidation(
            reusable_task_ids=reusable,
            stale_task_ids=stale,
            new_task_ids=added_or_changed,
            stale_chapter_ids=chapters,
        )

    @classmethod
    def sync_runtime(
        cls,
        runtime: RuntimeRepository,
        previous: SynthesisPlan | None,
        current: SynthesisPlan,
    ) -> PlanInvalidation:
        diff = cls.compare(previous, current)
        runtime.sync_synthesis_plan(
            current.revision,
            [
                {
                    "task_id": item.task_id,
                    "chapter_id": item.chapter_id,
                    "speaker_id": item.speaker_id,
                    "voice_id": item.voice_id,
                    "actual_text": item.actual_text,
                    "text_length": item.text_length,
                    "input_fingerprint": item.input_fingerprint,
                }
                for item in current.tasks
            ],
            diff.stale_task_ids,
        )
        return diff
