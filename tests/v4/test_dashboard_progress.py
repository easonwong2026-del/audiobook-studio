from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repositories.runtime_repository import RuntimeRepository
from services.invalidation_service import InvalidationService
from services.v4_progress import V4ProgressService


def _chapters():
    return [
        SimpleNamespace(
            chapter_id="chapter-1",
            segments=[
                SimpleNamespace(segment_id="segment-1"),
                SimpleNamespace(segment_id="segment-2"),
            ],
        ),
        SimpleNamespace(
            chapter_id="chapter-2",
            segments=[SimpleNamespace(segment_id="segment-3")],
        ),
        SimpleNamespace(
            chapter_id="chapter-3",
            segments=[SimpleNamespace(segment_id="segment-4")],
        ),
    ]


def _runtime_with_plan(tmp_path: Path):
    chapters = _chapters()
    runtime = RuntimeRepository(tmp_path / "runtime.db")
    runtime.initialize()
    tasks = []
    for chapter in chapters:
        for segment in chapter.segments:
            segment_id = segment.segment_id
            tasks.append(
                SimpleNamespace(
                    task_id=f"task-{segment_id}",
                    chapter_id=chapter.chapter_id,
                    speaker_id="narrator",
                    voice_id="voice-narrator",
                    source_segments=[segment_id],
                    actual_text=segment_id,
                    text_length=len(segment_id),
                    input_fingerprint=f"fingerprint-{segment_id}",
                )
            )
    plan = SimpleNamespace(revision=1, tasks=tasks)
    InvalidationService.sync_runtime(runtime, None, plan)
    return runtime, chapters


def _snapshot(runtime, chapters, project_path):
    return V4ProgressService.snapshot(
        runtime,
        project_path,
        chapters,
        plan_revision=1,
    )


def test_partial_first_chapter_does_not_count_as_completed(tmp_path):
    runtime, chapters = _runtime_with_plan(tmp_path)
    runtime.complete_task("task-segment-1", "audio/segment-1.wav")

    progress = _snapshot(runtime, chapters, tmp_path)

    assert progress.chapters_done == 0
    assert progress.segments_done == 1


def test_first_chapter_counts_only_after_all_original_segments_complete(tmp_path):
    runtime, chapters = _runtime_with_plan(tmp_path)
    runtime.complete_task("task-segment-1", "audio/segment-1.wav")
    runtime.complete_task("task-segment-2", "audio/segment-2.wav")

    progress = _snapshot(runtime, chapters, tmp_path)

    assert progress.chapters_done == 1
    assert progress.segments_done == 2


def test_split_children_count_as_one_original_segment(tmp_path):
    runtime, chapters = _runtime_with_plan(tmp_path)
    parent = runtime.claim_next_task()
    assert parent is not None
    runtime.split_task(
        parent,
        [
            {
                "task_id": "task-segment-1.split1",
                "cache_key": "child-1",
                "actual_text": "segment-1-a",
                "text_length": 11,
            },
            {
                "task_id": "task-segment-1.split2",
                "cache_key": "child-2",
                "actual_text": "segment-1-b",
                "text_length": 11,
            },
        ],
        error_type="oom",
    )
    runtime.complete_task("task-segment-1.split1", "audio/segment-1-a.wav")
    runtime.complete_task("task-segment-1.split2", "audio/segment-1-b.wav")

    progress = _snapshot(runtime, chapters, tmp_path)

    assert progress.segments_done == 1
    assert progress.segments_done <= progress.segments_total


def test_completed_task_rows_never_exceed_original_segment_total(tmp_path):
    runtime, chapters = _runtime_with_plan(tmp_path)
    parent = runtime.claim_next_task()
    assert parent is not None
    children = [
        {
            "task_id": f"task-segment-1.split{index}",
            "cache_key": f"child-{index}",
            "actual_text": f"part-{index}",
            "text_length": 6,
        }
        for index in range(1, 5)
    ]
    runtime.split_task(parent, children, error_type="oom")
    for child in children:
        runtime.complete_task(child["task_id"], f"audio/{child['task_id']}.wav")

    progress = _snapshot(runtime, chapters, tmp_path)

    assert progress.segments_done == 1
    assert progress.segments_done <= progress.segments_total


def test_no_completed_results_means_zero_progress(tmp_path):
    runtime, chapters = _runtime_with_plan(tmp_path)

    progress = _snapshot(runtime, chapters, tmp_path)

    assert progress.chapters_done == 0
    assert progress.segments_done == 0


def test_valid_current_chapter_output_is_preferred_and_marks_its_segments_done(tmp_path):
    runtime, chapters = _runtime_with_plan(tmp_path)
    output = tmp_path / "audio/chapters/chapter-1.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"valid audio")
    runtime.save_chapter_output(
        "chapter-1",
        1,
        "audio/chapters/chapter-1.wav",
        "chapter-fingerprint",
        1.0,
    )

    progress = _snapshot(runtime, chapters, tmp_path)

    assert progress.chapters_done == 1
    assert progress.segments_done == 2
