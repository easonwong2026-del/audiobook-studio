"""Fake-engine regression harness for trace semantics, not performance claims."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from services.performance_trace import PerformanceTrace


class FakeOOM(RuntimeError):
    code = "TTS_ENGINE_OOM_EXHAUSTED"


@dataclass
class FakeEngine:
    calls: int = 0
    fail_once_for: set[str] | None = None

    def infer(self, segment_id: str, part_index: int) -> None:
        self.calls += 1
        key = f"{segment_id}:{part_index}"
        if self.fail_once_for and key in self.fail_once_for:
            self.fail_once_for.remove(key)
            raise FakeOOM(key)


def run_fake_book(
    *,
    parts_by_segment: dict[str, int],
    cache_hits: set[str] | None = None,
    fail_once_for: set[str] | None = None,
    paused: bool = False,
    cancelled: bool = False,
):
    engine = FakeEngine(fail_once_for=set(fail_once_for or set()))
    trace = PerformanceTrace(gpu_sampler=None)
    trace.start_task("fake-task", "fake-book")
    trace.start_chapter("1")
    cache_hits = cache_hits or set()
    for segment_id, part_count in parts_by_segment.items():
        with trace.start_segment(segment_id, chapter_id="1") as segment:
            segment.record_cache(hit=segment_id in cache_hits)
            if segment_id in cache_hits:
                segment.record_status(0.001, status="done")
                continue
            if paused:
                segment.record_event("pause", data={"segment_id": segment_id})
                segment.record_event("resume", data={"segment_id": segment_id})
            for part_index in range(part_count):
                while True:
                    try:
                        engine.infer(segment_id, part_index)
                    except FakeOOM as exc:
                        segment.record_infer(
                            0.01,
                            part_index=part_index,
                            success=False,
                            error=exc,
                        )
                        segment.record_event("recovery", data={"code": exc.code})
                        continue
                    segment.record_infer(0.02, part_index=part_index)
                    break
            if cancelled:
                segment.record_event("cancel", data={"segment_id": segment_id})
                segment.record_status(0.001, status="cancelled")
            else:
                segment.record_publish(0.001)
                segment.record_status(0.001, status="done")
    trace.end_chapter("1")
    trace.end_task()
    return engine, trace


def test_one_segment_one_infer_and_cache_hit_zero_infers():
    engine, trace = run_fake_book(
        parts_by_segment={"1-001": 1, "1-002": 1},
        cache_hits={"1-002"},
    )
    assert engine.calls == 1
    assert trace.summary()["segments"] == 2
    assert trace.summary()["cache_hits"] == 1
    assert trace.summary()["infer_calls"] == 1
    assert trace.segment_details()[1]["infer_call_count"] == 0


def test_multipart_amplification_and_recovery_counts_real_calls():
    engine, trace = run_fake_book(
        parts_by_segment={"1-001": 3},
        fail_once_for={"1-001:1"},
        paused=True,
    )
    summary = trace.summary()
    detail = trace.segment_details()[0]
    assert engine.calls == 4  # 3 parts plus one recoverable retry
    assert detail["directed_part_count"] == 3
    assert detail["infer_call_count"] == 4
    assert detail["failures"] == 1
    assert any(event["name"] == "recovery" for event in summary["events"])
    assert any(event["name"] == "pause" for event in summary["events"])


def test_cancel_keeps_trace_closed_and_does_not_fake_publish():
    _, trace = run_fake_book(
        parts_by_segment={"1-001": 2},
        cancelled=True,
    )
    detail = trace.segment_details()[0]
    assert detail["last_status"] == "cancelled"
    assert "atomic_publish" not in detail["timings"]
    assert any(event["name"] == "cancel" for event in trace.summary()["events"])


def test_fake_harness_is_not_a_production_speed_measurement():
    _, trace = run_fake_book(parts_by_segment={"1-001": 1})
    summary = trace.summary()
    assert summary["timings"]["engine_infer"] == pytest.approx(0.02)
    assert summary["inference"]["median_seconds"] == pytest.approx(0.02)
