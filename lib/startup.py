"""生产启动阶段状态机（durable startup phase）。

启动阶段持久化在任务的 ``startup`` dict（SQLite ``startup_json`` 列）里，
Web / MCP / Agent / 日志 / 崩溃恢复共用同一份数据。

阶段语义（按时间推进）：
    task_submitted → runtime_starting → runtime_available → task_claimed
    → engine_loading → engine_ready → preparing_first_segment
    → synthesizing_first_segment → running

异常分支：``engine_failed``（引擎初始化失败，任务终态 error）。

``startup_slow`` 只是**诊断信号**：由读取方实时计算（当前阶段持续时间超过
可配置阈值），**绝不自动终止任务**——IndexTTS2 在 Windows+CUDA 下冷启动
本身可能需要 1-3 分钟。
"""
from __future__ import annotations

import datetime
import os
from typing import Any

STARTUP_PHASES: tuple[str, ...] = (
    "task_submitted",
    "runtime_starting",
    "runtime_available",
    "task_claimed",
    "engine_loading",
    "engine_ready",
    "preparing_first_segment",
    "synthesizing_first_segment",
    "running",
    "engine_failed",
)

# 进入这些阶段后不再累计“启动耗时”，startup_slow 不再有意义。
_NON_STARTUP_PHASES = frozenset({"running", "engine_failed"})

DEFAULT_STARTUP_SLOW_SECONDS = 120.0


def default_startup() -> dict[str, Any]:
    return {"phase": "", "phase_started_at": "", "submitted_at": ""}


def slow_threshold_seconds() -> float:
    raw = os.environ.get("AUDIOBOOK_STUDIO_STARTUP_SLOW_SECONDS", "")
    if not raw:
        return DEFAULT_STARTUP_SLOW_SECONDS
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return DEFAULT_STARTUP_SLOW_SECONDS


def _parse_utc(value: str) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def enrich(startup: dict[str, Any] | None, *, now: datetime.datetime | None = None) -> dict[str, Any]:
    """Return a JSON-safe startup snapshot with elapsed / slow diagnostics.

    输出包含规格要求的全部字段：``startup_phase``、``startup_phase_started_at``、
    ``startup_phase_elapsed_seconds``、``startup_slow``、``startup_diagnostics``、
    以及 ``task_claimed`` / ``first_segment_started`` / ``first_audio_ready``。
    """
    raw = dict(startup or {})
    result: dict[str, Any] = dict(raw)
    phase = str(raw.get("phase") or "")
    started = str(raw.get("phase_started_at") or "")
    elapsed: float | None = None
    if started:
        parsed = _parse_utc(started)
        if parsed is not None:
            base = now or datetime.datetime.now(datetime.timezone.utc)
            elapsed = max((base - parsed).total_seconds(), 0.0)
    result["startup_phase"] = phase
    result["startup_phase_started_at"] = started
    result["startup_phase_elapsed_seconds"] = elapsed
    slow = bool(raw.get("startup_slow"))
    if not slow and phase not in _NON_STARTUP_PHASES and elapsed is not None:
        slow = elapsed > slow_threshold_seconds()
    result["startup_slow"] = slow
    diagnostics = raw.get("startup_diagnostics")
    result["startup_diagnostics"] = (
        diagnostics if isinstance(diagnostics, dict) else {}
    )
    result["task_claimed"] = bool(raw.get("claimed_at"))
    result["first_segment_started"] = bool(raw.get("first_segment_started_at"))
    result["first_audio_ready"] = bool(raw.get("first_audio_ready_at"))
    return result


__all__ = [
    "DEFAULT_STARTUP_SLOW_SECONDS",
    "STARTUP_PHASES",
    "default_startup",
    "enrich",
    "slow_threshold_seconds",
]
