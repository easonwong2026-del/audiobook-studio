"""Small cross-platform process lock used by the production runtime."""
from __future__ import annotations

import getpass
import os
import re
import tempfile
from typing import BinaryIO


def default_runtime_lock_path() -> str:
    configured = os.environ.get("AUDIOBOOK_STUDIO_RUNTIME_LOCK")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    user = re.sub(r"[^A-Za-z0-9_.-]+", "_", getpass.getuser() or "user")
    return os.path.join(
        tempfile.gettempdir(),
        f"audiobook-studio-production-{user}.lock",
    )


class ProcessFileLock:
    """An advisory exclusive lock released automatically when the process dies."""

    def __init__(self, path: str | None = None) -> None:
        self.path = os.path.abspath(path or default_runtime_lock_path())
        self._file: BinaryIO | None = None

    @property
    def acquired(self) -> bool:
        return self._file is not None

    def acquire(self, *, blocking: bool = False) -> bool:
        if self._file is not None:
            return True
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle = open(self.path, "a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
            else:
                import fcntl

                flags = fcntl.LOCK_EX
                if not blocking:
                    flags |= fcntl.LOCK_NB
                fcntl.flock(handle.fileno(), flags)
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
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._file = None
            handle.close()

    def __enter__(self) -> "ProcessFileLock":
        if not self.acquire(blocking=True):
            raise RuntimeError("无法取得生产运行时进程锁")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


__all__ = ["ProcessFileLock", "default_runtime_lock_path"]
