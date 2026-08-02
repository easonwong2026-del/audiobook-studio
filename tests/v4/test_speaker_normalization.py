"""角色噪音规整单元测试（任务十.4 要求）。"""
from __future__ import annotations

import pytest

from services.source_segmenter import SourceSegmenter
from services.speaker_normalization import normalize_speaker_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 任务明确要求：不能生成这些噪音角色
        ("她自言自语", ""),
        ("顾川急", "顾川"),
        ("轻声说", ""),
        ("笑着问", ""),
        # 常见动作 / 情绪 / 语气后缀剥离
        ("林晚轻声说", "林晚"),
        ("林晚轻声说道", "林晚"),
        ("老周笑着问", "老周"),
        ("顾川急道", "顾川"),
        ("林晚淡淡道", "林晚"),
        ("老周苦笑道", "老周"),
        ("林晚心想", "林晚"),
        # 正常角色名不受影响
        ("林晚", "林晚"),
        ("顾川", "顾川"),
        ("老周", "老周"),
        ("王道长", "王道长"),
        # 代词 / 叙述占位无效
        ("他", ""),
        ("她", ""),
        ("旁白", ""),
        ("众人", ""),
        ("轻声", ""),
        ("缓缓", ""),
        ("脸色阴沉", ""),
        ("门外", ""),
        ("系统提示", ""),
        ("第一章", ""),
        ("项目名称", ""),
        ("声音", ""),
        ("男人", ""),
        ("年轻人", ""),
        # 空白与标点清理
        (" 林晚 ", "林晚"),
        ("林晚：", "林晚"),
    ],
)
def test_normalize_speaker_name(raw, expected):
    assert normalize_speaker_name(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "她自言自语：“今天天气真好。”",
        "顾川急道：“快走！”",
        "林晚轻声说：“别急。”",
        "老周笑着问：“吃饭了吗？”",
    ],
)
def test_rule_segmentation_does_not_produce_noise_speakers(text):
    result = SourceSegmenter().segment(text)
    speakers = {item.display_name for item in result.speakers.speakers}
    for noise in ("她自言自语", "顾川急", "轻声说", "笑着问"):
        assert noise not in speakers
    # 「她自言自语」是代词 + 动作 → unresolved（不产生角色）
    assert "她" not in speakers


def test_rule_segmentation_extracts_clean_names():
    text = "林晚轻声说：“别急。”\n顾川急道：“快走！”\n老周笑着问：“吃饭了吗？”"
    result = SourceSegmenter().segment(text)
    names = {item.display_name for item in result.speakers.speakers}
    for expected in ("林晚", "顾川", "老周"):
        assert expected in names


def test_noise_speaker_assignment_is_dropped_after_routing_normalization():
    """AI 路由返回噪音名时保持 unresolved（对应 speaker_routing_service._apply）。"""
    from domain.v4 import (
        ChapterScript,
        ScriptDocument,
        SemanticSegment,
        Speaker,
        SpeakersDocument,
    )
    from domain.v4.models import source_sha256
    from services.speaker_routing_service import SpeakerRoutingService

    source = "她自言自语：“今天天气真好。”"
    script = ScriptDocument(
        source_sha256=source_sha256(source),
        chapters=[
            ChapterScript(
                chapter_id="chapter_0001",
                title="第一章",
                start=0,
                end=len(source),
                segments=[
                    SemanticSegment(
                        segment_id="segment_000001",
                        chapter_id="chapter_0001",
                        start=0,
                        end=len(source),
                        kind="dialogue",
                        speaker_id=None,
                        speaker_source="unresolved",
                        status="unresolved",
                    )
                ],
            )
        ],
    )
    speakers = SpeakersDocument(speakers=[Speaker("narrator", "旁白", "confirmed", "narrator", locked=True)])
    updated_script, updated_speakers = SpeakerRoutingService._apply(
        script,
        speakers,
        [{"segment_id": "segment_000001", "speaker": "她自言自语"}],
    )
    segment = updated_script.chapters[0].segments[0]
    assert segment.status == "unresolved"
    assert segment.speaker_id is None
    assert len(updated_speakers.speakers) == 1
