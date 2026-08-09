"""Small cross-platform advisory file lock for repository transactions."""
from __future__ import annotations

import os
import time
from typing import BinaryIO


class RepositoryFileLock:
    """Exclusive OS lock with bounded waiting.

    The lock file is persistent so every process coordinates on the same inode.
    The operating system releases the advisory lock automatically if a worker
    exits or crashes.
    """

    def __init__(
        self,
        path: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.02,
    ) -> None:
        self.path = os.path.abspath(path)
        self.timeout = max(float(timeout), 0.0)
        self.poll_interval = max(float(poll_interval), 0.001)
        self._file: BinaryIO | None = None

    def _try_lock(self, handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def acquire(self) -> None:
        if self._file is not None:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle = open(self.path, "a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._try_lock(handle)
                self._file = handle
                return
            except (OSError, BlockingIOError) as exc:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(
                        f"等待 repository 文件锁超时: {self.path}"
                    ) from exc
                time.sleep(self.poll_interval)

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

    def __enter__(self) -> "RepositoryFileLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


__all__ = ["RepositoryFileLock"]
