"""Review-page state and selection regressions."""
from __future__ import annotations

from services.review_audio import ReviewAudioService


def test_segment_choices_keep_label_and_value_separate():
    script = {
        "chapters": [{
            "id": "chapter 1",
            "segments": [{"id": "10-001", "role": "旁白", "text": "有空格的显示文本"}],
        }],
    }
    assert ReviewAudioService.normalize_segment_id(("10-001 · 旁白 · 文本", "10-001"), script) == "10-001"
    # A display string is never split at the first space.  Only an exact
    # known label or the explicit Dropdown value is accepted.
    assert ReviewAudioService.normalize_segment_id("10-001 · 旁白 · 文本", script) == "10-001 · 旁白 · 文本"


def test_initialize_without_audio_still_selects_first_chapter(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    script = {
        "chapters": [{
            "id": 7,
            "title": "第七章",
            "segments": [{"id": "7-001", "role": "旁白", "text": "测试"}],
        }],
    }
    state = ReviewAudioService.initialize("book", str(project_dir), script)
    assert state.selected_chapter == "7"
    assert state.chapter_audio is None
    assert "没有可试听" in state.chapter_status
    assert state.selected_segment is None
    assert "没有已生成" in state.segment_status
