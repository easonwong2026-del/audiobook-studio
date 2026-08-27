"""MCP contract tests without Gradio or a running server process."""
from __future__ import annotations

import ast
import json
import os

import pytest

from mcp_server.server import (
    _ADVERTISED_TOOL_NAMES,
    _ALL_TOOLS,
    _TOOLS,
    handle_request,
)
from mcp_server.tools.projects import (
    get_project,
    get_project_outline,
    list_projects,
    list_segments,
)
from mcp_server.tools.scripts import create_project, validate_structured_script
from repositories.project_repo import ProjectRepository


ADVERTISED_TOOLS = {
    "list_projects", "create_project", "get_project", "list_segments",
    "list_voice_assets", "configure_voice_cast", "get_voice_cast",
    "confirm_voice_cast", "get_workflow_state", "plan_production",
    "start_production", "get_production_task", "control_production",
    "retry_failed_segments", "regenerate_segments", "get_repair_task",
    "plan_export", "start_export", "get_export_task", "get_delivery_manifest",
    "validate_structured_script", "list_production_tasks", "get_runtime_health",
    "get_production_performance",
}


def _call_tool(name, arguments=None):
    return handle_request({
        "jsonrpc": "2.0",
        "id": name,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })["result"]


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
    assert set(detail) == {
        "project_name", "meta", "project_meta", "script_summary", "roles",
        "voice_bindings", "synthesis", "storage", "integrity",
    }
    assert detail["integrity"]["ok"] is True
    with_outline = get_project({"project_name": "MCP 测试", "include_outline": True})
    assert set(with_outline) == set(detail) | {"outline"}
    assert with_outline["outline"]["chapter_count"] == 1


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
    assert first["segments"][0]["audio_status"] == "missing"
    assert first["segments"][0]["audio_available"] is False
    assert len(first["segments"][0]["text_preview"]) <= 160
    assert all("/" not in str(item) for item in first["segments"][0].values())


def test_mcp_metadata_declares_query_and_mutation_semantics():
    for tool_name in (
        "list_segments",
        "get_workflow_state",
        "get_runtime_health",
        "plan_production",
        "get_production_task",
        "get_production_performance",
    ):
        metadata = _TOOLS[tool_name]
        assert metadata["annotations"]["readOnlyHint"] is True
        assert metadata["outputSchema"]["type"] == "object"
    assert "next_actions" in _TOOLS["get_workflow_state"]["outputSchema"]["required"]
    assert _TOOLS["get_voice_cast"]["annotations"]["readOnlyHint"] is True
    assert _TOOLS["list_voice_assets"]["annotations"]["readOnlyHint"] is True
    assert _TOOLS["validate_structured_script"]["annotations"]["readOnlyHint"] is True
    assert _TOOLS["get_repair_task"]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    assert _TOOLS["configure_voice_cast"]["annotations"]["readOnlyHint"] is False
    assert _TOOLS["control_production"]["annotations"]["readOnlyHint"] is False

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
    tool_names = {item["name"] for item in tools["result"]["tools"]}
    assert tool_names == ADVERTISED_TOOLS
    assert [item["name"] for item in tools["result"]["tools"]] == list(
        _ADVERTISED_TOOL_NAMES
    )
    assert len(tool_names) == 24
    assert not tool_names & (set(_ALL_TOOLS) - ADVERTISED_TOOLS)
    assert not tool_names & {
        "get_quality_report", "list_review_segments", "get_segment_review",
        "mark_segment_review", "run_technical_qa",
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


def test_hidden_compat_alias_is_callable_but_not_advertised(isolated_projects):
    create_project({"project_name": "兼容书", "script": _script()})
    result = _call_tool("get_project_outline", {"project_name": "兼容书"})
    assert result["isError"] is False
    assert result["structuredContent"]["project_name"] == "兼容书"


def test_configure_voice_cast_aggregates_state_without_confirming(
    isolated_projects,
    monkeypatch,
):
    from services.voice_cast import VoiceCastResolver

    create_project({"project_name": "V2 声音", "script": _script()})
    library = os.path.join(isolated_projects, "data", "voice_library")
    os.makedirs(library, exist_ok=True)
    for filename, content in (("沉稳.wav", b"voice-one"), ("清亮.wav", b"voice-two")):
        with open(os.path.join(library, filename), "wb") as file:
            file.write(content)
    assets = _call_tool("list_voice_assets")["structuredContent"]["items"]
    assert len(assets) == 2
    selected = _call_tool(
        "list_voice_assets",
        {"voice_asset_id": assets[0]["voice_asset_id"]},
    )
    assert selected["structuredContent"]["items"] == [assets[0]]

    confirm_calls = []
    original_confirm = VoiceCastResolver.confirm_voice_cast

    def tracked_confirm(project_name):
        confirm_calls.append(project_name)
        return original_confirm(project_name)

    monkeypatch.setattr(
        VoiceCastResolver,
        "confirm_voice_cast",
        staticmethod(tracked_confirm),
    )
    roster = [
        {"role_id": "role_narrator", "name": "旁白"},
        {"role_id": "role_xiaoyu", "name": "小雨"},
    ]
    bindings = [
        {"role_id": "role_narrator", "voice_asset_id": assets[0]["voice_asset_id"]},
        {"role_id": "role_xiaoyu", "voice_asset_id": assets[1]["voice_asset_id"]},
    ]
    configured = _call_tool(
        "configure_voice_cast",
        {"project_name": "V2 声音", "roles": roster, "bindings": bindings},
    )
    state = configured["structuredContent"]
    assert configured["isError"] is False
    assert state["success"] is True
    assert state["status"] == "draft"
    assert state["validation"]["ready"] is True
    assert state["roster"]["roles"]["role_narrator"]["name"] == "旁白"
    assert state["bindings"]["role_narrator"]["voice_asset_id"] == assets[0]["voice_asset_id"]
    assert state["lock_state"]["cast_locked"] is False
    assert state["confirmation"]["confirmed"] is False
    assert state["readiness"]["cast_ready"] is True
    assert confirm_calls == []

    updated = _call_tool(
        "configure_voice_cast",
        {
            "project_name": "V2 声音",
            "roles": [{"role_id": "role_narrator", "description": "主叙述角色"}],
        },
    )
    assert updated["structuredContent"]["roster"]["roles"]["role_narrator"]["description"] == "主叙述角色"
    assert updated["structuredContent"]["confirmation"]["confirmed"] is False

    confirmed = _call_tool("confirm_voice_cast", {"project_name": "V2 声音"})
    assert confirmed["isError"] is False
    assert confirmed["structuredContent"]["confirmed"] is True
    assert confirm_calls == ["V2 声音"]


def test_control_production_preserves_pause_resume_cancel_dispatch(monkeypatch):
    from services.production_jobs import ProductionJobService

    calls = []

    def fake_operation(action):
        def operation(task_id):
            calls.append((action, task_id))
            return {"action": action, "task_id": task_id}

        return operation

    for name in ("pause", "resume", "cancel"):
        monkeypatch.setattr(
            ProductionJobService,
            name,
            staticmethod(fake_operation(name)),
        )

    for action in ("pause", "resume", "cancel"):
        result = _call_tool(
            "control_production",
            {"task_id": "task-v2", "action": action},
        )
        assert result["isError"] is False
        assert result["structuredContent"] == {
            "action": action,
            "task_id": "task-v2",
        }
    assert calls == [("pause", "task-v2"), ("resume", "task-v2"), ("cancel", "task-v2")]


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
            "name": "list_segments",
            "arguments": {
                "project_name": "MCP 工作流",
                "status": "missing",
            },
        },
    })["result"]
    assert filtered["isError"] is False
    # 从未生产的段落属于 missing audio。
    assert len(filtered["structuredContent"]["segments"]) == 2
    invalid = handle_request({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "get_quality_report",
            "arguments": {"project_name": "MCP 工作流"},
        },
    })
    assert invalid["error"]["code"] == -32602

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
    configured_rejected = _call_tool(
        "configure_voice_cast",
        {"project_name": "不存在"},
    )
    assert configured_rejected["isError"] is True
    assert set(configured_rejected["structuredContent"]["error"]) == {
        "code", "message", "fix_hint", "details",
    }


def test_workflow_next_actions_only_reference_advertised_tools(isolated_projects):
    create_project({"project_name": "V2 工作流", "script": _script()})
    result = _call_tool("get_workflow_state", {"project_name": "V2 工作流"})
    assert {
        item["tool"] for item in result["structuredContent"]["next_actions"]
    } <= ADVERTISED_TOOLS

    workflow_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "services", "workflow.py",
    )
    with open(workflow_path, encoding="utf-8") as file:
        tree = ast.parse(file.read())
    referenced = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in _ALL_TOOLS
    }
    assert referenced <= ADVERTISED_TOOLS


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
    segments = _call_list("list_segments", {"project_name": "中文书"})
    assert isinstance(segments["structuredContent"], dict)
    assert isinstance(segments["structuredContent"]["segments"], list)
    assert len(segments["structuredContent"]["segments"]) == 2
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

    # finalize_voice_cast locks the cast but does NOT record the human
    # confirmation; the new hard gate blocks production until the user has
    # explicitly confirmed through confirm_voice_cast.
    before_confirm = handle_request({
        "jsonrpc": "2.0", "id": 25, "method": "tools/call",
        "params": {
            "name": "plan_production",
            "arguments": {"project_name": "MCP 生产"},
        },
    })["result"]
    assert before_confirm["structuredContent"]["ready"] is False
    assert any(
        item.get("code") == "VOICE_CAST_CONFIRMATION_REQUIRED"
        for item in before_confirm["structuredContent"]["blockers"]
    )

    confirmed = handle_request({
        "jsonrpc": "2.0", "id": 26, "method": "tools/call",
        "params": {
            "name": "confirm_voice_cast",
            "arguments": {"project_name": "MCP 生产"},
        },
    })["result"]
    assert confirmed["structuredContent"]["confirmed"] is True
    assert confirmed["structuredContent"]["confirmed_revision"] == (
        confirmed["structuredContent"]["cast_revision"]
    )

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
    # Plan reports the effective engine and how it was selected.
    assert plan["structuredContent"]["engine"]["engine_identity"] in {
        "indextts:2", "indextts:2.5",
    }
    assert plan["structuredContent"]["engine_selection_source"] in {
        "explicit", "settings_default",
    }

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
