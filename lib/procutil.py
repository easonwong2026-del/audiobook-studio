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
import sys
from typing import Any


def _is_windows() -> bool:
    """Windows platform probe.

    独立函数而非直接读 ``os.name``：测试通过 monkeypatch 本函数切换平台
    分支，避免污染全局 ``os.name``（全局改动会波及 pytest 自身的 pathlib
    行为，例如 Linux 上 ``Path()`` 被错误实例化为 ``WindowsPath`` 崩溃）。
    """
    return os.name == "nt"


def no_window_kwargs(**kwargs: Any) -> dict[str, Any]:
    """Return ``kwargs`` with Windows ``CREATE_NO_WINDOW`` merged in.

    非 Windows 平台原样返回，不改变任何行为。
    """
    if not _is_windows():
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


def open_in_folder(path: str) -> bool:
    """Open a folder (or reveal a file) without spawning a console window.

    Windows: 文件用 ``explorer /select,<path>``（在资源管理器中定位选中），
    目录用 ``os.startfile`` —— 两者都不会弹出黑色 console；explorer 走
    ``popen_no_window`` 双保险。非 Windows 回退 ``open`` / ``xdg-open``。

    Args:
        path: 要打开的目录或文件绝对路径。

    Returns:
        ``True`` 表示已尝试打开；路径不存在或打开失败返回 ``False``。
    """
    target = os.path.abspath(str(path or ""))
    if not target or not os.path.exists(target):
        return False
    try:
        if _is_windows():
            if os.path.isfile(target):
                # explorer 自身是 GUI 程序（无 console），仍统一走 no-window helper
                # 防意外；subprocess 会把带空格的整个参数自动加引号。
                popen_no_window(["explorer", f"/select,{os.path.normpath(target)}"])
            else:
                os.startfile(target)  # noqa: S606 - 打开目录，无 console
            return True
        reveal = target if os.path.isdir(target) else os.path.dirname(target)
        if sys.platform == "darwin":
            subprocess.Popen(["open", reveal])
        else:
            subprocess.Popen(["xdg-open", reveal])
        return True
    except OSError:
        return False


__all__ = ["no_window_kwargs", "run_no_window", "popen_no_window", "open_in_folder"]
