"""Owned-process lifecycle controller for the Audiobook Studio service.

The controller never discovers or kills arbitrary processes.  It writes one
small owner record for this instance, runs registered cooperative cleanups,
closes the Gradio server, and optionally exits after the browser receives the
shutdown response.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from repositories.v4_atomic import atomic_write_json

OWNER = "audiobook-studio"
PID_FILE_NAME = "audiobook-studio-service.json"


class ServiceLifecycle:
    _lock = threading.RLock()
    _state = "stopped"
    _stop_event = threading.Event()
    _cleanup_hooks: ClassVar[list[tuple[str, Callable[[], Any]]]] = []
    _server_close: Callable[[], Any] | None = None
    _exit_callback: Callable[[], Any] | None = None
    _pid_path: Path | None = None
    _port = 7862
    _last_error = ""

    @classmethod
    def configure(
        cls,
        *,
        pid_path: str | Path,
        port: int = 7862,
        exit_callback: Callable[[], Any] | None = None,
    ) -> Path:
        with cls._lock:
            cls._pid_path = Path(pid_path)
            cls._port = int(port)
            cls._exit_callback = exit_callback
            cls._stop_event.clear()
            cls._state = "running"
            cls._last_error = ""
            cls._write_record("running")
            return cls._pid_path

    @classmethod
    def register_cleanup(cls, name: str, callback: Callable[[], Any]) -> None:
        with cls._lock:
            cls._cleanup_hooks = [
                item for item in cls._cleanup_hooks if item[0] != name
            ]
            cls._cleanup_hooks.append((name, callback))

    @classmethod
    def register_server(cls, close_callback: Callable[[], Any]) -> None:
        with cls._lock:
            cls._server_close = close_callback

    @classmethod
    def is_stopping(cls) -> bool:
        return cls._stop_event.is_set()

    @classmethod
    def status(cls) -> dict[str, Any]:
        with cls._lock:
            return {
                "owner": OWNER,
                "state": cls._state,
                "port": cls._port,
                "last_error": cls._last_error,
            }

    @classmethod
    def request_shutdown(cls, *, delay: float = 0.25) -> str:
        with cls._lock:
            if cls._state == "stopped":
                return "ℹ 服务已经停止。"
            if cls._state == "stopping":
                return "服务正在关闭，可以关闭此页面。"
            cls._state = "stopping"
            cls._stop_event.set()
            cls._write_record("stopping")
            worker = threading.Thread(
                target=cls._shutdown_worker,
                args=(max(0.0, float(delay)),),
                daemon=True,
                name="audiobook-studio-shutdown",
            )
            worker.start()
        return "服务正在关闭，可以关闭此页面。"

    @classmethod
    def _shutdown_worker(cls, delay: float) -> None:
        failures: list[str] = []
        for name, callback in reversed(cls._cleanup_hooks):
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - continue releasing resources
                failures.append(f"{name}: {type(exc).__name__}")
        if cls._server_close is not None:
            try:
                cls._server_close()
            except Exception as exc:  # noqa: BLE001 - process may already be closed
                failures.append(f"gradio: {type(exc).__name__}")
        if not cls.port_is_released():
            failures.append(f"port {cls._port} is still occupied")
        # Give the HTTP response a short chance to reach the browser.
        if delay:
            time.sleep(delay)
        with cls._lock:
            cls._state = "stopped"
            cls._last_error = "; ".join(failures)
            cls._write_record("stopped")
            exit_callback = cls._exit_callback
        if exit_callback is not None:
            try:
                exit_callback()
            except Exception:  # noqa: BLE001 - process exit is best effort in tests
                return

    @classmethod
    def _write_record(cls, state: str) -> None:
        if cls._pid_path is None:
            return
        try:
            cls._pid_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                cls._pid_path,
                {
                    "schema_version": "audiobook-studio-service-v1",
                    "owner": OWNER,
                    "pid": os.getpid(),
                    "parent_pid": os.getppid(),
                    "port": cls._port,
                    "state": state,
                    "updated_at": time.time(),
                },
            )
        except OSError:
            return

    @classmethod
    def pid_path_for_data_dir(cls, data_dir: str | Path) -> Path:
        return Path(data_dir) / "runtime" / PID_FILE_NAME

    @classmethod
    def stop_owned_instance(cls, pid_path: str | Path) -> str:
        """Stop only the PID recorded by this application, never a port owner."""
        path = Path(pid_path)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "ℹ 当前没有可停止的 Audiobook Studio 服务实例。"
        if not isinstance(record, dict) or record.get("owner") != OWNER:
            return "⚠ 服务记录不是当前 Audiobook Studio 实例，未执行停止。"
        if record.get("state") == "stopped":
            return "ℹ 服务已经停止。"
        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return "⚠ 服务记录中的 PID 无效，未执行停止。"
        if not _pid_exists(pid):
            return "ℹ 服务已经停止。"
        command_line = _command_line_for_pid(pid)
        if command_line is None or not any(
            marker in command_line.lower() for marker in ("app.py", "launcher.py")
        ):
            return "⚠ 无法确认 PID 属于当前 Audiobook Studio，未执行停止。"
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            return f"❌ 停止服务失败：{exc}"
        return f"✅ 已请求停止当前 Audiobook Studio 服务（PID {pid}）。"

    @classmethod
    def port_is_released(cls, port: int | None = None) -> bool:
        value = cls._port if port is None else int(port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", value))
            return True
        except OSError:
            return False
        finally:
            sock.close()


def _pid_exists(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _command_line_for_pid(pid: int) -> str | None:
    """Read one exact PID's command line for stale-PID protection."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                [
                    "wmic",
                    "process",
                    "where",
                    f"ProcessId={pid}",
                    "get",
                    "CommandLine",
                    "/value",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            return result.stdout or ""
        proc_path = Path(f"/proc/{pid}/cmdline")
        if proc_path.is_file():
            return proc_path.read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace"
            )
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None
