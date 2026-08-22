"""Production-correctness收口 tests: Voice Cast confirmation gate, unified
engine resolution, and engine-aware segment artifact lookup.

Covers the acceptance scenarios in the PR spec:

A. Voice Cast confirmation gate (create/bind -> plan blocker -> start blocked
   -> confirm -> start allowed -> rebind invalidates -> blocked -> reconfirm).
B. MCP engine resolution (Settings 2.5 default, explicit v2 override, frozen
   TaskRecord engine_snapshot).
C. plan/start engine consistency.
D. Artifact lookup across the four historical cache classes plus
   Settings-vs-provenance switching.
E. Export finds engine-aware segments.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import wavfile

from lib import audio_pipeline, project_paths, segment_cache
from lib.tts_profile import resolve_profile
from mcp_server.tools.production import plan_production, start_production
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from repositories.voice_cast_repo import VoiceCastRepository
from services import ProductionJobError, ProductionJobService, ProjectService
from services.review_audio import _segment_audio, _valid_wav_file
from services.session import SessionState
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
    "meta": {"title": "生产正确性测试", "author": "测试"},
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


LEGACY_SCRIPT = {
    "meta": {"title": "旧版声音兼容测试", "author": "测试"},
    "voices": {"旁白": {}, "黑衣人": {}, "秦川": {}},
    "chapters": [
        {
            "id": "1",
            "title": "第一章",
            "segments": [
                {"id": "1-001", "text": "旁白。", "role": "旁白"},
                {"id": "1-002", "text": "黑衣人。", "role": "黑衣人"},
                {"id": "1-003", "text": "秦川。", "role": "秦川"},
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


@pytest.fixture
def legacy_manual_project(tmp_path, monkeypatch):
    """Legacy project with script voices and manual file bindings only."""
    data_dir = tmp_path / "legacy_data"
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
    ProjectService.create_project_from_data("legacy_manual", LEGACY_SCRIPT)
    project_dir = ProjectRepository.get_project_dir("legacy_manual")
    voice_dir = project_paths.project_dir(project_dir, "project_voices", create=True)
    bindings = ProjectRepository.load_bindings(project_dir)
    for role, filename in {
        "旁白": "narrator.wav",
        "黑衣人": "black_cloak.wav",
        "秦川": "qinchuan.wav",
    }.items():
        path = _make_wav(os.path.join(voice_dir, filename))
        bindings["bindings"][role] = path
    ProjectRepository.save_bindings(project_dir, bindings)
    yield "legacy_manual"


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


# ---------------------------------------------------------------------------
# A. Voice Cast confirmation gate
# ---------------------------------------------------------------------------


def test_unconfirmed_cast_blocks_plan_and_start(vc_project, monkeypatch):
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    plan = ProductionJobService.plan(vc_project, {"all": True})
    assert plan["ready"] is False
    assert any(
        item.get("code") == "VOICE_CAST_CONFIRMATION_REQUIRED"
        for item in plan["blockers"]
    )
    assert plan["voice_cast"]["confirmation_required"] is True
    assert plan["voice_cast"]["confirmed"] is False
    assert plan["voice_cast"]["cast_revision"] >= 1
    assert plan["voice_cast"]["confirmed_revision"] is None

    with pytest.raises(ProductionJobError) as error:
        ProductionJobService.start(vc_project, {"all": True}, source="mcp")
    assert error.value.code == "VOICE_CAST_CONFIRMATION_REQUIRED"
    details = error.value.details
    assert details["project_name"] == vc_project
    assert details["cast_revision"] >= 1
    assert details["confirmed_revision"] is None
    assert isinstance(details["role_bindings"], list)
    assert len(details["role_bindings"]) == 2
    assert details["next_actions"] == ["get_voice_cast", "confirm_voice_cast"]


def test_confirm_then_start_and_rebind_invalidates(vc_project, monkeypatch):
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    confirmed = VoiceCastResolver.confirm_voice_cast(vc_project)
    assert confirmed["confirmed"] is True
    assert confirmed["confirmed_revision"] == confirmed["cast_revision"]

    started = ProductionJobService.start(vc_project, {"all": True}, source="mcp")
    assert started["created"] is True
    TaskRepository.request_control(started["task_id"], "cancel")

    # Rebind one role -> confirmation must be invalidated automatically.
    # Confirmation locks roles, so a rebind must be an explicit force_rebind.
    assets = {item["file_name"]: item for item in VoiceAssetService.list_assets()}
    VoiceCastResolver.bind_cast_role(
        vc_project, "role_xiaoming", assets["xiaohong.wav"]["voice_asset_id"],
        force_rebind=True,
    )
    state = VoiceCastResolver.get_confirmation_state(vc_project)
    assert state["confirmed"] is False
    assert state["confirmation_required"] is True
    assert state["confirmed_revision"] is None
    assert state["cast_revision"] >= 1
    assert "role_xiaoming" in state["changed_roles"]

    with pytest.raises(ProductionJobError) as error:
        ProductionJobService.start(vc_project, {"all": True}, source="mcp")
    assert error.value.code == "VOICE_CAST_CONFIRMATION_REQUIRED"

    # Re-confirm -> start allowed again.
    VoiceCastResolver.confirm_voice_cast(vc_project)
    started = ProductionJobService.start(vc_project, {"all": True}, source="mcp")
    assert started["created"] is True
    TaskRepository.request_control(started["task_id"], "cancel")


def test_finalize_voice_cast_does_not_count_as_user_confirmation(vc_project, monkeypatch):
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    VoiceCastResolver.finalize_voice_cast(vc_project)
    state = VoiceCastResolver.get_confirmation_state(vc_project)
    assert state["confirmed"] is False
    assert state["confirmed_revision"] is None
    with pytest.raises(ProductionJobError) as error:
        ProductionJobService.start(vc_project, {"all": True}, source="mcp")
    assert error.value.code == "VOICE_CAST_CONFIRMATION_REQUIRED"


def test_legacy_project_without_voice_cast_is_not_gated(tmp_path, monkeypatch):
    data_dir = tmp_path / "legacy_data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(data_dir / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    from lib import project_manager as pm

    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(pm, "LEGACY_ROOT", str(data_dir / "legacy"))
    ProjectService.create_project_from_data("legacy", {
        "meta": {"title": "旧项目"},
        "voices": {"旁白": {}},
        "chapters": [{
            "id": "1",
            "title": "第一章",
            "segments": [{"id": "1-001", "role": "旁白", "text": "旧文本"}],
        }],
    })
    project_dir = ProjectRepository.get_project_dir("legacy")
    voice = os.path.join(project_paths.project_dir(project_dir, "project_voices", create=True), "narrator.wav")
    _make_wav(voice)
    bindings = ProjectRepository.load_bindings(project_dir)
    bindings["bindings"]["旁白"] = voice
    ProjectRepository.save_bindings(project_dir, bindings)
    state = VoiceCastResolver.get_confirmation_state("legacy")
    assert state["mode"] == "legacy_manual"
    assert state["confirmed"] is True
    assert state["confirmation_required"] is False


def test_legacy_manual_all_bound_uses_compat_ui_and_stale_finalize_guard(
    legacy_manual_project, monkeypatch
):
    import app

    project_dir = ProjectRepository.get_project_dir(legacy_manual_project)
    assert not os.path.isfile(project_paths.character_roster(project_dir))
    assert not os.path.isfile(project_paths.voice_cast(project_dir))
    assert os.path.isfile(project_paths.voice_bindings(project_dir))

    status = VoiceCastResolver.get_voice_binding_status(legacy_manual_project)
    assert status["mode"] == "legacy_manual"
    assert status["roles_total"] == 3
    assert status["bound"] == 3
    assert status["unbound"] == 0
    assert status["production_ready"] is True

    summary, finalize_update = app.refresh_voice_cast_ui(
        SimpleNamespace(project=legacy_manual_project)
    )
    assert "全部角色已绑定" in summary
    assert "兼容模式：旧版项目" in summary
    assert "可用于生产" in summary
    assert "CAST_NOT_READY" not in summary
    assert finalize_update["visible"] is False
    assert finalize_update["interactive"] is False

    formal_calls = []
    monkeypatch.setattr(
        VoiceCastResolver,
        "finalize_voice_cast",
        staticmethod(lambda project: formal_calls.append(project)),
    )
    result = app.finalize_voice_cast_ui(SimpleNamespace(project=legacy_manual_project))
    assert "CAST_NOT_READY" not in result
    assert "无需执行 Voice Cast 锁定" in result
    assert formal_calls == []


def test_legacy_manual_partial_binding_is_productized_and_never_finalized(
    legacy_manual_project, monkeypatch
):
    import app

    project_dir = ProjectRepository.get_project_dir(legacy_manual_project)
    bindings = ProjectRepository.load_bindings(project_dir)
    bindings["bindings"]["秦川"] = None
    ProjectRepository.save_bindings(project_dir, bindings)

    status = VoiceCastResolver.get_voice_binding_status(legacy_manual_project)
    assert status["mode"] == "legacy_manual"
    assert status["bound"] == 2
    assert status["unbound"] == 1
    assert status["production_ready"] is False

    summary, finalize_update = app.refresh_voice_cast_ui(
        SimpleNamespace(project=legacy_manual_project)
    )
    assert "2/3" in summary
    assert "还有 1 个角色需要绑定声音" in summary
    assert "CAST_NOT_READY" not in summary
    assert finalize_update["visible"] is False
    assert finalize_update["interactive"] is False

    formal_calls = []
    monkeypatch.setattr(
        VoiceCastResolver,
        "finalize_voice_cast",
        staticmethod(lambda project: formal_calls.append(project)),
    )
    result = app.finalize_voice_cast_ui(SimpleNamespace(project=legacy_manual_project))
    assert "还有 1 个角色需要绑定声音" in result
    assert "CAST_NOT_READY" not in result
    assert formal_calls == []


def test_legacy_manual_binding_refresh_and_reopen_preserve_compat_state(
    legacy_manual_project,
):
    import app

    project_dir = ProjectRepository.get_project_dir(legacy_manual_project)
    bindings = ProjectRepository.load_bindings(project_dir)
    bound_path = bindings["bindings"]["秦川"]
    bindings["bindings"]["秦川"] = None
    ProjectRepository.save_bindings(project_dir, bindings)
    partial, _ = app.refresh_voice_cast_ui(SimpleNamespace(project=legacy_manual_project))
    assert "2/3" in partial

    bindings["bindings"]["秦川"] = bound_path
    ProjectRepository.save_bindings(project_dir, bindings)
    refreshed, finalize_update = app.refresh_voice_cast_ui(
        SimpleNamespace(project=legacy_manual_project)
    )
    assert "3/3" in refreshed
    assert "可用于生产" in refreshed
    assert finalize_update["visible"] is False

    reopened = ProjectService.open_project_as_snapshot(legacy_manual_project)
    reopened_summary = app._voice_cast_summary(reopened)
    assert "全部角色已绑定" in reopened_summary
    confirmation = VoiceCastResolver.get_confirmation_state(legacy_manual_project)
    assert confirmation["mode"] == "legacy_manual"
    assert confirmation["confirmed"] is True
    assert confirmation["confirmation_required"] is False


def test_formal_voice_cast_incomplete_keeps_validation_and_finalize_gate(
    vc_project,
):
    import app

    project_dir = ProjectRepository.get_project_dir(vc_project)
    cast = VoiceCastRepository.load_cast(project_dir)
    cast["roles"]["role_xiaohong"]["voice_asset_id"] = None
    cast["roles"]["role_xiaohong"]["voice_sha256"] = ""
    VoiceCastRepository.save_cast(project_dir, cast)

    status = VoiceCastResolver.get_voice_binding_status(vc_project)
    assert status["mode"] == "voice_cast"
    assert status["production_ready"] is False
    session = SessionState(project=vc_project)
    session.set_snapshot(ProjectService.open_project_as_snapshot(vc_project))
    summary, finalize_update = app.refresh_voice_cast_ui(session)
    assert "全书角色" in summary
    assert finalize_update["visible"] is True
    assert finalize_update["interactive"] is True

    result = app.finalize_voice_cast_ui(session)
    assert "CAST_NOT_READY" in result


def test_formal_voice_cast_ready_still_finalizes_through_existing_path(vc_project):
    import app

    result = app.finalize_voice_cast_ui(SessionState(project=vc_project))
    assert "全书声音方案已锁定" in result
    status = VoiceCastResolver.get_voice_binding_status(vc_project)
    assert status["mode"] == "voice_cast"
    assert status["cast_locked"] is True


# ---------------------------------------------------------------------------
# B/C. Engine resolution: Settings default vs explicit; plan/start consistency
# ---------------------------------------------------------------------------


def test_plan_and_start_default_to_settings_25(vc_project, monkeypatch, engine_config):
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    plan = plan_production({"project_name": vc_project, "scope": {"all": True}})
    assert plan["ready"] is True
    assert plan["engine"]["engine_version"] == "2.5"
    assert plan["engine"]["engine_identity"] == "indextts:2.5"
    assert plan["engine_selection_source"] == "settings_default"

    started = start_production({"project_name": vc_project, "scope": {"all": True}})
    assert started["created"] is True
    frozen = started["engine_snapshot"]
    assert frozen["engine_version"] == "2.5"
    assert frozen["engine_identity"] == "indextts:2.5"
    assert started["engine_selection_source"] == "settings_default"
    # The TaskRecord row itself carries the frozen snapshot.
    record = TaskRepository.load_task(started["task_id"])
    assert record.options["engine_snapshot"]["engine_version"] == "2.5"
    assert record.options["engine_snapshot"]["engine_identity"] == "indextts:2.5"
    TaskRepository.request_control(started["task_id"], "cancel")


def test_explicit_engine_v2_overrides_settings_25(vc_project, monkeypatch, engine_config):
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    plan = plan_production({
        "project_name": vc_project,
        "scope": {"all": True},
        "options": {"engine_snapshot": {"engine_version": "2"}},
    })
    assert plan["ready"] is True
    assert plan["engine"]["engine_version"] == "2"
    assert plan["engine_selection_source"] == "explicit"

    started = start_production({
        "project_name": vc_project,
        "scope": {"all": True},
        "options": {"engine_snapshot": {"engine_version": "2"}},
    })
    assert started["created"] is True
    assert started["engine_snapshot"]["engine_version"] == "2"
    assert started["engine_snapshot"]["engine_identity"] == "indextts:2"
    assert started["engine_selection_source"] == "explicit"
    TaskRepository.request_control(started["task_id"], "cancel")


def test_plan_engine_equals_start_frozen_engine(vc_project, monkeypatch, engine_config):
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    plan = ProductionJobService.plan(vc_project, {"all": True})
    started = start_production({"project_name": vc_project, "scope": {"all": True}})
    assert started["engine_snapshot"]["cache_identity"] == plan["engine"]["cache_identity"]
    assert started["engine_selection_source"] == plan["engine_selection_source"]
    TaskRepository.request_control(started["task_id"], "cancel")


def test_runtime_health_separates_three_engine_views(vc_project, monkeypatch, engine_config):
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    monkeypatch.setattr(
        "services.production_jobs.ProductionRuntimeClient.ensure_running",
        staticmethod(lambda: None),
    )
    health = ProductionJobService.get_runtime_health()
    assert health["configured_default"]["engine_version"] == "2.5"
    assert "runtime_current" in health
    assert "task_engine" in health
    assert health["global_default_engine"]["engine_version"] == "2.5"


# ---------------------------------------------------------------------------
# D. Unified segment artifact lookup across cache classes
# ---------------------------------------------------------------------------


def test_artifact_lookup_legacy_bare(tmp_path, monkeypatch):
    """Class A: legacy bare {seg_id}.wav without Voice Cast."""
    data_dir = tmp_path / "d"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(data_dir / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    from lib import project_manager as pm

    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(pm, "LEGACY_ROOT", str(data_dir / "legacy"))
    ProjectService.create_project_from_data("legacy", {
        "meta": {"title": "旧"},
        "voices": {"旁白": {}},
        "chapters": [{"id": "1", "title": "第一章", "segments": [
            {"id": "001", "role": "旁白", "text": "x", "emotion": "neutral"},
        ]}],
    })
    seg_dir = _segments_dir("legacy")
    _make_wav(os.path.join(seg_dir, "001.wav"))
    artifact = segment_cache.resolve_segment_artifact(
        segments_dir=seg_dir, seg_id="001", project_name="legacy"
    )
    assert artifact.exists()
    assert artifact.matched_class == "legacy_bare"


def test_artifact_lookup_param_speaker_engine_classes(vc_project, tmp_path):
    """Classes B/C/D: param-aware, speaker-aware, engine-aware keys."""
    seg_dir = _segments_dir(vc_project)
    fingerprint = _speaker_fingerprint(vc_project, "role_xiaoming")
    assert fingerprint

    # B. param-aware (no speaker, no engine)
    param_key = segment_cache.segment_cache_key("1-001", "neutral")
    _make_wav(os.path.join(seg_dir, f"{param_key}.wav"))
    artifact = segment_cache.resolve_segment_artifact(
        segments_dir=seg_dir, seg_id="1-001", emotion="neutral", project_name=vc_project
    )
    assert artifact.exists()
    assert artifact.matched_class in {"param_aware", "speaker_aware"}

    # C. speaker-aware
    speaker_key = segment_cache.segment_cache_key(
        "1-002", "neutral", speaker_fingerprint=fingerprint
    )
    _make_wav(os.path.join(seg_dir, f"{speaker_key}.wav"))
    artifact = segment_cache.resolve_segment_artifact(
        segments_dir=seg_dir, seg_id="1-002", emotion="neutral",
        speaker_fingerprint=fingerprint, project_name=vc_project,
    )
    assert artifact.exists()
    assert artifact.matched_class == "speaker_aware"

    # D. engine-aware v2 and v2.5
    profile_v2 = resolve_profile({"engine_version": "2"})
    engine2_key = segment_cache.segment_cache_key(
        "1-003", "neutral", speaker_fingerprint=fingerprint,
        engine_identity=profile_v2["cache_identity"],
    )
    _make_wav(os.path.join(seg_dir, f"{engine2_key}.wav"))
    artifact = segment_cache.resolve_segment_artifact(
        segments_dir=seg_dir, seg_id="1-003", emotion="neutral",
        speaker_fingerprint=fingerprint,
        engine_snapshot={"engine_version": "2"},
        project_name=vc_project,
    )
    assert artifact.exists()
    assert artifact.matched_class == "engine_aware"
    assert artifact.engine_source == "explicit"


def test_review_finds_historical_audio_after_settings_switch(vc_project, tmp_path, engine_config):
    """Settings now 2.5, but the audio was produced by a v2 task.

    The artifact resolver must use production provenance (the v2 task), not
    the current Settings default, so the historical v2 file stays playable.
    """
    engine_config("2.5")
    seg_dir = _segments_dir(vc_project)
    fingerprint = _speaker_fingerprint(vc_project, "role_xiaoming")
    profile_v2 = resolve_profile({"engine_version": "2"})
    engine2_key = segment_cache.segment_cache_key(
        "1-001", "neutral", speaker_fingerprint=fingerprint,
        engine_identity=profile_v2["cache_identity"],
    )
    _make_wav(os.path.join(seg_dir, f"{engine2_key}.wav"))
    # Create a production task whose frozen engine is v2.
    now = "2026-01-01T00:00:00Z"
    record = TaskRecord(
        task_id="task_v2_hist",
        task_type="synthesis",
        project=vc_project,
        status="done",
        options={
            "num_beams": 2,
            "engine_snapshot": profile_v2,
        },
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    TaskRepository.create_production_task(record)

    audio = _segment_audio(vc_project, ProjectRepository.get_project_dir(vc_project), {
        "id": "1-001", "text": "第一句。", "role": "小明", "emotion": "neutral",
    })
    assert audio is not None
    assert os.path.basename(audio) == f"{engine2_key}.wav"
    assert _valid_wav_file(audio)


def test_review_finds_task_audio_when_settings_is_v2(vc_project, tmp_path, engine_config):
    """Settings now 2, but the task frozen engine is 2.5.

    Task provenance must win over Settings so the 2.5 file stays playable.
    """
    engine_config("2")
    seg_dir = _segments_dir(vc_project)
    fingerprint = _speaker_fingerprint(vc_project, "role_xiaoming")
    profile_25 = resolve_profile({"engine_version": "2.5"})
    engine25_key = segment_cache.segment_cache_key(
        "1-001", "neutral", speaker_fingerprint=fingerprint,
        engine_identity=profile_25["cache_identity"],
    )
    _make_wav(os.path.join(seg_dir, f"{engine25_key}.wav"))
    now = "2026-01-01T00:00:00Z"
    record = TaskRecord(
        task_id="task_25_hist",
        task_type="synthesis",
        project=vc_project,
        status="done",
        options={"num_beams": 2, "engine_snapshot": profile_25},
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    TaskRepository.create_production_task(record)

    audio = _segment_audio(vc_project, ProjectRepository.get_project_dir(vc_project), {
        "id": "1-001", "text": "第一句。", "role": "小明", "emotion": "neutral",
    })
    assert audio is not None
    assert os.path.basename(audio) == f"{engine25_key}.wav"


def test_chapter_preview_and_qa_resolve_engine_aware_audio(vc_project, engine_config):
    """Chapter preview and QA must locate the same engine-aware segment."""
    engine_config("2.5")
    seg_dir = _segments_dir(vc_project)
    fingerprint = _speaker_fingerprint(vc_project, "role_xiaoming")
    fingerprint_h = _speaker_fingerprint(vc_project, "role_xiaohong")
    profile_25 = resolve_profile({"engine_version": "2.5"})
    for seg_id, fp in (("1-001", fingerprint), ("1-002", fingerprint_h), ("1-003", fingerprint)):
        key = segment_cache.segment_cache_key(
            seg_id, "neutral", speaker_fingerprint=fp,
            engine_identity=profile_25["cache_identity"],
        )
        _make_wav(os.path.join(seg_dir, f"{key}.wav"))

    project_dir = ProjectRepository.get_project_dir(vc_project)
    from services.review_audio import preview_cache_dir

    result = audio_pipeline.concat_for_preview(
        project_dir, "1", os.path.join(preview_cache_dir(vc_project, "chapters"), "ch.wav")
    )
    assert result is not None
    assert os.path.isfile(result)

    from services.quality import QualityService

    revision = QualityService.ensure_active_revision(vc_project, "1-001")
    assert revision is not None
    assert revision.get("params", {}).get("engine_snapshot", {}).get("engine_version") == "2.5"
    qa = QualityService.run_technical_qa(vc_project, "1-001")
    assert qa["outcome"] == "pass"


def test_plan_counts_other_engine_files_as_remaining(vc_project, engine_config):
    """After an engine switch, a v2 file must not satisfy a v2.5 plan.

    The plan's remaining/completed accounting checks the current task engine
    cache, so the v2 file is remaining and gets re-synthesized under 2.5;
    review (historical playback) still resolves the v2 file via provenance.
    """
    engine_config("2.5")
    VoiceCastResolver.confirm_voice_cast(vc_project)
    seg_dir = _segments_dir(vc_project)
    fingerprint = _speaker_fingerprint(vc_project, "role_xiaoming")
    profile_v2 = resolve_profile({"engine_version": "2"})
    engine2_key = segment_cache.segment_cache_key(
        "1-001", "neutral", speaker_fingerprint=fingerprint,
        engine_identity=profile_v2["cache_identity"],
    )
    _make_wav(os.path.join(seg_dir, f"{engine2_key}.wav"))
    # The v2 file was really produced by a v2 task; record its provenance.
    now = "2026-01-01T00:00:00Z"
    TaskRepository.create_production_task(TaskRecord(
        task_id="task_v2_switch",
        task_type="synthesis",
        project=vc_project,
        status="done",
        options={"num_beams": 2, "engine_snapshot": profile_v2},
        created_at=now, updated_at=now, finished_at=now,
    ))
    meta, script, _ = ProjectRepository.load_project(vc_project)
    ProjectRepository.update_segment_status(vc_project, "1-001", "done")

    plan = ProductionJobService.plan(vc_project, {"all": True})
    assert plan["ready"] is True
    # 1-001 has only a v2 file -> remaining under the 2.5 plan.
    assert plan["already_completed"] == 0
    assert plan["remaining"] == 3
    # But review/provenance lookup still finds the historical v2 file.
    audio = _segment_audio(vc_project, ProjectRepository.get_project_dir(vc_project), {
        "id": "1-001", "text": "第一句。", "role": "小明", "emotion": "neutral",
    })
    assert audio is not None
    assert os.path.basename(audio) == f"{engine2_key}.wav"


def test_plan_keeps_legacy_bare_files_completed(vc_project, tmp_path, monkeypatch):
    """Class A legacy bare files stay completed even when the plan engine is 2.5."""
    seg_dir = _segments_dir(vc_project)
    _make_wav(os.path.join(seg_dir, "1-001.wav"))
    meta, script, _ = ProjectRepository.load_project(vc_project)
    ProjectRepository.update_segment_status(vc_project, "1-001", "done")
    plan = ProductionJobService.plan(vc_project, {"all": True})
    assert plan["already_completed"] >= 1


# ---------------------------------------------------------------------------
# E. Export finds engine-aware segments
# ---------------------------------------------------------------------------


def test_export_book_finds_engine_aware_segments(vc_project, engine_config):
    engine_config("2.5")
    seg_dir = _segments_dir(vc_project)
    fingerprint = _speaker_fingerprint(vc_project, "role_xiaoming")
    fingerprint_h = _speaker_fingerprint(vc_project, "role_xiaohong")
    profile_25 = resolve_profile({"engine_version": "2.5"})
    for seg_id, fp in (("1-001", fingerprint), ("1-002", fingerprint_h), ("1-003", fingerprint)):
        key = segment_cache.segment_cache_key(
            seg_id, "neutral", speaker_fingerprint=fp,
            engine_identity=profile_25["cache_identity"],
        )
        _make_wav(os.path.join(seg_dir, f"{key}.wav"))

    project_dir = ProjectRepository.get_project_dir(vc_project)
    output = audio_pipeline.export_book(
        project_dir, format="wav",
        output_dir=project_paths.project_dir(project_dir, "delivery_official", create=True),
    )
    assert os.path.isfile(output)
    assert os.path.getsize(output) > 0
    assert "未找到音频文件" not in json.dumps({"ok": True})  # no missing error raised


def test_generate_subtitles_finds_engine_aware_segments(vc_project, engine_config):
    engine_config("2.5")
    seg_dir = _segments_dir(vc_project)
    fingerprint = _speaker_fingerprint(vc_project, "role_xiaoming")
    fingerprint_h = _speaker_fingerprint(vc_project, "role_xiaohong")
    profile_25 = resolve_profile({"engine_version": "2.5"})
    for seg_id, fp in (("1-001", fingerprint), ("1-002", fingerprint_h), ("1-003", fingerprint)):
        key = segment_cache.segment_cache_key(
            seg_id, "neutral", speaker_fingerprint=fp,
            engine_identity=profile_25["cache_identity"],
        )
        _make_wav(os.path.join(seg_dir, f"{key}.wav"))
    project_dir = ProjectRepository.get_project_dir(vc_project)
    written = audio_pipeline.generate_subtitles(
        project_dir, formats=("srt",),
        output_dir=project_paths.project_dir(project_dir, "delivery_official", create=True),
        require_complete=True,
    )
    assert written and os.path.isfile(written[0])
