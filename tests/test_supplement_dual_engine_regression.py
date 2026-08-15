"""Regression tests: #38 dual-engine supplement path (silent hang / long wait).

The supplement regression introduced by #38 is that every supplement task now
freezes an ``engine_snapshot`` and the runtime's ``ensure_ready(profile)``
enforces identity compliance on the already-loaded engine.  A profile
mismatch (stale runtime, engine switch, config drift) triggers a synchronous
``reset_engine()`` + full ``init_engine()`` — a multi-minute GPU reload —
inside the serial task path with no feedback.

These tests lock in the control-flow invariants with a **fake** engine:

1. supplement tasks freeze ``engine_snapshot``;
2. same profile  -> loaded engine == task engine -> NO recycle;
3. diff profile  -> exactly ONE recycle;
4. after recycle -> supplement uses the target engine;
5. engine init failure -> task enters ``error`` (never infinite pending);
6. legacy IndexTTS 2 supplement stays compatible;
7. IndexTTS 2.5 supplement covers the adapter path;
8. Windows spawn keeps the console-less (DETACHED) structure.

IMPORTANT: fake engine tests prove flow correctness, NOT GPU/model
correctness.  Real IndexTTS 2 / 2.5 validation requires a Windows RTX box.
"""
from __future__ import annotations

import os
import sys
import types
import uuid
from pathlib import Path

import pytest

from lib import project_manager as pm
from lib.tts_profile import resolve_profile
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services.production_runtime import ProductionRuntime
from services.runtime_engine import RuntimeEngineLifecycle

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
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
    ProjectRepository.create_project_from_data("book", SCRIPT)
    return data_dir


class FakeTtsEngine:
    """Process-local fake for ``lib.tts_engine`` with recorded transitions."""

    def __init__(self, init_error: Exception | None = None) -> None:
        self.init_calls: list[dict] = []
        self.reset_calls = 0
        self.synth_calls: list[dict] = []
        self._profile: dict = {}
        self._tts = None
        self.init_error = init_error

    def init_engine(
        self,
        model_dir=None,
        use_fp16=True,
        use_cuda_kernel=True,
        use_deepspeed=False,
        use_accel=False,
        *,
        profile=None,
    ):
        if self.init_error is not None:
            raise self.init_error
        resolved = resolve_profile(profile or {})
        self.init_calls.append(dict(resolved))
        self._profile = dict(resolved)
        self._tts = object()

    def reset_engine(self) -> None:
        self.reset_calls += 1
        self._tts = None
        self._profile = {}

    def get_engine_profile(self) -> dict:
        return dict(self._profile)

    def empty_cache(self) -> None:
        pass

    def synthesize_segment(
        self, *, text, speaker_audio, emotion, emo_alpha, speech_rate,
        output_path, num_beams,
    ) -> str:
        self.synth_calls.append({
            "text": text,
            "num_beams": num_beams,
            "engine_identity": self._profile.get("engine_identity", ""),
            "engine_version": self._profile.get("engine_version", ""),
        })
        Path(output_path).write_bytes(b"RIFF\x00" * 32)
        return output_path


def _install_fake_engine(monkeypatch, fake: FakeTtsEngine) -> None:
    module = types.ModuleType("lib.tts_engine")
    module.init_engine = fake.init_engine
    module.reset_engine = fake.reset_engine
    module.get_engine_profile = fake.get_engine_profile
    module.empty_cache = fake.empty_cache
    module.synthesize_segment = fake.synthesize_segment
    import lib

    monkeypatch.setitem(sys.modules, "lib.tts_engine", module)
    monkeypatch.setattr(lib, "tts_engine", module, raising=False)


def _make_runtime(tmp_path, monkeypatch, fake: FakeTtsEngine):
    _install_fake_engine(monkeypatch, fake)
    runtime = ProductionRuntime(
        owner_id="regression-test",
        lock_path=str(tmp_path / "runtime.lock"),
        status_path=str(tmp_path / "runtime_engine_status.json"),
    )
    return runtime


def _make_supplement_record(project: str, artifact_dir: str, *, snapshot=None):
    now = "2026-08-14T00:00:00Z"
    options = {
        "role": "旁白",
        "lines": ["第一句", "第二句"],
        "speaker_audio": os.path.join(artifact_dir, "speaker.wav"),
        "overrides": {},
        "num_beams": 2,
    }
    if snapshot is not None:
        options["engine_snapshot"] = snapshot
    return TaskRecord(
        task_id=f"task_regression_{uuid.uuid4().hex[:12]}",
        task_type="supplement",
        project=project,
        status="pending",
        artifact_dir=artifact_dir,
        source="web",
        scope={"all": False, "chapter_ids": [], "segment_ids": []},
        options=options,
        progress={"total": 2, "completed": 0, "failed": 0, "percent": 0.0},
        idempotency_key=f"key_{os.urandom(4).hex()}",
        created_at=now,
        updated_at=now,
    )


# ── 1. supplement task 冻结 engine_snapshot ──────────────────────────────
def test_supplement_task_freezes_engine_snapshot(runtime_project, monkeypatch):
    project_dir = ProjectRepository.get_project_dir("book")
    artifact_dir = os.path.join(project_dir, "cache", "supplement_tasks", "t1")
    os.makedirs(artifact_dir, exist_ok=True)
    record = _make_supplement_record("book", artifact_dir)  # no snapshot key
    assert "engine_snapshot" not in record.options

    outcome, durable = TaskRepository.create_runtime_task(record)
    assert outcome == "created"
    assert isinstance(durable.options, dict)
    snapshot = durable.options.get("engine_snapshot") or {}
    assert snapshot.get("engine_version") in {"2", "2.5"}
    assert snapshot.get("engine_identity") in {"indextts:2", "indextts:2.5"}
    assert snapshot.get("model_identity")
    assert snapshot.get("precision")


# ── 2. same profile -> no recycle ────────────────────────────────────────
def test_same_profile_does_not_recycle(tmp_path, monkeypatch):
    fake = FakeTtsEngine()
    _install_fake_engine(monkeypatch, fake)
    lifecycle = RuntimeEngineLifecycle(
        owner_id="t2", status_path=str(tmp_path / "s2.json")
    )
    profile = resolve_profile({})
    lifecycle.ensure_ready(profile)
    assert lifecycle.state == "ready"
    assert len(fake.init_calls) == 1
    assert fake.reset_calls == 0
    generation = lifecycle.snapshot()["engine_generation"]

    # warm engine + identical task snapshot -> reuse, no recycle
    lifecycle.ensure_ready(profile)
    assert len(fake.init_calls) == 1
    assert fake.reset_calls == 0
    assert lifecycle.snapshot()["engine_generation"] == generation
    assert lifecycle.snapshot()["recovery_count"] == 0


# ── 3. different profile -> exactly one recycle ──────────────────────────
def test_different_profile_recycles_exactly_once(tmp_path, monkeypatch):
    fake = FakeTtsEngine()
    _install_fake_engine(monkeypatch, fake)
    lifecycle = RuntimeEngineLifecycle(
        owner_id="t3", status_path=str(tmp_path / "s3.json")
    )
    profile_a = resolve_profile({})
    profile_b = resolve_profile({"engine_version": "2"})
    if profile_a["engine_identity"] == profile_b["engine_identity"]:
        pytest.skip("环境只有单一引擎，无法构造 diff profile")

    lifecycle.ensure_ready(profile_a)
    assert fake.reset_calls == 0
    lifecycle.ensure_ready(profile_b)
    # exactly one reset + one fresh init for the switch
    assert fake.reset_calls == 1
    assert len(fake.init_calls) == 2
    assert lifecycle.snapshot()["recovery_count"] == 1
    assert lifecycle.snapshot()["engine_generation"] == 2

    # idempotent after the switch: no extra recycle
    lifecycle.ensure_ready(profile_b)
    assert fake.reset_calls == 1
    assert len(fake.init_calls) == 2


# ── 4. recycle 后 supplement 使用目标 engine ─────────────────────────────
def test_supplement_uses_target_engine_after_recycle(tmp_path, monkeypatch):
    fake = FakeTtsEngine()
    _install_fake_engine(monkeypatch, fake)
    lifecycle = RuntimeEngineLifecycle(
        owner_id="t4", status_path=str(tmp_path / "s4.json")
    )
    target = resolve_profile({"engine_version": "2"})
    # warm the engine with the *other* profile so the task forces a recycle
    warm = resolve_profile({})
    if warm["engine_identity"] == target["engine_identity"]:
        pytest.skip("环境只有单一引擎，无法构造 diff profile")
    lifecycle.ensure_ready(warm)

    runtime = ProductionRuntime(
        owner_id="t4-runtime",
        lock_path=str(tmp_path / "l4.lock"),
        status_path=str(tmp_path / "s4.json"),
    )
    runtime._engine = lifecycle
    artifact_dir = str(tmp_path / "out4")
    os.makedirs(artifact_dir, exist_ok=True)
    results = runtime.run_supplement_direct(
        {
            "lines": ["目标引擎句"],
            "speaker_audio": "speaker.wav",
            "overrides": {},
            "num_beams": 2,
        },
        artifact_dir,
        initialize=True,
        engine_profile=target,
    )
    assert fake.reset_calls == 1
    assert results[0]["status"] == "ok"
    assert os.path.isfile(os.path.join(artifact_dir, "001.wav"))
    # inference ran under the TARGET engine
    assert fake.synth_calls
    assert fake.synth_calls[0]["engine_identity"] == target["engine_identity"]


# ── 5. engine init failure -> task error (never infinite pending/running) ─
def test_engine_init_failure_fails_task(runtime_project, tmp_path, monkeypatch):
    fake = FakeTtsEngine(init_error=RuntimeError("v2.5 bundle broken"))
    runtime = _make_runtime(tmp_path, monkeypatch, fake)
    project_dir = ProjectRepository.get_project_dir("book")
    artifact_dir = os.path.join(project_dir, "cache", "supplement_tasks", "t5")
    os.makedirs(artifact_dir, exist_ok=True)
    record = _make_supplement_record("book", artifact_dir)

    outcome, durable = TaskRepository.create_runtime_task(record)
    assert outcome == "created"
    # the runtime claims ownership first (as the main loop would)
    claimed = TaskRepository.claim_next_pending(
        runtime.owner_id, {"supplement"}, force=True
    )
    assert claimed is not None

    runtime._run_utility_task(claimed)
    loaded = TaskRepository.load_task(durable.task_id)
    assert loaded is not None
    assert loaded.status == "error"
    assert "TTS_ENGINE_INIT_FAILED" in str(loaded.error_summary or "")


# ── 6. legacy (IndexTTS 2) supplement 兼容 ────────────────────────────────
def test_legacy_v2_supplement_compatible(tmp_path, monkeypatch):
    fake = FakeTtsEngine()
    runtime = _make_runtime(tmp_path, monkeypatch, fake)
    target = resolve_profile({"engine_version": "2"})
    artifact_dir = str(tmp_path / "out6")
    os.makedirs(artifact_dir, exist_ok=True)
    results = runtime.run_supplement_direct(
        {
            "lines": ["旧引擎句"],
            "speaker_audio": "speaker.wav",
            "overrides": {},
            "num_beams": 2,
        },
        artifact_dir,
        initialize=True,
        engine_profile=target,
    )
    assert len(fake.init_calls) == 1
    assert results[0]["status"] == "ok"
    assert fake.synth_calls[0]["engine_version"] == "2"


# ── 7. IndexTTS 2.5 adapter 路径 ──────────────────────────────────────────
def test_indextts25_adapter_path(monkeypatch):
    from lib import tts_engine as real

    captured = {}
    fake_module = types.ModuleType("indextts.infer_v2_5")

    class _FakeIndexTTS25:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

    fake_module.IndexTTS2 = _FakeIndexTTS25
    monkeypatch.setitem(sys.modules, "indextts.infer_v2_5", fake_module)

    backend = real.IndexTTS25Backend()
    assert backend.version == "2.5"
    loaded = backend.load_class()
    assert loaded is _FakeIndexTTS25
    kwargs = backend.constructor_kwargs(
        cfg_path="/cfg", model_dir="/m", precision="BF16",
    )
    assert kwargs["use_bf16"] is True
    assert kwargs["use_qwen_emo"] is True
    assert kwargs["use_cuda_kernel"] is False
    assert kwargs["use_deepspeed"] is False
    assert kwargs["use_accel"] is False
    assert kwargs["use_torch_compile"] is False


# ── 8. Windows spawn 保持 console-less 结构 ───────────────────────────────
def test_windows_spawn_keeps_detached_structure(tmp_path, monkeypatch):
    import subprocess

    from services import production_runtime as pr

    captured: dict = {}
    fake_pid = 424242

    class _FakeProc:
        pid = fake_pid

    def _fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(pr, "_is_windows", lambda: True)
    # Inject the Windows creation-flag constants on every platform so the
    # Linux CI can still exercise the Windows spawn branch (the production
    # code reads them via getattr(..., 0)).  Without this, Linux sees 0|0=0
    # and `flags & detached` is always falsy.
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_RUNTIME_MODE", "process")  # bypass pytest inline default
    monkeypatch.setattr(pr.ProductionRuntimeClient, "_resolve_runtime_launch",
                        classmethod(lambda cls: (["py", "-m", "services.production_runtime", "--serve"], {})))
    monkeypatch.setattr(pr, "_open_bootstrap_log", lambda: None)
    monkeypatch.setattr(pr.ProcessFileLock, "acquire", lambda self, blocking: True)
    monkeypatch.setattr(pr.ProcessFileLock, "release", lambda self: None)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", None)

    pr.ProductionRuntimeClient.ensure_running()
    flags = int(captured["kwargs"].get("creationflags", 0))
    detached = int(getattr(subprocess, "DETACHED_PROCESS", 0))
    assert flags & detached, "Windows runtime spawn must stay console-less"
    assert captured["kwargs"].get("stdin") == subprocess.DEVNULL
