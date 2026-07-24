"""Regression tests for the start.bat encoding fix (round 3, root-cause fix).

Background
----------
Two earlier fixes failed on the user's Chinese Windows console:

* Round 1 saved start.bat as GBK while the console was UTF-8 -> garbage.
* Round 2 saved start.bat as UTF-8 + ``chcp 65001`` inside the .bat, but
  ``chcp`` only changes the code page for *subsequently started* processes,
  NOT for how cmd re-parses the *remaining lines* of the same .bat file. So
  the later Chinese ``echo`` line was still decoded with the old code page
  (936) and shown as garbage.

Root cause: a ``.bat`` file can never be guaranteed to show Chinese under an
unstable console code page. The fix is to make ``start.bat`` contain **no
non-ASCII characters at all**, and move the Chinese "启动中" banner into
``launcher.py`` (Python 3 prints via the console wide-char API
``WriteConsoleW``, which is code-page independent and never garbles).

This module statically verifies the on-disk bytes of ``start.bat``. It NEVER
runs start.bat / launcher.py / app.py.

Assertions
----------
1. Every byte of start.bat is ASCII (ord(b) < 128) -> zero Chinese/garbage risk.
2. ``chcp 65001`` is still present (harmless; keeps child env UTF-8).
3. The python venv launch line is present and uses relative path (%~dp0).
4. The ASCII ``echo Starting Audiobook Studio ...`` line is present.
5. The last meaningful line is still ``pause``.
"""

import os

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BAT_PATH = os.path.join(_PROJECT_ROOT, "start.bat")

# 5.7：去除硬编码路径后，start.bat 用 %~dp0 相对路径引用 venv，不绑定个人 PC 绝对路径。
PYTHON_RELATIVE_PATH = r"..\index-tts\.venv\Scripts\python.exe"


@pytest.fixture(scope="module")
def bat_bytes():
    with open(_BAT_PATH, "rb") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def bat_text(bat_bytes):
    # Decoding as ASCII IS the "all ASCII" assertion; a non-ASCII byte makes
    # the whole module fail fast.
    return bat_bytes.decode("ascii")


def test_all_bytes_ascii(bat_bytes):
    """Root-cause guard: start.bat must contain zero non-ASCII bytes."""
    non_ascii = [b for b in bat_bytes if b >= 128]
    assert not non_ascii, (
        "start.bat contains non-ASCII bytes (%r); under an unstable console "
        "code page these can render as garbage." % non_ascii[:8]
    )


def test_no_utf8_bom(bat_bytes):
    # A BOM is non-ASCII, so this is already covered by test_all_bytes_ascii,
    # but we keep it explicit for clarity.
    assert bat_bytes[:3] != b"\xef\xbb\xbf", "start.bat must not have a UTF-8 BOM"


def test_chcp_65001_present(bat_text):
    assert "chcp 65001" in bat_text, "start.bat is missing the 'chcp 65001' line"


def test_python_launch_line_present(bat_text):
    assert PYTHON_RELATIVE_PATH in bat_text, "python venv relative path missing"
    assert "launcher.py" in bat_text, "launcher.py invocation missing"


def test_ascii_echo_present(bat_text):
    assert "echo Starting Audiobook Studio ..." in bat_text, (
        "the ASCII echo line (instant English feedback on double-click) is missing"
    )


def test_pause_is_last_meaningful_line(bat_text):
    non_empty = [l for l in bat_text.splitlines() if l.strip()]
    assert non_empty, "start.bat is empty"
    assert non_empty[-1].strip() == "pause", "last meaningful line must be 'pause'"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
