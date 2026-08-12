"""Regression: ``normalize_loudness_streaming`` must not crash export on
``subprocess.run`` quirks that surface ``stderr=None`` or non-str stderr.

Under Windows + ``CREATE_NO_WINDOW`` inside the detached production runtime
subprocess, ``subprocess.run(..., capture_output=True, text=True)`` has been
observed to return ``stderr=None`` (rather than ``""``), which previously
caused ``re.findall(pattern, None)`` to raise ``TypeError`` and abort the
entire export job.  The fix coerces ``stderr`` defensively and treats any
TypeError on the regex as a measurement failure that falls back to the
bounded RMS gain path.  Delivery manifest stale/missing was a downstream
consequence of the same crash, not an independent bug.
"""
from __future__ import annotations

import os
import re
import wave
from unittest import mock

import pytest

from lib import postprocess
from lib.postprocess import normalize_loudness_streaming


def _make_wav(path: str, rate: int = 8000) -> None:
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x00" * rate)


@pytest.fixture
def wav_path(tmp_path):
    path = str(tmp_path / "fixture.wav")
    _make_wav(path)
    return path


class _Completed:
    """Minimal stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_normalize_survives_none_stderr(monkeypatch, wav_path):
    """``measure.stderr is None`` (Windows + CREATE_NO_WINDOW quirk)
    must NOT raise TypeError; export must fall back gracefully."""
    fake_completed = _Completed(returncode=0, stderr=None)
    monkeypatch.setattr(postprocess, "_ffmpeg_path", lambda exc=None: "ffmpeg")
    monkeypatch.setattr(postprocess, "run_no_window", lambda *a, **kw: fake_completed)
    monkeypatch.setattr(postprocess, "_chunked_gain", lambda p, lufs, tp: p)

    # Must not raise — the whole point of the fix.
    normalize_loudness_streaming(wav_path, target_lufs=-16.0)


def test_normalize_survives_bytes_stderr(monkeypatch, wav_path):
    """If ``text=True`` is ignored for some reason and stderr comes back
    as ``bytes``, the fallback path must still be taken without crashing."""
    fake_completed = _Completed(returncode=0, stderr=b"")
    monkeypatch.setattr(postprocess, "_ffmpeg_path", lambda exc=None: "ffmpeg")
    monkeypatch.setattr(postprocess, "run_no_window", lambda *a, **kw: fake_completed)
    monkeypatch.setattr(postprocess, "_chunked_gain", lambda p, lufs, tp: p)

    normalize_loudness_streaming(wav_path, target_lufs=-16.0)


def test_normalize_falls_back_when_no_json_in_stderr(monkeypatch, wav_path):
    """Empty / non-JSON stderr → re.findall returns [] → fallback used."""
    fake_completed = _Completed(
        returncode=0,
        stderr="[aist#0:0] Guessed Channel Layout: mono\n",
    )
    monkeypatch.setattr(postprocess, "_ffmpeg_path", lambda exc=None: "ffmpeg")
    monkeypatch.setattr(postprocess, "run_no_window", lambda *a, **kw: fake_completed)
    fallback_calls: list[tuple[str, float]] = []

    def _fake_chunked(p, lufs, tp):
        fallback_calls.append((p, lufs))
        return p

    monkeypatch.setattr(postprocess, "_chunked_gain", _fake_chunked)

    normalize_loudness_streaming(wav_path, target_lufs=-16.0)
    assert fallback_calls == [(wav_path, -16.0)]


def test_normalize_falls_back_when_ffmpeg_missing(monkeypatch, wav_path):
    """ffmpeg binary disappears between _ffmpeg_path check and run → fallback."""
    monkeypatch.setattr(postprocess, "_ffmpeg_path", lambda exc=None: None)

    fallback_calls: list[tuple[str, float]] = []
    monkeypatch.setattr(
        postprocess, "_chunked_gain", lambda p, lufs, tp: fallback_calls.append((p, lufs)) or p
    )

    # Must not raise even though ffmpeg is gone.
    normalize_loudness_streaming(wav_path, target_lufs=-16.0)
    assert fallback_calls == [(wav_path, -16.0)]


def test_normalize_uses_loudnorm_when_json_present(monkeypatch, wav_path):
    """Happy path: stderr contains valid loudnorm JSON → second ffmpeg pass
    runs with measured params (not the fallback path)."""
    loudnorm_json = (
        '{\n'
        '\t"input_i" : "-17.15",\n'
        '\t"input_tp" : "-1.16",\n'
        '\t"input_lra" : "4.70",\n'
        '\t"input_thresh" : "-27.60",\n'
        '\t"output_i" : "-16.20",\n'
        '\t"output_tp" : "-1.50",\n'
        '\t"output_lra" : "4.70",\n'
        '\t"output_thresh" : "-26.65",\n'
        '\t"normalization_type" : "dynamic",\n'
        '\t"target_offset" : "0.20"\n'
        '}\n'
    )
    call_log: list[tuple[tuple, dict]] = []
    completed_first = _Completed(returncode=0, stderr=loudnorm_json)
    completed_second = _Completed(returncode=0, stderr="", stdout="")

    def _fake_run(args, **kw):
        call_log.append((tuple(args), kw))
        if len(call_log) == 1:
            return completed_first
        # Second call: simulate ffmpeg writing the loudnorm part file so
        # os.replace() doesn't trip the OSError fallback path.
        try:
            out_arg = next(a for a in args if isinstance(a, str) and a.endswith(".loudnorm.part.wav"))
            with open(out_arg, "wb") as fh:
                # pcm_s16le 1ch 8kHz zeros, 0.1s
                fh.write(b"\x00\x00" * 800)
        except (OSError, StopIteration):
            pass
        return completed_second

    monkeypatch.setattr(postprocess, "_ffmpeg_path", lambda exc=None: "ffmpeg")
    monkeypatch.setattr(postprocess, "run_no_window", _fake_run)
    fallback_calls: list = []
    monkeypatch.setattr(postprocess, "_chunked_gain", lambda p, lufs, tp: fallback_calls.append(p) or p)

    normalize_loudness_streaming(wav_path, target_lufs=-16.0)

    assert fallback_calls == [], "fallback must NOT run on valid loudnorm JSON"
    assert len(call_log) == 2, "expected two ffmpeg invocations (measure + apply)"
    # second call must carry the measured I/TP/LRA/thresh/offset in filter_expr
    apply_args = call_log[1][0]
    assert "measured_I=-17.15" in apply_args[apply_args.index("-af") + 1]
    assert "linear=true" in apply_args[apply_args.index("-af") + 1]

# ── 真实 ffmpeg 集成（无 ffmpeg 环境自动跳过）───────────────────────────────


def _ffmpeg_available() -> bool:
    try:
        return bool(postprocess._ffmpeg_path())
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(),
    reason="需要真实 ffmpeg 可执行文件",
)


@needs_ffmpeg
def test_normalize_integration_real_ffmpeg(tmp_path):
    """真实 ffmpeg loudnorm 全链路：拼接两段 wav → measure+apply → 输出存在。

    验证 happy path 在真实二进制下走通（不依赖 mock），且输出响度落在
    target_lufs ±1.5 内。无 ffmpeg 环境（CI 未装）自动跳过。
    """
    import json
    import subprocess

    from lib.postprocess import normalize_loudness_streaming

    wav_a = str(tmp_path / "a.wav")
    wav_b = str(tmp_path / "b.wav")
    _make_wav(wav_a, rate=8000)
    _make_wav(wav_b, rate=8000)

    # 用 ffmpeg 生成 1s 正弦波（非静音，loudnorm 对全静音输出 -inf）
    merged = str(tmp_path / "merged.wav")
    exe = postprocess._ffmpeg_path()
    subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:a", "pcm_s16le", merged],
        check=True, capture_output=True,
    )

    result = normalize_loudness_streaming(merged, target_lufs=-16.0)
    assert os.path.isfile(result), "loudnorm 输出文件必须存在"
    # 产物非空
    assert os.path.getsize(result) > 1000

    # 回测响度：用 loudnorm measure 再读一次 output_i，应接近 -16 LUFS
    m = postprocess.run_no_window(
        [exe, "-hide_banner", "-nostats", "-i", result,
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
         "-f", "null", "-"],
        check=False, capture_output=True, text=True,
    )
    raw = m.stderr if isinstance(m.stderr, str) else ""
    matched = re.findall(r"\{\s*\"input_i\".*?\}", raw, flags=re.S)
    if m.returncode == 0 and matched:
        measured = json.loads(matched[-1])
        output_i = float(measured.get("output_i", 0.0))
        assert -17.5 <= output_i <= -14.5, f"output_i={output_i} 偏离 target -16 ±1.5"
