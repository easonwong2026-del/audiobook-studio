"""单元测试：lib/postprocess.py（D1 响度归一 + D3 人声均衡）

全部 CPU 真跑（numpy + pyloudnorm），不加载 TTS 模型、不依赖 ffmpeg。
"""
import os
import sys
import tempfile

import numpy as np
from scipy.io import wavfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import postprocess  # noqa: E402


def _write_tone(path, rate=44100, freq=200.0, dur=3.0, amp=0.3):
    """写一个 int16 正弦 WAV，返回 (rate, 采样数)。"""
    n = int(rate * dur)
    t = np.linspace(0.0, dur, n, endpoint=False)
    sig = amp * np.sin(2.0 * np.pi * freq * t)
    data = (sig * 32767.0).astype(np.int16)
    wavfile.write(path, rate, data)
    return rate, n


def _measure_lufs(path):
    """用 pyloudnorm 量测 WAV 的集成响度（LUFS）。"""
    import pyloudnorm as pyln
    rate, data = wavfile.read(path)
    if np.issubdtype(data.dtype, np.integer):
        audio = data.astype(np.float64) / 32768.0
    else:
        audio = data.astype(np.float64)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    meter = pyln.Meter(rate)
    return meter.integrated_loudness(audio)


def test_normalize_loudness_to_target(tmp_path):
    p = str(tmp_path / "tone.wav")
    _write_tone(p, rate=44100, freq=200.0, dur=3.0, amp=0.15)
    postprocess.normalize_loudness(p, target_lufs=-16.0)
    loud = _measure_lufs(p)
    assert abs(loud - (-16.0)) <= 1.0, f"归一后响度 {loud:.2f} LU 应落在 -16±1"


def test_normalize_loudness_custom_target(tmp_path):
    p = str(tmp_path / "tone.wav")
    _write_tone(p, rate=44100, freq=330.0, dur=2.5, amp=0.25)
    postprocess.normalize_loudness(p, target_lufs=-20.0)
    loud = _measure_lufs(p)
    assert abs(loud - (-20.0)) <= 1.0, f"归一后响度 {loud:.2f} LU 应落在 -20±1"


def test_normalize_loudness_is_idempotent(tmp_path):
    """对已是 -16 LU 的音频再归一，应基本不变（容差内）。"""
    p = str(tmp_path / "tone.wav")
    _write_tone(p, rate=44100, freq=200.0, dur=3.0, amp=0.15)
    postprocess.normalize_loudness(p, target_lufs=-16.0)
    first = _measure_lufs(p)
    postprocess.normalize_loudness(p, target_lufs=-16.0)
    second = _measure_lufs(p)
    assert abs(first - second) <= 1.0, f"重复归一应稳定：{first:.2f} -> {second:.2f}"


def test_apply_eq_disabled_is_noop(tmp_path):
    import shutil
    p = str(tmp_path / "eq.wav")
    _write_tone(p, rate=44100, freq=200.0, dur=2.0, amp=0.4)
    p_off = str(tmp_path / "eq_off.wav")
    shutil.copy(p, p_off)
    postprocess.apply_eq(p_off, enable=False)
    r1, d1 = wavfile.read(p_off)
    r0, d0 = wavfile.read(p)
    assert np.array_equal(d1, d0), "enable=False 时音频不应有任何改变"


def test_apply_eq_enabled_attenuates_high_freq(tmp_path):
    p = str(tmp_path / "eq.wav")
    rate = 44100
    dur = 2.0
    n = int(rate * dur)
    t = np.linspace(0.0, dur, n, endpoint=False)
    # 低频 200Hz + 高频 15000Hz 混合
    sig = (0.35 * np.sin(2.0 * np.pi * 200.0 * t)
           + 0.35 * np.sin(2.0 * np.pi * 15000.0 * t))
    wavfile.write(p, rate, (sig * 32767.0).astype(np.int16))

    postprocess.apply_eq(p, enable=True)
    r, d = wavfile.read(p)
    if np.issubdtype(d.dtype, np.integer):
        after = d.astype(np.float64)
    else:
        after = d.astype(np.float64)
    before = (sig * 32767.0).astype(np.int16).astype(np.float64)

    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    spec_before = np.abs(np.fft.rfft(before))
    spec_after = np.abs(np.fft.rfft(after))
    hi = freqs > 8000.0
    before_hi = spec_before[hi].mean()
    after_hi = spec_after[hi].mean()
    assert after_hi < before_hi * 0.9, "开启均衡后高频段能量应明显下降（低通补偿生效）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
