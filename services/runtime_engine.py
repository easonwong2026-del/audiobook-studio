"""Runtime-owned TTS engine lifecycle (single-flight, fail-fast).

The independent ``--serve`` ProductionRuntime process is the only owner of
``lib.tts_engine`` state.  This module provides:

- a thread-safe state machine ``uninitialized -> loading -> ready | error``
  with single-flight initialization (concurrent callers never double-load
  the GPU model);
- fail-fast semantics: a failed ``init_engine`` raises ``EngineInitError``
  (code ``TTS_ENGINE_INIT_FAILED``) so callers can end the whole task
  instead of entering the segment loop;
- a durable, GPU-free engine status snapshot under the existing
  ``logs/runtime`` area so Web/MCP status queries can distinguish
  ``cast_ready`` from ``engine_ready`` without loading the model.

Web/MCP processes must never call ``ensure_ready`` themselves: engine state
is process-local and only meaningful inside the runtime process.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

from repositories._atomic import atomic_write

logger = logging.getLogger(__name__)

# Stable engine state values exposed to status queries:
#   unknown         no runtime has declared state (or runtime shut down)
#   uninitialized   runtime alive, engine not loaded yet
#   loading         init in progress
#   ready           engine usable
#   recovering      engine recycle in progress (self-healing)
#   error           init/recycle terminal failure
ENGINE_STATES = ("unknown", "uninitialized", "loading", "ready", "recovering", "error")

# Runtime ownership is a separate concern from the model state.  A status
# file may outlive a crashed process, so ``engine_state=ready`` is only
# trustworthy while the owning runtime is live and heartbeating.
RUNTIME_STATES = ("unknown", "starting", "running", "recovering", "stopping", "error")
_LIVE_RUNTIME_STATES = frozenset({"starting", "running", "recovering", "stopping", "error"})
_STATUS_MAX_AGE_SECONDS = 10.0

# Stable machine-readable error code for engine bootstrap failures.
TTS_ENGINE_INIT_FAILED = "TTS_ENGINE_INIT_FAILED"

_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:Users|home|private|tmp|var|opt)/)[^\s,;)]*"
)


def sanitize_public_error(value: Any) -> str:
    """Strip local absolute paths from task-facing error text."""
    return _PATH_PATTERN.sub("<local-path>", str(value or "")).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def runtime_engine_status_path() -> str:
    """Status snapshot lives inside the existing logs/runtime directory."""
    from lib import config

    return os.path.join(config.get_data_dir(), "logs", "runtime_engine_status.json")


def _empty_runtime_engine_status() -> dict[str, Any]:
    return {
        "state": "unknown",  # backwards-compatible alias for engine_state
        "runtime_state": "unknown",
        "engine_state": "unknown",
        "pid": 0,
        "owner_id": "",
        "updated_at": "",
        "runtime_updated_at": "",
        "error_summary": "",
        "engine_generation": 0,
        "recovery_count": 0,
        "last_error_code": "",
        "last_recovery_at": "",
        "status_stale": True,
    }


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _timestamp_is_fresh(value: str) -> bool:
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - timestamp).total_seconds() <= _STATUS_MAX_AGE_SECONDS
    except (TypeError, ValueError, OverflowError):
        return False


def read_runtime_engine_status() -> dict[str, Any]:
    """Return the last engine state declared by the runtime process.

    This is a pure file read: it never imports torch or initializes the
    model, so status queries stay GPU-free.  When no runtime has declared a
    state yet the result is ``{"state": "unknown", ...}``.
    """
    empty = _empty_runtime_engine_status()
    path = runtime_engine_status_path()
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return empty
        engine_state = str(data.get("engine_state") or data.get("state") or "unknown")
        if engine_state not in ENGINE_STATES:
            engine_state = "unknown"
        runtime_state = str(data.get("runtime_state") or "unknown")
        if runtime_state not in RUNTIME_STATES:
            runtime_state = "unknown"
        pid = int(data.get("pid") or 0)
        runtime_updated_at = str(
            data.get("runtime_updated_at") or data.get("updated_at") or ""
        )
        live = (
            runtime_state in _LIVE_RUNTIME_STATES
            and _pid_is_alive(pid)
            and _timestamp_is_fresh(runtime_updated_at)
        )
        status = {
            "state": engine_state if live else "unknown",
            "runtime_state": runtime_state if live else "unknown",
            "engine_state": engine_state if live else "unknown",
            "pid": pid,
            "owner_id": str(data.get("owner_id") or ""),
            "updated_at": str(data.get("updated_at") or ""),
            "runtime_updated_at": runtime_updated_at,
            "error_summary": sanitize_public_error(data.get("error_summary")),
            "engine_generation": int(data.get("engine_generation") or 0),
            "recovery_count": int(data.get("recovery_count") or 0),
            "last_error_code": str(data.get("last_error_code") or ""),
            "last_recovery_at": str(data.get("last_recovery_at") or ""),
            "status_stale": not live,
        }
        return status
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return empty


class EngineInitError(RuntimeError):
    """Engine bootstrap failed; the task must fail before any segment work."""

    code = TTS_ENGINE_INIT_FAILED

    def __init__(
        self,
        summary: str = "",
        *,
        original_exception: BaseException | None = None,
    ) -> None:
        self.summary = sanitize_public_error(summary) or "TTS 引擎初始化失败"
        self.original_exception = original_exception
        super().__init__(f"{self.code}: {self.summary}")


class RuntimeEngineLifecycle:
    """Process-local engine state machine with single-flight initialization."""

    def __init__(
        self,
        *,
        owner_id: str = "",
        status_path: str | None = None,
    ) -> None:
        self._owner_id = str(owner_id or "")
        self._status_path = status_path or runtime_engine_status_path()
        self._condition = threading.RLock()
        self._state = "uninitialized"
        self._error_summary = ""
        self._pid = os.getpid()
        self._runtime_state = "unknown"
        self._runtime_updated_at = ""
        self._generation = 0
        self._recovery_count = 0
        self._last_error_code = ""
        self._last_recovery_at = ""

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def error_summary(self) -> str:
        with self._condition:
            return self._error_summary

    def snapshot(self) -> dict[str, Any]:
        """Thread-safe public snapshot used by tests and diagnostics."""
        with self._condition:
            return {
                "state": self._state,
                "error_summary": sanitize_public_error(self._error_summary),
                "owner_id": self._owner_id,
                "pid": self._pid,
                "updated_at": _now(),
                "runtime_state": self._runtime_state,
                "runtime_updated_at": self._runtime_updated_at,
                "engine_generation": self._generation,
                "recovery_count": self._recovery_count,
                "last_error_code": self._last_error_code,
                "last_recovery_at": self._last_recovery_at,
            }

    def reset(self) -> None:
        """Begin a fresh engine lifecycle (new serve cycle)."""
        with self._condition:
            self._state = "uninitialized"
            self._error_summary = ""
            self._last_error_code = ""
            self._publish_unlocked("uninitialized", error_summary="")

    @property
    def runtime_state(self) -> str:
        with self._condition:
            return self._runtime_state

    def set_runtime_state(self, state: str) -> None:
        """Publish runtime ownership state without changing engine state."""
        normalized = str(state or "unknown")
        if normalized not in RUNTIME_STATES:
            raise ValueError(f"invalid runtime state: {normalized}")
        with self._condition:
            self._runtime_state = normalized
            self._runtime_updated_at = _now()
            self._publish_unlocked(self._state, error_summary=self._error_summary)

    def heartbeat(self) -> None:
        """Refresh the liveness timestamp without loading or touching the model."""
        with self._condition:
            if self._runtime_state == "unknown":
                return
            self._runtime_updated_at = _now()
            self._publish_unlocked(self._state, error_summary=self._error_summary)

    def ensure_ready(self) -> None:
        """Initialize the TTS engine exactly once per lifecycle.

        - ``ready``: returns immediately (engine reused, no reload).
        - ``error``: raises ``EngineInitError`` immediately (fail-fast,
          no repeated GPU attempts for the remaining requests).
        - otherwise: this caller performs the single initialization while
          holding the lifecycle lock; every concurrent caller blocks on the
          same lock and sees the result when it completes.
        """
        with self._condition:
            if self._state == "ready":
                return
            if self._state == "error":
                raise EngineInitError(self._error_summary)
            self._state = "loading"
            self._publish_unlocked("loading", error_summary="")
            try:
                from lib import tts_engine

                tts_engine.init_engine()
            except Exception as exc:  # pylint: disable=broad-except
                summary = sanitize_public_error(exc)
                self._error_summary = summary
                self._state = "error"
                self._last_error_code = "TTS_ENGINE_INIT_FAILED"
                self._publish_unlocked("error", error_summary=summary)
                logger.error(
                    "runtime_event=engine_init_failure pid=%s owner=%s error=%s",
                    self._pid,
                    self._owner_id,
                    summary,
                )
                raise EngineInitError(
                    summary,
                    original_exception=exc,
                ) from exc
            self._generation += 1
            self._state = "ready"
            self._publish_unlocked("ready", error_summary="")
            logger.info(
                "runtime_event=engine_init_success pid=%s owner=%s",
                self._pid,
                self._owner_id,
            )

    def recycle(self) -> int:
        """Detach the real engine, reload it, and advance the generation.

        Calls ``lib.tts_engine.reset_engine()`` (actual ``_tts`` detach under
        ``_ENGINE_LOCK``, cache clearing, ``gc``, guarded CUDA cache flush)
        followed by a fresh ``init_engine()``.  On success the generation
        increments and the state returns to ``ready``; on failure the state
        becomes ``error`` and ``EngineInitError`` is raised so the caller
        can fail the task fast.
        """
        with self._condition:
            if self._state not in {"ready", "uninitialized", "error"}:
                raise EngineInitError("引擎当前状态不能执行 recycle")
            self._state = "recovering"
            self._publish_unlocked("recovering", error_summary="")
            try:
                from lib import tts_engine

                tts_engine.reset_engine()
                tts_engine.init_engine()
            except Exception as exc:  # pylint: disable=broad-except
                summary = sanitize_public_error(exc)
                self._error_summary = summary
                self._state = "error"
                self._last_error_code = "TTS_ENGINE_INIT_FAILED"
                self._publish_unlocked("error", error_summary=summary)
                logger.error(
                    "runtime_event=engine_recycle_failure pid=%s owner=%s "
                    "generation=%s recovery_count=%s error=%s",
                    self._pid,
                    self._owner_id,
                    self._generation,
                    self._recovery_count,
                    summary,
                )
                raise EngineInitError(
                    summary,
                    original_exception=exc,
                ) from exc
            self._generation += 1
            self._recovery_count += 1
            self._last_recovery_at = _now()
            self._last_error_code = ""
            self._state = "ready"
            self._publish_unlocked("ready", error_summary="")
            logger.info(
                "runtime_event=engine_recycle_success pid=%s owner=%s "
                "generation=%s recovery_count=%s",
                self._pid,
                self._owner_id,
                self._generation,
                self._recovery_count,
            )
            return self._generation

    def mark_unknown(self) -> None:
        """Declare the runtime no longer owns a live engine (shutdown)."""
        with self._condition:
            self._state = "unknown"
            self._runtime_state = "unknown"
            self._runtime_updated_at = _now()
            self._error_summary = ""
            self._publish_unlocked("unknown", error_summary="")

    def _publish_unlocked(self, state: str, *, error_summary: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._status_path), exist_ok=True)
            atomic_write(self._status_path, {
                "state": state,
                "engine_state": state,
                "runtime_state": self._runtime_state,
                "pid": self._pid,
                "owner_id": self._owner_id,
                "updated_at": _now(),
                "runtime_updated_at": self._runtime_updated_at,
                "error_summary": sanitize_public_error(error_summary),
                "engine_generation": self._generation,
                "recovery_count": self._recovery_count,
                "last_error_code": self._last_error_code,
                "last_recovery_at": self._last_recovery_at,
            })
        except Exception:  # pylint: disable=broad-except
            # A status snapshot is best-effort; engine work must not depend
            # on being able to write it.
            logger.warning("runtime_event=engine_status_write_failed path=%s", self._status_path)


__all__ = [
    "ENGINE_STATES",
    "RUNTIME_STATES",
    "EngineInitError",
    "RuntimeEngineLifecycle",
    "TTS_ENGINE_INIT_FAILED",
    "read_runtime_engine_status",
    "runtime_engine_status_path",
    "sanitize_public_error",
]
