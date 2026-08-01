"""V4 production progress derived from original script segments and outputs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repositories.runtime_repository import RuntimeRepository


@dataclass(frozen=True)
class V4ProgressSnapshot:
    chapters_done: int
    chapters_total: int
    segments_done: int
    segments_total: int


class V4ProgressService:
    @staticmethod
    def snapshot(
        runtime: RuntimeRepository | None,
        project_path: str | Path,
        chapters: list[Any],
        *,
        plan_revision: int | None = None,
    ) -> V4ProgressSnapshot:
        segment_ids_by_chapter = {
            str(chapter.chapter_id): {
                str(segment.segment_id) for segment in chapter.segments
            }
            for chapter in chapters
        }
        all_segment_ids = set().union(*segment_ids_by_chapter.values())
        completed_segment_ids: set[str] = set()
        output_chapter_ids: set[str] = set()
        if runtime is not None:
            completed_segment_ids = runtime.completed_segment_ids(
                segment_ids_by_chapter,
                plan_revision=plan_revision,
            )
            output_chapter_ids = runtime.valid_chapter_output_ids(
                project_path,
                set(segment_ids_by_chapter),
                plan_revision=plan_revision,
            )
            for chapter_id in output_chapter_ids:
                completed_segment_ids.update(segment_ids_by_chapter[chapter_id])

        completed_segment_ids &= all_segment_ids
        chapters_done = len(output_chapter_ids)
        chapters_done += sum(
            bool(segment_ids)
            and chapter_id not in output_chapter_ids
            and segment_ids <= completed_segment_ids
            for chapter_id, segment_ids in segment_ids_by_chapter.items()
        )
        return V4ProgressSnapshot(
            chapters_done=min(chapters_done, len(chapters)),
            chapters_total=len(chapters),
            segments_done=min(len(completed_segment_ids), len(all_segment_ids)),
            segments_total=len(all_segment_ids),
        )
