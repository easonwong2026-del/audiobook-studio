"""Regression tests: P0-1 pending engine-switch must not double-recycle.

Cross-layer race (#42):
1. Runtime is ready with IndexTTS 2;
2. Settings switch to 2.5 persists ``runtime_engine_command.json``;
3. A supplement click freezes snapshot = 2.5 (``runtime_switch_target``);
4. The runtime consumes the supplement and ``ensure_ready(2.5)`` recycles
   the engine v2 -> v2.5 exactly once;
5. When the idle loop later consumes the still-pending engine command, the
   recycle must be idempotent: same profile -> NO reset/init, NO generation
   or recovery bump, and the command file is acked/removed.

The idempotency guard lives in ``ProductionRuntime.request_engine_recycle``
(controlled Settings switch / command consumption).  ``RuntimeEngineLifecycle.recycle``
deliberately keeps its force-reload semantics — the self-healing recovery
path (``_build_recovery_hooks._recycle`` / ``lib.queue`` ``hooks.recycle``)
calls it directly and must reset+reinit the engine even for the same profile
after a segment failure.  These tests also lock in that a genuinely
different switch target still recycles exactly once (Settings-only switch
path), that the bottom-layer force-reload contract is preserved, and add a
#42 performance check counting ``persist_runtime_state`` calls per supplement
line (~2/line, no full scans).

IMPORTANT: fake engine tests prove flow correctness, NOT GPU/model
correctness.  Real IndexTTS 2 / 2.5 validation requires a Windows RTX box.
"""
from __future__ import annotations

import json
import os
import sys
import types
import uuid
from pathlib import Path

import pytest

from lib import project_paths, tts_engine
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

    def empty_cache(self, reason: str = "manual") -> None:
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


def _write_valid_wav(path: str) -> None:
    """Write a minimal valid mono 16-bit PCM WAV that ``wave.open`` accepts.

    ``ProductionRuntime._validate_wav`` (used by ``_run_utility_task``) opens
    the file with the stdlib ``wave`` module and requires non-empty frames, so
    the fake's ``b"RIFF\x00" * 32`` payload is not enough for end-to-end
    utility-task tests.
    """
    import wave

    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 400)  # 400 frames of silence (~25ms)


class FakeTtsEngineValidWav(FakeTtsEngine):
    """FakeTtsEngine whose synthesized output passes ``_validate_wav``."""

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
        _write_valid_wav(output_path)
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
        owner_id="recycle-test",
        lock_path=str(tmp_path / "runtime.lock"),
        status_path=str(tmp_path / "runtime_engine_status.json"),
    )
    return runtime


def _make_supplement_record(
    project: str, artifact_dir: str, *, snapshot=None, lines=None,
):
    now = "2026-08-15T00:00:00Z"
    line_list = list(lines) if lines else ["第一句", "第二句"]
    options = {
        "role": "旁白",
        "lines": line_list,
        "speaker_audio": os.path.join(artifact_dir, "speaker.wav"),
        "overrides": {},
        "num_beams": 2,
    }
    if snapshot is not None:
        options["engine_snapshot"] = snapshot
    return TaskRecord(
        task_id=f"task_recycle_{uuid.uuid4().hex[:12]}",
        task_type="supplement",
        project=project,
        status="pending",
        artifact_dir=artifact_dir,
        source="web",
        scope={"all": False, "chapter_ids": [], "segment_ids": []},
        options=options,
        progress={"total": len(line_list), "completed": 0, "failed": 0, "percent": 0.0},
        idempotency_key=f"key_{os.urandom(4).hex()}",
        created_at=now,
        updated_at=now,
    )


def _write_engine_command(project_data_dir: str, engine_id: str) -> str:
    """Persist a controlled recycle request the Settings handler would write."""
    command_path = os.path.join(project_data_dir, "logs", "runtime_engine_command.json")
    os.makedirs(os.path.dirname(command_path), exist_ok=True)
    with open(command_path, "w", encoding="utf-8") as fh:
        json.dump({
            "engine_id": engine_id,
            "requested_at": "2026-08-15T00:00:00Z",
        }, fh)
    return command_path


# ── 1. request_engine_recycle(same profile) -> idempotent no-op ───────────
def test_request_recycle_same_profile_is_noop(tmp_path, monkeypatch):
    """Controlled Settings switch: already on target -> no reset/init bump.

    The idempotency guard lives in ``ProductionRuntime.request_engine_recycle``
    (the controlled switch / command-consumption layer), NOT in
    ``RuntimeEngineLifecycle.recycle`` which keeps its force-reload semantics
    for the self-healing recovery path.
    """
    fake = FakeTtsEngine()
    runtime = _make_runtime(tmp_path, monkeypatch, fake)
    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_v25 = resolve_profile({"engine_version": "2.5"})
    if profile_v2["engine_identity"] == profile_v25["engine_identity"]:
        pytest.skip("环境只有单一引擎，无法构造 diff profile")

    runtime._engine.ensure_ready(profile_v25)
    generation = runtime._engine.snapshot()["engine_generation"]
    recovery = runtime._engine.snapshot()["recovery_count"]
    assert runtime._engine.state == "ready"
    assert len(fake.init_calls) == 1
    assert fake.init_calls[0]["engine_identity"] == "indextts:2.5"

    # Runtime already ready on 2.5; a repeated request for 2.5 must be a no-op.
    returned = runtime.request_engine_recycle("indextts25")
    assert returned is True
    assert fake.reset_calls == 0
    assert len(fake.init_calls) == 1
    assert runtime._engine.state == "ready"
    assert runtime._engine.snapshot()["engine_generation"] == generation
    assert runtime._engine.snapshot()["recovery_count"] == recovery


@pytest.mark.parametrize(("current_requested", "configured"), [(True, False), (False, True)])
def test_request_recycle_when_v25_accel_setting_changes_once(
    runtime_project, tmp_path, monkeypatch, current_requested, configured,
):
    from lib import config

    profile = resolve_profile({"engine_version": "2.5"})
    status = {
        "requested": current_requested,
        "reason": "accel_active" if current_requested else "user_disabled",
    }
    recycle_calls = []

    monkeypatch.setattr(tts_engine, "get_acceleration_status", lambda: dict(status))
    monkeypatch.setattr(config, "get_bool", lambda *_args, **_kwargs: configured)

    def recycle(_target):
        recycle_calls.append(True)
        status["requested"] = configured
        status["reason"] = "accel_active" if configured else "user_disabled"

    runtime = ProductionRuntime(
        owner_id="accel-recycle-test",
        lock_path=str(tmp_path / "runtime.lock"),
        status_path=str(tmp_path / "runtime_engine_status.json"),
    )
    runtime._engine = types.SimpleNamespace(
        snapshot=lambda: {"state": "ready", **profile},
        recycle=recycle,
    )

    assert runtime.request_engine_recycle("indextts25") is True
    assert runtime.request_engine_recycle("indextts25") is True
    assert recycle_calls == [True]


# ── 1b. lifecycle.recycle(same profile) keeps FORCE-reload semantics ──────
def test_lifecycle_recycle_same_profile_still_force_reloads(tmp_path, monkeypatch):
    """Guard: bottom-layer recycle() must NOT become idempotent.

    The self-healing recovery path (``_build_recovery_hooks._recycle`` ->
    ``lib.queue`` ``hooks.recycle``) calls ``RuntimeEngineLifecycle.recycle``
    directly after a segment failure; the engine may be corrupted and must be
    reset + re-initialized even when the profile did not change.  If someone
    later re-adds a global "ready + same profile => no-op" guard here, this
    test fails.
    """
    fake = FakeTtsEngine()
    _install_fake_engine(monkeypatch, fake)
    lifecycle = RuntimeEngineLifecycle(
        owner_id="force", status_path=str(tmp_path / "force.json")
    )
    profile = resolve_profile({})
    lifecycle.ensure_ready(profile)
    generation = lifecycle.snapshot()["engine_generation"]
    assert lifecycle.state == "ready"
    assert len(fake.init_calls) == 1

    returned = lifecycle.recycle(profile)  # same profile, still force reload
    assert returned == generation + 1
    assert fake.reset_calls == 1
    assert len(fake.init_calls) == 2
    assert lifecycle.snapshot()["recovery_count"] == 1
    assert lifecycle.state == "ready"


# ── 2. request_engine_recycle(different profile) -> exactly one recycle ──
def test_request_recycle_different_profile_recycles_once(tmp_path, monkeypatch):
    """Controlled Settings switch to a genuinely different target -> 1 recycle."""
    fake = FakeTtsEngine()
    runtime = _make_runtime(tmp_path, monkeypatch, fake)
    profile_a = resolve_profile({})
    profile_b = resolve_profile({"engine_version": "2"})
    if profile_a["engine_identity"] == profile_b["engine_identity"]:
        pytest.skip("环境只有单一引擎，无法构造 diff profile")

    runtime._engine.ensure_ready(profile_a)
    generation = runtime._engine.snapshot()["engine_generation"]
    engine_id = "indextts2" if profile_b["engine_version"] == "2" else "indextts25"
    returned = runtime.request_engine_recycle(engine_id)
    assert returned is True
    assert fake.reset_calls == 1
    assert len(fake.init_calls) == 2
    assert runtime._engine.snapshot()["recovery_count"] == 1
    assert runtime._engine.snapshot()["engine_generation"] == generation + 1
    assert runtime._engine.snapshot()["engine_identity"] == profile_b["engine_identity"]


# ── 3. P0-1 full lifecycle: pending switch + supplement -> no double recycle ─
def test_pending_switch_plus_supplement_does_not_double_recycle(
    runtime_project, tmp_path, monkeypatch,
):
    fake = FakeTtsEngineValidWav()
    runtime = _make_runtime(tmp_path, monkeypatch, fake)

    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_v25 = resolve_profile({"engine_version": "2.5"})
    if profile_v2["engine_identity"] == profile_v25["engine_identity"]:
        pytest.skip("环境只有单一引擎，无法构造 diff profile")

    # Runtime warm with IndexTTS 2 (init #1).
    runtime._engine.ensure_ready(profile_v2)
    assert len(fake.init_calls) == 1
    assert fake.init_calls[0]["engine_identity"] == "indextts:2"

    # Settings persisted a pending engine switch (runtime has not consumed it).
    command_path = _write_engine_command(runtime_project, "indextts25")

    # Supplement frozen with snapshot=2.5 (equivalent to _select_utility_engine
    # returning runtime_switch_target), claimed by the runtime.
    project_dir = ProjectRepository.get_project_dir("book")
    artifact_dir = os.path.join(project_paths.project_dir(project_dir, "cache", create=True), "supplement_tasks", "case_a")
    os.makedirs(artifact_dir, exist_ok=True)
    record = _make_supplement_record("book", artifact_dir, snapshot=profile_v25)
    outcome, durable = TaskRepository.create_runtime_task(record)
    assert outcome == "created"
    claimed = TaskRepository.claim_next_pending(
        runtime.owner_id, {"supplement"}, force=True
    )
    assert claimed is not None

    # Step 1: the command stays un-consumed while the utility task is active.
    runtime._consume_engine_command()
    assert os.path.isfile(command_path), "utility task active 时 command 不应被消费"

    # Steps 2-5: runtime executes the supplement -> ensure_ready(2.5) recycles
    # v2 -> v2.5 exactly once; the supplement runs on the target engine.
    runtime._run_utility_task(claimed)
    loaded = TaskRepository.load_task(durable.task_id)
    assert loaded is not None and loaded.status == "done"
    assert fake.reset_calls == 1
    assert len(fake.init_calls) == 2
    assert fake.init_calls[1]["engine_identity"] == "indextts:2.5"
    assert fake.synth_calls
    assert fake.synth_calls[0]["engine_identity"] == "indextts:2.5"
    snapshot = runtime._engine.snapshot()
    assert snapshot["engine_identity"] == "indextts:2.5"
    generation = snapshot["engine_generation"]
    recovery = snapshot["recovery_count"]

    # Step 6: the idle loop consumes the still-pending command -> must be a
    # no-op (same profile) and must ack/remove the command file.
    assert os.path.isfile(command_path)
    runtime._consume_engine_command()
    assert not os.path.isfile(command_path), "pending command 应在幂等 consume 后被删除"
    assert fake.reset_calls == 1
    assert len(fake.init_calls) == 2
    snapshot = runtime._engine.snapshot()
    assert snapshot["engine_generation"] == generation
    assert snapshot["recovery_count"] == recovery
    assert snapshot["engine_identity"] == "indextts:2.5"


# ── 4. genuinely different target (Settings-only) still recycles once ──────
def test_different_target_still_recycles_once(runtime_project, tmp_path, monkeypatch):
    fake = FakeTtsEngineValidWav()
    runtime = _make_runtime(tmp_path, monkeypatch, fake)

    profile_v2 = resolve_profile({"engine_version": "2"})
    profile_v25 = resolve_profile({"engine_version": "2.5"})
    if profile_v2["engine_identity"] == profile_v25["engine_identity"]:
        pytest.skip("环境只有单一引擎，无法构造 diff profile")

    runtime._engine.ensure_ready(profile_v2)
    assert len(fake.init_calls) == 1

    command_path = _write_engine_command(runtime_project, "indextts25")
    generation_before = runtime._engine.snapshot()["engine_generation"]
    recovery_before = runtime._engine.snapshot()["recovery_count"]

    # No utility task raced ahead: the switch must recycle exactly once.
    runtime._consume_engine_command()
    assert not os.path.isfile(command_path)
    assert fake.reset_calls == 1
    assert len(fake.init_calls) == 2
    snapshot = runtime._engine.snapshot()
    assert snapshot["engine_identity"] == "indextts:2.5"
    assert snapshot["engine_generation"] == generation_before + 1
    assert snapshot["recovery_count"] == recovery_before + 1


# ── 5. #42 performance check: 20-line supplement persistence count ─────────
def test_supplement_20_lines_persistence_count(runtime_project, tmp_path, monkeypatch):
    """#42 perf: ~2 persist_runtime_state calls per line, no full scans.

    记录为后续 P2（无需修复）：20 句 → persist_runtime_state 恰好 44 次
    （每句 supplement_infer_start + supplement_infer_done 各 1 次，另加
    running / engine_intent / profile_match / done 4 次固定开销）。每次
    persist 都带 project → 走 project-local O(1) load_project_task，不触发
    全库 load_task 扫描；连接数随句数线性增长（每次 persist 约 2 个连接：
    load_project_task + UPDATE，另加每句一次 heartbeat），无连接爆炸。
    """
    fake = FakeTtsEngineValidWav()
    runtime = _make_runtime(tmp_path, monkeypatch, fake)
    target = resolve_profile({})
    # 预热引擎 → run_supplement_direct 内 ensure_ready 仅为 profile_match no-op
    runtime._engine.ensure_ready(target)

    project_dir = ProjectRepository.get_project_dir("book")
    artifact_dir = os.path.join(project_paths.project_dir(project_dir, "cache", create=True), "supplement_tasks", "perf20")
    os.makedirs(artifact_dir, exist_ok=True)
    lines = [f"性能检查第{i}句" for i in range(1, 21)]
    record = _make_supplement_record("book", artifact_dir, snapshot=target, lines=lines)
    outcome, durable = TaskRepository.create_runtime_task(record)
    assert outcome == "created"
    claimed = TaskRepository.claim_next_pending(
        runtime.owner_id, {"supplement"}, force=True
    )
    assert claimed is not None

    persist_count = {"n": 0}
    full_load_count = {"n": 0}
    project_load_count = {"n": 0}
    connect_count = {"n": 0}
    statuses: list[str] = []
    original_persist = TaskRepository.persist_runtime_state
    original_load_task = TaskRepository.load_task
    original_load_project_task = TaskRepository.load_project_task
    original_connect = TaskRepository._connect

    def counting_persist(*args, **kwargs):
        persist_count["n"] += 1
        statuses.append(str(kwargs.get("status") or ""))
        return original_persist(*args, **kwargs)

    def counting_load_task(task_id):
        full_load_count["n"] += 1
        return original_load_task(task_id)

    def counting_load_project_task(project, task_id):
        project_load_count["n"] += 1
        return original_load_project_task(project, task_id)

    def counting_connect(*args, **kwargs):
        connect_count["n"] += 1
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        TaskRepository, "persist_runtime_state", staticmethod(counting_persist)
    )
    monkeypatch.setattr(TaskRepository, "load_task", staticmethod(counting_load_task))
    monkeypatch.setattr(
        TaskRepository, "load_project_task", staticmethod(counting_load_project_task)
    )
    monkeypatch.setattr(TaskRepository, "_connect", staticmethod(counting_connect))

    runtime._run_utility_task(claimed)

    expected = 2 * len(lines) + 4  # 每句 2 次 + running/engine_intent/profile_match/done
    assert persist_count["n"] == expected, f"persist={persist_count['n']} expected={expected}"
    assert statuses[-1] == "done", f"最终状态应为 done，实际最后 status={statuses[-1]}"
    assert "error" not in statuses
    assert len(fake.synth_calls) == len(lines)
    assert full_load_count["n"] == 0, "persist 热路径不得触发全库 load_task 扫描"
    assert project_load_count["n"] == persist_count["n"], "每次持久化应为 project-local O(1)"
    assert connect_count["n"] <= 3 * persist_count["n"], (
        f"连接数随句数线性增长（≤3×persist），实际 {connect_count['n']}"
    )
