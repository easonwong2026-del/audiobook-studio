"""统一日志配置：文件轮转 + 控制台输出。

约定：所有模块使用 ``logger = logging.getLogger(__name__)`` 创建模块级 logger，
然后通过本模块的 ``setup_logging()`` 在 ``app.py`` 入口处统一初始化根 logger。
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_LOG_DIR: str | None = None  # 延迟设置


def setup_logging(log_dir: str | None = None, level: int = logging.INFO) -> None:
    """统一日志配置：文件轮转 + 控制台输出。

    Args:
        log_dir: 日志目录（默认为 ``<程序目录>/logs``）。
        level: 日志级别（默认 ``INFO``）。
    """
    global _LOG_DIR
    if log_dir is None:
        log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
        )
    _LOG_DIR = log_dir
    os.makedirs(log_dir, exist_ok=True)

    # 根 logger
    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有 handler（避免重复配置，如 reload）
    root.handlers.clear()

    # 文件 handler（轮转，每个 5MB，保留 3 个备份）
    fh = RotatingFileHandler(
        os.path.join(log_dir, "audiobook-studio.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(fh)

    # 控制台 handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(ch)


def get_log_dir() -> str:
    """返回当前日志目录（未初始化时返回默认路径 ``<程序目录>/logs``）。"""
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
        )
    return _LOG_DIR
