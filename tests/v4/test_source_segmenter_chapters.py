"""题名页章节修复与规则噪音的切分测试（任务十.2 / 十.4）。"""
from __future__ import annotations

import pytest

from services.source_segmenter import SourceSegmenter

# 与测试稿 D:\AudiobookStudio-Test\source\测试书稿_雨夜书店.txt 同构的开头
TITLE_PAGE = "雨夜书店\n林晚著\n\n"


def _chapters(text):
    return SourceSegmenter().segment(text).script.chapters


def test_title_page_does_not_create_front_chapter():
    """书名 + 作者（纯题名页）不应产生「前言」伪章节，应并入第一章。"""
    text = TITLE_PAGE + (
        "第一章 雨夜的书店\n\n深夜十一点，城市刚下过一场大雨。\n\n"
        "第二章 手稿\n\n第二天一早，雨过天晴。\n\n"
        "第三章 约定\n\n傍晚时分，晚霞满天。"
    )
    result = SourceSegmenter().segment(text)
    chapters = result.script.chapters
    assert len(chapters) == 3
    assert "第一章 雨夜的书店" in chapters[0].title
    # lossless：题名行并入第一章旁白段，全文仍被完整覆盖
    result.script.validate(text)
    segments = [item for chapter in chapters for item in chapter.segments]
    joined = "".join(text[item.start:item.end] for item in segments)
    assert "雨夜书店" in joined
    # 题名行是旁白（narrator），不是独立章节
    first_segment = chapters[0].segments[0]
    assert first_segment.kind == "narration"


def test_non_title_prefix_becomes_序章():
    """含正文的前置内容（带标点）仍作为「序章」保留（lossless）。"""
    text = "序言：这是一个真正的序。\n\n第一章 开始\n张三说：“走吧。”"
    chapters = _chapters(text)
    assert len(chapters) == 2
    assert chapters[0].title == "序章"


def test_txt_and_docx_share_segmentation_behavior():
    """TXT 与 DOCX 都走同一 SourceSegmenter，章节行为一致。"""
    text = TITLE_PAGE + "第一章 雨夜的书店\n\n正文一。"
    chapters = _chapters(text)
    assert len(chapters) == 1
    assert "雨夜的书店" in chapters[0].title


def test_rule_speaker_modifier_is_stripped_in_segments():
    text = "顾川急道：“快走！”林晚轻声说：“别急。”"
    result = SourceSegmenter().segment(text)
    segments = [item for chapter in result.script.chapters for item in chapter.segments]
    dialogues = [item for item in segments if item.kind == "dialogue"]
    speakers = {item.display_name for item in result.speakers.speakers}
    assert len(dialogues) == 2
    assert "顾川" in speakers and "林晚" in speakers
    assert "顾川急" not in speakers and "轻声说" not in speakers
