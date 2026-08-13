"""Application-wide graceful shutdown coordination."""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class ApplicationLifecycleError(RuntimeError):
    """Raised when a new task is submitted after shutdown has started."""

    code = "APPLICATION_SHUTTING_DOWN"

    def __init__(self) -> None:
        super().__init__("Audiobook Studio 正在关闭，不能创建新任务")


class ApplicationLifecycleService:
    """Small single-flight coordinator for Web UI and the singleton Runtime."""

    STATES = ("running", "shutting_down", "stopped")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = "running"
        self._app: Any = None
        self._worker: threading.Thread | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def bind_app(self, app: Any) -> None:
        with self._lock:
            self._app = app

    def ensure_accepting_tasks(self) -> None:
        if self.state != "running":
            raise ApplicationLifecycleError()

    def request_shutdown(self, *, reason: str = "web_ui_shutdown") -> bool:
        """Start one background shutdown sequence and return immediately."""
        with self._lock:
            if self._state == "stopped":
                return False
            if self._state == "shutting_down":
                return False
            self._state = "shutting_down"
            worker = threading.Thread(
                target=self._shutdown_worker,
                args=(str(reason or "web_ui_shutdown"),),
                name="audiobook-application-shutdown",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return True

    def _shutdown_worker(self, reason: str) -> None:
        try:
            # Let the Gradio callback deliver its confirmation response before
            # the background worker closes the Blocks/server.
            threading.Event().wait(0.05)
            from .production_runtime import ProductionRuntimeClient

            stopped = ProductionRuntimeClient.request_shutdown(
                reason=reason,
                timeout=30.0,
            )
            if not stopped:
                logger.error("application shutdown timed out while stopping Production Runtime")
                with self._lock:
                    if self._state == "shutting_down":
                        self._state = "running"
                return
            # The Runtime has released its engine and singleton ownership.  The
            # process-local pool is now idle and can be discarded as part of
            # closing the Web application.
            from .synthesis import SynthesisService

            SynthesisService.reset_executor()
            with self._lock:
                self._state = "stopped"
                app = self._app
            close = getattr(app, "close", None)
            if callable(close):
                close()
        except Exception:
            logger.exception("application graceful shutdown failed")
            with self._lock:
                if self._state == "shutting_down":
                    self._state = "running"

    def reset_for_tests(self) -> None:
        """Restore the initial state for isolated service tests."""
        worker = self._worker
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        with self._lock:
            if self._state == "shutting_down":
                return
            self._state = "running"
            self._worker = None


_APPLICATION_LIFECYCLE = ApplicationLifecycleService()


def get_application_lifecycle() -> ApplicationLifecycleService:
    return _APPLICATION_LIFECYCLE


__all__ = [
    "ApplicationLifecycleError",
    "ApplicationLifecycleService",
    "get_application_lifecycle",
]
