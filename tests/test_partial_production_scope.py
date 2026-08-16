"""Scope-based readiness and UI contract tests for partial production."""
from __future__ import annotations

import pytest

import app
from lib import progress as synth_progress
from lib import project_manager as pm
from mcp_server.server import _TOOLS, handle_request
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRepository
from repositories.voice_cast_repo import VoiceCastRepository
from services import (
    ProductionJobError,
    ProductionJobService,
    ProjectService,
    VoiceAssetService,
    VoiceCastResolver,
)


@pytest.fixture
def scope_project(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    library = data_dir / "voice_library"
    library.mkdir(parents=True)
    (library / "沉稳_a.wav").write_bytes(b"voice-a")
    (library / "清亮_b.wav").write_bytes(b"voice-b")
    (library / "低沉_c.wav").write_bytes(b"voice-c")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(data_dir / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(pm, "LEGACY_ROOT", str(data_dir / "legacy"))
    monkeypatch.setattr(
        TaskRepository,
        "get_task_dir",
        staticmethod(lambda: str(tmp_path / "task_records")),
    )
    script = {
        "meta": {"title": "局部生产"},
        "voices": {"角色A": {}, "角色B": {}, "角色C": {}},
        "chapters": [
            {
                "id": "3",
                "title": "第三章",
                "segments": [
                    {"id": "3-005", "role": "角色A", "text": "只选这一段"},
                    {"id": "3-006", "role": "角色B", "text": "未配置的同章角色"},
                ],
            },
            {
                "id": "4",
                "title": "第四章",
                "segments": [
                    {"id": "4-001", "role": "角色C", "text": "全书范围应发现"},
                ],
            },
        ],
    }
    ProjectService.create_project_from_data("scope", script)
    VoiceCastResolver.set_character_roster(
        "scope",
        [
            {"role_id": "role_a", "name": "角色A"},
            {"role_id": "role_b", "name": "角色B"},
            {"role_id": "role_c", "name": "角色C"},
        ],
    )
    assets = {item["file_name"]: item for item in VoiceAssetService.list_assets()}
    VoiceCastResolver.set_voice_cast(
        "scope",
        {"role_a": {"voice_asset_id": assets["沉稳_a.wav"]["voice_asset_id"]}},
    )
    yield "scope"


def test_segment_readiness_is_exact_and_draft_cast_requires_confirmation(scope_project):
    selected = VoiceCastResolver.check_production_scope(scope_project, ["3-005"])
    assert selected["ready"] is True
    assert [role["role_id"] for role in selected["required_roles"]] == ["role_a"]

    same_chapter_unbound = VoiceCastResolver.check_production_scope(
        scope_project, ["3-006"]
    )
    assert same_chapter_unbound["ready"] is False
    assert any(item["code"] == "ROLE_UNBOUND" for item in same_chapter_unbound["errors"])

    # The human confirmation gate blocks plan readiness until the user has
    # explicitly confirmed the Voice Cast (confirmed_revision == cast_revision),
    # even for an otherwise scope-ready subset.
    plan = ProductionJobService.plan(scope_project, {"segment_ids": ["3-005"]})
    assert plan["ready"] is False
    assert any(
        item.get("code") == "VOICE_CAST_CONFIRMATION_REQUIRED"
        for item in plan["blockers"]
    )
    assert plan["voice_cast"]["scope_ready"] is True
    assert plan["voice_cast"]["confirmation_required"] is True
    assert plan["scope"] == {
        "all": False,
        "chapter_ids": [],
        "segment_ids": ["3-005"],
    }
    assert plan["voice_cast"]["full_book_ready"] is False
    # The plan always reports the effective engine it would freeze on start.
    assert plan["engine"]["engine_version"] in {"2", "2.5"}
    assert plan["engine_selection_source"] in {"explicit", "settings_default"}


def test_whole_book_still_requires_every_used_role(scope_project):
    plan = ProductionJobService.plan(scope_project, {"all": True})
    assert plan["ready"] is False
    assert {item["code"] for item in plan["blockers"]} >= {"ROLE_UNBOUND"}
    assert {item["role_id"] for item in plan["required_roles"]} == {
        "role_a", "role_b", "role_c",
    }


def test_plan_is_read_only_and_lock_only_touches_selected_roles(scope_project):
    before = VoiceCastResolver.get_voice_cast(scope_project)
    before_tasks = TaskRepository.list_tasks(project=scope_project, task_type="synthesis")
    plan = ProductionJobService.plan(scope_project, {"segment_ids": ["3-005"]})
    after = VoiceCastResolver.get_voice_cast(scope_project)
    assert plan["ready"] is False  # confirmation gate (read-only, no mutation)
    assert any(
        item.get("code") == "VOICE_CAST_CONFIRMATION_REQUIRED"
        for item in plan["blockers"]
    )
    assert after["roles"] == before["roles"]
    assert after["cast_revision"] == before["cast_revision"]
    assert TaskRepository.list_tasks(project=scope_project, task_type="synthesis") == before_tasks

    locked = VoiceCastResolver.lock_production_scope(scope_project, ["3-005"])
    assert locked["locked_role_ids"] == ["role_a"]
    cast = VoiceCastResolver.get_voice_cast(scope_project)
    assert cast["roles"]["role_a"]["locked"] is True
    assert cast["roles"].get("role_b", {}).get("locked", False) is False
    assert cast["roles"].get("role_c", {}).get("locked", False) is False
    bindings = ProjectRepository.load_bindings(ProjectRepository.get_project_dir(scope_project))
    assert bindings["role_bindings"]["role_a"]["locked"] is True


def test_start_persists_exact_scope_without_requiring_full_cast_lock(scope_project, monkeypatch):
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    # The confirmation gate rejects an unconfirmed Voice Cast.
    with pytest.raises(ProductionJobError) as gate:
        ProductionJobService.start(
            scope_project,
            {"segment_ids": ["3-005"]},
            source="mcp",
            idempotency_key="scope-start-before-confirm",
        )
    assert gate.value.code == "VOICE_CAST_CONFIRMATION_REQUIRED"
    assert "cast_revision" in gate.value.details
    assert "role_bindings" in gate.value.details
    assert gate.value.details["next_actions"] == ["get_voice_cast", "confirm_voice_cast"]

    # Bind the remaining roster roles so the cast can be confirmed, then
    # confirm through the user-facing gate.
    assets = {item["file_name"]: item for item in VoiceAssetService.list_assets()}
    for role_id, filename in (
        ("role_b", "清亮_b.wav"),
        ("role_c", "低沉_c.wav"),
    ):
        VoiceCastResolver.bind_cast_role(
            scope_project, role_id, assets[filename]["voice_asset_id"]
        )
    confirmed = VoiceCastResolver.confirm_voice_cast(scope_project)
    assert confirmed["confirmed"] is True
    assert confirmed["confirmed_revision"] == confirmed["cast_revision"]

    started = ProductionJobService.start(
        scope_project,
        {"segment_ids": ["3-005"]},
        source="mcp",
        idempotency_key="scope-start",
    )
    assert started["created"] is True
    assert started["scope"]["segment_ids"] == ["3-005"]
    assert started["progress"]["selected_total"] == 1
    assert started["progress"]["already_completed"] == 0
    # confirm_voice_cast locks every bound role; start does not widen scope.
    cast = VoiceCastResolver.get_voice_cast(scope_project)
    assert cast["roles"]["role_a"]["locked"] is True
    assert cast["roles"].get("role_b", {}).get("locked", False) is True
    TaskRepository.request_control(started["task_id"], "cancel")


def test_start_rechecks_after_plan_when_binding_becomes_invalid(scope_project, monkeypatch):
    assets = {item["file_name"]: item for item in VoiceAssetService.list_assets()}
    for role_id, filename in (
        ("role_b", "清亮_b.wav"),
        ("role_c", "低沉_c.wav"),
    ):
        VoiceCastResolver.bind_cast_role(
            scope_project, role_id, assets[filename]["voice_asset_id"]
        )
    VoiceCastResolver.confirm_voice_cast(scope_project)
    plan = ProductionJobService.plan(scope_project, {"segment_ids": ["3-005"]})
    assert plan["ready"] is True
    project_dir = ProjectRepository.get_project_dir(scope_project)
    cast = VoiceCastRepository.load_cast(project_dir)
    cast["roles"]["role_a"]["voice_asset_id"] = "voice_missing_after_plan"
    VoiceCastRepository.save_cast(project_dir, cast)
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    with pytest.raises(ProductionJobError) as error:
        ProductionJobService.start(scope_project, {"segment_ids": ["3-005"]}, source="mcp")
    assert error.value.code == "PRODUCTION_BLOCKED"
    assert any(
        item.get("code") == "VOICE_ASSET_NOT_FOUND"
        for item in error.value.details["blockers"]
    )


def test_ui_scope_mapping_and_exact_preview(scope_project):
    assert app._scope_from_ui("all", ["3"], ["3-005"]) == {
        "all": True,
        "chapter_ids": [],
        "segment_ids": [],
    }
    assert app._scope_from_ui("chapters", ["3"], []) == {
        "all": False,
        "chapter_ids": ["3"],
        "segment_ids": [],
    }
    assert app._scope_from_ui("segments", [], ["3-005"]) == {
        "all": False,
        "chapter_ids": [],
        "segment_ids": ["3-005"],
    }
    assert app._scope_from_ui("segments", [], []) == {
        "all": False,
        "chapter_ids": [],
        "segment_ids": [],
    }
    rows = synth_progress.build_scope_preview_rows_from_script(
        ProjectRepository.load_project(scope_project)[1],
        selected_segment_ids=["3-005"],
    )
    assert [row[2] for row in rows] == ["3-005"]
    states = synth_progress.build_segment_states(
        scope_project,
        selected_segment_ids=["3-005"],
    )
    assert [state["seg_id"] for state in states] == ["3-005"]


def test_mcp_schema_and_structured_plan_expose_exact_segment_scope(scope_project):
    scope_schema = _TOOLS["plan_production"]["inputSchema"]["properties"]["scope"]
    assert scope_schema["properties"]["segment_ids"]["minItems"] == 1
    assert "不会扩大" in _TOOLS["plan_production"]["description"]
    result = handle_request({
        "jsonrpc": "2.0",
        "id": 91,
        "method": "tools/call",
        "params": {
            "name": "plan_production",
            "arguments": {
                "project_name": scope_project,
                "scope": {"all": False, "segment_ids": ["3-005"]},
            },
        },
    })["result"]
    payload = result["structuredContent"]
    assert result["isError"] is False
    assert payload["scope"]["segment_ids"] == ["3-005"]
    assert payload["selected_segment_count"] == 1
    assert payload["scope"]["chapter_ids"] == []
    assert payload["voice_cast"]["scope_ready"] is True
