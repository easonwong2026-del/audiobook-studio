"""Phase-3 ProductionJobService contract tests."""
from __future__ import annotations

import json
import os

import pytest

from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import ProductionJobService, ProjectService
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
    voice_path = os.path.join(project_dir, "voices", "narrator.wav")
    os.makedirs(os.path.dirname(voice_path), exist_ok=True)
    with open(voice_path, "wb") as file:
        file.write(b"voice")
    bindings_path = os.path.join(project_dir, "voice_bindings.json")
    with open(bindings_path, encoding="utf-8") as file:
        bindings = json.load(file)
    bindings["bindings"]["旁白"] = voice_path
    with open(bindings_path, "w", encoding="utf-8") as file:
        json.dump(bindings, file, ensure_ascii=False)
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


def test_running_persisted_without_runtime_becomes_interrupted(production_project):
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

    assert snapshot["status"] == "interrupted"
    assert ProductionJobService.get_active_task(production_project) is None


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
    from mcp_server.server import _HANDLERS, _TOOLS

    names = {
        "plan_production", "start_production", "get_production_task",
        "list_production_tasks", "pause_production", "resume_production",
        "cancel_production", "retry_failed_segments",
    }
    assert names <= set(_TOOLS) <= set(_HANDLERS) | {"server_info"}
    source = open("mcp_server/server.py", encoding="utf-8").read()
    assert "import gradio" not in source
    assert "import app" not in source
