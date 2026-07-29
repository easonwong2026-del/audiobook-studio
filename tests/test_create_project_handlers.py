"""Filename-derived project defaults and create-page wiring regressions."""
from __future__ import annotations

from pathlib import Path

import pytest

from ui.create_project_handlers import (
    derive_json_project_name,
    derive_project_fields,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("厨神.txt", "厨神"),
        ("中文作品.docx", "中文作品"),
        ("Book-01_final.epub", "Book-01_final"),
        ("小说.v2.docx", "小说.v2"),
    ],
)
def test_source_filename_derives_name_and_title(filename, expected):
    assert derive_project_fields(f"/tmp/gradio-session/{filename}") == (
        expected,
        expected,
    )


def test_json_filename_derives_advanced_project_name():
    assert derive_json_project_name({"path": "/tmp/random", "name": "厨神.json"}) == "厨神"


def test_original_name_wins_over_temporary_upload_path():
    value = {
        "path": "/tmp/gradio/4fc884fbb8c44d9c/upload",
        "name": "真实书名.txt",
    }
    assert derive_project_fields(value) == ("真实书名", "真实书名")


def test_existing_user_values_are_not_overwritten():
    assert derive_project_fields(
        "/tmp/自动名.txt",
        current_name="手工项目名",
        current_title="手工作品名",
    ) == ("手工项目名", "手工作品名")


def test_illegal_characters_use_canonical_sanitizer():
    name, title = derive_project_fields("/tmp/厨神:测试?.txt")
    assert name == title == "厨神_测试_"


def test_empty_file_value_is_safe():
    assert derive_project_fields(None) == ("", "")
    assert derive_project_fields(None, "已有", "标题") == ("已有", "标题")


def test_create_page_has_non_lambda_file_change_wiring():
    app = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")
    assert "cp_source.change(" in app
    assert "create_ui.derive_project_fields" in app
    assert "cp_json_file.change(" in app
    assert "create_ui.derive_json_project_name" in app
