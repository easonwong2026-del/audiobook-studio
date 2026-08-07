"""JSON-order chapter numbering regressions."""
from __future__ import annotations

from lib.chapter_identity import chapter_file_stem, chapter_label, normalize_script_for_project


def test_chapter_numbers_follow_json_order_and_pad_to_three_digits():
    script = {"chapters": [{"id": "custom-a", "title": "A"} for _ in range(100)]}
    normalized = normalize_script_for_project(script)
    assert normalized["chapters"][0]["chapter_code"] == "001"
    assert normalized["chapters"][99]["chapter_code"] == "100"
    assert chapter_label(normalized["chapters"][0], 0, 100).startswith("第 1 章 · 001")
    assert chapter_file_stem(normalized["chapters"][99], 99, 100).startswith("100_")
