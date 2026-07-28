"""长篇剧本的内存与响应范围回归。"""
from __future__ import annotations

from services.script_director import ScriptDirectorService


def _large_script(chapters=80, segments_per_chapter=120):
    return {
        "version": "3.0",
        "meta": {"title": "长篇"},
        "voices": {"旁白": {"description": "沉稳"}},
        "chapters": [
            {
                "id": chapter_id,
                "title": f"第{chapter_id}章",
                "segments": [
                    {
                        "id": f"{chapter_id}-{index:03d}",
                        "speaker": "旁白",
                        "role": "旁白",
                        "text": f"第{chapter_id}章第{index}段。",
                        "emotion": "neutral",
                        "delivery": {
                            "speed": 1.0,
                            "intensity": 0.4,
                            "breath": "light",
                        },
                        "pause_before": 0,
                        "pause_after": 600,
                        "pauses": [],
                    }
                    for index in range(1, segments_per_chapter + 1)
                ],
            }
            for chapter_id in range(1, chapters + 1)
        ],
    }


def test_editor_only_serializes_selected_chapter():
    script = _large_script()
    choices = ScriptDirectorService.chapter_choices(script)
    rows = ScriptDirectorService.editor_rows(script, "37")

    assert len(choices) == 80
    assert len(rows) == 120
    assert all(row[0].startswith("37-") for row in rows)


def test_partial_chapter_edit_preserves_unloaded_chapters():
    script = ScriptDirectorService.normalize_script(
        _large_script(chapters=3, segments_per_chapter=4)
    )
    rows = ScriptDirectorService.editor_rows(script, "2")
    rows[0][3] = "cold"

    updated, changed = ScriptDirectorService.apply_segment_edits(script, rows)

    assert changed == 1
    assert len(updated["chapters"]) == 3
    assert len(updated["chapters"][0]["segments"]) == 4
    assert updated["chapters"][1]["segments"][0]["emotion"] == "cold"
    assert updated["chapters"][2]["segments"][0]["emotion"] == "neutral"
