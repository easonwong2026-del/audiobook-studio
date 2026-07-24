"""原子写工具函数（独立模块，避免循环导入）。

所有 JSON 写入必须使用本工具：临时文件 → f.flush() → os.fsync(f.fileno()) → os.replace。
"""
from __future__ import annotations

import json
import logging
import os

from .exceptions import AtomicWriteError

logger = logging.getLogger(__name__)


def atomic_write(path: str, data: dict) -> None:
    """原子写 JSON：临时文件 → fsync → os.replace。

    Args:
        path: 目标文件路径。
        data: 序��化为 JSON 的 dict。

    Raises:
        AtomicWriteError: 写入失败时抛出。
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        # 清理残留临时文件
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError as exc:
            logger.debug("原子写入后清理临时文件失败: %s", exc)
        raise AtomicWriteError(f"原子写入失败 {path}: {exc}") from exc
