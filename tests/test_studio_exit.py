"""UI exit control tests; shutdown itself is always mocked."""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest


@pytest.fixture
def app_module(monkeypatch):
    import app

    monkeypatch.setattr(app, "_studio_exit_scheduled", False)
    return app


def test_idle_exit_uses_normal_confirmation(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.ProductionJobService,
        "list_tasks",
        staticmethod(lambda **_kwargs: [{"status": "done"}]),
    )

    prompt, confirmation, exit_button = app_module.prepare_studio_exit_confirmation()

    assert "退出后后台服务将停止" in prompt
    assert "当前仍有合成任务正在运行" not in prompt
    assert confirmation["visible"] is True
    assert exit_button["visible"] is False


def test_active_production_uses_strong_confirmation(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.ProductionJobService,
        "list_tasks",
        staticmethod(lambda **_kwargs: [{"status": "running"}]),
    )

    prompt, _confirmation, _exit_button = app_module.prepare_studio_exit_confirmation()

    assert "当前仍有合成任务正在运行" in prompt
    assert "当前运行中的任务也将终止" in prompt


def test_cancel_only_hides_confirmation(app_module):
    prompt, confirmation, exit_button = app_module.cancel_studio_exit_confirmation()

    assert prompt == ""
    assert confirmation["visible"] is False
    assert exit_button["visible"] is True
    assert exit_button["interactive"] is True


def test_confirm_schedules_existing_lifecycle_after_callback_returns(app_module, monkeypatch):
    class FakeLifecycle:
        def __init__(self):
            self.calls = []

        def is_shutting_down(self):
            return False

        def request_application_shutdown(self, reason):
            self.calls.append(reason)

    class FakeTimer:
        created: ClassVar[list] = []

        def __init__(self, delay, target):
            self.delay = delay
            self.target = target
            self.daemon = False
            self.started = False
            self.created.append(self)

        def start(self):
            self.started = True

    class FakeGradioApp:
        def __init__(self):
            self.close_calls = []

        def close(self, **kwargs):
            self.close_calls.append(kwargs)

    lifecycle = FakeLifecycle()
    gradio_app = FakeGradioApp()
    monkeypatch.setattr(app_module, "get_application_lifecycle", lambda: lifecycle)
    monkeypatch.setattr(app_module, "app", gradio_app)
    monkeypatch.setattr(app_module.threading, "Timer", FakeTimer)

    prompt, confirmation, confirm, cancel, exit_button = app_module.confirm_studio_exit()

    assert "正在退出" in prompt
    assert confirmation["visible"] is True
    assert confirm["visible"] is False
    assert cancel["visible"] is False
    assert exit_button["visible"] is False
    assert lifecycle.calls == []
    assert len(FakeTimer.created) == 1
    assert FakeTimer.created[0].delay == 0.1
    assert FakeTimer.created[0].started is True

    # The actual shutdown is deferred until after the callback has returned.
    FakeTimer.created[0].target()
    assert lifecycle.calls == ["ui_exit"]
    assert gradio_app.close_calls == [{"verbose": False}]


def test_repeated_confirm_schedules_one_shutdown(app_module, monkeypatch):
    class FakeLifecycle:
        def is_shutting_down(self):
            return False

        def request_application_shutdown(self, _reason):
            raise AssertionError("timer target is not run in this test")

    class FakeTimer:
        count = 0

        def __init__(self, _delay, _target):
            pass

        def start(self):
            FakeTimer.count += 1

    monkeypatch.setattr(app_module, "get_application_lifecycle", lambda: FakeLifecycle())
    monkeypatch.setattr(app_module.threading, "Timer", FakeTimer)

    app_module.confirm_studio_exit()
    app_module.confirm_studio_exit()

    assert FakeTimer.count == 1


def test_exit_wiring_is_global_and_browser_close_never_stops_app(app_module):
    app_path = Path(app_module.__file__)
    source = app_path.read_text(encoding="utf-8")
    shared = (app_path.parent / "ui" / "shared.py").read_text(encoding="utf-8")

    assert 'elem_id="studio-exit"' in shared
    assert "studio_exit.click(" in source
    assert "studio_exit_confirm.click(" in source
    assert "beforeunload" not in source
    assert "visibilitychange" not in source
    assert "websocket disconnected" not in source
