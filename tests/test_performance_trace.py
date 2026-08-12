"""Regression tests for the independent production performance trace API."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from services.performance_trace import PerformanceTrace, TraceSession


_PYTHON_310_PRODUCTION_FILES = (
    "lib/directed_synthesis.py",
    "lib/queue.py",
    "lib/tts_engine.py",
    "mcp_server/models.py",
    "mcp_server/server.py",
    "mcp_server/tools/performance.py",
    "mcp_server/tools/projects.py",
    "services/engine_capabilities.py",
    "services/performance_trace.py",
    "services/production_runtime.py",
    "services/synthesis.py",
    "services/workflow.py",
)
_PYTHON_311_ONLY_TYPING_NAMES = {
    "Self",
    "NotRequired",
    "Required",
    "LiteralString",
    "Never",
}


def test_changed_production_modules_use_python310_typing_apis():
    root = Path(__file__).parents[1]
    for relative_path in _PYTHON_310_PRODUCTION_FILES:
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "typing"
            for alias in node.names
        }
        assert not imported_names & _PYTHON_311_ONLY_TYPING_NAMES, relative_path


def test_trace_lifecycle_accepts_zero_as_a_valid_start_time():
    ticks = iter([0.0, 0.0, 0.25, 0.25, 1.0])
    trace = PerformanceTrace(clock=lambda: next(ticks), gpu_sampler=None)

    trace.start_task()
    trace.start_segment("1-001")
    trace.end_segment("1-001")
    trace.end_task()

    assert trace.summary()["timings"]["segment_total"] == pytest.approx(0.25)
    assert trace.summary()["timings"]["task_total"] == pytest.approx(1.0)


def test_trace_accumulates_task_chapter_segment_and_phase_timings(tmp_path):
    ticks = iter([10.0, 10.5, 11.0, 11.25, 12.0, 12.5])
    trace = PerformanceTrace(
        task_id="task-1",
        project="book",
        clock=lambda: next(ticks),
        gpu_sampler=lambda: {"available": False},
    )

    trace.start_task()
    trace.start_chapter("1")
    segment = trace.start_segment("1-001", chapter_id="1")
    segment.record_cache(hit=False, lookup_elapsed=0.01)
    segment.add_timing("speaker_resolution", 0.02)
    segment.record_infer(0.30)
    segment.record_publish(0.04)
    segment.record_status(0.01, status="done")
    segment.close()
    trace.end_chapter("1")
    trace.end_task()

    summary = trace.summary()
    assert summary["trace_available"] is True
    assert summary["segments"] == 1
    assert summary["cache_misses"] == 1
    assert summary["infer_calls"] == 1
    assert summary["timings"]["engine_infer"] == 0.3
    assert summary["scope_timings"]["chapter"]["chapter_total"]["count"] == 1
    assert summary["segment_stats"]["count"] == 1
    assert summary["gpu_snapshots"][0]["boundary"] == "task_start"

    path = tmp_path / "logs" / "performance.json"
    assert trace.checkpoint(path)["infer_calls"] == 1
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["segment_details"][0]["segment_id"] == "1-001"
    assert persisted["segment_details"][0]["last_status"] == "done"


def test_multipart_and_retry_counts_are_distinct_from_logical_parts():
    trace = PerformanceTrace(gpu_sampler=None)
    trace.start_task()
    session = trace.start_segment("1-001", chapter_id="1")
    session.record_infer(0.10, part_index=0)
    session.record_infer(0.20, part_index=1)
    session.record_infer(
        0.05,
        part_index=1,
        success=False,
        error=RuntimeError("recoverable OOM"),
    )
    session.record_infer(0.12, part_index=1)
    session.close()
    trace.end_task()

    detail = trace.segment_details()[0]
    assert detail["directed_part_count"] == 2
    assert detail["infer_call_count"] == 4
    assert detail["infer_elapsed_total"] == pytest.approx(0.47)
    assert trace.summary()["inference"]["calls_per_segment"] == pytest.approx(4.0)
    assert trace.summary()["failures"] == 1


def test_cache_hit_does_not_imply_an_infer_call():
    trace = PerformanceTrace(gpu_sampler=None)
    with trace.start_segment("1-001", chapter_id="1") as segment:
        segment.record_cache(hit=True, lookup_elapsed=0.002)
        segment.record_status(0.001, status="done")

    summary = trace.summary()
    assert summary["cache_hits"] == 1
    assert summary["cache_misses"] == 0
    assert summary["infer_calls"] == 0
    assert trace.segment_details()[0]["infer_call_count"] == 0


def test_bound_trace_session_is_suitable_for_future_directed_context():
    trace = PerformanceTrace(gpu_sampler=None)
    context = trace.session(chapter_id="3", segment_id="3-007")
    assert isinstance(context, TraceSession)
    with context as segment:
        segment.add_timing("directed_synthesis_total", 0.8)
        segment.record_infer(0.4, part_index=0)
        segment.record_infer(0.3, part_index=1)

    detail = trace.segment_details()[0]
    assert detail["chapter_id"] == "3"
    assert detail["directed_part_count"] == 2
    assert detail["infer_call_count"] == 2


def test_trace_failure_isolation_covers_gpu_and_persist_failures(tmp_path):
    def broken_gpu():
        raise RuntimeError("CUDA probe unavailable")

    trace = PerformanceTrace(gpu_sampler=broken_gpu)
    trace.start_task()
    trace.record_infer("missing-segment", 0.1)
    trace.end_task()

    # A broken diagnostics callback and an unwritable target must not escape.
    assert trace.summary()["trace_errors"] >= 1
    assert trace.persist(tmp_path / "a-file-under-a-directory" / "x.json") is True
    assert trace.persist(tmp_path) is False
    assert trace.summary()["infer_calls"] == 1


def test_disabled_trace_is_a_noop_with_stable_summary():
    trace = PerformanceTrace(enabled=False, gpu_sampler=lambda: (_ for _ in ()).throw(RuntimeError()))
    with trace.start_segment("1-001") as segment:
        segment.record_cache(hit=False)
        segment.record_infer(1.0)
        segment.record_failure("engine_infer")
    summary = trace.summary()
    assert summary["trace_available"] is False
    assert summary["segments"] == 0
    assert trace.persist() is False


def test_task_end_closes_open_scopes_and_records_segment_gap():
    trace = PerformanceTrace(gpu_sampler=None)
    trace.start_task()
    trace.start_chapter("1")
    trace.start_segment("1-001", chapter_id="1")
    trace.end_segment("1-001", chapter_id="1")
    trace.start_segment("1-002", chapter_id="1")
    trace.end_task()

    summary = trace.summary()
    assert summary["segment_stats"]["count"] == 2
    assert summary["timings"]["segment_gap"] >= 0
    assert summary["scope_timings"]["chapter"]["chapter_total"]["count"] == 1
