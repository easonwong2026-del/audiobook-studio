"""PR B 修复 2：Windows 无黑框 —— 统一 no-window helper 回归测试。

覆盖：
- ``lib.procutil`` 的 run/popen no-window helper 在 Windows 分支追加
  CREATE_NO_WINDOW，非 Windows 原样返回；
- runtime spawn 保持 DETACHED_PROCESS（console-less 结构不被破坏）；
- 「打开所在文件夹」（open_in_folder）走 no-window 路径，不产生 console；
- ffmpeg/ffprobe 走 run_no_window（既有 audio_pipeline/metadata/postprocess）。

注意：monkeypatch 一律打在模块级 ``_is_windows`` 函数上，绝不改全局
``os.name``（避免 pytest 自身 pathlib 行为被 WindowsPath 污染而崩溃）。
"""
from __future__ import annotations

import os
import subprocess
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import procutil  # noqa: E402


def test_run_no_window_adds_create_no_window_on_windows(monkeypatch):
    monkeypatch.setattr(procutil, "_is_windows", lambda: True)
    captured: dict = {}

    class _FakeCompleted:
        returncode = 0

    def _fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = procutil.run_no_window(["tool.exe"], check=True)
    assert result.returncode == 0
    flags = int(captured["kwargs"].get("creationflags", 0))
    assert flags & getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_run_no_window_passthrough_on_posix(monkeypatch):
    monkeypatch.setattr(procutil, "_is_windows", lambda: False)
    captured: dict = {}

    class _FakeCompleted:
        returncode = 0

    def _fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    procutil.run_no_window(["tool"], capture_output=True)
    assert "creationflags" not in captured["kwargs"]


def test_popen_no_window_adds_create_no_window_on_windows(monkeypatch):
    monkeypatch.setattr(procutil, "_is_windows", lambda: True)
    captured: dict = {}

    class _FakeProc:
        pid = 1

    def _fake_popen(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    procutil.popen_no_window(["tool.exe"])
    flags = int(captured["kwargs"].get("creationflags", 0))
    assert flags & getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_open_in_folder_windows_uses_no_window_helper(monkeypatch, tmp_path):
    """Windows 打开文件所在文件夹：explorer 走 popen_no_window（no-window）。"""
    monkeypatch.setattr(procutil, "_is_windows", lambda: True)
    target = tmp_path / "demo.wav"
    target.write_bytes(b"RIFF" * 8)
    captured: dict = {}

    class _FakeProc:
        pid = 1

    def _fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    ok = procutil.open_in_folder(str(target))
    assert ok is True
    assert captured["args"][0][0].lower().endswith("explorer")
    assert "/select," in captured["args"][0][1]
    flags = int(captured["kwargs"].get("creationflags", 0))
    assert flags & getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_open_in_folder_windows_dir_uses_startfile(monkeypatch, tmp_path):
    """Windows 打开目录：os.startfile（本身无 console）。"""
    monkeypatch.setattr(procutil, "_is_windows", lambda: True)
    started: list = []
    monkeypatch.setattr(os, "startfile", lambda path: started.append(path))
    ok = procutil.open_in_folder(str(tmp_path))
    assert ok is True
    assert started == [str(tmp_path)]


def test_open_in_folder_missing_path_returns_false():
    assert procutil.open_in_folder(os.path.join(os.sep, "no_such_path_xyz")) is False


def test_runtime_spawn_keeps_detached_structure(tmp_path, monkeypatch):
    """Runtime spawn 仍走 DETACHED_PROCESS（console-less），不被破坏。"""
    import subprocess

    from services import production_runtime as pr

    captured: dict = {}

    class _FakeProc:
        pid = 424242

    def _fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(pr, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_RUNTIME_MODE", "process")
    monkeypatch.setattr(
        pr.ProductionRuntimeClient, "_resolve_runtime_launch",
        classmethod(lambda cls: (["py", "-m", "services.production_runtime", "--serve"], {})),
    )
    monkeypatch.setattr(pr, "_open_bootstrap_log", lambda: None)
    monkeypatch.setattr(pr.ProcessFileLock, "acquire", lambda self, blocking: True)
    monkeypatch.setattr(pr.ProcessFileLock, "release", lambda self: None)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(pr, "_RUNTIME_PROCESS", None)

    pr.ProductionRuntimeClient.ensure_running()
    flags = int(captured["kwargs"].get("creationflags", 0))
    detached = int(getattr(subprocess, "DETACHED_PROCESS", 0))
    assert flags & detached, "Windows runtime spawn must stay console-less"
    assert captured["kwargs"].get("stdin") == subprocess.DEVNULL


def test_ffmpeg_pipeline_uses_run_no_window():
    """ffmpeg/ffprobe 调用点统一走 run_no_window（源码级断言）。"""
    import inspect

    import lib.audio_pipeline as ap
    import lib.metadata as md
    import lib.postprocess as pp

    for module in (ap, md, pp):
        source = inspect.getsource(module)
        assert "run_no_window(" in source
        assert "from .procutil import run_no_window" in source
