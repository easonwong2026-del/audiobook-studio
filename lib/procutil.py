"""Windows 无控制台 subprocess 助手。

根因：生产运行时子进程以 ``DETACHED_PROCESS`` 启动（本身无控制台）。当它再
spawn 一个控制台程序（ffmpeg / ffprobe 等）且**不指定** ``CREATE_NO_WINDOW``
时，Windows 会为子进程**新建一个可见控制台窗口**——即用户看到的“黑框”。

统一出口：
- ``run_no_window(...)``  /  ``popen_no_window(...)``：自动追加
  ``CREATE_NO_WINDOW``，避免任何子进程弹出新 CMD 黑框。
- 不影响 stdout/stderr 重定向：调用方仍可 ``capture_output=True`` 或显式传文件，
  日志不会因隐藏窗口而丢失。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any


def no_window_kwargs(**kwargs: Any) -> dict[str, Any]:
    """Return ``kwargs`` with Windows ``CREATE_NO_WINDOW`` merged in.

    非 Windows 平台原样返回，不改变任何行为。
    """
    if os.name != "nt":
        return kwargs
    flags = int(kwargs.get("creationflags", 0) or 0)
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    kwargs["creationflags"] = flags
    return kwargs


def run_no_window(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """``subprocess.run`` + ``CREATE_NO_WINDOW``（Windows）。"""
    return subprocess.run(*args, **no_window_kwargs(**kwargs))


def popen_no_window(*args: Any, **kwargs: Any) -> subprocess.Popen:
    """``subprocess.Popen`` + ``CREATE_NO_WINDOW``（Windows）。"""
    return subprocess.Popen(*args, **no_window_kwargs(**kwargs))


__all__ = ["no_window_kwargs", "run_no_window", "popen_no_window"]
