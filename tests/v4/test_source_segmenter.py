from __future__ import annotations

import pytest

from services.source_segmenter import SourceSegmenter


def _segments(text):
    result = SourceSegmenter().segment(text)
    return result, [item for chapter in result.script.chapters for item in chapter.segments]


def test_plain_narration_is_confirmed_narrator():
    result, segments = _segments("风吹过树林。")
    assert len(segments) == 1
    assert segments[0].kind == "narration"
    assert segments[0].speaker_id == "narrator"
    assert result.speakers.speakers[0].locked is True


@pytest.mark.parametrize(
    "text",
    [
        "小明说：“你好。”夜色渐深。",
        "“你好。”小明说道。夜色渐深。",
    ],
)
def test_explicit_speaker_before_or_after_quote(text):
    result, segments = _segments(text)
    dialogue = next(item for item in segments if item.kind == "dialogue")
    assert dialogue.status == "confirmed"
    speaker = next(
        item for item in result.speakers.speakers if item.speaker_id == dialogue.speaker_id
    )
    assert speaker.display_name == "小明"


def test_unknown_dialogue_does_not_block_creation():
    _, segments = _segments("他说：“无人知道。”")
    dialogue = next(item for item in segments if item.kind == "dialogue")
    assert dialogue.status == "unresolved"
    assert dialogue.speaker_id is None


@pytest.mark.parametrize(
    "text",
    [
        "所谓“命运”，不过是自己的选择。",
        "他低声说：“我们走吧。”",
        "门外传来一个声音：“有人吗？”",
        "年轻人连声道谢：“谢谢。”",
        "系统提示：“操作已完成。”",
    ],
)
def test_ambiguous_quotes_never_create_formal_speakers(text):
    result, segments = _segments(text)
    names = {item.display_name for item in result.speakers.speakers}
    assert names == {"旁白"}
    dialogue = next(item for item in segments if item.kind == "dialogue")
    assert dialogue.status == "unresolved"
    assert dialogue.speaker_id is None
    assert dialogue.dialogue_type in {"suspected_dialogue", "quotation"}


@pytest.mark.parametrize(
    "text",
    [
        "林晚说道：“我们走吧。”",
        "“我不同意。”顾川回答。",
    ],
)
def test_high_confidence_dialogue_keeps_named_rule_speaker(text):
    _result, segments = _segments(text)
    dialogue = next(item for item in segments if item.kind == "dialogue")
    assert dialogue.status == "confirmed"
    assert dialogue.dialogue_type == "dialogue"
    assert dialogue.speaker_id != "narrator"


def test_multiple_and_continuous_dialogue_are_separate():
    text = "“甲。”“乙。”旁白。“丙。”"
    _, segments = _segments(text)
    assert sum(item.kind == "dialogue" for item in segments) == 3


@pytest.mark.parametrize(
    "text",
    [
        "第一段“跨过\n\n段落的对话”结尾。",
        "开始“没有闭合\n\n直到末尾",
        'He said, "hello." Then "bye."',
        "他说：「外层『内层』结束。」",
    ],
)
def test_quotes_and_unclosed_quotes_are_lossless(text):
    result, segments = _segments(text)
    result.script.validate(text)
    reconstructed = "".join(text[item.start:item.end] for item in segments)
    assert "".join(reconstructed.split()) == "".join(text.split())


def test_chapters_and_exact_source_positions():
    text = "序言。\n\n第一章 开始\n张三说：“走吧。”\n\n第二章 继续\n结束。"
    result, segments = _segments(text)
    assert len(result.script.chapters) == 3
    for segment in segments:
        assert text[segment.start:segment.end]
    result.script.validate(text)


def test_long_paragraph_is_not_truncated():
    text = "长" * 10000 + "“对话”" + "尾" * 10000
    result, _ = _segments(text)
    result.script.validate(text)
