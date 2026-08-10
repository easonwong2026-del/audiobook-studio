#!/usr/bin/env python3
"""Measure the durable Formal Export path on synthetic project audio.

This intentionally uses WAV output so it can run without a real FFmpeg/TTS
installation.  It measures wall time, process peak RSS (when available),
Python allocation peak, and the published artifact size.  The benchmark is a
control-plane/resource diagnostic, not a real IndexTTS2 or listening test.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import tracemalloc

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no stdlib resource module
    resource = None

import numpy as np
from scipy.io import wavfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from services.export import ExportService
from services.quality import QualityService
from services.production_runtime import ProductionRuntimeClient


def _script(count: int) -> dict:
    segments = [
        {"id": f"001-{index:06d}", "role": "旁白", "text": "benchmark"}
        for index in range(1, count + 1)
    ]
    return {
        "meta": {"title": f"Formal Export {count}", "author": "Benchmark"},
        "voices": {"旁白": {}},
        "chapters": [{"id": "001", "title": "Benchmark", "segments": segments}],
    }


def _rss_mb() -> float | None:
    if resource is None:
        return None
    try:
        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # macOS reports bytes; Linux reports KiB.
        if sys.platform == "darwin":
            value /= 1024.0 * 1024.0
        else:
            value /= 1024.0
        return round(value, 3)
    except (AttributeError, OSError, ValueError):
        return None


def _wait_export(project: str, export_id: str, timeout: float = 3600.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = ExportService.get_export_task(project, export_id)
        if record.get("status") in {"done", "error", "interrupted", "cancelled"}:
            return record
        time.sleep(0.05)
    raise TimeoutError(export_id)


def benchmark(segment_count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="audiobook-formal-export-") as root:
        os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = root
        os.environ["AUDIOBOOK_STUDIO_RUNTIME_MODE"] = "inline"
        ProjectRepository.WORKSPACE_ROOT = os.path.join(root, "projects")
        ProjectRepository.LEGACY_ROOT = os.path.join(root, "legacy")
        ProjectRepository._INITIALIZED = True
        project = f"formal_{segment_count}"
        ProjectRepository.create_project_from_data(project, _script(segment_count))
        project_dir = ProjectRepository.get_project_dir(project)
        segments_dir = project_paths.project_dir(project_dir, "segments", create=True)
        sample = os.path.join(segments_dir, "sample.wav")
        # Keep the synthetic segment above Technical QA's minimum duration.
        wavfile.write(sample, 16000, np.full(4000, 5000, dtype=np.int16))
        segment_ids = [
            f"001-{index:06d}" for index in range(1, segment_count + 1)
        ]
        for segment_id in segment_ids:
            target = os.path.join(segments_dir, f"{segment_id}.wav")
            try:
                os.link(sample, target)
            except OSError:
                with open(sample, "rb") as source, open(target, "wb") as destination:
                    destination.write(source.read())
            ProjectRepository.update_segment_status(project, segment_id, "done")
        QualityService.get_quality_report(project)
        QualityService.run_technical_qa_batch(project, segment_ids)
        state = QualityRepository.load(project)
        reviews = []
        for revision_id in state.get("active_revisions", {}).values():
            reviews.append((revision_id, {"review_status": "passed"}))
        QualityRepository.save_human_reviews_batch(project, reviews)

        rss_before = _rss_mb()
        tracemalloc.start()
        started = time.perf_counter()
        submitted = ExportService.start_export(project, "wav")
        finished = _wait_export(project, submitted["export_id"])
        wall = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        artifact_bytes = sum(
            int(item.get("size", 0) or 0)
            for item in (finished.get("outputs") or [])
            if isinstance(item, dict)
        )
        result = {
            "segments": segment_count,
            "status": finished.get("status"),
            "wall_seconds": round(wall, 6),
            "rss_before_mb": rss_before,
            "rss_peak_mb": _rss_mb(),
            "python_peak_mb": round(peak / (1024 * 1024), 3),
            "artifact_bytes": artifact_bytes,
        }
        ProductionRuntimeClient.reset_inline()
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=int, nargs="+", default=[1000])
    args = parser.parse_args()
    print(json.dumps({"results": [benchmark(size) for size in args.segments if size > 0]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
