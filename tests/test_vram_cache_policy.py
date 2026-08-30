"""GPU-free tests for the final threshold-only CUDA cache policy."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib import tts_engine


@pytest.fixture(autouse=True)
def reset_cache_policy_state():
    tts_engine._successful_segments_since_check = 0
    tts_engine._tts = None
    yield
    tts_engine._successful_segments_since_check = 0
    tts_engine._tts = None


def _snapshot(*, free: int, allocated: int, reserved: int) -> dict[str, int | bool]:
    return {
        "available": True,
        "free": free,
        "allocated": allocated,
        "reserved": reserved,
    }


def test_successful_segment_does_not_cleanup_before_the_tenth_check(monkeypatch):
    class Engine:
        def infer(self, **_kwargs):
            return None

    checks = []
    cleanups = []
    tts_engine._tts = Engine()
    monkeypatch.setattr(tts_engine, "gpu_snapshot", lambda: checks.append(1))
    monkeypatch.setattr(
        tts_engine,
        "empty_cache",
        lambda reason="manual": cleanups.append(reason),
    )

    tts_engine.synthesize_segment("短句", "speaker.wav", output_path="out.wav")

    assert checks == []
    assert cleanups == []


def test_memory_check_runs_on_every_tenth_success(monkeypatch):
    checks = []
    cleanups = []
    monkeypatch.setattr(
        tts_engine,
        "gpu_snapshot",
        lambda: checks.append(1) or _snapshot(
            free=4 * 1024**3,
            allocated=1 * 1024**3,
            reserved=2 * 1024**3,
        ),
    )
    monkeypatch.setattr(
        tts_engine,
        "empty_cache",
        lambda reason="manual": cleanups.append(reason),
    )

    for _ in range(9):
        tts_engine._note_segment_success()
    assert checks == []
    tts_engine._note_segment_success()
    for _ in range(20):
        tts_engine._note_segment_success()

    assert len(checks) == 3
    assert cleanups == []


@pytest.mark.parametrize("segments", [30, 50, 100])
def test_normal_thresholds_never_force_cleanup(segments, monkeypatch):
    checks = []
    cleanups = []
    monkeypatch.setattr(
        tts_engine,
        "gpu_snapshot",
        lambda: checks.append(1) or _snapshot(
            free=4 * 1024**3,
            allocated=1 * 1024**3,
            reserved=2 * 1024**3,
        ),
    )
    monkeypatch.setattr(
        tts_engine,
        "empty_cache",
        lambda reason="manual": cleanups.append(reason),
    )

    for _ in range(segments):
        tts_engine._note_segment_success()

    assert len(checks) == segments // tts_engine.CHECK_INTERVAL
    assert cleanups == []


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (
            _snapshot(
                free=2 * 1024**3 - 1,
                allocated=1 * 1024**3,
                reserved=2 * 1024**3,
            ),
            "low_free_vram",
        ),
        (
            _snapshot(
                free=4 * 1024**3,
                allocated=1 * 1024**3,
                reserved=1 * 1024**3 + int(1.5 * 1024**3) + 1,
            ),
            "cached_gap",
        ),
    ],
)
def test_threshold_breach_cleans_once(snapshot, reason, monkeypatch):
    cleanups = []
    monkeypatch.setattr(tts_engine, "gpu_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        tts_engine,
        "empty_cache",
        lambda reason="manual": cleanups.append(reason),
    )

    for _ in range(tts_engine.CHECK_INTERVAL):
        tts_engine._note_segment_success()

    assert cleanups == [reason]


def test_memory_telemetry_failure_does_not_escape(monkeypatch):
    def broken_snapshot():
        raise RuntimeError("mem_get_info unavailable")

    monkeypatch.setattr(tts_engine, "gpu_snapshot", broken_snapshot)
    for _ in range(tts_engine.CHECK_INTERVAL):
        tts_engine._note_segment_success()

    assert tts_engine._successful_segments_since_check == 0


def test_empty_cache_is_safe_without_torch_or_cuda(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert tts_engine.empty_cache(reason="task_end") is False

    calls = []
    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: False,
                empty_cache=lambda: calls.append(1),
            )
        ),
    )
    assert tts_engine.empty_cache(reason="task_end") is False
    assert calls == []


def test_empty_cache_failure_is_safe_and_success_logs_reason(monkeypatch, caplog):
    def broken_empty_cache():
        raise RuntimeError("CUDA cleanup unavailable")

    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                empty_cache=broken_empty_cache,
            )
        ),
    )
    assert tts_engine.empty_cache(reason="oom") is False

    reasons = []
    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                empty_cache=lambda: reasons.append("called"),
            )
        ),
    )
    with caplog.at_level("INFO", logger="lib.tts_engine"):
        assert tts_engine.empty_cache(reason="cached_gap") is True
    assert reasons == ["called"]
    assert "CUDA cache cleanup reason=cached_gap" in caplog.text


def test_engine_recycle_keeps_cleanup_with_reason(monkeypatch):
    reasons = []
    monkeypatch.setattr(tts_engine, "empty_cache", lambda reason="manual": reasons.append(reason))
    monkeypatch.setattr(tts_engine.gc, "collect", lambda: 0)
    tts_engine._tts = object()

    tts_engine.reset_engine()

    assert reasons == ["engine_recycle"]
