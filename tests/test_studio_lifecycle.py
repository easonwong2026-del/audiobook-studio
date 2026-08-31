"""Launcher-owned Studio lifecycle tests; no real process is terminated."""

from __future__ import annotations

import json
import os
import sys

import pytest

import launcher
from lib import studio_lifecycle as lifecycle


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    path = tmp_path / "instance.json"
    monkeypatch.setattr(lifecycle, "instance_state_path", lambda: str(path))
    saved_cwd = os.getcwd()
    yield path
    os.chdir(saved_cwd)


def _state(pid: int = 1234) -> dict[str, object]:
    return {
        "pid": pid,
        "started_at": "2026-08-28T12:00:00Z",
        "port": lifecycle.STUDIO_PORT,
        "instance_id": "instance-test-123",
        "repo_path": os.path.abspath(launcher.BASE_DIR),
        "app_path": os.path.abspath(launcher.APP_PATH),
        "python_path": sys.executable,
    }


def _patch_running(monkeypatch, state, command_line=None):
    pid = int(state["pid"])
    marker = f"{lifecycle.INSTANCE_ID_ARGUMENT}{state['instance_id']}"
    command_line = command_line or f'"{sys.executable}" "{launcher.APP_PATH}" {marker}'
    monkeypatch.setattr(lifecycle, "pid_is_alive", lambda value: value == pid)
    monkeypatch.setattr(
        lifecycle,
        "read_process_info",
        lambda value: lifecycle.ProcessInfo(value, command_line=command_line),
    )


def test_start_lock_serializes_launchers(isolated_state):
    first = lifecycle.acquire_start_lock()
    assert first is not None
    try:
        assert lifecycle.acquire_start_lock() is None
    finally:
        first.release()
    released = lifecycle.acquire_start_lock()
    assert released is not None
    released.release()


def test_status_without_state(isolated_state, capsys):
    assert launcher.main(["--status"]) == 0
    assert capsys.readouterr().out.strip() == "Audiobook Studio：未运行"


def test_status_reports_valid_running_studio(isolated_state, monkeypatch, capsys):
    state = _state()
    lifecycle.write_instance_state(state)
    _patch_running(monkeypatch, state)

    assert launcher.main(["--status"]) == 0
    output = capsys.readouterr().out
    assert "Audiobook Studio：运行中" in output
    assert "PID：1234" in output
    assert lifecycle.STUDIO_URL in output


def test_status_cleans_state_when_pid_is_stale(isolated_state, monkeypatch, capsys):
    state = _state()
    lifecycle.write_instance_state(state)
    monkeypatch.setattr(lifecycle, "pid_is_alive", lambda _pid: False)

    assert launcher.main(["--status"]) == 0
    assert "Audiobook Studio：未运行" in capsys.readouterr().out
    assert not isolated_state.exists()


def test_status_detects_pid_reuse_without_killing_foreign_process(
    isolated_state, monkeypatch, capsys
):
    state = _state()
    lifecycle.write_instance_state(state)
    _patch_running(monkeypatch, state, command_line=f"{sys.executable} unrelated.py")

    assert launcher.main(["--status"]) == 0
    output = capsys.readouterr().out
    assert "Audiobook Studio：未运行" in output
    assert not isolated_state.exists()


def test_start_refuses_second_instance(isolated_state, monkeypatch, capsys):
    state = _state()
    lifecycle.write_instance_state(state)
    _patch_running(monkeypatch, state)
    monkeypatch.setattr(
        launcher,
        "_resolve_python",
        lambda: pytest.fail("_resolve_python must not run for a duplicate start"),
    )

    assert launcher.main([]) == 0
    output = capsys.readouterr().out
    assert "Audiobook Studio 已在运行" in output
    assert "python launcher.py --stop" in output


def test_start_refuses_foreign_port_without_a_kill(isolated_state, monkeypatch, capsys):
    monkeypatch.setattr(lifecycle, "port_is_in_use", lambda: True)
    monkeypatch.setattr(
        launcher,
        "_resolve_python",
        lambda: pytest.fail("_resolve_python must not run for a foreign port"),
    )

    assert launcher.main([]) == 1
    output = capsys.readouterr().out
    assert "端口 7862 已被其他程序占用" in output
    assert "Audiobook Studio 未启动" in output


def test_stop_requests_graceful_shutdown_for_valid_studio(
    isolated_state, monkeypatch, capsys
):
    state = _state()
    lifecycle.write_instance_state(state)
    _patch_running(monkeypatch, state)
    graceful = []
    monkeypatch.setattr(lifecycle, "send_graceful_shutdown", lambda pid: graceful.append(pid) or True)
    monkeypatch.setattr(lifecycle, "wait_for_pid_exit", lambda _pid, timeout: True)
    monkeypatch.setattr(
        lifecycle,
        "terminate_confirmed_process",
        lambda _pid: pytest.fail("forced termination is not needed"),
    )

    assert launcher.main(["--stop"]) == 0
    assert graceful == [1234]
    assert "Audiobook Studio：已停止" in capsys.readouterr().out
    assert not isolated_state.exists()


def test_stop_without_state_is_normal(isolated_state, capsys):
    assert launcher.main(["--stop"]) == 0
    assert capsys.readouterr().out.strip() == "Audiobook Studio：未运行"


def test_stop_cleans_stale_state_without_signalling(isolated_state, monkeypatch, capsys):
    state = _state()
    lifecycle.write_instance_state(state)
    monkeypatch.setattr(lifecycle, "pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(
        lifecycle,
        "send_graceful_shutdown",
        lambda _pid: pytest.fail("stale PID must not be signalled"),
    )

    assert launcher.main(["--stop"]) == 0
    assert "已清理过期状态" in capsys.readouterr().out
    assert not isolated_state.exists()


def test_stop_never_terminates_pid_reused_by_foreign_process(
    isolated_state, monkeypatch, capsys
):
    state = _state()
    lifecycle.write_instance_state(state)
    _patch_running(monkeypatch, state, command_line=f"{sys.executable} unrelated.py")
    monkeypatch.setattr(
        lifecycle,
        "send_graceful_shutdown",
        lambda _pid: pytest.fail("foreign PID must not receive a signal"),
    )
    monkeypatch.setattr(
        lifecycle,
        "terminate_confirmed_process",
        lambda _pid: pytest.fail("foreign PID must never be terminated"),
    )

    assert launcher.main(["--stop"]) == 0
    output = capsys.readouterr().out
    assert "未发送终止信号" in output
    assert not isolated_state.exists()


def test_stale_state_is_recovered_before_start(isolated_state, monkeypatch):
    stale = _state()
    lifecycle.write_instance_state(stale)
    monkeypatch.setattr(lifecycle, "pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(lifecycle, "port_is_in_use", lambda: False)
    monkeypatch.setattr(launcher, "_resolve_python", lambda: sys.executable)
    monkeypatch.setattr(launcher, "_check_runtime_dependencies", lambda _python: True)
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    class FakeProcess:
        pid = 5678

        def wait(self):
            return 0

        def poll(self):
            return 0

    calls = []
    monkeypatch.setattr(
        launcher,
        "_start_app",
        lambda python, instance_id: calls.append((python, instance_id)) or FakeProcess(),
    )

    assert launcher.main([]) == 0
    assert calls == [(sys.executable, calls[0][1])]
    assert calls[0][1]
    assert not isolated_state.exists()


def test_stop_fallback_rechecks_identity_before_termination(
    isolated_state, monkeypatch, capsys
):
    state = _state()
    lifecycle.write_instance_state(state)
    _patch_running(monkeypatch, state)
    monkeypatch.setattr(lifecycle, "send_graceful_shutdown", lambda _pid: True)
    waits = iter([False, True])
    monkeypatch.setattr(lifecycle, "wait_for_pid_exit", lambda _pid, timeout: next(waits))
    forced = []
    monkeypatch.setattr(
        lifecycle,
        "terminate_confirmed_process",
        lambda pid: forced.append(pid) or True,
    )

    assert launcher.main(["--stop"]) == 0
    assert forced == [1234]
    assert "Audiobook Studio：已停止" in capsys.readouterr().out


def test_stop_keeps_state_when_process_identity_cannot_be_read(
    isolated_state, monkeypatch, capsys
):
    state = _state()
    lifecycle.write_instance_state(state)
    monkeypatch.setattr(lifecycle, "pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(lifecycle, "read_process_info", lambda _pid: None)
    monkeypatch.setattr(
        lifecycle,
        "send_graceful_shutdown",
        lambda _pid: pytest.fail("unknown identity must not be signalled"),
    )

    assert launcher.main(["--stop"]) == 1
    assert "未发送终止信号" in capsys.readouterr().out
    assert isolated_state.exists()


def test_windows_process_probe_reads_command_line_marker(monkeypatch):
    calls = []
    payload = {
        "CommandLine": "python app.py --studio-instance-id=marker",
        "ExecutablePath": r"C:\Python\python.exe",
    }
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: "powershell.exe")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload)})()

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)
    info = lifecycle._windows_process_info(321)

    assert info is not None
    assert info.command_line == payload["CommandLine"]
    assert "ProcessId = 321" in calls[0][0][-1]


def test_windows_start_uses_a_new_process_group(monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 99

    monkeypatch.setattr(launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(launcher.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or FakeProcess(),
    )

    launcher._start_app(sys.executable, "marker")
    assert captured["kwargs"]["creationflags"] == 0x200
    assert captured["command"][-1] == f"{lifecycle.INSTANCE_ID_ARGUMENT}marker"


def test_windows_graceful_shutdown_uses_attachconsole_delivery(monkeypatch):
    monkeypatch.setattr(lifecycle, "_is_windows", lambda: True)
    monkeypatch.setattr(
        lifecycle.os,
        "kill",
        lambda *_args: pytest.fail(
            "os.kill must not be used for Windows graceful shutdown"
        ),
    )
    sent = []
    monkeypatch.setattr(
        lifecycle, "_windows_send_ctrl_break", lambda pid: sent.append(pid) or True
    )

    assert lifecycle.send_graceful_shutdown(321) is True
    assert sent == [321]


def test_windows_graceful_shutdown_fallback_failure_reaches_hard_kill_ladder(
    monkeypatch,
):
    monkeypatch.setattr(lifecycle, "_is_windows", lambda: True)
    monkeypatch.setattr(lifecycle, "_windows_send_ctrl_break", lambda _pid: False)

    assert lifecycle.send_graceful_shutdown(321) is False


def test_windows_graceful_shutdown_never_lets_kill_systemerror_escape(monkeypatch):
    """Regression: CPython 3.11 reports a failed GenerateConsoleCtrlEvent as
    SystemError (WinError 87 as __cause__), which escaped the OSError guards
    and crashed ``launcher.py --stop``.  Whatever primitive the Windows path
    uses, that exception class must never escape this helper."""

    monkeypatch.setattr(lifecycle, "_is_windows", lambda: True)

    def broken_kill(_pid, _event):
        cause = OSError(87, "The parameter is incorrect")
        error = SystemError(
            "<built-in function kill> returned a result with an exception set"
        )
        error.__cause__ = cause
        raise error

    monkeypatch.setattr(lifecycle.os, "kill", broken_kill)
    monkeypatch.setattr(lifecycle, "_windows_send_ctrl_break", lambda _pid: False)

    assert lifecycle.send_graceful_shutdown(321) is False


def test_posix_graceful_shutdown_sends_sigterm(monkeypatch):
    monkeypatch.setattr(lifecycle, "_is_windows", lambda: False)
    sent = []
    monkeypatch.setattr(
        lifecycle.os, "kill", lambda pid, sig: sent.append((pid, sig))
    )

    assert lifecycle.send_graceful_shutdown(321) is True
    assert sent == [(321, lifecycle.signal.SIGTERM)]


def test_posix_graceful_shutdown_error_returns_false(monkeypatch):
    monkeypatch.setattr(lifecycle, "_is_windows", lambda: False)

    def lookup_failed(_pid, _sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(lifecycle.os, "kill", lookup_failed)

    assert lifecycle.send_graceful_shutdown(321) is False
