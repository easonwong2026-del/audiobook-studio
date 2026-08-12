"""MCP V1 contract tests without Gradio or a running server process."""
from __future__ import annotations

import ast
import json
import os

import pytest

from lib import project_manager as pm
from mcp_server.server import _TOOLS, handle_request
from mcp_server.tools.projects import (
    get_project,
    get_project_outline,
    list_projects,
    list_segments,
)
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
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
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

    listed = list_projects()["projects"]
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


def test_project_outline_and_segment_listing_are_compact_and_stable(isolated_projects):
    create_project({"project_name": "目录书", "script": _script()})

    outline = get_project_outline({"project_name": "目录书"})
    assert outline["chapter_count"] == 1
    assert outline["title"] == "MCP 测试书"
    assert outline["segment_count"] == 2
    assert outline["chapters"][0]["chapter_id"] == "100"
    assert outline["chapters"][0]["pending"] == 2
    assert outline["chapters"][0]["required_roles"] == ["旁白", "小雨"]

    first = list_segments({
        "project_name": "目录书",
        "chapter_id": "100",
        "offset": 0,
        "limit": 1,
    })
    second = list_segments({
        "project_name": "目录书",
        "chapter_id": "100",
        "offset": 1,
        "limit": 1,
    })
    assert first["total"] == 2
    assert first["segments"][0]["segment_id"] == "100-001"
    assert second["segments"][0]["segment_id"] == "100-002"
    assert first["segments"][0]["synthesis_status"] == "pending"
    assert first["segments"][0]["quality_status"] == "not_started"
    assert len(first["segments"][0]["text_preview"]) <= 160
    assert all("/" not in str(item) for item in first["segments"][0].values())


def test_mcp_metadata_declares_query_and_mutation_semantics():
    for tool_name in (
        "get_project_outline",
        "list_segments",
        "get_workflow_state",
        "get_runtime_health",
        "plan_production",
        "get_production_task",
        "get_quality_report",
    ):
        metadata = _TOOLS[tool_name]
        assert metadata["annotations"]["readOnlyHint"] is True
        assert metadata["outputSchema"]["type"] == "object"
    assert "next_actions" in _TOOLS["get_workflow_state"]["outputSchema"]["required"]

    assert _TOOLS["start_production"]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    assert _TOOLS["retry_failed_segments"]["annotations"]["idempotentHint"] is False


def test_stdio_methods_and_no_gradio_import():
    initialize = handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    assert initialize["result"]["capabilities"]["tools"]
    tools = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert {item["name"] for item in tools["result"]["tools"]} >= {
        "server_info", "validate_structured_script", "create_project", "list_projects", "get_project",
        "get_project_outline", "list_segments", "get_production_performance",
        "list_voice_assets", "get_voice_asset",
        "set_character_roster", "get_character_roster", "add_character_roles",
        "update_character_role", "validate_character_roster",
        "set_voice_cast", "get_voice_cast", "bind_cast_role",
        "validate_voice_cast", "finalize_voice_cast", "get_voice_binding_status",
        "check_chapter_roles",
        "get_workflow_state", "get_quality_report", "list_review_segments",
        "get_segment_review", "mark_segment_review", "run_technical_qa",
        "regenerate_segments", "get_repair_task", "list_repairs",
        "plan_export", "start_export", "get_export_task", "list_exports",
        "get_delivery_manifest",
        "get_runtime_health",
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


def test_phase4_workflow_smoke_and_schema_errors_are_structured(isolated_projects):
    create_project({"project_name": "MCP 工作流", "script": _script()})
    response = handle_request({
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "get_workflow_state",
            "arguments": {"project_name": "MCP 工作流"},
        },
    })
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["stage"] in {"cast_pending", "prepared"}
    assert result["structuredContent"]["next_actions"]

    filtered = handle_request({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "list_review_segments",
            "arguments": {
                "project_name": "MCP 工作流",
                "status": "not_started",
            },
        },
    })["result"]
    assert filtered["isError"] is False
    # 从未生产的段落属于 not_started，不再是 technical_warning
    assert len(filtered["structuredContent"]["segments"]) == 2
    # 而 technical_warning 筛选不应误报未生产段落
    no_warning = handle_request({
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {
            "name": "list_review_segments",
            "arguments": {
                "project_name": "MCP 工作流",
                "status": "technical_warning",
            },
        },
    })["result"]
    assert no_warning["isError"] is False
    assert len(no_warning["structuredContent"]["segments"]) == 0

    invalid = handle_request({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "get_quality_report",
            "arguments": {"project_name": "MCP 工作流", "unexpected": True},
        },
    })
    error_result = invalid["result"]
    assert error_result["isError"] is True
    assert set(error_result["structuredContent"]["error"]) == {
        "code", "message", "fix_hint", "details",
    }

    rejected = handle_request({
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {
            "name": "set_character_roster",
            "arguments": {
                "project_name": "不存在",
                "roles": [{"role_id": "narrator", "name": "旁白"}],
            },
        },
    })["result"]
    assert rejected["isError"] is True
    assert set(rejected["structuredContent"]["error"]) == {
        "code", "message", "fix_hint", "details",
    }


def test_list_projects_structured_content_is_object_and_json_safe(isolated_projects):
    """MCP clients reject list-valued structuredContent; every list tool
    must return a JSON object (regression for invalid_type errors)."""

    def _call_list(name, arguments=None):
        return handle_request({
            "jsonrpc": "2.0",
            "id": name,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })["result"]

    # 0 projects: structuredContent is a valid, serializable object.
    empty = _call_list("list_projects")
    assert empty["isError"] is False
    assert isinstance(empty["structuredContent"], dict)
    assert empty["structuredContent"] == {"projects": []}
    json.dumps(empty["structuredContent"], ensure_ascii=False)

    # 1 project with a Unicode Chinese name.
    create_project({"project_name": "中文书", "script": _script()})
    single = _call_list("list_projects")
    assert isinstance(single["structuredContent"], dict)
    projects = single["structuredContent"]["projects"]
    assert len(projects) == 1
    assert projects[0]["project_name"] == "中文书"
    json.dumps(single["structuredContent"], ensure_ascii=False)

    # Multiple projects, also with Chinese names.
    create_project({"project_name": "第二本书", "script": _script()})
    multi = _call_list("list_projects")
    names = {
        item["project_name"]
        for item in multi["structuredContent"]["projects"]
    }
    assert names == {"中文书", "第二本书"}
    json.dumps(multi["structuredContent"], ensure_ascii=False)

    # The other list tools keep structuredContent an object too.
    tasks = _call_list("list_production_tasks")
    assert isinstance(tasks["structuredContent"], dict)
    assert tasks["structuredContent"] == {"tasks": []}
    exports = _call_list("list_exports", {"project_name": "中文书"})
    assert isinstance(exports["structuredContent"], dict)
    assert exports["structuredContent"] == {"exports": []}
    repairs = _call_list("list_repairs", {"project_name": "中文书"})
    assert isinstance(repairs["structuredContent"], dict)
    assert repairs["structuredContent"] == {"repairs": []}
    review = _call_list("list_review_segments", {"project_name": "中文书"})
    assert isinstance(review["structuredContent"], dict)
    assert isinstance(review["structuredContent"]["segments"], list)
    assert len(review["structuredContent"]["segments"]) == 2
    assets = _call_list("list_voice_assets")
    assert isinstance(assets["structuredContent"], dict)
    assert "items" in assets["structuredContent"]


def test_mcp_production_chain_plan_start_query_cancel(
    isolated_projects,
    monkeypatch,
):
    """The MCP surface must expose the whole production loop through the
    existing services: plan -> start -> query -> control -> list."""
    from lib import tts_engine
    from services.production_runtime import ProductionRuntimeClient
    from services.synthesis import SynthesisService

    monkeypatch.setattr(tts_engine, "init_engine", lambda: None)
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)

    def _fake_start(state, _project, _bindings, **_kwargs):
        state.status = "running"
        state.notify()
        return state.task_id

    monkeypatch.setattr(SynthesisService, "start", staticmethod(_fake_start))
    create_project({"project_name": "MCP 生产", "script": _script()})

    # Complete Voice Cast through the same MCP surface so plan_production is
    # genuinely ready (cast_ready without any GPU/engine work).
    library = os.path.join(isolated_projects, "data", "voice_library")
    os.makedirs(library, exist_ok=True)
    with open(os.path.join(library, "沉稳_01.wav"), "wb") as file:
        file.write(b"voice-one")
    with open(os.path.join(library, "清亮_02.wav"), "wb") as file:
        file.write(b"voice-two")
    assets = handle_request({
        "jsonrpc": "2.0", "id": 21, "method": "tools/call",
        "params": {"name": "list_voice_assets", "arguments": {}},
    })["result"]["structuredContent"]["items"]
    assert len(assets) == 2
    asset_id = assets[0]["voice_asset_id"]
    roster = [
        {"role_id": "role_narrator", "name": "旁白", "aliases": ["叙述者"]},
        {"role_id": "role_xiaoyu", "name": "小雨", "aliases": ["小语"]},
    ]
    handle_request({
        "jsonrpc": "2.0", "id": 22, "method": "tools/call",
        "params": {
            "name": "set_character_roster",
            "arguments": {"project_name": "MCP 生产", "roles": roster},
        },
    })
    cast = {
        role["role_id"]: {"voice_asset_id": asset_id}
        for role in roster
    }
    handle_request({
        "jsonrpc": "2.0", "id": 23, "method": "tools/call",
        "params": {
            "name": "set_voice_cast",
            "arguments": {"project_name": "MCP 生产", "roles": cast},
        },
    })
    locked = handle_request({
        "jsonrpc": "2.0", "id": 24, "method": "tools/call",
        "params": {
            "name": "finalize_voice_cast",
            "arguments": {"project_name": "MCP 生产"},
        },
    })["result"]
    assert locked["structuredContent"]["status"] == "locked"

    plan = handle_request({
        "jsonrpc": "2.0", "id": 30, "method": "tools/call",
        "params": {
            "name": "plan_production",
            "arguments": {"project_name": "MCP 生产"},
        },
    })["result"]
    assert plan["isError"] is False
    assert plan["structuredContent"]["ready"] is True
    assert plan["structuredContent"]["voice_cast"]["cast_ready"] is True
    assert "runtime_status" in plan["structuredContent"]["voice_cast"]

    started_task = handle_request({
        "jsonrpc": "2.0", "id": 31, "method": "tools/call",
        "params": {
            "name": "start_production",
            "arguments": {"project_name": "MCP 生产"},
        },
    })["result"]
    assert started_task["isError"] is False
    assert started_task["structuredContent"]["created"] is True
    task_id = started_task["structuredContent"]["task_id"]

    query = handle_request({
        "jsonrpc": "2.0", "id": 32, "method": "tools/call",
        "params": {
            "name": "get_production_task",
            "arguments": {"task_id": task_id},
        },
    })["result"]
    assert query["isError"] is False
    assert query["structuredContent"]["task_id"] == task_id

    listed = handle_request({
        "jsonrpc": "2.0", "id": 33, "method": "tools/call",
        "params": {
            "name": "list_production_tasks",
            "arguments": {"project_name": "MCP 生产"},
        },
    })["result"]
    assert listed["isError"] is False
    assert any(
        item["task_id"] == task_id
        for item in listed["structuredContent"]["tasks"]
    )

    try:
        cancelled = handle_request({
            "jsonrpc": "2.0", "id": 34, "method": "tools/call",
            "params": {
                "name": "cancel_production",
                "arguments": {"task_id": task_id},
            },
        })["result"]
        assert cancelled["isError"] is False
        assert cancelled["structuredContent"]["task_id"] == task_id
        assert cancelled["structuredContent"]["status"] in {
            "cancelling",
            "cancelled",
        }
    finally:
        # The fake SynthesisService.start never produces a worker, so the
        # task stays in cancelling forever; release the inline runtime so it
        # does not starve later tests in the same process.
        ProductionRuntimeClient.reset_inline()
