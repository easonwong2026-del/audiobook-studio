#!/usr/bin/env python3
"""Measure app cold import and idle RSS without launching a public server."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _rss_mb() -> float:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return (
            round(counters.WorkingSetSize / (1024 * 1024), 3)
            if ok else -1.0
        )
    import resource

    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if platform.system() == "Darwin" else 1024
    return round(maximum / divisor, 3)


def _child() -> int:
    started = time.perf_counter()
    import app  # noqa: F401

    print(json.dumps({
        "app_import_seconds": round(time.perf_counter() - started, 6),
        "ui_idle_rss_mb": _rss_mb(),
    }))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    arguments = parser.parse_args()
    if arguments.child:
        return _child()
    with tempfile.TemporaryDirectory(prefix="audiobook-app-benchmark-") as root:
        environment = dict(os.environ)
        environment["AUDIOBOOK_STUDIO_DATA_DIR"] = root
        environment["AUDIOBOOK_STUDIO_RUNTIME_MODE"] = "off"
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        wall = time.perf_counter() - started
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    payload["cold_process_wall_seconds"] = round(wall, 6)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
