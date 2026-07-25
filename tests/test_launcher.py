"""Verification tests for the audiobook-studio launcher UX change.

These tests confirm the launcher's printing order and that it truly invokes
``app.py``, WITHOUT loading the IndexTTS2 model or running the real
``start.bat`` / ``launcher.py`` flow. All external process execution is
stubbed via ``unittest.mock.patch("subprocess.run")``.
"""

import contextlib
import io
import os
import subprocess
import sys
from unittest import mock

import pytest

# Make the project root (where launcher.py lives) importable regardless of the
# current working directory pytest is launched from.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import launcher  # noqa: E402  (import after sys.path is prepared)


def _make_fake_run():
    """Return a fake for subprocess.run that records every call.

    The fake returns a successful ``CompletedProcess`` so the launcher's
    dependency check never triggers a real ``pip install``, and the app
    launch never actually executes ``app.py``.
    """
    calls = []

    def _fake_run(*args, **kwargs):
        calls.append(mock.call(*args, **kwargs))
        cmd = list(args[0]) if args else []
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    _fake_run.calls = calls
    return _fake_run


def test_launcher_print_order_and_app_start():
    """Dynamic check: print order + real app.py invocation via mocked subprocess."""
    fake_run = _make_fake_run()
    buffer = io.StringIO()
    saved_cwd = os.getcwd()

    try:
        with mock.patch("subprocess.run", side_effect=fake_run):
            with contextlib.redirect_stdout(buffer):
                launcher.main()
    finally:
        # launcher.main() calls os.chdir(APP_DIR); restore to avoid leaking cwd.
        os.chdir(saved_cwd)

    output = buffer.getvalue()

    # 1) Required content is present.
    assert "正在检查运行环境，请稍候..." in output
    assert "有声书合成工作台" in output
    assert "正在加载语音合成引擎，首次约 10-30 秒..." in output

    # 2) Order is correct:
    #    检查运行环境 < 横幅(有声书合成工作台) < 加载语音合成引擎
    pos_check = output.index("正在检查运行环境，请稍候...")
    pos_banner = output.index("有声书合成工作台")
    pos_engine = output.index("正在加载语音合成引擎，首次约 10-30 秒...")
    assert pos_check < pos_banner < pos_engine

    # 3) The launcher really invoked app.py (not just printed).
    app_calls = [c for c in fake_run.calls if "app.py" in c.args[0]]
    assert app_calls, "launcher did not invoke 'app.py' (subprocess.run call missing)"

    # Sanity: dependency check ran before app launch (first call is the
    # "import gradio" check, second is the app.py start).
    assert len(fake_run.calls) >= 2
    assert "app.py" in fake_run.calls[-1].args[0]


def test_launcher_dependency_check_runs_first():
    """The dependency check (import gradio) must happen before launching app."""
    fake_run = _make_fake_run()
    buffer = io.StringIO()
    saved_cwd = os.getcwd()

    try:
        with mock.patch("subprocess.run", side_effect=fake_run):
            with contextlib.redirect_stdout(buffer):
                launcher.main()
    finally:
        os.chdir(saved_cwd)

    assert fake_run.calls, "no subprocess.run calls were recorded"
    first_call_cmd = fake_run.calls[0].args[0]
    assert "import gradio" in first_call_cmd, "first call should be the gradio dependency check"


def test_start_bat_echo_before_launcher():
    """New contract (root-cause fix):

    1. start.bat MUST be 100% ASCII (no Chinese bytes at all) so it can never
       be mis-read as GBK/garbage regardless of the console code page.
    2. The Chinese "启动中" banner is emitted by launcher.py (Python's
       WriteConsoleW output is code-page independent), printed BEFORE the
       "正在检查运行环境" line.
    """
    bat_path = os.path.join(_PROJECT_ROOT, "start.bat")
    with open(bat_path, "rb") as fh:
        bat_data = fh.read()

    # (1) Root-cause guard: zero non-ASCII bytes in start.bat.
    assert all(b < 128 for b in bat_data), (
        "start.bat still contains non-ASCII bytes (Chinese) which can be "
        "mis-read as garbage by cmd under an unstable code page."
    )

    # start.bat must carry the REM comment (English, ASCII) + the python launcher line.
    bat_text = bat_data.decode("ascii")
    assert "Audiobook Studio launcher" in bat_text
    assert "launcher.py" in bat_text


def test_launcher_prints_startup_banner_before_env_check():
    """The Chinese 启动中 banner is printed by Python BEFORE 正在检查运行环境.

    Uses the same mock-subprocess + redirect_stdout pattern as the other
    launcher tests, so it never loads IndexTTS2 or runs the real flow.
    """
    fake_run = _make_fake_run()
    buffer = io.StringIO()
    saved_cwd = os.getcwd()

    try:
        with mock.patch("subprocess.run", side_effect=fake_run):
            with contextlib.redirect_stdout(buffer):
                launcher.main()
    finally:
        os.chdir(saved_cwd)

    output = buffer.getvalue()

    banner = "有声书工作台启动中，请稍后..."
    env_check = "正在检查运行环境，请稍候..."

    assert banner in output, "launcher did not print the Chinese 启动中 banner"
    assert env_check in output, "launcher did not print the 正在检查运行环境 line"

    # 启动中 must appear BEFORE 正在检查运行环境.
    pos_banner = output.index(banner)
    pos_env = output.index(env_check)
    assert pos_banner < pos_env, (
        "the Chinese 启动中 banner must be printed BEFORE 正在检查运行环境"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
