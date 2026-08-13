"""Delivery input freshness and historical manifest contracts."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest
from scipy.io import wavfile

from lib import postprocess, project_paths
from lib.tts_profile import resolve_profile
from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from repositories.voice_cast_repo import VoiceCastRepository
from services.delivery import compute_delivery_input_snapshot
from services.production_jobs import ProductionJobService
from services.quality import QualityService
from services.voice_cast import VoiceCastResolver
from services.workflow import WorkflowService


SCRIPT = {
    "meta": {"title": "Freshness 测试", "author": "测试作者"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试音频。"}],
    }],
}


@pytest.fixture
def freshness_project(tmp_path, monkeypatch):
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("freshness", SCRIPT)
    project_dir = ProjectRepository.get_project_dir("freshness")
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
    yield "freshness"
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


def _set_revision_engine(project_name: str, version: str) -> None:
    active = QualityRepository.get_active_revision(project_name, "001-001")
    assert active
    params = dict(active.get("params") or {})
    params["engine_snapshot"] = resolve_profile({
        "engine_version": version,
        "model_dir": os.path.join("/tmp", f"model-{version}"),
    })
    QualityRepository.update_revision(
        project_name,
        active["revision_id"],
        params=params,
    )


def _manifest(project_name: str, *, with_hash: bool = True) -> dict:
    payload = {"project": project_name, "ready": True, "format": "wav"}
    if with_hash:
        payload["delivery_input_hash"] = compute_delivery_input_snapshot(
            project_name
        )["delivery_input_hash"]
    return QualityRepository.create_history_record(
        project_name, "delivery_manifests", "manifest", payload
    )


def test_delivery_snapshot_is_deterministic_and_manifest_history_is_queryable(
    freshness_project,
):
    _finish_and_review(freshness_project)
    first = compute_delivery_input_snapshot(freshness_project)
    second = compute_delivery_input_snapshot(freshness_project)
    assert first == second
    assert first["delivery_input_hash"]
    assert first["structured_script_identity"]
    assert first["segment_order"][0]["segment_id"] == "001-001"
    revision = first["active_revisions"][0]
    assert {
        "revision_id",
        "audio_revision",
        "relative_path",
        "sha256",
        "cache_identity",
        "voice_fingerprint",
    } <= revision.keys()
    assert {"identity", "fingerprint"} <= first["voice_cast"].keys()

    historical = _manifest(freshness_project, with_hash=False)
    state = WorkflowService.get_state(freshness_project)
    assert state["stage"] == "quality_passed"
    assert state["summary"]["delivered"] is False
    assert QualityRepository.get_history_record(
        freshness_project, "delivery_manifests", historical["manifest_id"]
    )["manifest_id"] == historical["manifest_id"]


def test_delivery_snapshot_records_uniform_engine_provenance(freshness_project):
    _finish_and_review(freshness_project)
    _set_revision_engine(freshness_project, "2.5")

    snapshot = compute_delivery_input_snapshot(freshness_project)

    assert snapshot["engine_provenance"]["status"] == "uniform"
    assert snapshot["engine_provenance"]["engine_snapshot"]["engine_version"] == "2.5"
    assert "/tmp/model-2.5" not in str(snapshot)


def test_delivery_snapshot_marks_mixed_engine_provenance(freshness_project):
    _finish_and_review(freshness_project)
    # Add a second segment revision with the other native engine identity to
    # exercise the same provenance shape used by a mixed historical project.
    project_dir = ProjectRepository.get_project_dir(freshness_project)
    script_path = os.path.join(project_dir, "structured_script.json")
    with open(script_path, encoding="utf-8") as file:
        script = json.load(file)
    script["chapters"][0]["segments"].append({
        "id": "001-002", "role": "旁白", "text": "第二段。",
    })
    with open(script_path, "w", encoding="utf-8") as file:
        json.dump(script, file, ensure_ascii=False)
    segments = project_paths.project_dir(project_dir, "segments", create=True)
    wavfile.write(os.path.join(segments, "001-002.wav"), 22050, np.ones(22050, dtype=np.int16))
    ProjectRepository.update_segment_status(freshness_project, "001-002", "done")
    QualityService.ensure_active_revision(freshness_project, "001-002")
    _set_revision_engine(freshness_project, "2.5")
    _set_revision_engine(freshness_project, "2")
    second = QualityRepository.get_active_revision(freshness_project, "001-002")
    assert second
    params = dict(second.get("params") or {})
    params["engine_snapshot"] = resolve_profile({
        "engine_version": "2.5",
        "model_dir": os.path.join("/tmp", "model-2.5"),
    })
    QualityRepository.update_revision(freshness_project, second["revision_id"], params=params)

    snapshot = compute_delivery_input_snapshot(freshness_project)

    assert snapshot["engine_provenance"]["status"] == "mixed"
    assert {item["engine_version"] for item in snapshot["engine_provenance"]["engines"]} == {"2", "2.5"}


def test_repair_revision_stales_manifest_and_new_export_restores_delivery(
    freshness_project,
):
    _finish_and_review(freshness_project)
    old_manifest = _manifest(freshness_project)
    assert WorkflowService.get_state(freshness_project)["stage"] == "delivered"

    active = QualityRepository.get_active_revision(freshness_project, "001-001")
    assert active
    repaired = QualityRepository.create_revision(
        freshness_project,
        "001-001",
        relative_path=active["relative_path"],
        cache_identity=active["cache_identity"],
        voice_fingerprint=active["voice_fingerprint"],
        params=active["params"],
        metadata=active["metadata"],
        status="ready",
        activate=True,
    )
    assert repaired["revision_id"] != active["revision_id"]
    stale = WorkflowService.get_state(freshness_project)
    assert stale["stage"] != "delivered"
    assert stale["summary"]["delivered"] is False

    # The old record remains available for audit, while a fresh export records
    # the new active revision and makes delivery current again after QA.
    assert QualityRepository.get_history_record(
        freshness_project, "delivery_manifests", old_manifest["manifest_id"]
    )
    QualityService.run_technical_qa(freshness_project, "001-001")
    QualityService.mark_review(freshness_project, "001-001", "passed")
    new_manifest = _manifest(freshness_project)
    assert old_manifest["delivery_input_hash"] != new_manifest["delivery_input_hash"]
    restored = WorkflowService.get_state(freshness_project)
    assert restored["stage"] == "delivered"
    assert restored["summary"]["delivery_manifest_id"] == new_manifest["manifest_id"]


def test_voice_cast_change_and_invalidation_stale_delivery(
    freshness_project,
):
    _finish_and_review(freshness_project)
    _manifest(freshness_project)
    assert WorkflowService.get_state(freshness_project)["stage"] == "delivered"

    project_dir = ProjectRepository.get_project_dir(freshness_project)
    VoiceCastRepository.save_cast(project_dir, {
        "version": "1.0",
        "project_name": freshness_project,
        "status": "locked",
        "roles": {
            "role_narrator": {
                "name": "旁白",
                "voice_asset_id": "voice_changed",
                "voice_sha256": "changed",
                "project_voice_path": "voices/narrator.wav",
                "locked": True,
            },
        },
    })
    changed_cast = WorkflowService.get_state(freshness_project)
    assert changed_cast["stage"] == "quality_passed"
    assert changed_cast["summary"]["delivered"] is False

    ProjectRepository.invalidate_done_segments(freshness_project, ["001-001"])
    invalidated = WorkflowService.get_state(freshness_project)
    assert invalidated["stage"] == "ready_for_production"
    assert invalidated["summary"]["delivered"] is False
