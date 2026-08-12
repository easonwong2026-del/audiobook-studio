"""Thin real-boundary trace hooks with a fake IndexTTS infer implementation."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile

from lib import directed_synthesis, tts_engine
from services.performance_trace import PerformanceTrace


class _FakeTTS:
    def __init__(self):
        self.calls = 0

    def infer(self, **kwargs):
        self.calls += 1
        wavfile.write(
            kwargs["output_path"],
            16000,
            np.zeros(1600, dtype=np.int16),
        )


def test_directed_synthesis_records_real_infer_boundary_and_parts(tmp_path, monkeypatch):
    fake = _FakeTTS()
    monkeypatch.setattr(tts_engine, "_tts", fake)
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)
    monkeypatch.setattr(tts_engine, "get_speaker_embedding", lambda _path: None)

    speaker = tmp_path / "speaker.wav"
    wavfile.write(speaker, 16000, np.zeros(1600, dtype=np.int16))
    output = tmp_path / "segment.wav"
    trace = PerformanceTrace(gpu_sampler=None)
    trace.start_task("task-1", "book")
    segment = {
        "id": "1-001",
        "text": "第一句。第二句。",
        "pause_before": 0,
        "pause_after": 0,
        "pauses": [{"position": 3, "duration": 100}],
    }

    directed_synthesis.synthesize(
        segment,
        str(speaker),
        str(output),
        emotion="neutral",
        emo_alpha=1.0,
        speech_rate=1.0,
        engine=tts_engine,
        trace=trace,
        trace_chapter_id="1",
    )
    trace.end_task()

    summary = trace.summary()
    detail = trace.segment_details()[0]
    assert fake.calls == 2
    assert summary["infer_calls"] == 2
    assert summary["timings"]["engine_infer"] > 0
    assert summary["timings"]["directed_synthesis_total"] > 0
    assert summary["timings"]["wav_compose"] > 0
    assert detail["directed_part_count"] == 2
    assert detail["infer_call_count"] == 2
    assert Path(output).is_file()
