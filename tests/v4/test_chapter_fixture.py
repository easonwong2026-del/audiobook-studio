from __future__ import annotations

from pathlib import Path

from services.source_segmenter import SourceSegmenter


def test_copyright_safe_chapter_fixture_is_one_lossless_input():
    fixture = Path(__file__).parents[1] / "fixtures/v4/sample_chapter.txt"
    text = fixture.read_text(encoding="utf-8")
    result = SourceSegmenter().source_only_chapter(text, title="第一章 雨站")

    assert len(result.script.chapters) == 1
    assert result.script.chapters[0].title == "第一章 雨站"
    assert result.script.chapters[0].segments[0].dialogue_type == "unanalysed"
    assert result.speakers.speakers[0].speaker_id == "narrator"

