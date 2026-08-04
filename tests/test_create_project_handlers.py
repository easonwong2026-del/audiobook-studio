"""JSON-only new-project page defaults and preview wiring."""
from __future__ import annotations

import json
from pathlib import Path

from services.structured_script_import import StructuredScriptImportService
from ui.create_project_handlers import (
    derive_json_project_name,
    format_creation_warnings,
    format_json_preview,
)

ROOT = Path(__file__).resolve().parents[1]


def test_json_filename_derives_project_name():
    assert derive_json_project_name({"path": "/tmp/random", "name": "厨神.json"}) == "厨神"


def test_json_project_name_does_not_overwrite_manual_value():
    assert derive_json_project_name(
        {"path": "/tmp/random", "name": "new.json"},
        current_name="手工项目名",
    ) == "手工项目名"


def test_json_metadata_name_has_priority(tmp_path):
    source = tmp_path / "random-file.json"
    source.write_text(
        json.dumps({"project_name": "明确项目", "meta": {"title": "作品"}}),
        encoding="utf-8",
    )
    assert derive_json_project_name(str(source)) == "明确项目"


def test_creation_warnings_are_escaped_and_limited():
    warnings = ["<script>alert(1)</script>"] + [f"warning-{index}" for index in range(25)]
    rendered = format_creation_warnings(warnings)
    assert "共 26 项 warning" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert rendered.count("\n- ") == 10
    assert "另有 16 条未展示" in rendered


def test_preview_contains_required_workbench_summary():
    preview = StructuredScriptImportService.inspect(
        str(ROOT / "tests" / "fixtures" / "structured_script_valid.json")
    )
    rendered = format_json_preview(preview)
    for text in ("作品", "作者", "章节", "片段", "角色", "旁白", "警告", "错误"):
        assert text in rendered


def test_create_page_is_json_only_and_not_an_advanced_entry():
    source = (ROOT / "ui/pages/create_project_page.py").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'file_types=[".json"]' in source
    assert "TXT" not in source and "DOCX" not in source and "EPUB" not in source
    assert "AI 分析并创建项目" not in source
    assert "高级" not in source
    assert 'cp_json_check.click(' in app
    assert 'create_ui.inspect_json' in app
    assert 'cp_json_create.click(' in app
    assert 'create_ui.create_from_json' in app
    assert "_goto(\"voices\")" in app
