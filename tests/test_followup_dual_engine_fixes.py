"""Follow-up regression tests for PR #43 review findings.

Covers the three P0/P1 follow-up fixes:
- P0-1: plan/retry must use the real Voice Cast speaker fingerprint so done
        segments produced under a cast binding are counted as completed and
        are never reported retryable.
- P0-2: segment artifact provenance must support per-segment engine candidates
        (batch / mixed-engine projects), not just the newest production task.
- P1:   the UI top status must show the project's historical production
        engine(s) separately from the runtime current engine.

The 2.5 synthesis path is unavailable in the test venv (no infer_v2_5 module,
no whisper), so every 2.5 scenario uses the same stubbed artifact generation
as the existing PR #43 tests: write a real WAV named by the engine-aware cache
key and let the resolver match it by provenance.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import wavfile

from lib import audio_pipeline, project_paths, segment_cache
from lib.tts_profile import resolve_profile
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import ProductionJobService, ProjectService
from services.review_audio import _segment_audio, _valid_wav_file, preview_cache_dir
from services.voice_assets import VoiceAssetService
from services.voice_cast import VoiceCastResolver


@pytest.fixture
def engine_config(monkeypatch):
    """Force Settings default engine and keep the real config stable."""
    from lib import tts_profile

    def _set(version: str) -> None:
        monkeypatch.setattr(
            tts_profile,
            "_raw_config",
            lambda: {
                "engine_version": version,
                "model_dir": "C:/models/index-tts/checkpoints",
                "model_dir_v25": "C:/models/index-tts/checkpoints-v2.5",
            },
        )
    return _set


def _make_wav(path: str, *, seconds: float = 0.3, rate: int = 16000, tone: bool = True) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if tone:
        time_axis = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
        data = (np.sin(2 * np.pi * 220 * time_axis) * 6000).astype(np.int16)
    else:
        data = np.zeros(int(rate * seconds), dtype=np.int16)
    wavfile.write(path, rate, data)
    return path


SCRIPT = {
    "meta": {"title": "followup", "author": "测试"},
    "voices": {"小明": {}, "小红": {}},
    "chapters": [
        {
            "id": "1",
            "title": "第一章",
            "segments": [
                {"id": "1-001", "text": "第一句。", "role": "小明", "emotion": "neutral"},
                {"id": "1-002", "text": "第二句。", "role": "小红", "emotion": "neutral"},
                {"id": "1-003", "text": "第三句。", "role": "小明", "emotion": "neutral"},
            ],
        }
    ],
}


@pytest.fixture
def vc_project(tmp_path, monkeypatch):
    """Voice Cast project with two bound roles and two library voices."""
    data_dir = tmp_path / "data"
    library = data_dir / "voice_library"
    library.mkdir(parents=True)
    _make_wav(str(library / "xiaoming.wav"), tone=True)
    _make_wav(str(library / "xiaohong.wav"), tone=True)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(data_dir / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    from lib import project_manager as pm

    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(pm, "LEGACY_ROOT", str(data_dir / "legacy"))
    monkeypatch.setattr(
        TaskRepository,
        "get_task_dir",
        staticmethod(lambda: str(tmp_path / "task_records")),
    )
    ProjectService.create_project_from_data("vc", SCRIPT)
    VoiceCastResolver.set_character_roster(
        "vc",
        [
            {"role_id": "role_xiaoming", "name": "小明"},
            {"role_id": "role_xiaohong", "name": "小红"},
        ],
    )
    assets = {item["file_name"]: item for item in VoiceAssetService.list_assets()}
    VoiceCastResolver.set_voice_cast(
        "vc",
        {
            "role_xiaoming": {"voice_asset_id": assets["xiaoming.wav"]["voice_asset_id"]},
            "role_xiaohong": {"voice_asset_id": assets["xiaohong.wav"]["voice_asset_id"]},
        },
    )
    yield "vc"


@pytest.fixture(autouse=True)
def _clear_provenance_cache():
    segment_cache._TASK_ENGINE_CACHE.clear()
    yield
    segment_cache._TASK_ENGINE_CACHE.clear()


def _speaker_fingerprint(project_name: str, role_id: str) -> str | None:
    project_dir = ProjectRepository.get_project_dir(project_name)
    bindings = ProjectRepository.load_bindings(project_dir)
    binding = bindings.get("role_bindings", {}).get(role_id, {})
    path = str(binding.get("project_voice_path") or "")
    if path and not os.path.isabs(path):
        path = os.path.join(project_dir, path)
    return segment_cache.speaker_fingerprint_for_path(path)


def _segments_dir(project_name: str) -> str:
    return project_paths.project_dir(
        ProjectRepository.get_project_dir(project_name), "segments"
    )


def _write_engine_wav(project_name: str, seg_id: str, fp: str, profile: dict) -> str:
    key = segment_cache.segment_cache_key(
        seg_id, "neutral", speaker_fingerprint=fp,
        engine_identity=profile["cache_identity"],
    )
    path = os.path.join(_segments_dir(project_name), f"{key}.wav")
    _make_wav(path)
    return key


def _create_task(
    project_name: str,
    task_id: str,
    *,
    profile: dict,
    segment_ids: list[str] | None = None,
    all_scope: bool = False,
    created_at: str,
) -> None:
    scope = (
        {"all": True, "chapter_ids": [], "segment_ids": []}
        if all_scope
        else {"all": False, "chapter_ids": [], "segment_ids": list(segment_ids or [])}
    )
    TaskRepository.create_production_task(TaskRecord(
        task_id=task_id,
        task_type="synthesis",
        project=project_name,
        status="done",
        scope=scope,
        options={"num_beams": 2, "engine_snapshot": profile},
        created_at=created_at,
        updated_at=created_at,
        finished_at=created_at,
    ))


# ---------------------------------------------------------------------------
# P0-1: plan / retry use the real speaker fingerprint
# ---------------------------------------------------------------------------


def test_plan_counts_done_speaker_engine_wav_as_completed(vc_project, engine_config):
    """Voice Cast + Settings 2.5 + done segment with real speaker+engine wav."""
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    fp = _speaker_fingerprint(vc_project, "role_xiaoming")
    profile_25 = resolve_profile({"engine_version": "2.5"})
    _write_engine_wav(vc_project, "1-001", fp, profile_25)
    ProjectRepository.update_segment_status(vc_project, "1-001", "done")

    plan = ProductionJobService.plan(vc_project, {"all": True})
    assert plan["already_completed"] == 1
    assert plan["remaining"] == 2
    assert plan["failed"] == 0
    # The plan's effective engine is still the frozen task engine (2.5).
    assert plan["engine"]["engine_version"] == "2.5"


def test_retryable_segments_excludes_done_speaker_engine_wav(vc_project, engine_config):
    """Same scenario: _retryable_segments must not include the done segment."""
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    fp = _speaker_fingerprint(vc_project, "role_xiaoming")
    profile_25 = resolve_profile({"engine_version": "2.5"})
    _write_engine_wav(vc_project, "1-001", fp, profile_25)
    ProjectRepository.update_segment_status(vc_project, "1-001", "done")
    record = TaskRecord(
        task_id="task_retry",
        task_type="synthesis",
        project=vc_project,
        status="error",
        scope={"all": True, "chapter_ids": [], "segment_ids": []},
        options={"num_beams": 2, "engine_snapshot": profile_25},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:00Z",
    )
    TaskRepository.create_production_task(record)
    retryable = ProductionJobService._retryable_segments(record)
    assert "1-001" not in retryable
    assert "1-002" in retryable
    assert "1-003" in retryable


# ---------------------------------------------------------------------------
# P0-2: per-segment engine provenance for batch / mixed-engine projects
# ---------------------------------------------------------------------------


def test_old_task_v2_segment_reviewable_after_newer_v25_task(vc_project, engine_config):
    """Task A(v2, 1-001) then Task B(v2.5, 1-002); Settings 2.5; 1-001 stays reviewable."""
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    fp = _speaker_fingerprint(vc_project, "role_xiaoming")
    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_25 = resolve_profile({"engine_version": "2.5"})
    key_v2 = _write_engine_wav(vc_project, "1-001", fp, profile_v2)
    _create_task(
        vc_project, "task_a_v2",
        profile=profile_v2, segment_ids=["1-001"], created_at="2026-01-01T00:00:00Z",
    )
    _create_task(
        vc_project, "task_b_v25",
        profile=profile_25, segment_ids=["1-002"], created_at="2026-01-02T00:00:00Z",
    )

    project_dir = ProjectRepository.get_project_dir(vc_project)
    audio = _segment_audio(vc_project, project_dir, {
        "id": "1-001", "text": "第一句。", "role": "小明", "emotion": "neutral",
    })
    assert audio is not None
    assert os.path.basename(audio) == f"{key_v2}.wav"
    assert _valid_wav_file(audio)


def test_chapter_preview_mixed_engine_segments(vc_project, engine_config):
    """Same chapter with segment A=v2 and segment B=v2.5 previews correctly."""
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    fp = _speaker_fingerprint(vc_project, "role_xiaoming")
    fp_h = _speaker_fingerprint(vc_project, "role_xiaohong")
    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_25 = resolve_profile({"engine_version": "2.5"})
    # Segment A (1-001) and C (1-003) were produced by the v2 task; B by v2.5.
    _write_engine_wav(vc_project, "1-001", fp, profile_v2)
    _write_engine_wav(vc_project, "1-002", fp_h, profile_25)
    _write_engine_wav(vc_project, "1-003", fp, profile_v2)
    _create_task(
        vc_project, "task_a_v2",
        profile=profile_v2, segment_ids=["1-001", "1-003"],
        created_at="2026-01-01T00:00:00Z",
    )
    _create_task(
        vc_project, "task_b_v25",
        profile=profile_25, segment_ids=["1-002"],
        created_at="2026-01-02T00:00:00Z",
    )

    project_dir = ProjectRepository.get_project_dir(vc_project)
    output_path = os.path.join(preview_cache_dir(vc_project, "chapters"), "mixed.wav")
    result = audio_pipeline.concat_for_preview(project_dir, "1", output_path)
    assert result is not None
    assert os.path.isfile(result)
    assert os.path.getsize(result) > 0


def test_export_mixed_engine_project_succeeds(vc_project, engine_config):
    """Export must find every segment's real file across two engines."""
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    fp = _speaker_fingerprint(vc_project, "role_xiaoming")
    fp_h = _speaker_fingerprint(vc_project, "role_xiaohong")
    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_25 = resolve_profile({"engine_version": "2.5"})
    _write_engine_wav(vc_project, "1-001", fp, profile_v2)
    _write_engine_wav(vc_project, "1-002", fp_h, profile_25)
    _write_engine_wav(vc_project, "1-003", fp, profile_v2)
    _create_task(
        vc_project, "task_a_v2",
        profile=profile_v2, segment_ids=["1-001", "1-003"],
        created_at="2026-01-01T00:00:00Z",
    )
    _create_task(
        vc_project, "task_b_v25",
        profile=profile_25, segment_ids=["1-002"],
        created_at="2026-01-02T00:00:00Z",
    )

    project_dir = ProjectRepository.get_project_dir(vc_project)
    output = audio_pipeline.export_book(
        project_dir, format="wav",
        output_dir=project_paths.project_dir(project_dir, "delivery_official", create=True),
    )
    assert os.path.isfile(output)
    assert os.path.getsize(output) > 0


def test_provenance_cache_invalidated_on_new_task(vc_project, engine_config):
    """A new task is recognized immediately, not after the 10s TTL window."""
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    fp = _speaker_fingerprint(vc_project, "role_xiaoming")
    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_25 = resolve_profile({"engine_version": "2.5"})
    _write_engine_wav(vc_project, "1-001", fp, profile_v2)
    _create_task(
        vc_project, "task_a_v2",
        profile=profile_v2, segment_ids=["1-001"],
        created_at="2026-01-01T00:00:00Z",
    )
    # Fill the provenance cache for 1-001.
    artifact = segment_cache.resolve_segment_artifact(
        segments_dir=_segments_dir(vc_project), seg_id="1-001",
        speaker_fingerprint=fp, project_name=vc_project,
    )
    assert artifact.exists()
    # Create Task B within the TTL window; create_production_task must
    # invalidate the cache so the new engine is visible immediately.
    key_25 = _write_engine_wav(vc_project, "1-002", fp, profile_25)
    _create_task(
        vc_project, "task_b_v25",
        profile=profile_25, segment_ids=["1-002"],
        created_at="2026-01-02T00:00:00Z",
    )
    artifact_b = segment_cache.resolve_segment_artifact(
        segments_dir=_segments_dir(vc_project), seg_id="1-002",
        speaker_fingerprint=fp, project_name=vc_project,
    )
    assert artifact_b.exists()
    assert os.path.basename(artifact_b.path) == f"{key_25}.wav"
    assert artifact_b.engine_source == "task"


def test_project_production_engines_reports_distinct_engines(vc_project, engine_config):
    engine_config("2.5")
    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_25 = resolve_profile({"engine_version": "2.5"})
    _create_task(
        vc_project, "task_a_v2",
        profile=profile_v2, all_scope=True, created_at="2026-01-01T00:00:00Z",
    )
    _create_task(
        vc_project, "task_b_v25",
        profile=profile_25, all_scope=True, created_at="2026-01-02T00:00:00Z",
    )
    engines = segment_cache.project_production_engines(vc_project)
    identities = {item.get("cache_identity") for item in engines}
    assert len(identities) == 2
    assert profile_v2["cache_identity"] in identities
    assert profile_25["cache_identity"] in identities
    # Newest task first.
    assert engines[0]["cache_identity"] == profile_25["cache_identity"]


# ---------------------------------------------------------------------------
# P1: UI production-engine display semantics
# ---------------------------------------------------------------------------


class _FakeSS:
    """Minimal session state consumed by app.refresh_top_status."""

    def __init__(self, project: str, script: dict):
        self.project = project
        self._script = script

    def ensure_snapshot(self):
        return SimpleNamespace(
            meta=SimpleNamespace(completed_count=1, total_segments=3),
            script=self._script,
        )

    def set_snapshot(self, snap):
        pass


def test_ui_shows_project_history_engine_not_runtime_current(vc_project, engine_config, monkeypatch):
    """Project history = 2.5, runtime current = 2 → UI must not claim 2."""
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    profile_25 = resolve_profile({"engine_version": "2.5"})
    _create_task(
        vc_project, "task_25",
        profile=profile_25, all_scope=True, created_at="2026-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        ProductionJobService,
        "get_runtime_health",
        staticmethod(lambda: {
            "runtime_state": "running",
            "owner_id": "runtime-1",
            "pid": 1234,
            "engine_state": "ready",
            "engine_generation": 1,
            "recovery_count": 0,
            "last_error_code": "",
            "last_recovery_at": "",
            "updated_at": "",
            "runtime_updated_at": "",
            "status_stale": False,
            "active_task_id": None,
            "active_task": None,
            "engine_backend": "indextts",
            "engine_version": "2",
            "engine_identity": "indextts:2",
            "model_identity": "model-v2",
            "precision": "FP16",
            "device": "cuda",
            "cache_identity": "indextts:2|model-v2|FP16",
            "global_default_engine": {},
            "configured_default": {},
            "runtime_current": {
                "engine_backend": "indextts",
                "engine_version": "2",
                "engine_identity": "indextts:2",
                "model_identity": "model-v2",
                "precision": "FP16",
                "device": "cuda",
                "cache_identity": "indextts:2|model-v2|FP16",
            },
            "task_engine": {},
        }),
    )
    import app

    text = app.refresh_top_status(_FakeSS(vc_project, SCRIPT))
    assert "项目引擎：IndexTTS 2.5" in text
    assert "Runtime：IndexTTS 2" in text
    # The runtime current engine must never be presented as the project engine.
    assert "项目引擎：IndexTTS 2 ·" not in text
    assert "生产引擎: IndexTTS 2" not in text


def test_ui_no_production_shows_no_history(vc_project, engine_config, monkeypatch):
    """No production task → '项目引擎：尚无生产记录' (Settings not disguised)."""
    engine_config("2.5")
    monkeypatch.setattr(
        ProductionJobService,
        "get_runtime_health",
        staticmethod(lambda: {
            "runtime_state": "idle",
            "owner_id": "",
            "pid": 0,
            "engine_state": "unknown",
            "engine_generation": 0,
            "recovery_count": 0,
            "last_error_code": "",
            "last_recovery_at": "",
            "updated_at": "",
            "runtime_updated_at": "",
            "status_stale": False,
            "active_task_id": None,
            "active_task": None,
            "engine_backend": "",
            "engine_version": "",
            "engine_identity": "",
            "model_identity": "",
            "precision": "",
            "device": "",
            "cache_identity": "",
            "global_default_engine": {},
            "configured_default": {},
            "runtime_current": {},
            "task_engine": {},
        }),
    )
    import app

    text = app.refresh_top_status(_FakeSS(vc_project, SCRIPT))
    assert "项目引擎：尚无生产记录" in text
    assert "项目引擎：IndexTTS" not in text


def test_ui_mixed_engine_shows_both(vc_project, engine_config, monkeypatch):
    """Mixed-engine project must not claim a single engine."""
    engine_config("2.5")
    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_25 = resolve_profile({"engine_version": "2.5"})
    _create_task(
        vc_project, "task_a_v2",
        profile=profile_v2, all_scope=True, created_at="2026-01-01T00:00:00Z",
    )
    _create_task(
        vc_project, "task_b_v25",
        profile=profile_25, all_scope=True, created_at="2026-01-02T00:00:00Z",
    )
    monkeypatch.setattr(
        ProductionJobService,
        "get_runtime_health",
        staticmethod(lambda: {
            "runtime_state": "idle",
            "owner_id": "",
            "pid": 0,
            "engine_state": "unknown",
            "engine_generation": 0,
            "recovery_count": 0,
            "last_error_code": "",
            "last_recovery_at": "",
            "updated_at": "",
            "runtime_updated_at": "",
            "status_stale": False,
            "active_task_id": None,
            "active_task": None,
            "engine_backend": "",
            "engine_version": "",
            "engine_identity": "",
            "model_identity": "",
            "precision": "",
            "device": "",
            "cache_identity": "",
            "global_default_engine": {},
            "configured_default": {},
            "runtime_current": {},
            "task_engine": {},
        }),
    )
    import app

    text = app.refresh_top_status(_FakeSS(vc_project, SCRIPT))
    assert "项目引擎：IndexTTS 2.5 / IndexTTS 2" in text


def test_refresh_production_engine_status_separates_project_and_runtime(
    vc_project, engine_config, monkeypatch,
):
    engine_config("2.5")
    profile_25 = resolve_profile({"engine_version": "2.5"})
    _create_task(
        vc_project, "task_25",
        profile=profile_25, all_scope=True, created_at="2026-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        ProductionJobService,
        "get_runtime_health",
        staticmethod(lambda: {
            "runtime_state": "running",
            "owner_id": "runtime-1",
            "pid": 9,
            "engine_state": "ready",
            "engine_generation": 1,
            "recovery_count": 0,
            "last_error_code": "",
            "last_recovery_at": "",
            "updated_at": "",
            "runtime_updated_at": "",
            "status_stale": False,
            "active_task_id": None,
            "active_task": None,
            "engine_backend": "indextts",
            "engine_version": "2",
            "engine_identity": "indextts:2",
            "model_identity": "model-v2",
            "precision": "FP16",
            "device": "cuda",
            "cache_identity": "indextts:2|model-v2|FP16",
            "global_default_engine": {},
            "configured_default": {},
            "runtime_current": {},
            "task_engine": {},
        }),
    )
    import app

    text = app.refresh_production_engine_status(_FakeSS(vc_project, SCRIPT))
    assert text.split("\n")[0] == "项目引擎：IndexTTS 2.5"
    assert "Runtime：IndexTTS 2" in text
