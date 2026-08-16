"""PR B 修复 5：Quick TTS（无项目临时配音）回归测试。

覆盖：
1. 无 project + 全局 voice + 一句台词 → durable utility task 创建（不污染书架）；
2. task frozen engine = Settings 默认（排队后 Settings 改变不变 engine）；
3. runtime worker 完成 → Audio path 存在；
4. Quick TTS 不出现在项目扫描（无 project.json / 不入书架）；
5. Quick TTS export 成功（<data_dir>/quick_tts/exports/）；
6. 正式 production active → Quick TTS 返回 busy 不抢占；
7. runtime warm same engine → 不 reload。
"""
from __future__ import annotations

import os
import sys
import types
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import tts_profile  # noqa: E402
from lib import project_manager as pm  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402
from repositories.task_repo import QUICK_TTS_CONTEXT, TaskRecord, TaskRepository  # noqa: E402
from services import runtime_tts  # noqa: E402
from services.quick_tts import QuickTTSBusyError, QuickTTSService  # noqa: E402
from services.runtime_tts import RuntimeTTSService  # noqa: E402


SCRIPT = {
    "meta": {"title": "Quick"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


@pytest.fixture
def quick_env(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_RUNTIME_MODE", "inline")
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", ProjectRepository.WORKSPACE_ROOT)
    monkeypatch.setattr(pm, "LEGACY_ROOT", ProjectRepository.LEGACY_ROOT)
    ProjectRepository.create_project_from_data("book", SCRIPT)
    TaskRepository.reset_schema_cache()
    return data_dir


@pytest.fixture
def global_default_v25(monkeypatch):
    def _raw_config():
        return {
            "engine_version": "2.5",
            "model_dir_v25": "D:/models/v25",
            "model_dir_v2": "D:/models/v2",
        }

    monkeypatch.setattr(tts_profile, "_raw_config", _raw_config)
    return tts_profile


def _write_wav(path: str, frames: int = 800) -> str:
    import wave

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * frames)
    return path


def _submit_spy(monkeypatch, tmp_path):
    """Replace RuntimeTTSService._submit with a spy capturing frozen options."""
    captured: dict = {}

    def _fake_submit(
        cls, *, project_name, task_type, artifact_dir, options, total, timeout,
        progress_cb=None,
    ):
        captured["project_name"] = project_name
        captured["task_type"] = task_type
        captured["artifact_dir"] = artifact_dir
        captured["options"] = dict(options or {})
        captured["total"] = total
        wav = _write_wav(os.path.join(str(artifact_dir), "001.wav"))
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        progress = {
            "total": total, "completed": total, "failed": 0, "percent": 100.0,
            "result": {"wav_path": wav, "status": "ok"},
        }
        return TaskRecord(
            task_id=f"task_qt_{uuid.uuid4().hex[:12]}",
            task_type=task_type,
            project=project_name,
            status="done",
            artifact_dir=str(artifact_dir),
            source="web",
            scope={},
            options=captured["options"],
            progress=progress,
            idempotency_key="qt",
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(RuntimeTTSService, "_submit", classmethod(_fake_submit))
    return captured


# ── E1: 无 project + 全局 voice + 一句台词 → durable utility task ─────────
def test_quick_tts_creates_utility_task(quick_env, tmp_path, monkeypatch, global_default_v25):
    captured = _submit_spy(monkeypatch, tmp_path)
    wav = RuntimeTTSService.quick_tts_synthesize(
        text="这是一句临时配音。",
        speaker_audio="/tmp/global_voice.wav",
        timeout=10,
    )
    assert os.path.isfile(wav)
    assert captured["task_type"] == "quick_tts"
    assert captured["project_name"] == QUICK_TTS_CONTEXT
    # artifact 在 <data_dir>/quick_tts/cache/<task_id>/
    assert os.path.join("quick_tts", "cache") in captured["artifact_dir"]


def test_quick_tts_utility_context_never_project(quick_env, tmp_path):
    """Utility context 解析到 runtime/utility_tasks.sqlite3，不是项目目录。"""
    db = TaskRepository.get_database_path(QUICK_TTS_CONTEXT, create=False)
    assert db is not None
    assert db.endswith(os.path.join("runtime", "utility_tasks.sqlite3"))
    assert "projects" not in db
    # 不会在 workspace 创建 __quick_tts__ 项目目录
    assert not os.path.isdir(os.path.join(quick_env, "projects", QUICK_TTS_CONTEXT))


# ── E2: task frozen engine = Settings 默认 ───────────────────────────────
def test_quick_tts_frozen_engine_is_settings_default(
    quick_env, tmp_path, monkeypatch, global_default_v25,
):
    captured = _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.quick_tts_synthesize(
        text="引擎冻结测试。", speaker_audio="/tmp/v.wav", timeout=10,
    )
    snapshot = captured["options"].get("engine_snapshot") or {}
    assert snapshot.get("engine_identity") == "indextts:2.5"
    assert snapshot.get("engine_version") == "2.5"
    profile, source = runtime_tts._select_utility_engine(None)
    assert source == "global_default"


# ── E3: runtime worker 完成 → Audio path 存在 ────────────────────────────
class FakeTtsEngine:
    def __init__(self) -> None:
        self.init_calls = 0
        self.reset_calls = 0
        self.synth_calls: list[dict] = []
        self._profile: dict = {}
        self._tts = None

    def init_engine(self, *, profile=None, **kwargs):
        resolved = tts_profile.resolve_profile(profile or {})
        self.init_calls += 1
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

    def synthesize_segment(self, *, text, speaker_audio, emotion, emo_alpha,
                           speech_rate, output_path, num_beams) -> str:
        self.synth_calls.append({
            "text": text, "num_beams": num_beams,
            "engine_identity": self._profile.get("engine_identity", ""),
        })
        _write_wav(output_path)
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


def test_quick_tts_runtime_worker_produces_wav(tmp_path, monkeypatch, quick_env):
    fake = FakeTtsEngine()
    _install_fake_engine(monkeypatch, fake)
    from services.production_runtime import ProductionRuntime

    runtime = ProductionRuntime(
        owner_id="qt-test",
        lock_path=str(tmp_path / "qt.lock"),
        status_path=str(tmp_path / "qt-status.json"),
    )
    target = tts_profile.resolve_profile({"engine_version": "2"})
    artifact = str(tmp_path / "cache" / "qt1")
    result = runtime.run_quick_tts_direct(
        {"text": "临时一句", "speaker_audio": "spk.wav", "num_beams": 2},
        artifact,
        validate_output=True,
        engine_profile=target,
    )
    assert result["status"] == "ok"
    assert os.path.isfile(result["wav_path"])
    assert fake.synth_calls[0]["engine_identity"] == "indextts:2"


# ── E4: Quick TTS 不出现在项目扫描 ──────────────────────────────────────
def test_quick_tts_not_in_project_scan(quick_env, tmp_path, monkeypatch):
    from services.project import ProjectService

    # 创建一条 Quick TTS 任务（utility DB）
    _submit_spy(monkeypatch, tmp_path)
    RuntimeTTSService.quick_tts_synthesize(
        text="不污染书架", speaker_audio="/tmp/v.wav", timeout=10,
    )
    projects = ProjectService.scan_projects()
    assert "book" in projects
    assert QUICK_TTS_CONTEXT not in projects
    assert "quick_tts" not in projects
    # 项目书架目录也没有 __quick_tts__ / quick_tts 项目
    root = os.path.join(quick_env, "projects")
    assert not os.path.isdir(os.path.join(root, QUICK_TTS_CONTEXT))


# ── E5: Quick TTS export 成功 ────────────────────────────────────────────
def test_quick_tts_export_success(quick_env, tmp_path, monkeypatch):
    wav = _write_wav(str(tmp_path / "src.wav"))
    final = QuickTTSService.export(
        wav_path=wav, name="abc.wav", fmt="mp3", bitrate="192k",
    )
    assert final.endswith("abc.mp3")
    assert os.path.isfile(final)
    assert final.startswith(QuickTTSService.exports_root())
    # 再导出同名 → abc_2.mp3（不覆盖）
    final2 = QuickTTSService.export(
        wav_path=wav, name="abc.wav", fmt="mp3", bitrate="192k",
    )
    assert final2.endswith("abc_2.mp3")


def test_quick_tts_export_missing_source(quick_env, tmp_path):
    with pytest.raises(RuntimeError):
        QuickTTSService.export(
            wav_path=str(tmp_path / "missing.wav"), name="x", fmt="wav",
        )


# ── E6: 正式 production active → Quick TTS 返回 busy 不抢占 ─────────────
def test_quick_tts_busy_when_production_active(
    quick_env, tmp_path, monkeypatch,
):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    outcome, _ = TaskRepository.create_production_task(TaskRecord(
        task_id="task_prod_active",
        task_type="synthesis",
        project="book",
        status="running",
        idempotency_key="prod",
        created_at=now,
        updated_at=now,
    ))
    assert outcome == "created"
    active = runtime_tts._active_tts_lane()
    assert active is not None
    assert active.task_type == "synthesis"

    captured = _submit_spy(monkeypatch, tmp_path)
    with pytest.raises(QuickTTSBusyError):
        RuntimeTTSService.quick_tts_synthesize(
            text="不能抢 GPU", speaker_audio="/tmp/v.wav", timeout=10,
        )
    # busy 时不创建任务
    assert "project_name" not in captured


def test_quick_tts_busy_message_mentions_production():
    active = TaskRecord(
        task_id="t1", task_type="synthesis", project="book", status="running",
    )
    err = QuickTTSBusyError(active)
    assert "正式生产任务正在使用 TTS 引擎" in str(err)
    assert err.code == "QUICK_TTS_BUSY"


# ── E7: runtime warm same engine → 不 reload ─────────────────────────────
def test_quick_tts_reuses_warm_engine(tmp_path, monkeypatch, quick_env):
    fake = FakeTtsEngine()
    _install_fake_engine(monkeypatch, fake)
    from services.production_runtime import ProductionRuntime

    runtime = ProductionRuntime(
        owner_id="qt-warm",
        lock_path=str(tmp_path / "warm.lock"),
        status_path=str(tmp_path / "warm-status.json"),
    )
    target = tts_profile.resolve_profile({"engine_version": "2"})
    # 先预热引擎
    runtime._engine.ensure_ready(target)
    init_calls_before = fake.init_calls
    artifact = str(tmp_path / "cache" / "qt_warm")
    result = runtime.run_quick_tts_direct(
        {"text": "预热复用", "speaker_audio": "spk.wav", "num_beams": 2},
        artifact,
        validate_output=True,
        engine_profile=target,
    )
    assert result["status"] == "ok"
    # 同一 profile：无第二次 init / reset
    assert fake.init_calls == init_calls_before
    assert fake.reset_calls == 0
