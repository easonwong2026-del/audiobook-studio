"""MCP V1 contract tests without Gradio or a running server process."""
from __future__ import annotations

import ast
import os

import pytest

from mcp_server.server import handle_request
from mcp_server.tools.projects import get_project, list_projects
from mcp_server.tools.scripts import create_project, validate_structured_script
from repositories.project_repo import ProjectRepository


def _script():
    return {
        "version": "3.0",
        "meta": {"title": "MCP 测试书", "author": "测试作者", "total_chapters": 1, "total_segments": 2},
        "voices": {"旁白": {}, "小雨": {}},
        "chapters": [{
            "id": 100,
            "title": "第一章",
            "segments": [
                {"id": "100-001", "role": "旁白", "text": "这是第一段文本。"},
                {"id": "100-002", "role": "小雨", "text": "这是第二段文本。"},
            ],
        }],
    }


@pytest.fixture
def isolated_projects(tmp_path, monkeypatch):
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path / "data"))
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "data" / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "data" / "legacy")
    ProjectRepository._INITIALIZED = True
    yield tmp_path
    (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    ) = original


def test_validate_valid_script_returns_machine_contract(isolated_projects):
    result = validate_structured_script({"project_name": "MCP 测试", "script": _script()})
    assert result["valid"] is True
    assert result["can_create"] is True
    assert result["script_summary"] == {
        "title": "MCP 测试书",
        "author": "测试作者",
        "chapters": 1,
        "segments": 2,
        "roles": 2,
    }


def test_validate_invalid_script_has_code_path_and_severity(isolated_projects):
    raw = _script()
    raw["chapters"][0]["segments"][0].update({"role": "林晓", "text": "", "id": "same"})
    raw["chapters"][0]["segments"][1]["id"] = "same"
    raw["meta"]["total_segments"] = 99
    result = validate_structured_script({"project_name": "坏稿", "script": raw})
    assert result["valid"] is False
    assert result["can_create"] is False
    assert {issue["code"] for issue in result["errors"]} >= {
        "missing_voice", "empty_text", "duplicate_segment_id", "count_mismatch",
    }
    assert all(issue["severity"] == "error" and issue["path"] for issue in result["errors"])


def test_warning_only_script_can_create(isolated_projects):
    raw = _script()
    raw["voices"]["未使用"] = {}
    result = validate_structured_script({"project_name": "有警告", "script": raw})
    assert result["valid"] is True
    assert result["can_create"] is True
    assert result["summary"]["warnings"] > 0


def test_create_duplicate_list_and_get_summary(isolated_projects):
    created = create_project({"project_name": "MCP 测试", "script": _script()})
    assert created["created"] is True
    with pytest.raises(ValueError, match="已存在"):
        create_project({"project_name": "MCP 测试", "script": _script()})

    listed = list_projects()
    assert listed[0]["project_name"] == "MCP 测试"
    assert listed[0]["chapter_count"] == 1
    assert listed[0]["segment_count"] == 2
    assert listed[0]["completed_segments"] == 0
    assert listed[0]["storage_bytes"] > 0
    detail = get_project({"project_name": "MCP 测试"})
    assert detail["script_summary"]["title"] == "MCP 测试书"
    assert detail["roles"] == ["旁白", "小雨"]
    assert "chapters" not in detail
    assert detail["integrity"]["ok"] is True


def test_stdio_methods_and_no_gradio_import():
    initialize = handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    assert initialize["result"]["capabilities"]["tools"]
    tools = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert {item["name"] for item in tools["result"]["tools"]} == {
        "server_info", "validate_structured_script", "create_project", "list_projects", "get_project",
    }
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_server")
    for root, _dirs, files in os.walk(path):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(root, filename), encoding="utf-8").read())
            assert not any(
                isinstance(node, ast.Import) and any(alias.name == "gradio" for alias in node.names)
                or isinstance(node, ast.ImportFrom) and node.module == "gradio"
                for node in ast.walk(tree)
            )
