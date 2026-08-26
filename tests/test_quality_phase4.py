"""Audio revision, legacy bootstrap and repository persistence contracts."""
from __future__ import annotations

import json
import multiprocessing
import os

import numpy as np
import pytest
from scipy.io import wavfile

from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from services.quality import QualityService


SCRIPT = {
    "meta": {"title": "质量测试", "author": "测试作者"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [
            {"id": "001-001", "role": "旁白", "text": "这是一段有效音频。"},
            {"id": "001-002", "role": "旁白", "text": "这是一段静音。"},
        ],
    }],
}


def _write_quality_history(
    workspace_root,
    legacy_root,
    project_name,
    collection,
    prefix,
    start_event,
    count,
):
    """Spawn-safe writer used to prove OS-level repository serialization."""
    ProjectRepository.WORKSPACE_ROOT = workspace_root
    ProjectRepository.LEGACY_ROOT = legacy_root
    ProjectRepository._INITIALIZED = True
    if not start_event.wait(10):
        raise TimeoutError("测试进程未收到并发启动信号")
    for index in range(count):
        QualityRepository.create_history_record(
            project_name,
            collection,
            prefix,
            {"writer": prefix, "sequence": index},
        )


@pytest.fixture
def quality_project(tmp_path):
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("quality", SCRIPT)
    project_dir = ProjectRepository.get_project_dir("quality")
    voice = os.path.join(
        project_paths.project_dir(project_dir, "voices", create=True),
        "narrator.wav",
    )
    wavfile.write(voice, 22050, np.ones(2205, dtype=np.int16))
    bindings = ProjectRepository.load_bindings(project_dir)
    bindings["bindings"]["旁白"] = voice
    ProjectRepository.save_bindings(project_dir, bindings)
    segments = project_paths.project_dir(project_dir, "segments", create=True)
    time_axis = np.linspace(0, 1, 22050, endpoint=False)
    valid = (np.sin(2 * np.pi * 220 * time_axis) * 6000).astype(np.int16)
    wavfile.write(os.path.join(segments, "001-001.wav"), 22050, valid)
    wavfile.write(
        os.path.join(segments, "001-002.wav"),
        22050,
        np.zeros(22050, dtype=np.int16),
    )
    yield "quality"
    (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    ) = original


def test_revision_inventory_bootstraps_legacy_audio(quality_project):
    inventory = QualityService.get_active_revision_inventory(quality_project)

    assert inventory["summary"] == {
        "segments": 2,
        "active_revisions": 2,
        "valid_audio": 2,
        "missing_revisions": 0,
        "invalid_audio": 0,
    }
    first = inventory["segments"][0]
    assert first["audio_revision"]["audio_revision"] == 1
    assert first["audio_status"] == "valid"
    assert first["checksum"]
    assert not os.path.isabs(first["relative_path"])


def test_new_state_omits_decommissioned_maps(quality_project):
    state = QualityRepository.load(quality_project)
    assert "technical_qa" not in state
    assert "human_reviews" not in state


def test_old_qa_maps_are_tolerated_without_being_used(quality_project):
    state = QualityRepository.load(quality_project)
    legacy = {
        "technical_qa": {"rev_old": {"outcome": "pass"}},
        "human_reviews": {"rev_old": {"status": "passed"}},
    }
    state.update(legacy)
    QualityRepository.save(quality_project, state)

    inventory = QualityService.get_active_revision_inventory(quality_project)
    loaded = QualityRepository.load(quality_project)

    assert inventory["summary"]["valid_audio"] == 2
    assert loaded["technical_qa"] == legacy["technical_qa"]
    assert loaded["human_reviews"] == legacy["human_reviews"]


def test_invalid_active_revision_is_reported_without_bootstrap(quality_project):
    project_dir = ProjectRepository.get_project_dir(quality_project)
    segments = project_paths.project_dir(project_dir, "segments", create=True)
    corrupt = os.path.join(segments, "corrupt.wav")
    revision = QualityRepository.create_revision(
        quality_project,
        "001-001",
        relative_path=project_paths.make_relative(project_dir, corrupt),
        status="ready",
        activate=True,
    )
    with open(corrupt, "wb") as file:
        file.write(b"not a wav")

    inventory = QualityService.get_active_revision_inventory(quality_project)
    item = next(item for item in inventory["segments"] if item["segment_id"] == "001-001")
    assert item["audio_revision"]["revision_id"] == revision["revision_id"]
    assert item["audio_exists"] is True
    assert item["audio_valid"] is False
    assert item["audio_status"] == "invalid"


def test_revision_history_is_project_local_and_json_safe(quality_project):
    first = QualityService.ensure_active_revision(quality_project, "001-001")
    archived = QualityService.archive_active_revision(quality_project, "001-001")
    assert archived["revision_id"] == first["revision_id"]
    assert archived["metadata"]["archived"] is True

    state_path = QualityRepository.state_path(quality_project)
    with open(state_path, encoding="utf-8") as file:
        state = json.load(file)
    assert state["active_revisions"]["001-001"] == first["revision_id"]
    assert not os.path.isabs(first["relative_path"])


def test_cross_process_mutations_keep_independent_record_types(quality_project):
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    count = 20
    args = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        quality_project,
    )
    repair_writer = context.Process(
        target=_write_quality_history,
        args=(
            *args,
            "repair_history",
            "repair",
            start_event,
            count,
        ),
    )
    export_writer = context.Process(
        target=_write_quality_history,
        args=(
            *args,
            "export_jobs",
            "export",
            start_event,
            count,
        ),
    )
    repair_writer.start()
    export_writer.start()
    start_event.set()
    repair_writer.join(20)
    export_writer.join(20)
    for process in (repair_writer, export_writer):
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert repair_writer.exitcode == 0
    assert export_writer.exitcode == 0

    state = QualityRepository.load(quality_project)
    assert len(state["repair_history"]) == count
    assert len(state["export_jobs"]) == count
    assert {
        record["sequence"] for record in state["repair_history"].values()
    } == set(range(count))
    assert {
        record["sequence"] for record in state["export_jobs"].values()
    } == set(range(count))


def test_failed_mutation_releases_process_lock(quality_project):
    def fail(_state):
        raise RuntimeError("intentional")

    with pytest.raises(RuntimeError, match="intentional"):
        QualityRepository._mutate(quality_project, fail)
    record = QualityRepository.create_history_record(
        quality_project,
        "repair_history",
        "repair",
        {"after_failure": True},
    )
    assert record["after_failure"] is True


SCRIPT_THREE = {
    "meta": {"title": "质量测试-三段", "author": "测试作者"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [
            {"id": "001-001", "role": "旁白", "text": "一段。"},
            {"id": "001-002", "role": "旁白", "text": "二段。"},
            {"id": "001-003", "role": "旁白", "text": "三段（从未生产）。"},
        ],
    }],
}


def test_never_produced_project_inventory_has_missing_audio(quality_project):
    """A project without generated files remains queryable as missing audio."""
    ProjectRepository.create_project_from_data("never3", SCRIPT_THREE)
    inventory = QualityService.get_active_revision_inventory("never3")
    assert inventory["summary"]["active_revisions"] == 0
    assert inventory["summary"]["valid_audio"] == 0
    assert inventory["summary"]["missing_revisions"] == 3
    assert {item["audio_status"] for item in inventory["segments"]} == {"missing"}
