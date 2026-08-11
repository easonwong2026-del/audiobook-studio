"""Windows 进程创建追踪诊断工具（真实桌面运行）。

用途：定位「点击开始合成 → 弹出黑框」时实际创建了哪些进程。
用法（真实 Windows 桌面）：
    python scripts/diagnose_windows_process_spawn.py --duration 180
    启动后 → 打开 Web 点击「开始合成」→ 等 engine_ready 后 5 秒 → Ctrl+C 或等 --duration 结束

日志输出到 <data_dir>/logs/windows_process_spawn_trace.log，包含：
    [baseline]   启动时全部进程（PID / PPID / name / exe）
    [new]        每个新出现进程：timestamp / PID / PPID / name / exe / cmdline
    [chain]      parent chain（新进程 → 父 → 祖父 → …）
    [exit]       已消失进程（可选）

判定线索：
    - 新增 conhost.exe / OpenConsole.exe  → 有进程创建了控制台（黑框宿主）
    - 新增 ninja.exe / cl.exe / nvcc.exe / link.exe → 第三方库在 JIT/编译（如 Torch cpp ext）
    - 新增 ffmpeg.exe / ffprobe.exe        → 音频工具
    - 新增 cmd.exe / powershell.exe        → 脚本/打包器
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

KERNEL32 = ctypes.windll.kernel32

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(wt.ULONG)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


def _process_snapshot() -> dict[int, dict[str, Any]]:
    """PID -> {pid, ppid, name, exe} via Toolhelp32 + QueryFullProcessImageName."""
    result: dict[int, dict[str, Any]] = {}
    snapshot = KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return result
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = KERNEL32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            result[pid] = {
                "pid": pid,
                "ppid": int(entry.th32ParentProcessID),
                "name": entry.szExeFile,
                "exe": _process_exe_path(pid),
            }
            ok = KERNEL32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        KERNEL32.CloseHandle(snapshot)
    return result


def _process_exe_path(pid: int) -> str:
    handle = KERNEL32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if KERNEL32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value[: size.value]
        return ""
    finally:
        KERNEL32.CloseHandle(handle)


def _command_line(pid: int) -> str:
    """Best-effort command line via wmic (may be unavailable on modern Windows)."""
    try:
        result = subprocess.run(
            ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine",
             "/value"],
            capture_output=True, text=True, timeout=3, errors="ignore",
        )
        for line in (result.stdout or "").splitlines():
            if line.startswith("CommandLine="):
                return line[len("CommandLine="):].strip()[:400]
    except Exception:
        pass
    return ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text + "\n")
    print(text, flush=True)


def _parent_chain(snapshot: dict[int, dict[str, Any]], pid: int) -> list[int]:
    chain: list[int] = []
    seen: set[int] = set()
    current = pid
    while current and current not in seen and len(chain) < 16:
        seen.add(current)
        chain.append(current)
        info = snapshot.get(current)
        current = info["ppid"] if info else 0
    return chain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=0.0,
                        help="追踪时长（秒），0 = 直到 Ctrl+C")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="轮询间隔（秒）")
    parser.add_argument("--log", default="",
                        help="日志路径（默认 <data_dir>/logs/windows_process_spawn_trace.log）")
    parser.add_argument("--include-exits", action="store_true",
                        help="同时记录消失的进程")
    args = parser.parse_args()

    if args.log:
        log_path = args.log
    else:
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from lib import config

            log_dir = os.path.join(config.get_data_dir(), "logs")
        except Exception:
            log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "windows_process_spawn_trace.log")

    _log(log_path, "=" * 80)
    _log(log_path, f"[session-start] {_now()} pid={os.getpid()} interval={args.interval}s")
    baseline = _process_snapshot()
    _log(log_path, f"[baseline] total={len(baseline)} processes")
    for pid, info in sorted(baseline.items()):
        _log(log_path, "  %s pid=%s ppid=%s name=%s exe=%s" % (
            _now(), pid, info["ppid"], info["name"], info["exe"] or "-"))

    known: dict[int, dict[str, Any]] = dict(baseline)
    seen_pids: set[int] = set(baseline)
    deadline = time.time() + args.duration if args.duration > 0 else None
    try:
        while True:
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(max(args.interval, 0.2))
            snapshot = _process_snapshot()
            current_pids = set(snapshot)
            for pid in sorted(current_pids - seen_pids):
                info = snapshot[pid]
                chain = _parent_chain(snapshot, pid)
                chain_names = " <- ".join(
                    "%s(%s)" % (snapshot[p]["name"], p) if p in snapshot else str(p)
                    for p in reversed(chain)
                )
                _log(log_path, "[new] %s pid=%s ppid=%s name=%s exe=%s cmd=%s" % (
                    _now(), pid, info["ppid"], info["name"],
                    info["exe"] or "-", _command_line(pid) or "-"))
                _log(log_path, "[chain] %s" % chain_names)
                if info["name"].lower() in {"conhost.exe", "openconsole.exe", "cmd.exe",
                                            "ninja.exe", "cl.exe", "nvcc.exe", "link.exe",
                                            "ffmpeg.exe", "ffprobe.exe", "pythonw.exe",
                                            "powershell.exe"}:
                    _log(log_path, "[!] %s 出现（黑框/编译/工具链相关进程）" % info["name"])
            if args.include_exits:
                for pid in sorted(seen_pids - current_pids):
                    info = known.get(pid)
                    _log(log_path, "[exit] %s pid=%s name=%s" % (
                        _now(), pid, info["name"] if info else "-"))
            seen_pids = current_pids
            known = snapshot
    except KeyboardInterrupt:
        pass
    _log(log_path, f"[session-end] {_now()} total-new={len(seen_pids - set(baseline))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
