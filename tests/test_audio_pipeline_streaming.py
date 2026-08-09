"""The whole-book WAV path must not concatenate all segment arrays in RAM."""
from __future__ import annotations

import json

import numpy as np
from scipy.io import wavfile

from lib import audio_pipeline, postprocess


def test_export_book_streams_segments_without_numpy_concatenate(tmp_path, monkeypatch):
    project = tmp_path / "book"
    segments = project / "segments"
    segments.mkdir(parents=True)
    script = {
        "meta": {"title": "streaming"},
        "voices": {"旁白": {}},
        "chapters": [{
            "id": "1",
            "title": "一",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "一"},
                {"id": "1-002", "role": "旁白", "text": "二"},
            ],
        }],
    }
    (project / "structured_script.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    wavfile.write(segments / "1-001.wav", 16000, np.full(800, 1000, dtype=np.int16))
    wavfile.write(segments / "1-002.wav", 22050, np.full(1100, 2000, dtype=np.int16))
    monkeypatch.setattr(
        audio_pipeline.np,
        "concatenate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("整书导出不得调用 np.concatenate")
        ),
    )
    monkeypatch.setattr(postprocess, "apply_eq", lambda path, enable=False: path)
    monkeypatch.setattr(
        postprocess,
        "normalize_loudness",
        lambda path, target_lufs=-16.0: path,
    )
    output = audio_pipeline.export_book(str(project), format="wav")
    rate, data = wavfile.read(output)
    assert rate == 16000
    assert len(data) > 1600
