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
"""
from __future__ import annotations

import logging
from typing import Any

from repositories._atomic import atomic_write

logger = logging.getLogger(__name__)

_CONFIG_KEY = "prewarm_default_engine"
_DEFAULT_ENABLED = True


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
    def prewarm() -> str:
        """Request the singleton runtime to load the default engine in background.

        Returns a short message for logging / tests.  Never blocks on the model
        load itself: ``request_engine_recycle`` only writes a command file and
        spawns the runtime (``Popen``), the multi-minute model load happens in
        the runtime process asynchronously.
        """
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


__all__ = ["PrewarmService"]
