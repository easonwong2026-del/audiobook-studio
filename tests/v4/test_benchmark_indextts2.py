from __future__ import annotations

import wave
from pathlib import Path

from tools import benchmark_indextts2 as benchmark


class FakeEngine:
    def infer(
        self,
        spk_audio_prompt,
        text,
        output_path,
        max_text_tokens_per_segment,
        use_emo_text,
        emo_text,
        emo_alpha,
        use_random,
        do_sample,
    ):
        with wave.open(output_path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(22050)
            handle.writeframes(b"\x00\x00" * 100)


def _safe_run(tier: int) -> dict:
    return {
        "tier": tier,
        "success": True,
        "error_type": None,
        "error_message": None,
        "max_memory_allocated": 7 * 1024**3,
        "total_vram": 12 * 1024**3,
        "free_vram_after": 2 * 1024**3,
        "memory_reserved_after": 7 * 1024**3,
    }


def test_benchmark_covers_required_text_categories_and_sanitized_fields(tmp_path):
    assert {item[0] for item in benchmark.SAMPLES} == {
        "chinese_narration",
        "chinese_dialogue",
        "mixed_language",
        "numbers_dates",
        "pinyin_hint",
        "long_unpunctuated",
        "auto_emotion",
    }
    output = tmp_path / "sample.wav"
    result = benchmark._invoke(
        FakeEngine(),
        "不应写入报告的完整测试文本",
        output,
        Path("voice.wav"),
        40,
        "chinese_narration",
    )
    assert result["success"] is True
    assert result["text_chars"] > 0
    assert result["text_tokens"] > 0
    assert result["audio_duration"] > 0
    assert "text" not in result
    assert "不应写入" not in str(result)


def test_recommendation_never_raises_above_100_and_downgrades_after_oom():
    runs = [_safe_run(tier) for tier in benchmark.TOKEN_TIERS]
    assert benchmark._recommend(runs)["max_text_tokens"] == 100

    for item in runs:
        if item["tier"] == 100:
            item.update(
                {
                    "success": False,
                    "error_type": "OutOfMemoryError",
                    "error_message": "CUDA out of memory",
                }
            )
    recommendation = benchmark._recommend(runs)
    assert recommendation["max_text_tokens"] == 80
    assert recommendation["tiers"]["100"]["safe"] is False
