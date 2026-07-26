# -*- coding: utf-8 -*-
"""Launcher — resolve Python interpreter, check dependencies, start app.py.

All Python interpreter detection is centralized here; ``start.bat`` delegates
to this file via the system ``python`` on PATH.

Resolution priority
   1. ``AUDIOBOOK_STUDIO_PYTHON`` environment variable
      - If set *and* the file exists → use it.
      - If set but the file does *not* exist → warn and continue with fallback.
   2. Sibling ``../index-tts/.venv`` (relative, relocatable)
      - Windows: ``.venv/Scripts/python.exe``
      - macOS / Linux: ``.venv/bin/python``
   3. System PATH
      - ``shutil.which("python")`` or ``shutil.which("python3")``
   4. If nothing is found → exit with a clear, actionable error message.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# 程序目录：由本文件位置推导（仓库可整体移动，不依赖绝对路径）
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Python 解释器解析（入口：若起 launcher 的 python 没有 subprocess 可用，
# 本身就到不了这里，因此 stdlib 依赖安全。）
# ---------------------------------------------------------------------------

PYTHON: str = ""   # will be filled by _resolve_python()


def _resolve_python() -> str:
    """Resolve Python interpreter according to the priority documented above."""

    # ---- 1. Environment variable ----
    env_py = os.environ.get("AUDIOBOOK_STUDIO_PYTHON")
    if env_py:
        if os.path.isfile(env_py):
            return env_py
        print(f"⚠ AUDIOBOOK_STUDIO_PYTHON 指向的文件不存在：{env_py}")
        print("  将尝试其他 Python 解释器。")

    # ---- 2. Sibling index-tts/.venv (cross‑platform) ----
    venv_dir = os.path.normpath(os.path.join(BASE_DIR, "..", "index-tts", ".venv"))
    if os.path.isdir(venv_dir):
        candidates = [
            os.path.join(venv_dir, "Scripts", "python.exe"),  # Windows
            os.path.join(venv_dir, "bin", "python"),           # Unix
        ]
        for cand in candidates:
            if os.path.isfile(cand):
                print(f"使用仓库同级 venv 的 Python：{cand}")
                return cand

    # ---- 3. System PATH ----
    path_py = shutil.which("python") or shutil.which("python3")
    if path_py:
        return path_py

    # ---- 4. Nothing found — abort with clear message ----
    print()
    print("=" * 50)
    print("  错误：找不到 Python 解释器！")
    print()
    print("  请将 Python 3.10+ 加入系统 PATH，或设置")
    print("  AUDIOBOOK_STUDIO_PYTHON 环境变量指向仓库同级的")
    print("  index-tts/.venv/Scripts/python.exe。")
    print()
    print("  ffmpeg 下载地址：https://ffmpeg.org/download.html")
    print("=" * 50)
    print()
    sys.exit(1)


# ---------------------------------------------------------------------------
# version helpers (single source in lib/__init__.py will be used at runtime)
# ---------------------------------------------------------------------------
def _read_version() -> str:
    """Try to read version from ``lib/__init__``; fall back to a literal."""
    try:
        sys.path.insert(0, BASE_DIR)
        from lib import __version__  # type: ignore[import]
        return __version__
    except Exception:
        # 版本的权威值只存在于 lib.__version__；导入异常时不要复制旧版本号。
        return "unknown"


VERSION = _read_version()

# ────────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point: prepare runtime environment, check deps, start app."""
    global PYTHON
    PYTHON = _resolve_python()

    os.chdir(BASE_DIR)

    # 双击后的首个中文即时反馈（由 Python 输出，避免 .bat 中文编码乱码）
    print("有声书工作台启动中，请稍后...")

    # 检查运行环境（依赖检查较慢，先给出提示，避免控制台空屏）
    print("正在检查运行环境，请稍候...")

    # 依赖检查与自动安装（Gradio + pydub）
    result = subprocess.run(
        [PYTHON, "-c", "import gradio"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("-> 正在安装 gradio / pydub ...")
        subprocess.run([PYTHON, "-m", "pip", "install", "gradio", "pydub"], check=True)

    # 科学计算 / 音频后处理依赖
    result = subprocess.run(
        [PYTHON, "-c", "import numpy, scipy, pyloudnorm, mutagen"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("-> 正在安装 numpy / scipy / pyloudnorm / mutagen ...")
        subprocess.run(
            [PYTHON, "-m", "pip", "install", "numpy", "scipy", "pyloudnorm", "mutagen"],
            check=True,
        )

    # ffmpeg 系统二进制检查（非 pip 包）
    # 缺失时导出会显式报错（ExportError），已生成的中间 WAV 仍保留。
    if shutil.which("ffmpeg") is None:
        print()
        print("=" * 50)
        print("  ⚠ 警告：未检测到 ffmpeg！")
        print("  导出 mp3 / m4b 需要 ffmpeg（系统二进制，不通过 pip 安装）。")
        print("  缺失时导出会显式报错（已生成的中间 WAV 仍保留），")
        print("  请下载 ffmpeg 并加入 PATH，或改用 WAV 格式导出。")
        print("  下载地址：https://ffmpeg.org/download.html")
        print("=" * 50)
        print()

    # 启动标题
    print()
    print("=" * 50)
    print(f"      有声书合成工作台 | Audiobook Studio v{VERSION}")
    print("=" * 50)
    print()
    print("  浏览器访问地址：")
    print("  -->  http://localhost:7862  <--")
    print()
    print("  首次加载模型需要等待 10-30 秒")
    print("  关闭此窗口即可停止服务")
    print()
    print("=" * 50)
    print()

    # 加载语音合成引擎（首次约 10-30 秒），先给出提示
    print("正在加载语音合成引擎，首次约 10-30 秒...")
    subprocess.run([PYTHON, "app.py"], check=True)


if __name__ == "__main__":
    main()
