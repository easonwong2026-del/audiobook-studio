"""Regression tests for the start.bat encoding fix.

Background
----------
The root cause: a ``.bat`` file can never be guaranteed to show Chinese under an
unstable console code page. The fix is to make ``start.bat`` contain **no
non-ASCII characters at all**, and move all Chinese output into ``launcher.py``
(Python 3 prints via the console wide-char API ``WriteConsoleW``, which is
code-page independent and never garbles).

The interpreter detection logic is also centralized in ``launcher.py`` (with
three-tier fallback: env var > sibling index-tts/.venv > PATH). ``start.bat``
only delegates to ``launcher.py`` via the system ``python`` on PATH.

This module statically verifies the on-disk bytes of ``start.bat``. It NEVER
runs start.bat / launcher.py / app.py.

Assertions
----------
1. Every byte of start.bat is ASCII (ord(b) < 128) -> zero Chinese/garbage risk.
2. ``chcp 65001`` is still present (keeps child env UTF-8).
3. The ``launcher.py`` invocation line is present (with %~dp0 relative path).
4. Interpreter detection is centralized in launcher.py (not duplicated in .bat).
5. The last meaningful line is still ``pause``.
"""

from __future__ import annotations

import os

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BAT_PATH = os.path.join(_PROJECT_ROOT, "start.bat")


@pytest.fixture(scope="module")
def bat_bytes() -> bytes:
    with open(_BAT_PATH, "rb") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def bat_text(bat_bytes: bytes) -> str:
    return bat_bytes.decode("ascii")


def test_all_bytes_ascii(bat_bytes: bytes) -> None:
    """Root-cause guard: start.bat must contain zero non-ASCII bytes."""
    non_ascii = [b for b in bat_bytes if b >= 128]
    assert not non_ascii, (
        "start.bat contains non-ASCII bytes (%r); under an unstable console "
        "code page these can render as garbage." % non_ascii[:8]
    )


def test_no_utf8_bom(bat_bytes: bytes) -> None:
    assert bat_bytes[:3] != b"\xef\xbb\xbf", "start.bat must not have a UTF-8 BOM"


def test_chcp_65001_present(bat_text: str) -> None:
    assert "chcp 65001" in bat_text, "start.bat is missing the 'chcp 65001' line"


def test_launcher_invocation_present(bat_text: str) -> None:
    """start.bat must call launcher.py (interpreter detection is in launcher)."""
    assert "launcher.py" in bat_text, "launcher.py invocation missing"
    assert "%~dp0" in bat_text, "%~dp0 relative path required"


def test_no_venv_hardcode_in_start_bat(bat_text: str) -> None:
    """Interpreter detection must NOT be duplicated in start.bat."""
    forbidden = [
        ".venv",
        "AUDIOBOOK_STUDIO_PYTHON",
        "python.exe",
        "index-tts",
    ]
    for token in forbidden:
        assert token not in bat_text, (
            f"start.bat must not contain interpreter-detection logic ('{token}')"
        )


def test_pause_is_last_meaningful_line(bat_text: str) -> None:
    non_empty = [l for l in bat_text.splitlines() if l.strip()]
    assert non_empty, "start.bat is empty"
    assert non_empty[-1].strip() == "pause", "last meaningful line must be 'pause'"


def test_stop_bat_is_ascii_and_delegates_to_launcher() -> None:
    path = os.path.join(_PROJECT_ROOT, "stop.bat")
    with open(path, "rb") as fh:
        data = fh.read()
    text = data.decode("ascii")
    assert "launcher.py" in text
    assert "--stop" in text
    assert text.splitlines()[-1].strip() == "pause"
