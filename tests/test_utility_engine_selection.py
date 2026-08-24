"""Regression tests: utility (preview/supplement) engine selection policy.

Second-round fix for the #38 supplement regression: a supplement/preview task
that freezes the *settings default* while the singleton runtime is warm with a
different engine forces a full ``reset_engine()`` + ``init_engine()`` reload
on every click.  The selection policy is now:

    explicit engine_profile > runtime_current > global_default

The chosen profile is then frozen into ``engine_snapshot`` (durable truth);
it never tracks later settings changes.  Production snapshot logic lives in
``services/production_jobs.py`` and is intentionally untouched.
"""
from __future__ import annotations

import inspect
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib import tts_profile
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import runtime_tts
from services.runtime_tts import RuntimeTTSService

SCRIPT = {
    "meta": {"title": "Runtime"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


@pytest.fixture
def runtime_project(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("book", SCRIPT)
    return data_dir


@pytest.fixture
def global_default_v25(monkeypatch):
    """Force the settings/global default to IndexTTS 2.5."""
    def _raw_config():
        return {
            "engine_version": "2.5",
            "model_dir_v25": "D:/models/v25",
            "model_dir_v2": "D:/models/v2",
        }
    monkeypatch.setattr(tts_profile, "_raw_config", _raw_config)
    return tts_profile


@pytest.fixture
def global_default_v2(monkeypatch):
    """Force the settings/global default to IndexTTS 2 Legacy."""
    def _raw_config():
        return {
            "engine_version": "2",
            "model_dir": "D:/models/v2",
            "model_dir_v2": "D:/models/v2",
            "model_dir_v25": "D:/models/v25",
        }
    monkeypatch.setattr(tts_profile, "_raw_config", _raw_config)
    return tts_profile


def _write_runtime_status(
    monkeypatch,
    tmp_path,
    *,
    engine_version: str,
    engine_identity: str,
    model_identity: str,
    precision: str,
    state: str = "ready",
) -> None:
    """Publish a live runtime_engine_status.json the runtime would have written."""
    import lib.config as cfg

    data_dir = str(tmp_path / "runtime-data")
    monkeypatch.setattr(cfg, "get_data_dir", lambda: data_dir)
    path = os.path.join(data_dir, "logs", "runtime_engine_status.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    json.dump({
        "state": state,
        "engine_state": state,
        "runtime_state": "running",
        "pid": os.getpid(),
        "owner_id": "test-runtime",
        "updated_at": now,
        "runtime_updated_at": now,
        "error_summary": "",
        "engine_generation": 1,
        "recovery_count": 0,
        "last_error_code": "",
        "last_recovery_at": "",
        "engine_backend": "indextts",
        "engine_version": engine_version,
        "engine_identity": engine_identity,
        "model_identity": model_identity,
        "precision": precision,
        "device": "cuda:0",
        "cache_identity": f"{engine_identity}|{model_identity}|{precision}",
    }, open(path, "w", encoding="utf-8"))


def _write_engine_command(monkeypatch, tmp_path, engine_id: str) -> None:
    """Persist a controlled recycle request the Settings handler would write."""
    import lib.config as cfg

    data_dir = str(tmp_path / "runtime-data")
    monkeypatch.setattr(cfg, "get_data_dir", lambda: data_dir)
    path = os.path.join(data_dir, "logs", "runtime_engine_command.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({
        "engine_id": engine_id,
        "requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }, open(path, "w", encoding="utf-8"))


def _write_broken_engine_command(monkeypatch, tmp_path) -> None:
    """Corrupt the controlled recycle request file (not valid JSON)."""
    import lib.config as cfg

    data_dir = str(tmp_path / "runtime-data")
    monkeypatch.setattr(cfg, "get_data_dir", lambda: data_dir)
    path = os.path.join(data_dir, "logs", "runtime_engine_command.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not valid json")


def _submit_spy(monkeypatch, tmp_path):
    """Replace RuntimeTTSService._submit with a spy capturing the frozen options.

    Returns the captured ``options`` dict and a fake done TaskRecord so
    synthesize_supplement / preview return normally without a real runtime.
    """
    captured: dict = {}

    def _fake_submit(
        cls, *, project_name, task_type, artifact_dir, options, total, timeout,
        progress_cb=None,
    ):
        captured["task_type"] = task_type
        captured["options"] = dict(options or {})
        captured["total"] = total
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        items = [
            {"index": i, "text": f"句{i}", "wav_path": None, "status": "ok", "error": ""}
            for i in range(total)
        ] if task_type == "supplement" else []
        progress = {
            "total": total, "completed": total, "failed": 0, "percent": 100.0,
            "result": {"items": items} if task_type == "supplement" else {
                "preview_path": str(tmp_path / "preview.wav"),
            },
        }
        return TaskRecord(
            task_id=f"task_spy_{uuid.uuid4().hex[:12]}",
            task_type=task_type,
            project=project_name,
            status="done",
            artifact_dir=artifact_dir,
            source="web",
            scope={},
            options=captured["options"],
            progress=progress,
            idempotency_key="spy",
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(RuntimeTTSService, "_submit", classmethod(_fake_submit))
    return captured


def _snapshot_of(captured: dict) -> dict:
    return captured["options"].get("engine_snapshot") or {}


# ── Case 1: runtime Legacy + default 2.5, no explicit → Legacy, no recycle ─
def test_supplement_without_explicit_reuses_runtime_current(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16",
    )
    captured = _submit_spy(monkeypatch, tmp_path)
    artifact_dir = str(tmp_path / "sup1")
    RuntimeTTSService.synthesize_supplement(
        project_name="book", role="旁白", lines=["甲"], speaker_audio="a.wav",
        overrides=None, num_beams=2, artifact_dir=artifact_dir,
    )
    snapshot = _snapshot_of(captured)
    assert snapshot["engine_identity"] == "indextts:2"
    assert snapshot["engine_version"] == "2"
    # selection source is runtime_current (not global_default=2.5)
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "runtime_current"
    assert profile["engine_identity"] == "indextts:2"


# ── Case 2: runtime 2.5 + default Legacy, no explicit → 2.5, no recycle ───
def test_supplement_reuses_runtime_25_when_default_is_legacy(
    runtime_project, tmp_path, monkeypatch, global_default_v2,
):
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2.5", engine_identity="indextts:2.5",
        model_identity="fp-25", precision="BF16",
    )
    captured = _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.synthesize_supplement(
        project_name="book", role="旁白", lines=["甲"], speaker_audio="a.wav",
        overrides=None, num_beams=2, artifact_dir=str(tmp_path / "sup2"),
    )
    snapshot = _snapshot_of(captured)
    assert snapshot["engine_identity"] == "indextts:2.5"
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "runtime_current"
    assert profile["engine_identity"] == "indextts:2.5"


# ── Case 3: runtime not running / uninitialized → global default ──────────
def test_supplement_falls_back_to_global_default_when_runtime_not_ready(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    # no status file -> runtime considered unknown / not running
    import lib.config as cfg

    monkeypatch.setattr(cfg, "get_data_dir", lambda: str(tmp_path / "empty-data"))
    captured = _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.synthesize_supplement(
        project_name="book", role="旁白", lines=["甲"], speaker_audio="a.wav",
        overrides=None, num_beams=2, artifact_dir=str(tmp_path / "sup3"),
    )
    snapshot = _snapshot_of(captured)
    assert snapshot["engine_identity"] == "indextts:2.5"
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "global_default"
    assert profile["engine_identity"] == "indextts:2.5"


# ── Case 4: explicit engine_profile overrides runtime_current ─────────────
def test_supplement_explicit_profile_overrides_runtime_current(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16",
    )
    captured = _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.synthesize_supplement(
        project_name="book", role="旁白", lines=["甲"], speaker_audio="a.wav",
        overrides=None, num_beams=2, artifact_dir=str(tmp_path / "sup4"),
        engine_profile={"engine_version": "2.5"},
    )
    snapshot = _snapshot_of(captured)
    assert snapshot["engine_identity"] == "indextts:2.5"
    profile, source = runtime_tts._select_utility_engine({"engine_version": "2.5"})
    assert source == "explicit"
    assert profile["engine_identity"] == "indextts:2.5"


# ── Case 5: frozen snapshot survives later settings changes ───────────────
def test_frozen_snapshot_survives_settings_change(
    runtime_project, monkeypatch, global_default_v25,
):
    snapshot = tts_profile.resolve_profile({})
    assert snapshot["engine_identity"] == "indextts:2.5"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = TaskRecord(
        task_id=f"task_frozen_{uuid.uuid4().hex[:12]}",
        task_type="supplement",
        project="book",
        status="pending",
        artifact_dir=str(Path(ProjectRepository.get_project_dir("book")) / "cache" / "t5"),
        source="web",
        scope={},
        options={"lines": ["甲"], "engine_snapshot": snapshot},
        progress={"total": 1, "completed": 0, "failed": 0, "percent": 0.0},
        idempotency_key=f"key_{os.urandom(4).hex()}",
        created_at=now,
        updated_at=now,
    )
    outcome, durable = TaskRepository.create_runtime_task(record)
    assert outcome == "created"

    # settings flip to Legacy AFTER the task was created
    monkeypatch.setattr(tts_profile, "_raw_config", lambda: {
        "engine_version": "2", "model_dir": "D:/models/v2",
    })
    loaded = TaskRepository.load_task(durable.task_id)
    assert loaded is not None
    frozen = loaded.options.get("engine_snapshot") or {}
    assert frozen["engine_identity"] == "indextts:2.5"


# ── Case 6: preview without explicit reuses runtime_current ───────────────
def test_preview_without_explicit_reuses_runtime_current(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16",
    )
    captured = _submit_spy(monkeypatch, tmp_path)
    Path(tmp_path / "preview.wav").write_bytes(b"RIFF\x00" * 32)
    RuntimeTTSService.test_voice_and_concat_wavs(
        "book", "旁白", "speaker.wav",
    )
    snapshot = _snapshot_of(captured)
    assert captured["task_type"] == "voice_preview"
    assert snapshot["engine_identity"] == "indextts:2"


# ── Case 7: preview explicit 2.5 uses 2.5 ─────────────────────────────────
def test_preview_explicit_profile_uses_requested_engine(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16",
    )
    captured = _submit_spy(monkeypatch, tmp_path)
    Path(tmp_path / "preview.wav").write_bytes(b"RIFF\x00" * 32)
    RuntimeTTSService.test_voice_and_concat_wavs(
        "book", "旁白", "speaker.wav",
        engine_profile={"engine_version": "2.5"},
    )
    snapshot = _snapshot_of(captured)
    assert snapshot["engine_identity"] == "indextts:2.5"


# ── Case 8: Production snapshot logic untouched ───────────────────────────
def test_production_snapshot_logic_untouched():
    import services.production_jobs as pj

    source = inspect.getsource(pj)
    # production keeps its own snapshot resolution and does not depend on the
    # new utility selection helper
    assert "_select_utility_engine" not in source
    assert "resolve_profile" in source


# ═══════════════════════════════════════════════════════════════════════════
# Third round: pending-switch window (Case 9-14)
# Settings save persists a controlled recycle request (engine command file)
# BEFORE the runtime consumes it; during that window the runtime still reports
# the old engine as ready.  Utility tasks must honor the pending target.
# ═══════════════════════════════════════════════════════════════════════════

# ── Case 9: pending switch 2.5 + runtime still ready Legacy -> supplement ──
def test_supplement_honors_pending_switch_target(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16",
    )
    _write_engine_command(monkeypatch, tmp_path, engine_id="indextts25")
    captured = _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.synthesize_supplement(
        project_name="book", role="旁白", lines=["甲"], speaker_audio="a.wav",
        overrides=None, num_beams=2, artifact_dir=str(tmp_path / "sup9"),
    )
    snapshot = _snapshot_of(captured)
    # must NOT silently freeze the old Legacy; honors the requested 2.5
    assert snapshot["engine_identity"] == "indextts:2.5"
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "runtime_switch_target"
    assert profile["engine_identity"] == "indextts:2.5"


# ── Case 10: pending switch 2.5 + runtime ready Legacy -> preview ──────────
def test_preview_honors_pending_switch_target(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16",
    )
    _write_engine_command(monkeypatch, tmp_path, engine_id="indextts25")
    captured = _submit_spy(monkeypatch, tmp_path)
    Path(tmp_path / "preview.wav").write_bytes(b"RIFF\x00" * 32)
    RuntimeTTSService.test_voice_and_concat_wavs("book", "旁白", "speaker.wav")
    snapshot = _snapshot_of(captured)
    assert captured["task_type"] == "voice_preview"
    assert snapshot["engine_identity"] == "indextts:2.5"


# ── Case 11: runtime already recovering/loading target 2.5 -> no Legacy ────
def test_utility_does_not_reuse_old_engine_while_recovering(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    # engine_state=recovering: the published engine_identity may still show
    # the OLD Legacy value; selection must NOT treat it as current.
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16", state="recovering",
    )
    captured = _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.synthesize_supplement(
        project_name="book", role="旁白", lines=["甲"], speaker_audio="a.wav",
        overrides=None, num_beams=2, artifact_dir=str(tmp_path / "sup11"),
    )
    snapshot = _snapshot_of(captured)
    # falls back to the global default (2.5) — never the recovering old engine
    assert snapshot["engine_identity"] == "indextts:2.5"
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "global_default"


# ── Case 12: switch finished, runtime ready 2.5 -> runtime_current ─────────
def test_utility_uses_runtime_current_after_switch_completed(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2.5", engine_identity="indextts:2.5",
        model_identity="fp-25", precision="BF16",
    )
    # no pending command: switch already consumed
    captured = _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.synthesize_supplement(
        project_name="book", role="旁白", lines=["甲"], speaker_audio="a.wav",
        overrides=None, num_beams=2, artifact_dir=str(tmp_path / "sup12"),
    )
    snapshot = _snapshot_of(captured)
    assert snapshot["engine_identity"] == "indextts:2.5"
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "runtime_current"
    assert profile["engine_identity"] == "indextts:2.5"


# ── Case 13: recycle request invalid/corrupt -> no permanent pending ───────
def test_utility_survives_invalid_engine_command(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    # engine_id present but not a valid 2/2.5 alias: mirror the runtime's own
    # fallback (anything without "25"/"2.5" resolves to Legacy v2); the
    # selection must not hang or fail.
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16",
    )
    _write_engine_command(monkeypatch, tmp_path, engine_id="garbage-id")
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "runtime_switch_target"
    assert profile["engine_identity"] == "indextts:2"  # mirrors runtime fallback

    # corrupt command file (invalid JSON) -> ignored, no crash, no pending
    _write_broken_engine_command(monkeypatch, tmp_path)
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "runtime_current"
    assert profile["engine_identity"] == "indextts:2"


# ── Case 14: no pending switch -> config drift is NOT an active switch ─────
def test_utility_distinguishes_config_drift_from_active_switch(
    runtime_project, tmp_path, monkeypatch, global_default_v25,
):
    # runtime ready Legacy, settings default 2.5, but NO engine command file:
    # this is the second-round regression scenario — reuse Legacy, no recycle.
    _write_runtime_status(
        monkeypatch, tmp_path,
        engine_version="2", engine_identity="indextts:2",
        model_identity="fp-legacy", precision="FP16",
    )
    captured = _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.synthesize_supplement(
        project_name="book", role="旁白", lines=["甲"], speaker_audio="a.wav",
        overrides=None, num_beams=2, artifact_dir=str(tmp_path / "sup14"),
    )
    snapshot = _snapshot_of(captured)
    assert snapshot["engine_identity"] == "indextts:2"
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "runtime_current"
    assert profile["engine_identity"] == "indextts:2"
