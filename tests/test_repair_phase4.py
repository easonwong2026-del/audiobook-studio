"""RepairService uses ProductionJobService while preserving prior revisions."""
from __future__ import annotations

import os

import numpy as np
import pytest
from scipy.io import wavfile

from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from services.production_jobs import ProductionJobService
from services.quality import QualityService
from services.repair import RepairService


SCRIPT = {
    "meta": {"title": "修复测试", "author": "测试作者"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [
            {"id": "001-001", "role": "旁白", "text": "需要重新生成的段落。"},
        ],
    }],
}


@pytest.fixture
def repair_project(tmp_path):
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("repair", SCRIPT)
    project_dir = ProjectRepository.get_project_dir("repair")
    voice = os.path.join(
        project_paths.project_dir(project_dir, "project_voices", create=True),
        "narrator.wav",
    )
    wavfile.write(voice, 22050, np.ones(2205, dtype=np.int16))
    bindings = ProjectRepository.load_bindings(project_dir)
    bindings["bindings"]["旁白"] = voice
    ProjectRepository.save_bindings(project_dir, bindings)
    segments = project_paths.project_dir(project_dir, "segments", create=True)
    wavfile.write(
        os.path.join(segments, "001-001.wav"),
        22050,
        np.full(22050, 2000, dtype=np.int16),
    )
    ProjectRepository.update_segment_status("repair", "001-001", "done")
    yield "repair"
    (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    ) = original


def test_repair_submits_segment_scope_and_activates_new_revision(
    repair_project, monkeypatch
):
    old = QualityService.ensure_active_revision(repair_project, "001-001")
    calls = []

    def fake_start(project, scope, options, **kwargs):
        calls.append((project, scope, options, kwargs))
        target, _identity, _fingerprint, _params = QualityService.expected_audio_path(
            project, "001-001", params=options
        )
        time_axis = np.linspace(0, 1, 22050, endpoint=False)
        audio = (np.sin(2 * np.pi * 330 * time_axis) * 5000).astype(np.int16)
        wavfile.write(target, 22050, audio)
        ProjectRepository.update_segment_status(project, "001-001", "done")
        return {"task_id": "task_fake_repair", "status": "done"}

    monkeypatch.setattr(ProductionJobService, "start", staticmethod(fake_start))
    monkeypatch.setattr(
        ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda _task_id: {
            "task_id": "task_fake_repair",
            "status": "done",
            "progress": {"total": 1, "completed": 1},
            "finished_at": "2026-08-09T00:00:00Z",
            "error_summary": "",
        }),
    )
    result = RepairService.start(
        repair_project,
        ["001-001"],
        emotion="happy",
        emo_alpha=0.8,
        speech_rate=1.1,
        source="mcp",
        requested_by="agent:test",
        idempotency_key="repair-once",
    )

    assert result["status"] == "done"
    assert calls[0][1] == {"segment_ids": ["001-001"]}
    revisions = QualityRepository.list_revisions(repair_project, "001-001")
    assert len(revisions) == 2
    active = QualityRepository.get_active_revision(repair_project, "001-001")
    assert active["revision_id"] != old["revision_id"]
    assert active["params"]["emotion"] == "happy"
    assert os.path.isfile(QualityService.resolve_active_audio(repair_project, "001-001"))
    state = QualityRepository.load(repair_project)
    assert state["technical_qa"][active["revision_id"]]["outcome"] == "pass"

    replay = RepairService.start(
        repair_project,
        ["001-001"],
        emotion="happy",
        idempotency_key="repair-once",
    )
    assert replay["created"] is False
    assert replay["repair_id"] == result["repair_id"]


def test_repair_copies_temporary_voice_and_routes_it_per_segment(
    repair_project, monkeypatch, tmp_path
):
    override = tmp_path / "temporary_voice.wav"
    wavfile.write(override, 22050, np.full(2205, 700, dtype=np.int16))
    captured = {}

    def fake_start(project, _scope, options, **_kwargs):
        captured.update(options)
        relative = options["voice_overrides"]["001-001"]
        copied = os.path.join(
            ProjectRepository.get_project_dir(project),
            *relative.split("/"),
        )
        assert os.path.isfile(copied)
        target, _identity, _fingerprint, _params = QualityService.expected_audio_path(
            project,
            "001-001",
            params=options,
            speaker_override=copied,
        )
        wavfile.write(target, 22050, np.full(22050, 1200, dtype=np.int16))
        ProjectRepository.update_segment_status(project, "001-001", "done")
        return {"task_id": "task_voice_override", "status": "done"}

    monkeypatch.setattr(ProductionJobService, "start", staticmethod(fake_start))
    monkeypatch.setattr(
        ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda _task_id: {
            "task_id": "task_voice_override",
            "status": "done",
            "progress": {"total": 1, "completed": 1},
            "finished_at": "2026-08-09T00:00:00Z",
            "error_summary": "",
        }),
    )

    result = RepairService.start(
        repair_project,
        ["001-001"],
        voice_override=str(override),
    )

    assert result["status"] == "done"
    relative = captured["voice_overrides"]["001-001"]
    assert not os.path.isabs(relative)
    assert relative.startswith("99_系统数据/质检/")
