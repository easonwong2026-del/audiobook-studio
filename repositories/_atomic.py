"""原子写工具函数（独立模块，避免循环导入）。

所有 JSON 写入必须使用本工具：临时文件 → f.flush() → os.fsync(f.fileno()) → os.replace。
"""
from __future__ import annotations

import json
import logging
import os
import time

from .exceptions import AtomicWriteError

logger = logging.getLogger(__name__)


def _transient_windows_error(exc: OSError) -> bool:
    """Return True for transient Windows file-lock failures (WinError 5/32)."""
    if os.name != "nt":
        return False
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32)


def atomic_write(path: str, data: dict) -> None:
    """原子写 JSON：临时文件 → fsync → os.replace。

    Args:
        path: 目标文件路径。
        data: 序��化为 JSON 的 dict。

    Raises:
        AtomicWriteError: 写入失败时抛出。
    """
    tmp = path + ".tmp"
    # Windows CI / AV scanners intermittently lock the temp file between
    # close and replace (WinError 5/32).  Retry briefly on Windows only;
    # POSIX behavior is unchanged (single attempt).
    attempts = 3 if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return
        except OSError as exc:
            if attempt + 1 < attempts and _transient_windows_error(exc):
                time.sleep(0.05 * (attempt + 1))
                continue
            # 清理残留临时文件
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError as cleanup_exc:
                logger.debug("原子写入后清理临时文件失败: %s", cleanup_exc)
            raise AtomicWriteError(f"原子写入失败 {path}: {exc}") from exc
