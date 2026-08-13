"""Application shutdown coordination contracts."""
from __future__ import annotations

import threading

import pytest

from services.application_lifecycle import (
    ApplicationLifecycleError,
    ApplicationLifecycleService,
)
from services.production_runtime import ProductionRuntimeClient
from services.synthesis import SynthesisService


def test_shutdown_is_single_flight_and_rejects_new_work(monkeypatch):
    lifecycle = ApplicationLifecycleService()
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def fake_request_shutdown(cls, *, reason, timeout):
        calls.append(f"runtime:{reason}:{timeout}")
        entered.set()
        release.wait(2.0)
        return True

    monkeypatch.setattr(
        ProductionRuntimeClient,
        "request_shutdown",
        classmethod(fake_request_shutdown),
    )
    monkeypatch.setattr(
        SynthesisService,
        "reset_executor",
        staticmethod(lambda: calls.append("executor")),
    )

    closed: list[str] = []
    lifecycle.bind_app(type("App", (), {"close": lambda _self: closed.append("app")})())

    assert lifecycle.request_shutdown(reason="test") is True
    assert entered.wait(1.0)
    assert lifecycle.state == "shutting_down"
    with pytest.raises(ApplicationLifecycleError):
        lifecycle.ensure_accepting_tasks()
    assert lifecycle.request_shutdown(reason="duplicate") is False

    release.set()
    worker = lifecycle._worker
    assert worker is not None
    worker.join(timeout=2.0)
    assert lifecycle.state == "stopped"
    assert calls == ["runtime:test:30.0", "executor"]
    assert closed == ["app"]
