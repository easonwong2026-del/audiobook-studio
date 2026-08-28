"""Small, local-only lifecycle helpers for the Audiobook Studio launcher.

The launcher owns the Studio UI process.  This module deliberately does not
inspect or manage arbitrary processes: a PID is actionable only when its
command line contains both the expected ``app.py`` path and the per-start
instance marker recorded in the state file.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, BinaryIO


STUDIO_PORT = 7862
STUDIO_URL = f"http://localhost:{STUDIO_PORT}"
INSTANCE_ID_ARGUMENT = "--studio-instance-id="
INSTANCE_ID_ENVIRONMENT = "AUDIOBOOK_STUDIO_INSTANCE_ID"


def _is_windows() -> bool:
    """Platform probe kept separate so tests can exercise Windows branches."""
    return os.name == "nt"


def instance_state_path() -> str:
    """Return the temp-only state path; project/user data stays untouched."""
    return os.path.join(tempfile.gettempdir(), "audiobook-studio", "instance.json")


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    executable: str = ""
    command_line: str = ""
    cwd: str = ""


@dataclass(frozen=True)
class InstanceCheck:
    status: str
    state: dict[str, Any] | None = None
    process: ProcessInfo | None = None
    reason: str = ""


class _StartLock:
    """One-process launcher lock; the OS releases it if the launcher dies."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> bool:
        if self._file is not None:
            return True
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - lock must keep the handle open
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if _is_windows():
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        try:
            handle.seek(0)
            if _is_windows():
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._file = None
            handle.close()


def acquire_start_lock() -> _StartLock | None:
    lock = _StartLock(instance_state_path() + ".lock")
    return lock if lock.acquire() else None


def load_instance_state() -> dict[str, Any] | None:
    try:
        with open(instance_state_path(), encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_instance_state(state: Mapping[str, Any]) -> None:
    path = instance_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Reuse the repository's existing atomic JSON writer; importing it lazily
    # keeps launcher startup independent of the service layer.
    from repositories._atomic import atomic_write

    atomic_write(path, dict(state))


def remove_instance_state(expected: Mapping[str, Any] | None = None) -> bool:
    if expected is not None:
        current = load_instance_state()
        if not current or not _same_instance(current, expected):
            return False
    try:
        os.remove(instance_state_path())
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def _same_instance(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (
            str(left.get("instance_id") or "") == str(right.get("instance_id") or "")
            and int(left.get("pid") or 0) == int(right.get("pid") or 0)
        )
    except (TypeError, ValueError):
        return False


def _normalise_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(value))).replace("\\", "/").casefold()


def _normalise_command(value: str) -> str:
    return str(value or "").replace("\\", "/").casefold()


def _valid_state(state: Mapping[str, Any]) -> bool:
    try:
        pid = int(state.get("pid") or 0)
        port = int(state.get("port") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        pid > 0
        and port == STUDIO_PORT
        and str(state.get("instance_id") or "")
        and str(state.get("started_at") or "")
        and str(state.get("repo_path") or "")
        and str(state.get("app_path") or "")
    )


def check_instance(state: Mapping[str, Any] | None, repo_path: str, app_path: str) -> InstanceCheck:
    """Classify a state record without ever sending a process signal."""
    if state is None:
        return InstanceCheck("absent")
    state_dict = dict(state)
    if not _valid_state(state_dict):
        return InstanceCheck("stale", state=state_dict, reason="invalid_state")

    expected_repo = _normalise_path(repo_path)
    if _normalise_path(str(state_dict.get("repo_path") or "")) != expected_repo:
        return InstanceCheck("foreign", state=state_dict, reason="different_repo")
    if _normalise_path(str(state_dict.get("app_path") or "")) != _normalise_path(app_path):
        return InstanceCheck("stale", state=state_dict, reason="different_app")

    pid = int(state_dict["pid"])
    if not pid_is_alive(pid):
        return InstanceCheck("stale", state=state_dict, reason="pid_not_alive")
    process = read_process_info(pid)
    if process is None:
        return InstanceCheck("unknown", state=state_dict, reason="process_info_unavailable")
    if _matches_instance(state_dict, process, app_path):
        return InstanceCheck("running", state=state_dict, process=process)
    return InstanceCheck("foreign", state=state_dict, process=process, reason="identity_mismatch")


def _matches_instance(state: Mapping[str, Any], process: ProcessInfo, app_path: str) -> bool:
    command = _normalise_command(process.command_line)
    instance_id = str(state.get("instance_id") or "").casefold()
    marker = f"{INSTANCE_ID_ARGUMENT}{instance_id}".casefold()
    expected_app = _normalise_path(app_path)
    return bool(instance_id and marker in command and expected_app in command)


def pid_is_alive(pid: int) -> bool:
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
    try:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            return False
        from ctypes import wintypes

        kernel32 = win_dll("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = open_process(0x1000, False, wintypes.DWORD(pid))
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            return bool(get_exit_code(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            close_handle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def read_process_info(pid: int) -> ProcessInfo | None:
    if pid <= 0:
        return None
    return _windows_process_info(pid) if _is_windows() else _posix_process_info(pid)


def _posix_process_info(pid: int) -> ProcessInfo | None:
    proc_dir = os.path.join("/proc", str(pid))
    if os.path.isdir(proc_dir):
        command_line = ""
        executable = ""
        cwd = ""
        try:
            with open(os.path.join(proc_dir, "cmdline"), "rb") as file:
                command_line = file.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            pass
        try:
            executable = os.readlink(os.path.join(proc_dir, "exe"))
        except OSError:
            pass
        try:
            cwd = os.readlink(os.path.join(proc_dir, "cwd"))
        except OSError:
            pass
        if command_line or executable or cwd:
            return ProcessInfo(pid, executable, command_line, cwd)

    # macOS does not normally expose /proc; ps is a read-only OS utility.
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    command_line = str(result.stdout or "").strip()
    return ProcessInfo(pid, command_line=command_line) if result.returncode == 0 and command_line else None


def _windows_process_info(pid: int) -> ProcessInfo | None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return None
    # PID is converted to int before interpolation, so this command contains
    # no user-controlled shell syntax.
    command = (
        "$p = Get-CimInstance -ClassName Win32_Process "
        f"-Filter 'ProcessId = {int(pid)}'; "
        "if ($null -ne $p) { "
        "[pscustomobject]@{ CommandLine=$p.CommandLine; "
        "ExecutablePath=$p.ExecutablePath } | ConvertTo-Json -Compress }"
    )
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
    }
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if no_window:
        kwargs["creationflags"] = no_window
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            check=False,
            **kwargs,
        )
        data = json.loads(str(result.stdout or "").strip())
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return ProcessInfo(
        pid,
        executable=str(data.get("ExecutablePath") or ""),
        command_line=str(data.get("CommandLine") or ""),
    )


def port_is_in_use() -> bool:
    """Return whether TCP ``port`` can be bound locally.

    This is a refusal-only check.  It never discovers or terminates the
    process occupying the port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if _is_windows():
            try:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass
        try:
            probe.bind(("127.0.0.1", STUDIO_PORT))
        except OSError:
            return True
    return False


def send_graceful_shutdown(pid: int) -> bool:
    """Ask a confirmed Studio process to exit through its normal hooks."""
    try:
        if _is_windows():
            event = getattr(signal, "CTRL_BREAK_EVENT", None)
            if event is None:
                return False
            try:
                os.kill(pid, event)
            except (OSError, ProcessLookupError, ValueError):
                # stop.bat normally runs in a new console.  Attach briefly to
                # the Studio console so Ctrl+Break can still reach its process
                # group before the hard-termination fallback is considered.
                return _windows_send_ctrl_break(pid)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError, ValueError):
        return False
    return True


def _windows_send_ctrl_break(pid: int) -> bool:
    try:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            return False
        from ctypes import wintypes

        kernel32 = win_dll("kernel32", use_last_error=True)
        free_console = kernel32.FreeConsole
        free_console.argtypes = ()
        free_console.restype = wintypes.BOOL
        attach_console = kernel32.AttachConsole
        attach_console.argtypes = (wintypes.DWORD,)
        attach_console.restype = wintypes.BOOL
        generate_event = kernel32.GenerateConsoleCtrlEvent
        generate_event.argtypes = (wintypes.DWORD, wintypes.DWORD)
        generate_event.restype = wintypes.BOOL

        try:
            free_console()
            if not attach_console(wintypes.DWORD(pid)):
                return False
            return bool(generate_event(1, wintypes.DWORD(pid)))
        finally:
            free_console()
            # Restore the stop command's parent console for any final output.
            attach_console(wintypes.DWORD(0xFFFFFFFF))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def terminate_confirmed_process(pid: int) -> bool:
    """Last-resort termination; callers must re-check identity immediately first."""
    if _is_windows():
        return _windows_terminate_process(pid)
    try:
        os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError, ValueError):
        return False
    return True


def _windows_terminate_process(pid: int) -> bool:
    try:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            return False
        from ctypes import wintypes

        kernel32 = win_dll("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        terminate_process = kernel32.TerminateProcess
        terminate_process.argtypes = (wintypes.HANDLE, wintypes.UINT)
        terminate_process.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = open_process(0x0001, False, wintypes.DWORD(pid))
        if not handle:
            return False
        try:
            return bool(terminate_process(handle, 1))
        finally:
            close_handle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def wait_for_pid_exit(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while pid_is_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "INSTANCE_ID_ARGUMENT",
    "INSTANCE_ID_ENVIRONMENT",
    "STUDIO_PORT",
    "STUDIO_URL",
    "InstanceCheck",
    "ProcessInfo",
    "acquire_start_lock",
    "check_instance",
    "instance_state_path",
    "load_instance_state",
    "now_utc",
    "pid_is_alive",
    "port_is_in_use",
    "read_process_info",
    "remove_instance_state",
    "send_graceful_shutdown",
    "terminate_confirmed_process",
    "wait_for_pid_exit",
    "write_instance_state",
]
