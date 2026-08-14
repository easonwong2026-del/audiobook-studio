"""Standalone fake production runtime for cross-process shutdown tests.

It deliberately avoids importing the heavy ``services`` stack: it only needs to
(1) hold the same singleton ``ProcessFileLock`` the real runtime uses, and
(2) publish a fresh ``runtime_engine_status.json`` so the client's liveness
probe can find it.  In ``respond`` mode it honors ``runtime_shutdown_command.json``
and exits (releasing the lock); in ``ignore`` mode it stays alive forever so the
client's graceful-timeout → terminate fallback is exercised.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_lock_path() -> str:
    """Mirror ``services.runtime_lock.default_runtime_lock_path`` exactly.

    Honoring ``AUDIOBOOK_STUDIO_RUNTIME_LOCK`` matters for test isolation: each
    test points both the client and this fake runtime at a private lock file so
    a real runtime on the developer machine is never touched.
    """
    configured = os.environ.get("AUDIOBOOK_STUDIO_RUNTIME_LOCK")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    user = re.sub(r"[^A-Za-z0-9_.-]+", "_", getpass.getuser() or "user")
    return os.path.join(tempfile.gettempdir(), f"audiobook-studio-production-{user}.lock")


def _write_status(data_dir: str) -> None:
    path = os.path.join(data_dir, "logs", "runtime_engine_status.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump({
            "state": "ready",
            "engine_state": "ready",
            "runtime_state": "running",
            "pid": os.getpid(),
            "owner_id": "test-runtime",
            "updated_at": _now_iso(),
            "runtime_updated_at": _now_iso(),
            "error_summary": "",
            "engine_generation": 0,
            "recovery_count": 0,
            "last_error_code": "",
            "last_recovery_at": "",
            "engine_backend": "",
            "engine_version": "",
            "engine_identity": "",
            "model_identity": "",
            "precision": "",
            "device": "",
            "cache_identity": "",
        }, file)


def main() -> None:
    data_dir = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "respond"

    lock_path = _default_lock_path()
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    handle = open(lock_path, "a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    if os.name == "nt":  # pragma: no cover - platform specific
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    command_path = os.path.join(data_dir, "logs", "runtime_shutdown_command.json")
    _write_status(data_dir)

    if mode == "ignore":
        while True:
            time.sleep(1.0)
            _write_status(data_dir)
    else:
        while True:
            if os.path.exists(command_path):
                try:
                    os.remove(command_path)
                except OSError:
                    pass
                break
            time.sleep(0.1)
            _write_status(data_dir)

    if os.name == "nt":  # pragma: no cover - platform specific
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


if __name__ == "__main__":
    main()
