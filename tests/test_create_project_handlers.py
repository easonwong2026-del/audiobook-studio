"""JSON-only new-project page defaults and preview wiring."""
from __future__ import annotations

import json
import inspect
from pathlib import Path
from types import SimpleNamespace

import gradio as gr
import pytest

from services.structured_script_import import StructuredScriptImportService
from services.session import SessionState
from ui import create_project_handlers as create_handlers
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


def _creation_result(project_name="C"):
    return SimpleNamespace(
        project_name=project_name,
        title="作品 C",
        chapter_count=3,
        segment_count=12,
        role_count=2,
        warnings=[],
    )


def test_create_from_json_declares_one_two_value_contract():
    signature = inspect.signature(create_handlers.create_from_json)
    assert signature.return_annotation == "tuple[str, bool]"


def test_create_from_json_failure_paths_return_false_without_mutating_opened_session(
    monkeypatch, tmp_path
):
    source = tmp_path / "script.json"
    source.write_text("{}", encoding="utf-8")
    cases = [
        ("", None, ValueError("service must not run for an empty name")),
        ("C", str(tmp_path / "missing.json"), ValueError("service must not run for a missing file")),
        ("C", str(source), ValueError("slot occupied")),
        ("C", str(source), RuntimeError("unexpected failure")),
    ]

    for name, json_file, error in cases:
        ss = SessionState(project="B", script={"project": "B"}, bindings={"old": "voice"})
        service_calls = []

        def fail_service(_name, _source, error=error):
            service_calls.append((_name, _source))
            if not name or not Path(_source).exists():
                raise AssertionError("create service should not run before input validation")
            raise error

        monkeypatch.setattr(
            create_handlers.ProjectCreationService,
            "create_from_structured_script",
            fail_service,
        )
        result = create_handlers.create_from_json(name, json_file, ss)

        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], str)
        assert result[1] is False
        assert ss.project == "B"
        assert ss.script == {"project": "B"}
        assert ss.bindings == {"old": "voice"}
        assert bool(service_calls) is bool(name and Path(json_file).exists())


def test_create_from_json_success_updates_session_and_returns_true(monkeypatch, tmp_path):
    source = tmp_path / "script.json"
    source.write_text("{}", encoding="utf-8")
    snapshot = SimpleNamespace(script={"project": "C"}, bindings={"旁白": "voice-c"})
    monkeypatch.setattr(
        create_handlers.ProjectCreationService,
        "create_from_structured_script",
        lambda _name, _source: _creation_result("C"),
    )
    from services import ProjectService

    monkeypatch.setattr(ProjectService, "open_project_as_snapshot", lambda _name: snapshot)
    ss = SessionState(project="B", script={"project": "B"}, bindings={"old": "voice"})

    result = create_handlers.create_from_json("C", str(source), ss)

    assert isinstance(result, tuple) and len(result) == 2
    assert isinstance(result[0], str)
    assert result[1] is True
    assert ss.project == "C"
    assert ss.project_snapshot is snapshot
    assert ss.script == snapshot.script
    assert ss.bindings == snapshot.bindings


def test_creation_success_gate_blocks_failure_chain_and_allows_success(monkeypatch, tmp_path):
    source = tmp_path / "script.json"
    source.write_text("{}", encoding="utf-8")
    ss = SessionState(project="B", script={"project": "B"}, bindings={"old": "voice"})
    hydrate_calls = []
    goto_calls = []

    monkeypatch.setattr(
        create_handlers.ProjectCreationService,
        "create_from_structured_script",
        lambda _name, _source: (_ for _ in ()).throw(ValueError("slot occupied")),
    )
    _, creation_success = create_handlers.create_from_json("C", str(source), ss)
    assert creation_success is False
    assert ss.project == "B"
    with pytest.raises(gr.Error):
        create_handlers.require_creation_success(creation_success)
    assert hydrate_calls == []
    assert goto_calls == []

    snapshot = SimpleNamespace(script={"project": "C"}, bindings={"旁白": "voice-c"})
    monkeypatch.setattr(
        create_handlers.ProjectCreationService,
        "create_from_structured_script",
        lambda _name, _source: _creation_result("C"),
    )
    from services import ProjectService

    monkeypatch.setattr(ProjectService, "open_project_as_snapshot", lambda _name: snapshot)
    _, creation_success = create_handlers.create_from_json("C", str(source), ss)
    assert creation_success is True
    assert ss.project == "C"
    assert ss.project_snapshot is snapshot
    assert create_handlers.require_creation_success(creation_success) is None
    hydrate_calls.append(ss.project)
    goto_calls.append("voices")
    assert hydrate_calls == ["C"]
    assert goto_calls == ["voices"]


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
