"""声音推荐与 AI 导演试听测试。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from services.voice_director import DirectorAuditionService, VoiceDirectorService


def _script():
    return {
        "version": "3.0",
        "meta": {"title": "测试"},
        "voices": {
            "旁白": {"description": "沉稳男中音，纪录片叙事"},
            "小雨": {"description": "年轻清亮女声"},
        },
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [
                {
                    "id": "1-001",
                    "speaker": "旁白",
                    "role": "旁白",
                    "text": "夜色降临。",
                    "emotion": "cold",
                    "emotion_strength": 0.6,
                    "delivery": {
                        "speed": 0.92,
                        "intensity": 0.6,
                        "breath": "normal",
                    },
                    "pause_before": 500,
                    "pause_after": 500,
                    "pauses": [{"position": 2, "duration": 1000}],
                },
                {
                    "id": "1-002",
                    "speaker": "小雨",
                    "role": "小雨",
                    "text": "太好了！",
                    "emotion": "happy",
                    "delivery": {"speed": 1.05, "intensity": 0.7, "breath": "light"},
                    "pause_before": 0,
                    "pause_after": 600,
                    "pauses": [],
                },
            ],
        }],
    }


def test_recommendation_prefers_explainable_matching_voice():
    assets = [
        {
            "name": "沉稳_纪录片男中音.wav",
            "path": "/voices/a.wav",
            "category": "沉稳",
        },
        {
            "name": "清亮_少女.wav",
            "path": "/voices/b.wav",
            "category": "清亮",
        },
    ]
    recommendations = VoiceDirectorService.recommend(
        _script(),
        "旁白",
        assets=assets,
    )
    assert recommendations[0]["voice_name"] == "沉稳_纪录片男中音.wav"
    assert recommendations[0]["score"] > recommendations[1]["score"]
    assert "匹配标签" in recommendations[0]["reasons"]


def test_role_and_segment_choices_filter_by_role():
    roles = VoiceDirectorService.role_choices(_script())
    assert [value for _, value in roles] == ["旁白", "小雨"]
    choices = VoiceDirectorService.segment_choices(_script(), "小雨")
    assert choices == [("1-002 · 小雨 · 太好了！", "1-002")]


class _FakeEngine:
    def __init__(self):
        self.initialized = 0
        self.calls = []

    def init_engine(self):
        self.initialized += 1

    def synthesize_segment(self, **kwargs):
        self.calls.append(kwargs)
        # 0.1 秒、16 kHz，试听服务会归一到 22.05 kHz。
        wavfile.write(
            kwargs["output_path"],
            16000,
            np.ones(1600, dtype=np.int16),
        )
        return kwargs["output_path"]


def test_audition_applies_delivery_pauses_and_cache(tmp_path, monkeypatch):
    script_path = tmp_path / "structured_script.json"
    script_path.write_text(
        json.dumps(_script(), ensure_ascii=False),
        encoding="utf-8",
    )
    voice_path = tmp_path / "沉稳_纪录片男中音.wav"
    wavfile.write(voice_path, 16000, np.ones(1600, dtype=np.int16))
    assets = [{
        "name": voice_path.name,
        "path": str(voice_path),
        "category": "沉稳",
    }]
    monkeypatch.setattr(
        "services.voice_director.config.get_preview_dir",
        lambda: str(tmp_path / "preview"),
    )
    engine = _FakeEngine()

    output, cached = DirectorAuditionService.synthesize(
        str(script_path),
        "1-001",
        voice_path.name,
        engine=engine,
        assets=assets,
    )

    assert not cached
    assert Path(output).is_file()
    assert engine.initialized == 1
    assert len(engine.calls) == 2
    assert all(call["emotion"] == "cold" for call in engine.calls)
    assert all(call["emo_alpha"] == 0.6 for call in engine.calls)
    assert all(call["speech_rate"] == 0.92 for call in engine.calls)
    rate, data = wavfile.read(output)
    assert rate == 22050
    # 前 500ms + 两段各约 100ms + 内部 1000ms + 后 500ms。
    assert 48000 <= len(data) <= 49000

    output_again, cached_again = DirectorAuditionService.synthesize(
        str(script_path),
        "1-001",
        voice_path.name,
        engine=engine,
        assets=assets,
    )
    assert cached_again
    assert output_again == output
    assert len(engine.calls) == 2
