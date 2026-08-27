"""Phase-3 ProductionJobService contract tests."""
from __future__ import annotations
from lib import project_paths

import json
import os

import pytest

from lib import tts_engine
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import ProductionJobError, ProductionJobService, ProjectService
from services.synthesis import SynthesisService


SCRIPT = {
    "meta": {"title": "生产任务测试"},
    "voices": {"旁白": {}},
    "chapters": [
        {
            "id": "001",
            "title": "第一章",
            "segments": [
                {"id": "001-001", "role": "旁白", "text": "一"},
                {"id": "001-002", "role": "旁白", "text": "二"},
            ],
        },
        {
            "id": "002",
            "title": "第二章",
            "segments": [
                {"id": "002-001", "role": "旁白", "text": "三"},
            ],
        },
    ],
}


@pytest.fixture
def production_project(tmp_path, monkeypatch):
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    monkeypatch.setattr(
        TaskRepository,
        "get_task_dir",
        staticmethod(lambda: str(tmp_path / "task_records")),
    )
    ProjectService.create_project_from_data("production", SCRIPT)
    project_dir = ProjectRepository.get_project_dir("production")
    voice_path = os.path.join(project_paths.project_dir(project_dir, "project_voices", create=True), "narrator.wav")
    os.makedirs(os.path.dirname(voice_path), exist_ok=True)
    with open(voice_path, "wb") as file:
        file.write(b"voice")
    bindings_path = project_paths.project_file(project_dir, "voice_bindings")
    with open(bindings_path, encoding="utf-8") as file:
        bindings = json.load(file)
    bindings["bindings"]["旁白"] = voice_path
    with open(bindings_path, "w", encoding="utf-8") as file:
        json.dump(bindings, file, ensure_ascii=False)
    # The inline runtime now owns engine bootstrap; keep service tests
    # GPU-free by stubbing the engine lifecycle without touching synthesis.
    monkeypatch.setattr(tts_engine, "init_engine", lambda: None)
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)
    ProductionJobService.reset_runtime()
    yield "production"
    ProductionJobService.reset_runtime()


def _fake_start(state, *_args, **_kwargs):
    state.status = "running"
    state.notify()
    return state.task_id


def test_plan_chapter_scope_and_completed_count(production_project):
    meta, _, _ = ProjectRepository.load_project(production_project)
    meta.segments_status["001-001"] = "done"
    ProjectRepository._save_meta(ProjectRepository.get_project_dir(production_project), meta)

    plan = ProductionJobService.plan(
        production_project,
        {"chapter_ids": ["001"]},
    )

    assert plan["ready"] is True
    assert plan["scope"]["chapter_ids"] == ["001"]
    assert plan["chapters"] == 1
    assert plan["segments"] == 2
    assert plan["already_completed"] == 0  # done without a cache is not complete
    assert plan["remaining"] == 2


def test_start_idempotency_and_active_project_constraint(production_project, monkeypatch):
    monkeypatch.setattr(SynthesisService, "start", staticmethod(_fake_start))
    first = ProductionJobService.start(
        production_project,
        {"chapter_ids": ["001"]},
        source="mcp",
        idempotency_key="agent-run-1",
    )
    replay = ProductionJobService.start(
        production_project,
        {"chapter_ids": ["001"]},
        source="mcp",
        idempotency_key="agent-run-1",
    )
    assert first["created"] is True
    assert replay["created"] is False
    assert replay["task_id"] == first["task_id"]

    with pytest.raises(Exception) as error:
        ProductionJobService.start(production_project, {"all": True}, source="web")
    assert getattr(error.value, "code", None) == "PROJECT_HAS_ACTIVE_TASK"


@pytest.mark.parametrize("task_type", ["synthesis", "export"])
@pytest.mark.parametrize(
    ("initial_status", "expected_status"),
    [
        ("interrupted", "cancelled"),
        ("pending", "cancelled"),
        ("paused", "cancelled"),
        ("cancelled", "cancelled"),
        ("done", "done"),
        ("error", "error"),
    ],
)
def test_cancel_is_terminal_and_idempotent_for_runtime_tasks(
    production_project,
    task_type,
    initial_status,
    expected_status,
):
    task_id = f"cancel_{task_type}_{initial_status}"
    record = TaskRecord(
        task_id=task_id,
        task_type=task_type,
        project=production_project,
        status=initial_status,
        source="mcp",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:00:00Z",
    )
    if task_type == "export":
        outcome, _ = TaskRepository.create_runtime_task(record)
        assert outcome == "created"
    else:
        TaskRepository.save_task(record)

    first = TaskRepository.request_control(task_id, "cancel")
    assert first.status == expected_status
    if initial_status in {"interrupted", "pending", "paused"}:
        assert first.control_intent == ""
        assert first.finished_at

    second = TaskRepository.request_control(task_id, "cancel")
    assert second.status == expected_status
    assert second.version == first.version
    assert second.updated_at == first.updated_at
    assert second.finished_at == first.finished_at


def test_active_export_blocks_start_production(production_project, monkeypatch):
    export = TaskRecord(
        task_id="active_export_for_production",
        task_type="export",
        project=production_project,
        status="running",
        owner_id="export-runtime",
        source="mcp",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:00:00Z",
    )
    outcome, _ = TaskRepository.create_runtime_task(export)
    assert outcome == "created"
    monkeypatch.setattr(SynthesisService, "start", staticmethod(_fake_start))

    with pytest.raises(ProductionJobError) as error:
        ProductionJobService.start(
            production_project,
            {"all": True},
            source="mcp",
        )

    assert error.value.code == "PROJECT_HAS_ACTIVE_TASK"
    assert error.value.details["task_id"] == export.task_id
    assert error.value.details["status"] == export.status


@pytest.mark.parametrize("task_type", ["synthesis", "export"])
@pytest.mark.parametrize("initial_status", ["pending", "paused"])
def test_claimed_pending_or_paused_cancel_requests_worker_stop(
    production_project,
    task_type,
    initial_status,
):
    task_id = f"claimed_cancel_{task_type}_{initial_status}"
    record = TaskRecord(
        task_id=task_id,
        task_type=task_type,
        project=production_project,
        status=initial_status,
        owner_id="runtime-owner",
        source="mcp",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:00:00Z",
    )
    if task_type == "export":
        outcome, _ = TaskRepository.create_runtime_task(record)
        assert outcome == "created"
    else:
        TaskRepository.save_task(record)

    first = TaskRepository.request_control(task_id, "cancel")
    assert first.status == "cancelling"
    assert first.control_intent == "cancel"

    second = TaskRepository.request_control(task_id, "cancel")
    assert second.status == "cancelling"
    assert second.control_intent == "cancel"
    assert second.version == first.version
    assert second.updated_at == first.updated_at


def test_idempotency_replays_only_same_normalized_payload(production_project, monkeypatch):
    monkeypatch.setattr(SynthesisService, "start", staticmethod(_fake_start))
    first = ProductionJobService.start(
        production_project,
        {"all": False, "segment_ids": ["001-002", "001-001", "001-002"]},
        {"num_beams": 2, "emo_alpha": 1},
        source="mcp",
        idempotency_key="normalized-payload",
    )
    replay = ProductionJobService.start(
        production_project,
        {"segment_ids": ["001-001", "001-002"]},
        {"emo_alpha": 1.0, "num_beams": 2},
        source="mcp",
        idempotency_key="normalized-payload",
    )
    assert replay["created"] is False
    assert replay["task_id"] == first["task_id"]


def test_voice_overrides_are_preserved_and_order_independent(
    production_project,
    monkeypatch,
):
    monkeypatch.setattr(SynthesisService, "start", staticmethod(_fake_start))
    first = ProductionJobService.start(
        production_project,
        {"segment_ids": ["001-001", "001-002"]},
        {
            "voice_overrides": {
                "001-002": "08_质检记录/repair_voices/two.wav",
                "001-001": "08_质检记录/repair_voices/one.wav",
            },
        },
        source="mcp",
        idempotency_key="voice-overrides",
    )
    replay = ProductionJobService.start(
        production_project,
        {"segment_ids": ["001-002", "001-001"]},
        {
            "voice_overrides": {
                "001-001": "08_质检记录/repair_voices/one.wav",
                "001-002": "08_质检记录/repair_voices/two.wav",
            },
        },
        source="mcp",
        idempotency_key="voice-overrides",
    )
    assert replay["created"] is False
    assert replay["task_id"] == first["task_id"]
    assert first["options"]["voice_overrides"] == {
        "001-001": "08_质检记录/repair_voices/one.wav",
        "001-002": "08_质检记录/repair_voices/two.wav",
    }


@pytest.mark.parametrize(
    ("scope", "options"),
    [
        ({"segment_ids": ["002-001"]}, {"num_beams": 2}),
        ({"segment_ids": ["001-001"]}, {"num_beams": 3}),
        (
            {"segment_ids": ["001-001"]},
            {
                "num_beams": 2,
                "voice_overrides": {"001-001": "08_质检记录/repair_voices/b.wav"},
            },
        ),
    ],
)
def test_idempotency_payload_conflict_is_stable(
    production_project,
    monkeypatch,
    scope,
    options,
):
    monkeypatch.setattr(SynthesisService, "start", staticmethod(_fake_start))
    first = ProductionJobService.start(
        production_project,
        {"segment_ids": ["001-001"]},
        {
            "num_beams": 2,
            "voice_overrides": {"001-001": "08_质检记录/repair_voices/a.wav"},
        } if "voice_overrides" in options else {"num_beams": 2},
        source="mcp",
        idempotency_key="conflicting-payload",
    )
    with pytest.raises(Exception) as error:
        ProductionJobService.start(
            production_project,
            scope,
            options,
            source="mcp",
            idempotency_key="conflicting-payload",
        )
    assert getattr(error.value, "code", None) == "IDEMPOTENCY_CONFLICT"
    assert error.value.details["task_id"] == first["task_id"]


def test_reading_running_task_does_not_infer_interrupted(production_project):
    TaskRepository.save_task(TaskRecord(
        task_id="task_restarted",
        task_type="synthesis",
        project=production_project,
        status="running",
        source="mcp",
        scope={"all": True, "chapter_ids": [], "segment_ids": []},
        progress={"total": 3, "completed": 1, "failed": 0},
        created_at="2026-08-08T00:00:00Z",
        updated_at="2026-08-08T00:01:00Z",
    ))
    ProductionJobService.reset_runtime()

    snapshot = ProductionJobService.get_task_snapshot("task_restarted")

    assert snapshot["status"] == "running"
    assert ProductionJobService.get_active_task(production_project)["task_id"] == "task_restarted"


def test_web_source_is_visible_to_task_listing(production_project, monkeypatch):
    monkeypatch.setattr(SynthesisService, "start", staticmethod(_fake_start))
    started = ProductionJobService.start(
        production_project,
        {"segment_ids": ["001-001"]},
        source="web",
    )
    listed = ProductionJobService.list_tasks(
        project_name=production_project,
        source="web",
    )
    assert listed[0]["task_id"] == started["task_id"]
    assert listed[0]["source"] == "web"


def test_mcp_server_has_phase3_tools_and_no_ui_dependency():
    from mcp_server.server import _ALL_TOOLS, _HANDLERS, _TOOLS

    names = {
        "plan_production", "start_production", "get_production_task",
        "list_production_tasks", "pause_production", "resume_production",
        "cancel_production", "retry_failed_segments",
    }
    assert names <= set(_ALL_TOOLS)
    assert set(_ALL_TOOLS) == set(_HANDLERS)
    assert set(_TOOLS) <= set(_ALL_TOOLS)
    source = open("mcp_server/server.py", encoding="utf-8").read()
    assert "import gradio" not in source
    assert "import app" not in source
