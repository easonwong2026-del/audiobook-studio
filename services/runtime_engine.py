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
import time
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
        "engine_backend": "",
        "engine_version": "",
        "engine_identity": "",
        "model_identity": "",
        "precision": "",
        "device": "",
        "cache_identity": "",
    }


def _is_windows() -> bool:
    """Windows platform probe.

    独立函数而非直接读 ``os.name``：测试通过 monkeypatch 本函数切换平台
    分支，避免污染全局 ``os.name``（全局改动会波及 pytest 自身的 pathlib
    行为，例如 Linux 上 ``Path()`` 被错误实例化为 ``WindowsPath`` 崩溃）。
    """
    return os.name == "nt"


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _is_windows():
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    """Probe a Windows PID without sending it a signal or control event.

    ``os.kill(pid, 0)`` is intentionally POSIX-only here.  On Windows the
    documented ``os.kill`` implementation is signal/control-process based,
    so use the read-only Win32 process query APIs instead.
    """
    try:
        import ctypes
        from ctypes import wintypes

        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            return False
        kernel32 = win_dll("kernel32", use_last_error=True)
        process_query_limited_information = 0x1000
        still_active = 259

        open_process = kernel32.OpenProcess
        open_process.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = open_process(
            process_query_limited_information,
            False,
            wintypes.DWORD(pid),
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            close_handle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


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
            "engine_backend": str(data.get("engine_backend") or "") if live else "",
            "engine_version": str(data.get("engine_version") or "") if live else "",
            "engine_identity": str(data.get("engine_identity") or "") if live else "",
            "model_identity": str(data.get("model_identity") or "") if live else "",
            "precision": str(data.get("precision") or "") if live else "",
            "device": str(data.get("device") or "") if live else "",
            "cache_identity": str(data.get("cache_identity") or "") if live else "",
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
        self._engine_profile: dict[str, Any] = {}

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
                "engine_backend": self._engine_profile.get("engine_backend", ""),
                "engine_version": self._engine_profile.get("engine_version", ""),
                "engine_identity": self._engine_profile.get("engine_identity", ""),
                "model_identity": self._engine_profile.get("model_identity", ""),
                "precision": self._engine_profile.get("precision", ""),
                "device": self._engine_profile.get("device", ""),
                "cache_identity": self._engine_profile.get("cache_identity", ""),
            }

    def reset(self) -> None:
        """Begin a fresh engine lifecycle (new serve cycle)."""
        with self._condition:
            self._state = "uninitialized"
            self._error_summary = ""
            self._last_error_code = ""
            self._engine_profile = {}
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

    def ensure_ready(
        self,
        profile: dict[str, Any] | None = None,
        *,
        progress_cb: Any = None,
    ) -> None:
        """Initialize the TTS engine exactly once per lifecycle.

        - ``ready``: returns immediately (engine reused, no reload).
        - ``error``: raises ``EngineInitError`` immediately (fail-fast,
          no repeated GPU attempts for the remaining requests).
        - otherwise: this caller performs the single initialization while
          holding the lifecycle lock; every concurrent caller blocks on the
          same lock and sees the result when it completes.

        ``progress_cb(event, **fields)`` is an optional structured phase
        reporter (no-op by default).  It is invoked at every observable
        engine transition (``profile_match`` / ``engine_recycle_start`` /
        ``engine_reset_done`` / ``engine_init_start`` / ``engine_init_done``)
        with path-free identity fields so a task/UI can render "正在加载
        IndexTTS 2.5…" instead of appearing hung during a multi-minute
        model (re)load.
        """
        with self._condition:
            from lib.tts_profile import profile_matches, public_profile, resolve_profile
            desired = resolve_profile(profile or {})
            target_fields = {
                "engine_version": desired.get("engine_version", ""),
                "engine_identity": desired.get("engine_identity", ""),
                "precision": desired.get("precision", ""),
            }
            if self._state == "ready":
                if profile_matches(self._engine_profile, desired):
                    if progress_cb is not None:
                        progress_cb(
                            "profile_match",
                            engine_generation=self._generation,
                            **target_fields,
                        )
                    return
                # The runtime is serial: a new task can only reach here after
                # the previous task retired.  Recycle the actual adapter before
                # loading the newly frozen identity.
                self._state = "recovering"
                self._publish_unlocked("recovering", error_summary="")
                if progress_cb is not None:
                    progress_cb(
                        "engine_recycle_start",
                        engine_generation=self._generation,
                        **target_fields,
                    )
                recycle_started = time.monotonic()
                try:
                    from lib import tts_engine

                    tts_engine.reset_engine()
                    if progress_cb is not None:
                        progress_cb(
                            "engine_reset_done",
                            engine_generation=self._generation,
                            elapsed_ms=int((time.monotonic() - recycle_started) * 1000),
                        )
                    if progress_cb is not None:
                        progress_cb(
                            "engine_init_start",
                            engine_generation=self._generation,
                            **target_fields,
                        )
                    self._init_adapter(tts_engine, desired)
                    self._engine_profile = dict(tts_engine.get_engine_profile() or desired)
                    self._engine_profile.update(public_profile(self._engine_profile))
                except Exception as exc:
                    summary = sanitize_public_error(exc)
                    self._error_summary = summary
                    self._state = "error"
                    self._last_error_code = "TTS_ENGINE_INIT_FAILED"
                    self._publish_unlocked("error", error_summary=summary)
                    if progress_cb is not None:
                        progress_cb(
                            "engine_init_failed",
                            engine_generation=self._generation,
                            error_summary=summary,
                            elapsed_ms=int((time.monotonic() - recycle_started) * 1000),
                        )
                    raise EngineInitError(summary, original_exception=exc) from exc
                self._generation += 1
                self._recovery_count += 1
                self._last_recovery_at = _now()
                self._state = "ready"
                self._publish_unlocked("ready", error_summary="")
                if progress_cb is not None:
                    progress_cb(
                        "engine_init_done",
                        engine_generation=self._generation,
                        elapsed_ms=int((time.monotonic() - recycle_started) * 1000),
                        **target_fields,
                    )
                return
            if self._state == "error":
                raise EngineInitError(self._error_summary)
            self._state = "loading"
            self._publish_unlocked("loading", error_summary="")
            if progress_cb is not None:
                progress_cb(
                    "engine_init_start",
                    engine_generation=self._generation,
                    **target_fields,
                )
            load_started = time.monotonic()
            try:
                from lib import tts_engine

                self._init_adapter(tts_engine, desired)
                self._engine_profile = dict(tts_engine.get_engine_profile() or desired)
                self._engine_profile.update(public_profile(self._engine_profile))
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
                if progress_cb is not None:
                    progress_cb(
                        "engine_init_failed",
                        engine_generation=self._generation,
                        error_summary=summary,
                        elapsed_ms=int((time.monotonic() - load_started) * 1000),
                    )
                raise EngineInitError(
                    summary,
                    original_exception=exc,
                ) from exc
            self._generation += 1
            self._state = "ready"
            self._publish_unlocked("ready", error_summary="")
            if progress_cb is not None:
                progress_cb(
                    "engine_init_done",
                    engine_generation=self._generation,
                    elapsed_ms=int((time.monotonic() - load_started) * 1000),
                    **target_fields,
                )
            logger.info(
                "runtime_event=engine_init_success pid=%s owner=%s",
                self._pid,
                self._owner_id,
            )

    @staticmethod
    def _init_adapter(tts_engine, profile: dict[str, Any]) -> None:
        """Call the profile-aware adapter while retaining old test stubs."""
        try:
            tts_engine.init_engine(profile=profile)
        except TypeError as exc:
            if "unexpected keyword" not in str(exc) and "positional argument" not in str(exc):
                raise
            tts_engine.init_engine()

    def recycle(
        self,
        profile: dict[str, Any] | None = None,
        *,
        progress_cb: Any = None,
    ) -> int:
        """Detach the real engine, reload it, and advance the generation.

        Calls ``lib.tts_engine.reset_engine()`` (actual ``_tts`` detach under
        ``_ENGINE_LOCK``, cache clearing, ``gc``, guarded CUDA cache flush)
        followed by a fresh ``init_engine()``.  On success the generation
        increments and the state returns to ``ready``; on failure the state
        becomes ``error`` and ``EngineInitError`` is raised so the caller
        can fail the task fast.

        ``progress_cb`` mirrors ``ensure_ready``'s structured phase reporter.
        """
        with self._condition:
            if self._state not in {"ready", "uninitialized", "error"}:
                raise EngineInitError("引擎当前状态不能执行 recycle")
            self._state = "recovering"
            self._publish_unlocked("recovering", error_summary="")
            recycle_started = time.monotonic()
            try:
                from lib import tts_engine

                tts_engine.reset_engine()
                if progress_cb is not None:
                    progress_cb(
                        "engine_reset_done",
                        engine_generation=self._generation,
                        elapsed_ms=int((time.monotonic() - recycle_started) * 1000),
                    )
                from lib.tts_profile import public_profile, resolve_profile

                desired = resolve_profile(profile or self._engine_profile or {})
                if progress_cb is not None:
                    progress_cb(
                        "engine_init_start",
                        engine_generation=self._generation,
                        engine_version=desired.get("engine_version", ""),
                        engine_identity=desired.get("engine_identity", ""),
                        precision=desired.get("precision", ""),
                    )
                self._init_adapter(tts_engine, desired)
                self._engine_profile = dict(tts_engine.get_engine_profile() or desired)
                self._engine_profile.update(public_profile(self._engine_profile))
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
                if progress_cb is not None:
                    progress_cb(
                        "engine_init_failed",
                        engine_generation=self._generation,
                        error_summary=summary,
                        elapsed_ms=int((time.monotonic() - recycle_started) * 1000),
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
            if progress_cb is not None:
                progress_cb(
                    "engine_init_done",
                    engine_generation=self._generation,
                    elapsed_ms=int((time.monotonic() - recycle_started) * 1000),
                    engine_version=desired.get("engine_version", ""),
                    engine_identity=desired.get("engine_identity", ""),
                    precision=desired.get("precision", ""),
                )
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
            self._engine_profile = {}
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
                "engine_backend": self._engine_profile.get("engine_backend", ""),
                "engine_version": self._engine_profile.get("engine_version", ""),
                "engine_identity": self._engine_profile.get("engine_identity", ""),
                "model_identity": self._engine_profile.get("model_identity", ""),
                "precision": self._engine_profile.get("precision", ""),
                "device": self._engine_profile.get("device", ""),
                "cache_identity": self._engine_profile.get("cache_identity", ""),
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
