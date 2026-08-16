"""PR B 修复 1：默认引擎后台预热（PrewarmService）回归测试。

覆盖：
1. prewarm disabled → 不预加载；
2. enabled + Settings 默认 2.5 → 后台 runtime target=2.5（不阻塞 UI 初始化）；
3. runtime 已 Ready 且 profile 匹配 → 不重复 init（幂等）；
4. Settings 切 v2 且 runtime idle → 预热 target 变 v2；
5. 有活动 TTS 任务 → 不抢占预热。

PR #44 竞态修复（UI-ready one-shot prewarm）回归：
6. UI load 未发生 → prewarm 不调用；
7. 第一次 UI load → prewarm request 一次；
8. 重复 UI load / 多次 callback → 仍只 request 一次（single-flight）；
9. lifecycle 已 shutting_down / stopped → prewarm 不调用 ProductionRuntimeClient；
10. UI-ready 已排队 → 随后 application shutdown → worker 执行 → 不得
    ensure_running / request_engine_recycle；
11. 正常：lifecycle running + enabled → 后台 request，callback 本身快速返回。
"""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
import time
import types
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.prewarm as prewarm_mod  # noqa: E402
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

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    TaskRepository.create_runtime_task(TaskRecord(
        task_id="task_active_prewarm",
        task_type="supplement",
        project=QUICK_TTS_CONTEXT,
        status="running",
        owner_id="runtime_live_prewarm",
        heartbeat_at=now,
        idempotency_key="prewarm-active",
        created_at=now,
        updated_at=now,
    ))
    assert PrewarmService.has_active_tts_tasks() is True
    assert PrewarmService.should_prewarm() is False


# ═══════════ PR #44：UI-ready one-shot prewarm（竞态修复）═══════════════

def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.01) -> bool:
    """轮询等待后台 daemon worker 达成条件（避免固定 sleep）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture(autouse=True)
def _reset_prewarm_gate():
    """每个测试前后重置 one-shot 闸门状态，避免跨测试污染。"""
    prewarm_mod.reset_prewarm_state()
    yield
    prewarm_mod.reset_prewarm_state()


@pytest.fixture
def lifecycle_running(monkeypatch):
    """Fresh application lifecycle singleton (running) + hermetic runtime stub.

    The stub records ``request_engine_recycle`` / ``ensure_running`` calls so
    tests can prove prewarm never starts a runtime after shutdown, and it
    provides ``request_shutdown`` so lifecycle transitions never touch a real
    runtime in tests.
    """
    from services.application_lifecycle import (
        get_application_lifecycle,
        reset_application_lifecycle,
    )

    reset_application_lifecycle()
    lifecycle = get_application_lifecycle()
    calls: dict = {
        "request_shutdown": 0,
        "request_engine_recycle": [],
        "ensure_running": 0,
    }

    class _FakeClient:
        @classmethod
        def request_shutdown(cls, reason="application_shutdown", timeout=30.0, terminate_timeout=10.0):
            calls["request_shutdown"] += 1
            return True

        @classmethod
        def request_engine_recycle(cls, engine_id: str) -> bool:
            # Mirror the real ProductionRuntimeClient contract: the request
            # path also ensures the runtime process is up.
            calls["request_engine_recycle"].append(engine_id)
            calls["ensure_running"] += 1
            return True

        @classmethod
        def ensure_running(cls):
            calls["ensure_running"] += 1
            return None

    module = types.ModuleType("services.production_runtime")
    module.ProductionRuntimeClient = _FakeClient
    monkeypatch.setitem(sys.modules, "services.production_runtime", module)
    try:
        yield lifecycle, calls
    finally:
        reset_application_lifecycle()


# ── 6. UI load 未发生 → prewarm 不调用 ─────────────────────────────────
def test_prewarm_not_requested_before_ui_load(lifecycle_running):
    """UI-ready 事件发生前，prewarm 不得触碰 runtime client。"""
    _lifecycle, calls = lifecycle_running
    assert prewarm_mod._prewarm_state == "not_started"
    assert calls["request_engine_recycle"] == []
    assert calls["ensure_running"] == 0


# ── 7. 第一次 UI load → prewarm request 一次 ────────────────────────────
def test_first_ui_load_requests_once(
    prewarm_config, settings_default_v25, no_active_tasks, lifecycle_running,
):
    prewarm_config({"prewarm_default_engine": True})
    _lifecycle, calls = lifecycle_running

    result = prewarm_mod.PrewarmService.request_ui_prewarm()

    assert result == "prewarm_requested"
    assert prewarm_mod._prewarm_state == "requested"
    # worker 是 daemon 线程：等后台真正发出 request
    assert _wait_until(lambda: len(calls["request_engine_recycle"]) == 1)
    assert calls["request_engine_recycle"] == ["indextts25"]
    assert calls["ensure_running"] == 1


# ── 8. 重复 UI load / 多次 callback → 仍只 request 一次（single-flight） ─
def test_repeated_ui_load_single_flight(
    prewarm_config, settings_default_v25, no_active_tasks, lifecycle_running,
):
    prewarm_config({"prewarm_default_engine": True})
    _lifecycle, calls = lifecycle_running

    first = prewarm_mod.PrewarmService.request_ui_prewarm()
    assert first == "prewarm_requested"
    for _ in range(4):  # 浏览器刷新 / 多标签 / 多次 app.load
        duplicate = prewarm_mod.PrewarmService.request_ui_prewarm()
        assert duplicate == "prewarm_skipped=duplicate"

    assert _wait_until(lambda: len(calls["request_engine_recycle"]) == 1)
    assert calls["request_engine_recycle"] == ["indextts25"]
    assert calls["ensure_running"] == 1, "single-flight：ensure_running 只允许一次"


# ── 9. lifecycle 已 shutting_down / stopped → prewarm 不调用 ────────────
def test_lifecycle_stopped_skips_prewarm(
    prewarm_config, settings_default_v25, lifecycle_running,
):
    prewarm_config({"prewarm_default_engine": True})
    lifecycle, calls = lifecycle_running

    assert lifecycle.request_application_shutdown("test_shutdown") is True
    assert lifecycle.state == "stopped"

    result = prewarm_mod.PrewarmService.request_ui_prewarm()

    assert result == "prewarm_skipped=application_shutdown"
    assert calls["request_engine_recycle"] == []
    assert calls["ensure_running"] == 0


def test_lifecycle_shutting_down_intermediate_also_skips(
    prewarm_config, settings_default_v25, lifecycle_running,
):
    """shutdown 序列进行中（shutting_down，尚未 stopped）同样拒绝 prewarm。"""
    prewarm_config({"prewarm_default_engine": True})
    lifecycle, calls = lifecycle_running

    # white-box：模拟 shutdown 已开始但尚未完成的状态
    with lifecycle._lock:
        lifecycle._state = "shutting_down"
    assert lifecycle.is_shutting_down() is True

    result = prewarm_mod.PrewarmService.request_ui_prewarm()

    assert result == "prewarm_skipped=application_shutdown"
    assert calls["request_engine_recycle"] == []
    assert calls["ensure_running"] == 0


# ── 10. UI-ready 已排队 → 随后 shutdown → worker 执行 → 不得启动 runtime ─
def test_worker_after_shutdown_never_starts_runtime(
    prewarm_config, settings_default_v25, lifecycle_running, monkeypatch,
):
    """竞态核心路径：callback 已排队后 shutdown 先发生，worker 必须放弃。"""
    prewarm_config({"prewarm_default_engine": True})
    lifecycle, calls = lifecycle_running

    release = threading.Event()
    worker_done = threading.Event()
    original_worker = prewarm_mod.PrewarmService._prewarm_worker

    def gated_worker(cls):  # noqa: ANN001 - classmethod 绑定后首参为 cls
        release.wait(timeout=10.0)
        try:
            original_worker()
        finally:
            worker_done.set()

    monkeypatch.setattr(
        prewarm_mod.PrewarmService,
        "_prewarm_worker",
        classmethod(gated_worker),
    )

    # UI-ready callback 已排队（worker 被 gate 挡住，尚未执行）
    result = prewarm_mod.PrewarmService.request_ui_prewarm()
    assert result == "prewarm_requested"

    # 随后 launch 失败 → finally → application shutdown 先完成
    assert lifecycle.request_application_shutdown("test_shutdown") is True
    release.set()

    assert worker_done.wait(timeout=10.0) is True
    assert calls["request_engine_recycle"] == []
    assert calls["ensure_running"] == 0, "shutdown 后 worker 不得重启 runtime"


# ── 11. 正常：running + enabled → 后台 request，callback 快速返回 ───────
def test_ui_ready_callback_returns_fast_and_requests_in_background(
    prewarm_config, settings_default_v25, no_active_tasks, lifecycle_running,
):
    prewarm_config({"prewarm_default_engine": True})
    _lifecycle, calls = lifecycle_running

    started = time.monotonic()
    result = prewarm_mod.PrewarmService.request_ui_prewarm()
    elapsed = time.monotonic() - started

    assert result == "prewarm_requested"
    assert elapsed < 0.5, f"app.load callback 应快速返回，实际 {elapsed:.3f}s"
    # 真正模型请求发生在后台 worker（不阻塞 callback）
    assert _wait_until(lambda: len(calls["request_engine_recycle"]) == 1)
    assert calls["request_engine_recycle"] == ["indextts25"]
    assert calls["ensure_running"] == 1


# ── AST：app.py 接线（UI-ready one-shot prewarm，替代 sleep 线程）────────
_APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)
with open(_APP_PATH, encoding="utf-8") as _app_fh:
    _APP_SRC = _app_fh.read()
_APP_TREE = ast.parse(_APP_SRC)


def test_app_load_wires_ui_ready_prewarm():
    """app.py 用 app.load(_on_ui_ready_prewarm) 接线，且旧 sleep 线程方案已移除。"""
    assert "def _on_ui_ready_prewarm" in _APP_SRC, "缺少 UI-ready prewarm callback"
    assert "app.load(_on_ui_ready_prewarm)" in _APP_SRC, "app.load 未接线"
    assert "_start_background_prewarm" not in _APP_SRC, (
        "旧「线程+sleep(2s) 猜 UI Ready」方案必须移除"
    )
    assert "time.sleep(2.0)" not in _APP_SRC.split("def _on_ui_ready_prewarm")[0]


def test_app_main_keeps_finally_shutdown_guard():
    """__main__ 仍保留 try/finally request_application_shutdown（最可靠退出边）。"""
    main_block = next(
        node
        for node in _APP_TREE.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(comp, ast.Name) and comp.id == "__name__"
            for comp in ast.walk(node.test)
        )
    )
    source = ast.get_source_segment(_APP_SRC, main_block) or ""
    assert "app.queue().launch(" in source
    assert "request_application_shutdown" in source
    assert "_start_background_prewarm()" not in source
