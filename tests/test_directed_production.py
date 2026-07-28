"""structured_script v3 导演参数进入正式合成、缓存与导出。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from lib import audio_pipeline, directed_synthesis, script_loader, segment_cache


class _FakeEngine:
    def __init__(self):
        self.calls = []

    def synthesize_segment(self, **kwargs):
        self.calls.append(kwargs)
        wavfile.write(
            kwargs["output_path"],
            16000,
            np.ones(1600, dtype=np.int16),
        )
        return kwargs["output_path"]


def _v3_segment(segment_id="1-001", text="夜色降临。"):
    return {
        "id": segment_id,
        "speaker": "旁白",
        "role": "旁白",
        "text": text,
        "emotion": "cold",
        "emotion_strength": 0.6,
        "emo_alpha": 0.6,
        "speech_rate": 0.92,
        "delivery": {
            "speed": 0.92,
            "pitch": -1,
            "intensity": 0.6,
            "breath": "normal",
        },
        "pause_before": 500,
        "pause_after": 500,
        "pauses": [{"position": 2, "duration": 1000}],
    }


def test_v3_loader_preserves_director_metadata():
    raw = {
        "version": "3.0",
        "voices": {"旁白": {"description": "沉稳"}},
        "chapters": [{"id": 1, "title": "一", "segments": [_v3_segment()]}],
    }
    segment = script_loader.from_dict(raw).chapters[0].segments[0]
    assert segment.pitch == -1
    assert segment.breath == "normal"
    assert segment.pause_before == 500
    assert segment.pause_after == 500
    assert segment.pauses == [{"position": 2, "duration": 1000}]


def test_cache_key_changes_with_director_timing_but_v2_key_stays_stable():
    old = segment_cache.segment_cache_key(
        "1-001", "neutral", 1.0, 1.0, None
    )
    # 老公式的固定回归值，确保新增字段不导致全部 v2 缓存失效。
    import hashlib
    expected = "1-001_" + hashlib.md5(
        "neutral|1.0|1.0|None".encode("utf-8")
    ).hexdigest()[:8]
    assert old == expected

    first = segment_cache.segment_cache_key(
        "1-001",
        "neutral",
        1.0,
        1.0,
        None,
        {"pause_after": 500},
    )
    second = segment_cache.segment_cache_key(
        "1-001",
        "neutral",
        1.0,
        1.0,
        None,
        {"pause_after": 1200},
    )
    assert first != second


def test_formal_directed_synthesis_inserts_all_pauses(tmp_path):
    segment = _v3_segment()
    engine = _FakeEngine()
    output = tmp_path / "segment.wav"

    directed_synthesis.synthesize(
        segment,
        "voice.wav",
        str(output),
        emotion="cold",
        emo_alpha=0.6,
        speech_rate=0.92,
        engine=engine,
    )

    assert len(engine.calls) == 2
    rate, data = wavfile.read(output)
    assert rate == 22050
    assert 48000 <= len(data) <= 49000


def _write_v3_project(tmp_path: Path) -> tuple[Path, dict]:
    project = tmp_path / "project"
    segments_dir = project / "segments"
    segments_dir.mkdir(parents=True)
    first = _v3_segment("1-001", "第一段")
    first["pauses"] = []
    first["pause_before"] = 100
    first["pause_after"] = 200
    second = _v3_segment("1-002", "第二段")
    second["pauses"] = []
    second["pause_before"] = 50
    second["pause_after"] = 150
    script = {
        "version": "3.0",
        "meta": {"title": "导演成品"},
        "voices": {"旁白": {"description": "沉稳"}},
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [first, second],
        }],
    }
    (project / "structured_script.json").write_text(
        json.dumps(script, ensure_ascii=False),
        encoding="utf-8",
    )
    for segment in (first, second):
        path = segment_cache.segment_wav_path(
            str(segments_dir),
            segment["id"],
            segment["emotion"],
            segment["emo_alpha"],
            segment["speech_rate"],
            segment.get("pinyin_hints"),
            segment_cache.director_metadata_for(segment),
        )
        # 每个文件 1 秒，假定已经包含各自的前后停顿。
        wavfile.write(path, 16000, np.ones(16000, dtype=np.int16))
    return project, script


def test_v3_export_does_not_add_fixed_segment_silence(tmp_path, monkeypatch):
    project, _ = _write_v3_project(tmp_path)
    monkeypatch.setattr("lib.postprocess.apply_eq", lambda path, enable=False: path)
    monkeypatch.setattr(
        "lib.postprocess.normalize_loudness",
        lambda path, target_lufs=-16.0: path,
    )

    output = audio_pipeline.export_book(str(project), format="wav")

    rate, data = wavfile.read(output)
    assert rate == 16000
    assert len(data) == 32000


def test_v3_subtitles_exclude_boundary_silence(tmp_path):
    project, _ = _write_v3_project(tmp_path)
    paths = audio_pipeline.generate_subtitles(
        str(project),
        formats=("srt",),
    )
    content = Path(paths[0]).read_text(encoding="utf-8")
    assert "00:00:00,100 --> 00:00:00,800" in content
    assert "00:00:01,050 --> 00:00:01,850" in content
