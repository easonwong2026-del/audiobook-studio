"""Workflow and formal delivery contracts."""
from __future__ import annotations

import os
import time

import numpy as np
import pytest
from scipy.io import wavfile

from lib import postprocess, project_paths
from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from repositories.task_repo import TaskRecord, TaskRepository
from repositories.voice_cast_repo import VoiceCastRepository
from services.export import (
    DeliveryInputChanged,
    ExportIdempotencyConflict,
    ExportPlanError,
    ExportService,
)
from services.production_jobs import ProductionJobService
from services.quality import QualityService
from services.production_runtime import ProductionRuntimeClient
from services.voice_cast import VoiceCastResolver
from services.workflow import WorkflowService


SCRIPT = {
    "meta": {"title": "交付测试", "author": "测试作者"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [
            {"id": "001-001", "role": "旁白", "text": "交付测试音频。"},
        ],
    }],
}


@pytest.fixture
def delivery_project(tmp_path, monkeypatch):
    # Export is intentionally asynchronous.  Stop a prior inline runtime
    # before changing the process-global project roots so its worker cannot
    # observe this fixture's temporary directory mid-creation.
    ProductionRuntimeClient.reset_inline()
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("delivery", SCRIPT)
    project_dir = ProjectRepository.get_project_dir("delivery")
    voice = os.path.join(
        project_paths.project_dir(project_dir, "voices", create=True),
        "narrator.wav",
    )
    wavfile.write(voice, 22050, np.ones(2205, dtype=np.int16))
    bindings = ProjectRepository.load_bindings(project_dir)
    bindings["bindings"]["旁白"] = voice
    ProjectRepository.save_bindings(project_dir, bindings)
    monkeypatch.setattr(
        VoiceCastResolver,
        "get_voice_binding_status",
        staticmethod(lambda _project: {
            "bound": 1,
            "unbound": 0,
            "cast_locked": True,
            "synthesis_ready": True,
            "mode": "legacy_manual",
        }),
    )
    monkeypatch.setattr(
        ProductionJobService, "get_active_task", staticmethod(lambda _project: None)
    )
    monkeypatch.setattr(postprocess, "apply_eq", lambda path, enable=False: path)
    monkeypatch.setattr(
        postprocess,
        "normalize_loudness",
        lambda path, target_lufs=-16.0: path,
    )
    yield "delivery"
    ProductionRuntimeClient.reset_inline()
    (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    ) = original


def _finish_and_review(project_name: str) -> None:
    project_dir = ProjectRepository.get_project_dir(project_name)
    segments = project_paths.project_dir(project_dir, "segments", create=True)
    time_axis = np.linspace(0, 1, 22050, endpoint=False)
    audio = (np.sin(2 * np.pi * 220 * time_axis) * 5000).astype(np.int16)
    wavfile.write(os.path.join(segments, "001-001.wav"), 22050, audio)
    ProjectRepository.update_segment_status(project_name, "001-001", "done")
    QualityService.ensure_active_revision(project_name, "001-001")
    QualityService.run_technical_qa(project_name, "001-001")
    QualityService.mark_review(project_name, "001-001", "passed")


def test_workflow_moves_from_ready_to_quality_passed(delivery_project):
    ready = WorkflowService.get_state(delivery_project)
    assert ready["stage"] == "ready_for_production"
    assert ready["next_actions"][0]["tool"] == "start_production"

    _finish_and_review(delivery_project)
    passed = WorkflowService.get_state(delivery_project)
    assert passed["stage"] == "quality_passed"
    assert passed["next_actions"][0]["tool"] == "plan_export"


def test_formal_export_requires_qa_and_returns_only_public_paths(delivery_project):
    project_dir = ProjectRepository.get_project_dir(delivery_project)
    segments = project_paths.project_dir(project_dir, "segments", create=True)
    wavfile.write(
        os.path.join(segments, "001-001.wav"),
        22050,
        np.full(22050, 2000, dtype=np.int16),
    )
    ProjectRepository.update_segment_status(delivery_project, "001-001", "done")
    with pytest.raises(ExportPlanError):
        ExportService.start_export(delivery_project, "wav")

    QualityService.ensure_active_revision(delivery_project, "001-001")
    QualityService.run_technical_qa(delivery_project, "001-001")
    QualityService.mark_review(delivery_project, "001-001", "passed")
    exported = ExportService.start_export(
        delivery_project,
        "wav",
        subtitle_formats=("srt", "lrc"),
        idempotency_key="delivery-once",
    )
    assert exported["status"] in {"pending", "running", "done"}
    deadline = time.monotonic() + 5.0
    while exported["status"] not in {"done", "error", "interrupted"} and time.monotonic() < deadline:
        time.sleep(0.05)
        exported = ExportService.get_export_task(
            delivery_project, exported["export_id"]
        )
    assert exported["status"] == "done"
    manifest = ExportService.get_delivery_manifest(
        delivery_project, exported["export_id"]
    )
    assert manifest is not None
    assert manifest["ready"] is True
    assert {item["format"] for item in manifest["outputs"]} == {"wav", "srt", "lrc"}
    assert all(not os.path.isabs(item["relative_path"]) for item in manifest["outputs"])
    assert ExportService.get_delivery_manifest(delivery_project)["manifest_id"] == manifest["manifest_id"]

    workflow = WorkflowService.get_state(delivery_project)
    assert workflow["stage"] == "delivered"


def test_plan_export_blocks_active_production_and_repair(delivery_project):
    _finish_and_review(delivery_project)
    task = TaskRecord(
        task_id="synthesis_active_for_export",
        task_type="synthesis",
        project=delivery_project,
        status="running",
        owner_id="runtime-test",
    )
    TaskRepository.save_task(task)
    try:
        plan = ExportService.plan_export(delivery_project, "wav")
        assert plan["ready"] is False
        assert any(item["code"] == "PRODUCTION_ACTIVE" for item in plan["blockers"])
    finally:
        TaskRepository.delete_task(task.task_id)

    repair = QualityRepository.create_history_record(
        delivery_project,
        "repair_history",
        "repair",
        {"status": "running", "segment_ids": ["001-001"]},
    )
    plan = ExportService.plan_export(delivery_project, "wav")
    assert any(item["code"] == "REPAIR_ACTIVE" for item in plan["blockers"])
    assert repair["repair_id"]


def test_export_idempotency_replay_and_conflict(delivery_project):
    _finish_and_review(delivery_project)
    first = ExportService.start_export(
        delivery_project, "wav", idempotency_key="export-same"
    )
    replay = ExportService.start_export(
        delivery_project, "wav", idempotency_key="export-same"
    )
    assert replay["created"] is False
    assert replay["export_id"] == first["export_id"]
    with pytest.raises(ExportIdempotencyConflict):
        ExportService.start_export(
            delivery_project, "mp3", idempotency_key="export-same"
        )


def test_export_worker_rejects_delivery_input_mutation(delivery_project, monkeypatch):
    _finish_and_review(delivery_project)
    plan = ExportService.plan_export(delivery_project, "wav")
    record = TaskRecord(
        task_id="export_snapshot_toc",
        task_type="export",
        project=delivery_project,
        status="running",
        options=ExportService._task_options(plan, bitrate="192k"),
    )

    def mutate_during_export(project_dir, _fmt, _bitrate, output_dir="", **_kwargs):
        output = os.path.join(output_dir, "toc.wav")
        wavfile.write(output, 22050, np.ones(2205, dtype=np.int16))
        VoiceCastRepository.save_cast(
            project_dir,
            {"version": "1.0", "status": "locked", "roles": {
                "changed": {"voice_asset_id": "different", "voice_sha256": "changed"},
            }},
        )
        return output

    monkeypatch.setattr(ExportService, "export", staticmethod(mutate_during_export))
    with pytest.raises(DeliveryInputChanged):
        ExportService.execute_export_job(record)
    assert not QualityRepository.list_history(delivery_project, "delivery_manifests")


def test_start_export_returns_before_slow_worker_finishes(delivery_project, monkeypatch):
    _finish_and_review(delivery_project)

    def slow_export(_project_dir, _fmt, _bitrate, output_dir="", **_kwargs):
        time.sleep(0.5)
        output = os.path.join(output_dir, "slow.wav")
        wavfile.write(output, 22050, np.ones(2205, dtype=np.int16))
        return output

    monkeypatch.setattr(ExportService, "export", staticmethod(slow_export))
    started = time.monotonic()
    submitted = ExportService.start_export(delivery_project, "wav")
    elapsed = time.monotonic() - started
    assert elapsed < 0.30
    assert submitted["status"] in {"pending", "running"}
    finished = submitted
    deadline = time.monotonic() + 5.0
    while finished["status"] not in {"done", "error", "interrupted"} and time.monotonic() < deadline:
        time.sleep(0.05)
        finished = ExportService.get_export_task(
            delivery_project, submitted["export_id"]
        )
    assert finished["status"] == "done"
    ProductionRuntimeClient.reset_inline()
