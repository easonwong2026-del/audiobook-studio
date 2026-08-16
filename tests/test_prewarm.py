"""PR B 修复 1：默认引擎后台预热（PrewarmService）回归测试。

覆盖：
1. prewarm disabled → 不预加载；
2. enabled + Settings 默认 2.5 → 后台 runtime target=2.5（不阻塞 UI 初始化）；
3. runtime 已 Ready 且 profile 匹配 → 不重复 init（幂等）；
4. Settings 切 v2 且 runtime idle → 预热 target 变 v2；
5. 有活动 TTS 任务 → 不抢占预热。
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.prewarm import PrewarmService  # noqa: E402


@pytest.fixture
def prewarm_config(monkeypatch, tmp_path):
    """Point PrewarmService config reads at an isolated temp config file."""
    import services.prewarm as prewarm_mod

    config_file = str(tmp_path / "config.json")

    def _read() -> dict:
        try:
            with open(config_file, encoding="utf-8") as fh:
                value = json.load(fh)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    monkeypatch.setattr(prewarm_mod, "_config_path", lambda: config_file)
    monkeypatch.setattr(prewarm_mod, "_read_raw_config", _read)

    def _write(data: dict) -> None:
        with open(config_file, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    return _write


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    """Redirect the global data dir so task scans never touch the real one."""
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def settings_default_v25(monkeypatch):
    """Settings 默认引擎 = IndexTTS 2.5。"""
    from ui import settings_handlers

    monkeypatch.setattr(
        settings_handlers,
        "get_tts_engine_settings",
        lambda: {
            "engine": "indextts25",
            "legacy_model_dir": "D:/models/v2",
            "indextts25_model_dir": "D:/models/v25",
        },
    )
    return settings_handlers


@pytest.fixture
def settings_default_v2(monkeypatch):
    """Settings 默认引擎 = IndexTTS 2 Legacy。"""
    from ui import settings_handlers

    monkeypatch.setattr(
        settings_handlers,
        "get_tts_engine_settings",
        lambda: {
            "engine": "legacy",
            "legacy_model_dir": "D:/models/v2",
            "indextts25_model_dir": "D:/models/v25",
        },
    )
    return settings_handlers


@pytest.fixture
def no_active_tasks(monkeypatch):
    monkeypatch.setattr(
        PrewarmService,
        "has_active_tts_tasks",
        staticmethod(lambda: False),
    )


def _fake_runtime_client(monkeypatch, captured: dict) -> None:
    """Stub ProductionRuntimeClient so prewarm never spawns a real runtime."""
    import services.prewarm as prewarm_mod

    class _FakeClient:
        @classmethod
        def request_engine_recycle(cls, engine_id: str) -> bool:
            captured["engine_id"] = engine_id
            return True

    module = types.ModuleType("services.production_runtime")
    module.ProductionRuntimeClient = _FakeClient
    monkeypatch.setitem(sys.modules, "services.production_runtime", module)
    prewarm_mod.PrewarmService.prewarm()


# ── A1: disabled → 不预加载 ──────────────────────────────────────────────
def test_prewarm_disabled_skips(prewarm_config, settings_default_v25, no_active_tasks):
    prewarm_config({"prewarm_default_engine": False})
    assert PrewarmService.is_enabled() is False
    assert PrewarmService.should_prewarm() is False


def test_prewarm_default_enabled(prewarm_config, settings_default_v25, no_active_tasks):
    prewarm_config({})
    assert PrewarmService.is_enabled() is True
    assert PrewarmService.should_prewarm() is True


# ── A2: enabled + Settings 2.5 → target=2.5，后台请求不阻塞 ──────────────
def test_prewarm_targets_v25_when_settings_default_v25(
    prewarm_config, settings_default_v25, no_active_tasks, monkeypatch,
):
    prewarm_config({"prewarm_default_engine": True})
    captured: dict = {}
    _fake_runtime_client(monkeypatch, captured)
    assert captured.get("engine_id") == "indextts25"


def test_prewarm_targets_v2_when_settings_default_v2(
    prewarm_config, settings_default_v2, no_active_tasks, monkeypatch,
):
    prewarm_config({"prewarm_default_engine": True})
    captured: dict = {}
    _fake_runtime_client(monkeypatch, captured)
    assert captured.get("engine_id") == "legacy"


# ── A3: runtime 已 Ready 同 profile → 不重复 init（幂等） ───────────────
def test_prewarm_same_profile_does_not_reload(
    tmp_path, monkeypatch, isolated_data_dir,
):
    from lib.tts_profile import resolve_profile
    from services.production_runtime import ProductionRuntime

    fake_init_calls: list = []

    class _FakeEngine:
        def __init__(self) -> None:
            self._profile: dict = {}
            self._tts = None

        def init_engine(self, *, profile=None, **kwargs):
            fake_init_calls.append(profile)
            self._profile = dict(resolve_profile(profile or {}))
            self._tts = object()

        def reset_engine(self) -> None:
            self._tts = None
            self._profile = {}

        def get_engine_profile(self) -> dict:
            return dict(self._profile)

        def empty_cache(self) -> None:
            pass

    module = types.ModuleType("lib.tts_engine")
    fake = _FakeEngine()
    module.init_engine = fake.init_engine
    module.reset_engine = fake.reset_engine
    module.get_engine_profile = fake.get_engine_profile
    module.empty_cache = fake.empty_cache
    import lib

    monkeypatch.setitem(sys.modules, "lib.tts_engine", module)
    monkeypatch.setattr(lib, "tts_engine", module, raising=False)

    runtime = ProductionRuntime(
        owner_id="prewarm-test",
        lock_path=str(tmp_path / "runtime.lock"),
        status_path=str(tmp_path / "status.json"),
    )
    profile = resolve_profile({"engine_version": "2"})
    runtime._engine.ensure_ready(profile)
    assert len(fake_init_calls) == 1

    # 同 profile 再预热 → request_engine_recycle 幂等，无第二次 init/reset
    runtime.request_engine_recycle("legacy")
    assert len(fake_init_calls) == 1
    assert runtime._engine.snapshot()["recovery_count"] == 0


# ── A4: Settings 切 v2 且 runtime idle → 预热 target 变 v2 ───────────────
def test_prewarm_switch_target_follows_settings(
    prewarm_config, settings_default_v2, no_active_tasks, monkeypatch,
):
    prewarm_config({"prewarm_default_engine": True})
    assert PrewarmService.default_engine_id() == "legacy"
    captured: dict = {}
    _fake_runtime_client(monkeypatch, captured)
    assert captured.get("engine_id") == "legacy"


# ── A5: 活动 TTS 任务 → 不抢占 ──────────────────────────────────────────
def test_prewarm_skips_when_active_tasks(
    prewarm_config, settings_default_v25, isolated_data_dir, monkeypatch,
):
    from repositories.task_repo import QUICK_TTS_CONTEXT, TaskRecord, TaskRepository

    prewarm_config({"prewarm_default_engine": True})
    # 无活动任务 → 允许预热
    assert PrewarmService.has_active_tts_tasks() is False
    assert PrewarmService.should_prewarm() is True

    now = "2026-08-16T00:00:00Z"
    TaskRepository.create_runtime_task(TaskRecord(
        task_id="task_active_prewarm",
        task_type="supplement",
        project=QUICK_TTS_CONTEXT,
        status="running",
        idempotency_key="prewarm-active",
        created_at=now,
        updated_at=now,
    ))
    assert PrewarmService.has_active_tts_tasks() is True
    assert PrewarmService.should_prewarm() is False
