"""AI Script Director 第一阶段质量守卫。"""
from __future__ import annotations

import json

import pytest

from ai.providers.base import ScriptAnalysisProvider
from lib import script_loader
from services.script_director import ScriptDirectorService


class _JumpingSpeedProvider(ScriptAnalysisProvider):
    name = "fake"

    def extract_characters(self, text):
        return ["旁白"]

    def generate_segments(self, text, characters):
        return []

    def analyze_script(self, text, *, title="", author=""):
        return {
            "segments": [
                {"speaker": "旁白", "text": "第一段", "delivery": {"speed": 1.3}},
                {"speaker": "旁白", "text": "第二段", "delivery": {"speed": 0.8}},
                {"speaker": "旁白", "text": "第三段", "delivery": {"speed": 1.4}},
            ]
        }


def _segments(script):
    return [
        segment
        for chapter in script["chapters"]
        for segment in chapter["segments"]
    ]


def test_continuous_dialogue_is_one_natural_speaking_action():
    text = """第一章

张三冷冷地说道：“你来了。”

“我等你很久了。”

“但是现在已经晚了。”
"""
    segments = _segments(ScriptDirectorService().analyze_text(text))
    assert len(segments) == 1
    assert segments[0]["speaker"] == "张三"
    assert segments[0]["text"].count("”") == 3


def test_emotion_is_inherited_across_continuous_dialogue():
    text = """张三冷冷地说道：“你来了。”

“我等你很久了。”

“但是现在已经晚了。”
"""
    segment = _segments(ScriptDirectorService().analyze_text(text))[0]
    assert segment["emotion"] == "cold"


def test_pronoun_speaker_and_confidence_are_resolved_from_context():
    text = """李云龙走过去。

他看着敌人说道：
“你们几个，还不够资格让我害怕。”
"""
    segments = _segments(ScriptDirectorService().analyze_text(text))
    dialogue = segments[-1]
    assert dialogue["speaker"] == "李云龙"
    assert dialogue["emotion"] == "confident"


def test_long_sentence_uses_pause_metadata_without_fragmenting_segment():
    text = (
        "旁白说道：“夜色逐渐压下来，远处的风穿过废弃车站，"
        "卷起地上的旧报纸，也把所有人没有说出口的话，带向了看不见的远方。”"
    )
    segments = _segments(ScriptDirectorService().analyze_text(text))
    assert len(segments) == 1
    assert segments[0]["pauses"]
    assert all(pause["duration"] >= 100 for pause in segments[0]["pauses"])


def test_speed_is_clamped_and_smoothed():
    script = ScriptDirectorService(_JumpingSpeedProvider()).analyze_text("文本")
    speeds = [segment["delivery"]["speed"] for segment in _segments(script)]
    assert all(0.85 <= speed <= 1.15 for speed in speeds)
    assert all(abs(right - left) <= 0.1001 for left, right in zip(speeds, speeds[1:]))


def test_txt_input_writes_v3_and_is_compatible_with_existing_loader(tmp_path):
    source = tmp_path / "novel.txt"
    output = tmp_path / "structured_script.json"
    source.write_text("第一章\n\n张三说道：“开始吧。”", encoding="utf-8")

    script = ScriptDirectorService().analyze_txt(
        str(source),
        output_path=str(output),
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert script["version"] == saved["version"] == "3.0"
    assert saved["meta"]["title"] == "novel"
    parsed = script_loader.load_script(str(output))
    assert script_loader.validate_script(parsed) == []
    assert parsed.chapters[0].segments[0].role == "张三"


def test_existing_loader_accepts_native_v3_speaker_and_delivery_fields():
    parsed = script_loader.from_dict({
        "version": "3.0",
        "voices": {"张三": {"description": "角色"}},
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [{
                "id": "1-001",
                "speaker": "张三",
                "text": "开始。",
                "emotion": "confident",
                "emotion_strength": 0.7,
                "delivery": {"speed": 0.95, "intensity": 0.7},
            }],
        }],
    })
    assert script_loader.validate_script(parsed) == []
    segment = parsed.chapters[0].segments[0]
    assert segment.role == "张三"
    assert segment.speech_rate == 0.95
    assert segment.emo_alpha == 0.7


def test_first_phase_rejects_non_txt_and_empty_text(tmp_path):
    with pytest.raises(ValueError, match="仅接受 .txt"):
        ScriptDirectorService().analyze_txt(str(tmp_path / "novel.md"))
    with pytest.raises(ValueError, match="内容为空"):
        ScriptDirectorService().analyze_text("  \n")
