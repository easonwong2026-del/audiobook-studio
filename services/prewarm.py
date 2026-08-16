"""默认 TTS 引擎后台预热服务（UI Ready 后启动，不阻塞首屏）。

目标：把「首次物理模型加载」从用户第一次点击（补录 / 试听 / 正式生产）移动到
Studio 启动后的后台预热阶段。实现基于既有受控引擎切换通道：

- ``ProductionRuntimeClient.request_engine_recycle(engine_id)`` 写一份 durable
  engine command，并确保 singleton runtime 已启动；runtime 在自己的 idle tick
  消费该命令并 ``recycle()`` 到 Settings 默认引擎 —— 完全复用现有 active-task
  guard（生产 / 导出运行中不会强制切换）与双引擎选择规则，不复制一套新 resolution。
- 幂等：runtime 已 ready 且 profile 匹配时 ``request_engine_recycle`` 直接返回，
  不会 reset/recycle/init_engine 第二次（符合「不得重复加载」）。

本模块只读配置 + 调 runtime client，禁止 import gradio / torch / tts_engine。

PR #44 竞态修复：prewarm 不再用「启动线程 + sleep(2s) 猜 UI Ready」，而是
由 Gradio ``app.load`` UI-ready 事件触发 one-shot 请求（``request_ui_prewarm``）：

- UI Ready 才发起：只有 Gradio 确认 server/UI 可用后 ``app.load`` 才会触发；
  launch 失败（端口占用 / startup error）时 finally 停机先发生，prewarm 不会启动。
- single-flight：同一 App 进程生命周期内 prewarm 最多请求一次
  （``not_started`` → ``requested``），浏览器刷新 / 多标签不会重复
  request_engine_recycle / ensure_running。
- shutdown guard：worker 真正执行 ``prewarm()`` 前（以及 ``prewarm()`` 内部）
  再查 application lifecycle 是否仍允许启动新 runtime；若已 shutting_down /
  stopped → ``prewarm_skipped=application_shutdown``，绝不启动 detached runtime。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from repositories._atomic import atomic_write

logger = logging.getLogger(__name__)

_CONFIG_KEY = "prewarm_default_engine"
_DEFAULT_ENABLED = True

# ── one-shot 预热闸门（每个 App 进程生命周期一次）───────────────────────
# 状态机：``not_started`` → ``requested``（首次 UI-ready 事件消费后永不再发起）。
_prewarm_lock = threading.Lock()
_prewarm_state: str = "not_started"


def _config_path() -> str:
    from repositories.config_repo import ConfigRepository

    repo_path = str(getattr(ConfigRepository, "CONFIG_PATH", "") or "")
    if repo_path:
        return repo_path
    from lib import config as _cfg

    return str(getattr(_cfg, "CONFIG_PATH", "") or "config.json")


def _read_raw_config() -> dict[str, Any]:
    try:
        with open(_config_path(), encoding="utf-8") as file:
            import json

            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError, ImportError):
        return {}


class PrewarmService:
    """Settings「启动后预热默认 TTS 引擎」开关与后台预热编排。"""

    @staticmethod
    def is_enabled() -> bool:
        """Read the prewarm toggle (default enabled)."""
        raw = _read_raw_config().get(_CONFIG_KEY, _DEFAULT_ENABLED)
        return bool(raw) if isinstance(raw, (bool, int)) else _DEFAULT_ENABLED

    @staticmethod
    def set_enabled(enabled: bool) -> str:
        """Persist the prewarm toggle; returns a user-facing message."""
        data = _read_raw_config()
        data[_CONFIG_KEY] = bool(enabled)
        atomic_write(_config_path(), data)
        state = "开启" if bool(enabled) else "关闭"
        return f"✅ 已{'开启' if bool(enabled) else '关闭'}「启动后预热默认 TTS 引擎」；{state}后下次启动生效。"

    @staticmethod
    def default_engine_id() -> str | None:
        """Resolve the Settings default engine id (``legacy`` / ``indextts25``).

        GPU-free：只读 config / 环境变量，绝不加载模型。
        """
        try:
            from ui.settings_handlers import get_tts_engine_settings

            settings = get_tts_engine_settings()
            return str(settings.get("engine") or "") or None
        except Exception:  # pragma: no cover - best effort read
            logger.debug("读取默认引擎设置失败", exc_info=True)
            return None

    @staticmethod
    def has_active_tts_tasks() -> bool:
        """Whether any TTS/export lane is active (production / supplement / ...)."""
        try:
            from repositories.task_repo import TaskRepository

            return any(
                str(getattr(record, "status", ""))
                in {
                    "active", "pending", "queued", "starting", "preparing",
                    "submitting", "running", "pausing", "paused", "recovering",
                    "cancelling",
                }
                for record in TaskRepository.list_tasks()
                if str(getattr(record, "task_type", ""))
                in {"synthesis", "voice_preview", "preview", "supplement", "quick_tts", "export"}
            )
        except Exception:  # pragma: no cover - a read failure must not crash
            logger.debug("读取活动任务失败", exc_info=True)
            return True

    @staticmethod
    def should_prewarm() -> bool:
        """Prewarm precondition: toggle on + default engine resolvable + idle."""
        if not PrewarmService.is_enabled():
            return False
        if not PrewarmService.default_engine_id():
            return False
        if PrewarmService.has_active_tts_tasks():
            return False
        return True

    @staticmethod
    def _lifecycle_allows_runtime() -> bool:
        """True only while the application lifecycle still permits a runtime.

        Guards the one-shot prewarm against the startup race: if the Gradio
        server failed fast and ``request_application_shutdown`` already moved
        the lifecycle to ``shutting_down`` / ``stopped``, prewarm must never
        call ``request_engine_recycle`` / ``ensure_running`` (which would
        resurrect a detached runtime after graceful shutdown).
        """
        try:
            from services.application_lifecycle import get_application_lifecycle

            return get_application_lifecycle().state == "running"
        except Exception:  # pragma: no cover - a read failure must not crash
            logger.debug("读取 application lifecycle 失败", exc_info=True)
            return True

    @classmethod
    def request_ui_prewarm(cls) -> str:
        """One-shot UI-ready prewarm entry (Gradio ``app.load`` callback).

        Fired only after Gradio confirms the UI/server is usable, so prewarm
        is never started on a launch that failed (port busy / server error).
        The callback stays fast: it validates the gate, spawns a daemon
        worker and returns immediately -- the multi-minute model load happens
        inside the runtime process, never here.

        Returns a short message for logging / tests:
          ``prewarm_requested`` / ``prewarm_skipped=duplicate`` /
          ``prewarm_skipped=application_shutdown`` / ``prewarm_skipped=disabled``.
        """
        global _prewarm_state
        with _prewarm_lock:
            if _prewarm_state != "not_started":
                return "prewarm_skipped=duplicate"
            if not cls._lifecycle_allows_runtime():
                _prewarm_state = "requested"
                return "prewarm_skipped=application_shutdown"
            if not cls.is_enabled():
                _prewarm_state = "requested"
                return "prewarm_skipped=disabled"
            _prewarm_state = "requested"
        thread = threading.Thread(
            target=cls._prewarm_worker, daemon=True, name="audiobook-prewarm"
        )
        thread.start()
        return "prewarm_requested"

    @classmethod
    def _prewarm_worker(cls) -> None:
        """Daemon worker: re-check the shutdown guard, then run the real prewarm.

        The worker may be queued while the application enters shutdown (e.g. a
        UI-ready callback already fired, then ``app.launch`` failed and the
        ``finally`` block ran).  Re-checking the lifecycle here -- immediately
        before ``PrewarmService.prewarm()`` -- is what closes the race: once
        ``shutting_down`` / ``stopped``, the runtime is never started.
        """
        try:
            if not cls._lifecycle_allows_runtime():
                logger.info("prewarm_event=prewarm_skipped=application_shutdown")
                return
            if cls.should_prewarm():
                message = cls.prewarm()
                logger.info("prewarm_event=%s", message)
            else:
                logger.info("prewarm_event=skipped (disabled / no default / busy)")
        except Exception:  # pragma: no cover - prewarm is best effort
            logger.exception("后台预热失败")

    @staticmethod
    def prewarm() -> str:
        """Request the singleton runtime to load the default engine in background.

        Returns a short message for logging / tests.  Never blocks on the model
        load itself: ``request_engine_recycle`` only writes a command file and
        spawns the runtime (``Popen``), the multi-minute model load happens in
        the runtime process asynchronously.

        Defense-in-depth shutdown guard: even a direct caller (not just the
        UI-ready worker) is refused once the application lifecycle is
        ``shutting_down`` / ``stopped``.
        """
        if not PrewarmService._lifecycle_allows_runtime():
            return "prewarm_skipped=application_shutdown"
        engine_id = PrewarmService.default_engine_id()
        if not engine_id:
            return "prewarm_skipped=no_default_engine"
        if PrewarmService.has_active_tts_tasks():
            return "prewarm_skipped=active_tasks"
        try:
            from services.production_runtime import ProductionRuntimeClient

            ProductionRuntimeClient.request_engine_recycle(engine_id)
            return f"prewarm_requested engine={engine_id}"
        except Exception as exc:  # pragma: no cover - prewarm is best effort
            logger.warning("后台预热请求失败: %s", exc)
            return f"prewarm_failed error={exc}"


def reset_prewarm_state() -> None:
    """Reset the one-shot gate (test isolation / app lifecycle restart)."""
    global _prewarm_state
    with _prewarm_lock:
        _prewarm_state = "not_started"


__all__ = ["PrewarmService", "reset_prewarm_state"]
