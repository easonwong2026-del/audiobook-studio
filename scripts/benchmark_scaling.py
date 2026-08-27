#!/usr/bin/env python3
"""Synthetic 1k/5k/10k project scaling benchmark.

This benchmark does not run TTS.  It measures the large-book control-plane hot
paths that should scale independently of GPU availability: project open,
active-revision inventory, task query, status persistence, and streaming WAV
write.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
import wave
from typing import Any, Callable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import audio_pipeline
from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRepository
from services.quality import QualityService


def _measure(operation: Callable[[], Any]) -> tuple[Any, float, float]:
    tracemalloc.start()
    started = time.perf_counter()
    result = operation()
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, round(elapsed, 6), round(peak / (1024 * 1024), 3)


def _script(segment_count: int, chapter_count: int = 100) -> dict[str, Any]:
    chapters: list[dict[str, Any]] = []
    per_chapter = max((segment_count + chapter_count - 1) // chapter_count, 1)
    segment_index = 0
    for chapter_index in range(chapter_count):
        segments = []
        for _ in range(per_chapter):
            if segment_index >= segment_count:
                break
            segment_index += 1
            segments.append({
                "id": f"{chapter_index + 1:03d}-{segment_index:06d}",
                "role": "旁白",
                "text": f"扩展性测试段落 {segment_index}",
                "emotion": "neutral",
            })
        if segments:
            chapters.append({
                "id": f"{chapter_index + 1:03d}",
                "title": f"第 {chapter_index + 1} 章",
                "segments": segments,
            })
    return {
        "version": "3.0",
        "meta": {"title": f"Synthetic {segment_count}", "author": "Benchmark"},
        "voices": {"旁白": {}},
        "chapters": chapters,
    }


def _tiny_wav(path: str) -> None:
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 160)


def _prepare_revision_audio(project: str, segment_ids: list[str]) -> None:
    """Create one tiny, project-local WAV for each synthetic segment.

    The revision inventory discovers legacy ``{segment_id}.wav`` files and
    bootstraps active revisions in one repository mutation.  Hard links keep
    setup storage small on POSIX while the copy fallback keeps the benchmark
    usable on filesystems (including Windows) that do not support them.
    """
    project_dir = ProjectRepository.get_project_dir(project)
    segments_dir = project_paths.project_dir(project_dir, "segments", create=True)
    sample = os.path.join(segments_dir, "__benchmark_audio_sample.wav")
    _tiny_wav(sample)
    for segment_id in segment_ids:
        target = os.path.join(segments_dir, f"{segment_id}.wav")
        try:
            os.link(sample, target)
        except OSError:
            shutil.copyfile(sample, target)


def _benchmark_size(root: str, segment_count: int, status_updates: int) -> dict[str, Any]:
    project = f"bench_{segment_count}"
    ProjectRepository.create_project_from_data(project, _script(segment_count))

    (_meta, script, _bindings), open_seconds, open_peak = _measure(
        lambda: ProjectRepository.load_project(project)
    )
    segment_ids = [
        str(segment["id"])
        for chapter in script.get("chapters", [])
        for segment in chapter.get("segments", [])
    ]
    _prepare_revision_audio(project, segment_ids)

    # The first inventory discovers real WAV files and bootstraps active
    # revisions; the second measures the normal read path after bootstrap.
    _inventory, revision_seconds, revision_peak = _measure(
        lambda: QualityService.get_active_revision_inventory(project)
    )
    refreshed, revision_refresh_seconds, revision_refresh_peak = _measure(
        lambda: QualityService.get_active_revision_inventory(project)
    )
    tasks, task_seconds, task_peak = _measure(
        lambda: TaskRepository.list_tasks(project=project)
    )
    update_count = min(len(segment_ids), max(int(status_updates), 0))

    def update_statuses() -> None:
        writer = ProjectRepository.segment_status_batch(
            project,
            flush_every=0,
        )
        try:
            for index, segment_id in enumerate(segment_ids[:update_count], 1):
                writer.update(segment_id, "done")
                if index % 100 == 0:
                    writer.checkpoint()
        finally:
            writer.flush()

    _unused, status_seconds, status_peak = _measure(update_statuses)

    sample = os.path.join(root, f"sample_{segment_count}.wav")
    output = os.path.join(root, f"stream_{segment_count}.wav")
    _tiny_wav(sample)
    entries = [(index // max(segment_count // 100, 1), sample)
               for index in range(segment_count)]
    _markers, stream_seconds, stream_peak = _measure(
        lambda: audio_pipeline._write_streaming_book_wav(
            entries,
            output,
            16000,
            director_timing=True,
        )
    )
    return {
        "segments": segment_count,
        "chapters": len(script.get("chapters", [])),
        "project_open_seconds": open_seconds,
        "project_open_peak_python_mb": open_peak,
        "revision_inventory_seconds": revision_seconds,
        "revision_inventory_peak_python_mb": revision_peak,
        "revision_refresh_seconds": revision_refresh_seconds,
        "revision_refresh_peak_python_mb": revision_refresh_peak,
        "active_revisions": refreshed["summary"]["active_revisions"],
        "task_query_seconds": task_seconds,
        "task_query_peak_python_mb": task_peak,
        "task_count": len(tasks),
        "status_updates": update_count,
        "status_update_seconds": status_seconds,
        "status_updates_per_second": (
            round(update_count / status_seconds, 1) if status_seconds else None
        ),
        "status_update_peak_python_mb": status_peak,
        "stream_export_seconds": stream_seconds,
        "stream_export_peak_python_mb": stream_peak,
        "stream_output_bytes": os.path.getsize(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=[1000, 5000, 10000],
    )
    parser.add_argument("--status-updates", type=int, default=1000)
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="audiobook-scaling-") as root:
        os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = root
        ProjectRepository.WORKSPACE_ROOT = os.path.join(root, "projects")
        ProjectRepository.LEGACY_ROOT = os.path.join(root, "legacy")
        ProjectRepository._INITIALIZED = True
        results = [
            _benchmark_size(root, size, arguments.status_updates)
            for size in arguments.sizes
            if size > 0
        ]
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
