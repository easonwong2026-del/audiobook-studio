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

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from lib import studio_lifecycle
from lib.environment import resolve_python_interpreter

# ---------------------------------------------------------------------------
# 程序目录：由本文件位置推导（仓库可整体移动，不依赖绝对路径）
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(BASE_DIR, "app.py")

# ---------------------------------------------------------------------------
# Python 解释器解析（入口：若起 launcher 的 python 没有 subprocess 可用，
# 本身就到不了这里，因此 stdlib 依赖安全。）
# ---------------------------------------------------------------------------

PYTHON: str = ""   # will be filled by _resolve_python()

REQUIRED_MODULES = ("gradio", "numpy", "scipy", "pyloudnorm", "mutagen")
REQUIREMENTS_FILE = os.path.join(BASE_DIR, "requirements.txt")


def _is_windows() -> bool:
    return os.name == "nt"


def _resolve_python() -> str:
    """Resolve Python interpreter according to the priority documented above."""
    resolution = resolve_python_interpreter()
    for warning in resolution.warnings:
        print(f"⚠ {warning}")
        print("  将尝试其他 Python 解释器。")
    if resolution.executable:
        if resolution.source == "sibling_venv":
            print(f"使用仓库同级 venv 的 Python：{resolution.executable}")
        return resolution.executable

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


def _dependency_check_code() -> str:
    """Return the import probe executed by the resolved interpreter."""
    return "import " + ", ".join(REQUIRED_MODULES)


def _check_runtime_dependencies(python: str) -> bool:
    """Check all runtime modules in one subprocess using ``python``."""
    result = subprocess.run(
        [python, "-c", _dependency_check_code()],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _install_runtime_dependencies(python: str) -> None:
    """Install the project's pinned runtime requirements into ``python``."""
    print("-> 检测到运行依赖缺失，正在使用已选定的 Python 安装 requirements.txt ...")
    result = subprocess.run(
        [python, "-m", "pip", "install", "-r", REQUIREMENTS_FILE],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("❌ 运行依赖安装失败，应用不会继续启动。")
        print("   请检查网络、pip 和 requirements.txt 后重试。")
        raise SystemExit(1)

    if not _check_runtime_dependencies(python):
        print("❌ 运行依赖安装后仍无法导入全部模块，应用不会继续启动。")
        print("   请检查该 Python 环境后重试。")
        raise SystemExit(1)


def _current_instance() -> studio_lifecycle.InstanceCheck:
    return studio_lifecycle.check_instance(
        studio_lifecycle.load_instance_state(),
        BASE_DIR,
        APP_PATH,
    )


def _print_running(state: dict[str, object]) -> None:
    print("Audiobook Studio 已在运行")
    print(f"地址：{studio_lifecycle.STUDIO_URL}")
    print(f"PID：{state.get('pid')}")
    print()
    print("如需停止：")
    print("python launcher.py --stop")


def _print_foreign_port() -> None:
    print(f"端口 {studio_lifecycle.STUDIO_PORT} 已被其他程序占用。")
    print("Audiobook Studio 未启动。")


def _app_environment(instance_id: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment[studio_lifecycle.INSTANCE_ID_ENVIRONMENT] = instance_id
    return environment


def _start_app(python: str, instance_id: str) -> subprocess.Popen:
    kwargs: dict[str, object] = {
        "cwd": BASE_DIR,
        "env": _app_environment(instance_id),
    }
    if _is_windows():
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if flags:
            kwargs["creationflags"] = flags
    return subprocess.Popen(
        [python, APP_PATH, f"{studio_lifecycle.INSTANCE_ID_ARGUMENT}{instance_id}"],
        **kwargs,
    )


def _instance_state(instance_id: str, pid: int) -> dict[str, object]:
    return {
        "pid": int(pid),
        "started_at": studio_lifecycle.now_utc(),
        "port": studio_lifecycle.STUDIO_PORT,
        "instance_id": instance_id,
        "repo_path": os.path.abspath(BASE_DIR),
        "app_path": os.path.abspath(APP_PATH),
    }


def _cleanup_owned_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5.0)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def _start_locked() -> int:
    global PYTHON

    os.chdir(BASE_DIR)

    # 双击后的首个中文即时反馈（由 Python 输出，避免 .bat 中文编码乱码）
    print("有声书工作台启动中，请稍后...")

    existing = _current_instance()
    if existing.status == "running" and existing.state is not None:
        _print_running(existing.state)
        return 0
    if existing.status == "unknown":
        print("无法安全确认现有 Studio 进程身份，未启动新的实例。")
        print("请稍后重试，或检查当前用户是否有读取进程信息的权限。")
        return 1
    if existing.status == "foreign" and existing.reason == "different_repo":
        _print_foreign_port()
        return 1
    if existing.status in {"stale", "foreign"}:
        studio_lifecycle.remove_instance_state(existing.state)

    if studio_lifecycle.port_is_in_use():
        _print_foreign_port()
        return 1

    PYTHON = _resolve_python()

    # 检查运行环境（依赖检查较慢，先给出提示，避免控制台空屏）
    print("正在检查运行环境，请稍候...")

    if not _check_runtime_dependencies(PYTHON):
        _install_runtime_dependencies(PYTHON)

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

    # 依赖检查期间可能有其他程序占用端口，启动前再拒绝一次。
    if studio_lifecycle.port_is_in_use():
        _print_foreign_port()
        return 1

    instance_id = uuid.uuid4().hex
    process = None
    state = _instance_state(instance_id, 0)
    try:
        process = _start_app(PYTHON, instance_id)
        state["pid"] = int(process.pid)
        studio_lifecycle.write_instance_state(state)
    except Exception as exc:  # noqa: BLE001 - state failure must reap this exact child
        if process is not None:
            _cleanup_owned_process(process)
        print(f"Audiobook Studio 启动失败：{exc}")
        return 1

    # 启动标题
    print()
    print("=" * 50)
    print(f"      有声书合成工作台 | Audiobook Studio v{VERSION}")
    print("=" * 50)
    print()
    print("  浏览器访问地址：")
    print(f"  -->  {studio_lifecycle.STUDIO_URL}  <--")
    print()
    print("  首次加载模型需要等待 10-30 秒")
    print("  关闭此窗口即可停止服务，或运行 python launcher.py --stop")
    print()
    print("=" * 50)
    print()

    # 加载语音合成引擎（首次约 10-30 秒），先给出提示
    print("正在加载语音合成引擎，首次约 10-30 秒...")
    try:
        return int(process.wait())
    except KeyboardInterrupt:
        # Ctrl+C may reach the launcher before the app's own signal hook.
        if process.poll() is None:
            studio_lifecycle.send_graceful_shutdown(process.pid)
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            _cleanup_owned_process(process)
        return 130
    finally:
        studio_lifecycle.remove_instance_state(state)


def _start() -> int:
    lock = studio_lifecycle.acquire_start_lock()
    if lock is None:
        current = _current_instance()
        if current.status == "running" and current.state is not None:
            _print_running(current.state)
        else:
            print("已有 Studio 启动操作正在进行，请稍后查看状态。")
        return 0
    try:
        return _start_locked()
    finally:
        lock.release()


def _status() -> int:
    current = _current_instance()
    if current.status == "running" and current.state is not None:
        print("Audiobook Studio：运行中")
        print(f"PID：{current.state.get('pid')}")
        print(f"地址：{studio_lifecycle.STUDIO_URL}")
        return 0
    if current.status in {"stale", "foreign"} and current.reason != "different_repo":
        studio_lifecycle.remove_instance_state(current.state)
        print("Audiobook Studio：未运行")
        return 0
    if current.status == "foreign":
        print("Audiobook Studio：未运行")
        print(f"端口 {studio_lifecycle.STUDIO_PORT} 由其他 Studio 实例占用。")
        return 1
    if current.status == "unknown":
        print("Audiobook Studio：状态无法确认")
        print("无法安全验证记录中的 PID，未执行任何停止操作。")
        return 1
    print("Audiobook Studio：未运行")
    return 0


def _stop() -> int:
    current = _current_instance()
    if current.status == "absent":
        print("Audiobook Studio：未运行")
        return 0
    if current.status in {"stale", "foreign"} and current.reason != "different_repo":
        studio_lifecycle.remove_instance_state(current.state)
        if current.status == "foreign":
            print("Audiobook Studio：未运行")
            print("记录中的 PID 已属于其他进程，未发送终止信号。")
        else:
            print("Audiobook Studio：未运行（已清理过期状态）")
        return 0
    if current.status == "foreign":
        print("记录属于另一个 Audiobook Studio 实例，未发送终止信号。")
        return 1
    if current.status == "unknown" or current.state is None:
        print("无法安全确认 Studio 进程身份，未发送终止信号。")
        return 1

    pid = int(current.state["pid"])
    print(f"正在停止 Audiobook Studio（PID：{pid}）...")
    studio_lifecycle.send_graceful_shutdown(pid)
    if studio_lifecycle.wait_for_pid_exit(pid, timeout=10.0):
        studio_lifecycle.remove_instance_state(current.state)
        print("Audiobook Studio：已停止")
        return 0

    # Timeout fallback is guarded by a fresh identity check.  The port is
    # intentionally never used to choose a termination target.
    refreshed = _current_instance()
    if refreshed.status in {"stale", "foreign"}:
        if refreshed.reason != "different_repo":
            studio_lifecycle.remove_instance_state(current.state)
        print("Audiobook Studio：目标进程已退出，未发送额外终止信号。")
        return 0
    if refreshed.status != "running" or refreshed.state is None:
        print("无法安全确认 Studio 进程身份，未发送额外终止信号。")
        return 1
    if not studio_lifecycle.terminate_confirmed_process(pid):
        print("Audiobook Studio 未能正常退出，且安全终止失败。")
        return 1
    if not studio_lifecycle.wait_for_pid_exit(pid, timeout=10.0):
        print("Audiobook Studio 未能在限定时间内退出。")
        return 1
    studio_lifecycle.remove_instance_state(refreshed.state)
    print("Audiobook Studio：已停止")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the start, status, or stop command."""
    parser = argparse.ArgumentParser(description="Audiobook Studio lifecycle control")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="show Studio status")
    group.add_argument("--stop", action="store_true", help="stop the Studio instance")
    arguments = parser.parse_args([] if argv is None else argv)
    if arguments.status:
        return _status()
    if arguments.stop:
        return _stop()
    return _start()

# ────────────────────────────────────────────────────────────────────────────
# legacy start implementation removed; lifecycle-aware entry point is above
# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
