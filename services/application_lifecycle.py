"""Application lifecycle coordinator (graceful shutdown owner).

Single-flight orchestrator that owns the "shut the application down" sequence.
Every exit trigger — ``atexit``, ``SIGINT``/``SIGTERM``/``SIGBREAK`` handlers, and the Gradio
server-close ``finally`` block — funnels into ``request_application_shutdown``
so there is exactly one shutdown path.  The coordinator then asks the detached
production runtime to stop gracefully and waits for it, avoiding the orphan
Runtime process that earlier survived app exit.
"""
from __future__ import annotations

import atexit
import logging
import signal
import threading
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)


class ApplicationLifecycleService:
    """Single-flight application shutdown coordinator.

    States: ``running`` → ``shutting_down`` → ``stopped``.
    Repeated / concurrent ``request_application_shutdown`` calls are safe:
    only the first one runs the sequence; later calls return ``False``.
    """

    def __init__(self) -> None:
        self._state = "running"
        self._reason: Optional[str] = None
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[str], None]] = []
        self._hooks_installed = False
        self._prev_signal_handlers: dict[int, Any] = {}

    @property
    def state(self) -> str:
        return self._state

    def is_shutting_down(self) -> bool:
        """True once shutdown has begun (callers may refuse new work)."""
        with self._lock:
            return self._state != "running"

    def register_shutdown_callback(self, callback: Callable[[str], None]) -> None:
        self._callbacks.append(callback)

    # ── exit triggers ──────────────────────────────────────────────────────

    def install_process_exit_hooks(self, signums: Optional[Sequence[int]] = None) -> None:
        """Wire every process-exit edge into the single shutdown path.

        Reliability order (the caller additionally wraps the server ``launch``
        in ``try/finally``, which is the most reliable edge of all):
          1. server-close ``finally``  → ``request_application_shutdown``
          2. ``SIGINT`` / ``SIGTERM``  → chained handler, ours runs first
          3. ``atexit``                → final fallback only

        Because ``request_application_shutdown`` is single-flight, all three
        firing in one exit still performs exactly one runtime shutdown.
        Installing twice is a no-op, and the previous signal handlers are saved
        and chained so Gradio's own ``Ctrl+C`` handling keeps working.
        """
        with self._lock:
            if self._hooks_installed:
                return
            self._hooks_installed = True

        candidates: list[Any]
        if signums is None:
            candidates = [
                getattr(signal, "SIGINT", None),
                getattr(signal, "SIGTERM", None),
                getattr(signal, "SIGBREAK", None),
            ]
        else:
            candidates = list(signums)

        for signum in candidates:
            if signum is None:
                continue
            try:
                self._prev_signal_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._make_signal_handler(signum))
            except (ValueError, OSError, RuntimeError, AttributeError):
                # Signal handlers can only be installed from the main thread and
                # only for signals the platform supports; never break startup.
                self._prev_signal_handlers.pop(signum, None)

        atexit.register(self._atexit_hook)

    def _make_signal_handler(self, signum: int) -> Callable[[int, Any], None]:
        def _handler(received: int, frame: Any) -> None:
            # Ours first: stopping the detached runtime is the whole point, and
            # a chained handler may raise (KeyboardInterrupt) and skip us.
            self._safe_shutdown(f"signal_{int(received)}")
            previous = self._prev_signal_handlers.get(signum)
            if callable(previous):
                previous(received, frame)
                return
            if previous == getattr(signal, "SIG_IGN", object()):
                return
            # No prior Python-level handler: preserve conventional behaviour so
            # the ``finally`` / ``atexit`` chain still runs.
            if signum == getattr(signal, "SIGINT", None):
                raise KeyboardInterrupt
            raise SystemExit(128 + int(received))

        return _handler

    def _atexit_hook(self) -> None:
        # Final fallback: normally the server-close ``finally`` already ran and
        # this is a cheap no-op thanks to single-flight.
        self._safe_shutdown("application_exit")

    def _safe_shutdown(self, reason: str) -> bool:
        try:
            return self.request_application_shutdown(reason=reason)
        except Exception:  # pragma: no cover - an exit hook must never raise
            logger.exception("runtime_event=app_shutdown_hook_failed reason=%s", reason)
            return False

    def request_application_shutdown(self, reason: str = "application_shutdown") -> bool:
        """Begin (and own) the application shutdown sequence.

        Idempotent: returns ``False`` if shutdown already started or finished.
        """
        with self._lock:
            if self._state != "running":
                return False
            self._state = "shutting_down"
            self._reason = reason
        logger.info("runtime_event=app_shutdown_begin reason=%s", reason)

        try:
            from .production_runtime import ProductionRuntimeClient

            ProductionRuntimeClient.request_shutdown(reason=reason, timeout=30.0)
        except Exception:  # pragma: no cover - shutdown must never raise
            logger.exception("runtime_event=runtime_shutdown_failed reason=%s", reason)

        # Release any application-level resources registered by the host.
        for callback in self._callbacks:
            try:
                callback(reason)
            except Exception:  # pragma: no cover - defensive
                logger.exception("应用关闭回调异常")

        with self._lock:
            self._state = "stopped"
        logger.info("runtime_event=app_shutdown_complete reason=%s", reason)
        return True


_lifecycle: Optional[ApplicationLifecycleService] = None
_lifecycle_lock = threading.Lock()


def get_application_lifecycle() -> ApplicationLifecycleService:
    """Process-wide singleton coordinator."""
    global _lifecycle
    with _lifecycle_lock:
        if _lifecycle is None:
            _lifecycle = ApplicationLifecycleService()
        return _lifecycle


def reset_application_lifecycle() -> None:
    """Drop the singleton and undo its exit hooks (test isolation)."""
    global _lifecycle
    with _lifecycle_lock:
        previous = _lifecycle
        _lifecycle = None
    if previous is None or not previous._hooks_installed:
        return
    try:
        atexit.unregister(previous._atexit_hook)
    except Exception:  # pragma: no cover - defensive
        pass
    for signum, handler in list(previous._prev_signal_handlers.items()):
        try:
            signal.signal(signum, handler)
        except (ValueError, OSError, RuntimeError, TypeError):
            pass
    previous._prev_signal_handlers.clear()
    previous._hooks_installed = False


__all__ = [
    "ApplicationLifecycleService",
    "get_application_lifecycle",
    "reset_application_lifecycle",
]
