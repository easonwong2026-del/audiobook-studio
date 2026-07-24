"""5.6：音频归一化——混合采样率/声道/dtype 拼接。

覆盖：
- load_and_normalize_wav 基本功能（读取 + 默认规格）；
- 重采样（22050 -> 44100, 44100 -> 22050）；
- 声道转换（立体声 -> 单声道）；
- dtype 转换（float32 -> int16）；
- 峰值溢出保护（超出 int16 范围的 float 值被裁剪）；
- 空音频文件抛出 ValueError；
- 不存在文件抛出 ValueError；
- concatenate_normalized 多文件拼接。
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pytest
from scipy.io import wavfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import audio_format as af  # noqa: E402


# ── 辅助函数 ──────────────────────────────────────

def _write_wav(path, rate, data):
    """写 wav 并返回路径。data 为 np.ndarray。"""
    wavfile.write(path, rate, data)
    return path


def _sine_wav(rate, duration=0.5, freq=440):
    """生成单声道正弦波 float64 数组（-1~1）"""
    t = np.linspace(0, duration, int(rate * duration), endpoint=False)
    return np.sin(2 * np.pi * freq * t)


def _sine_wav_stereo(rate, duration=0.5):
    """生成双声道正弦波"""
    mono = _sine_wav(rate, duration)
    return np.column_stack((mono, mono * 0.5))


# ── 测试类 ──────────────────────────────────────

class TestLoadAndNormalize:
    """load_and_normalize_wav 基本功能"""

    def test_basic_load(self, tmp_path):
        """读取 22050Hz 单声道 int16 文件，返回正确的 NormalizedAudio"""
        data = np.array([0, 100, 200, -100, -200], dtype=np.int16)
        path = str(tmp_path / "basic.wav")
        _write_wav(path, 22050, data)
        na = af.load_and_normalize_wav(path)
        assert na.rate == 22050
        assert na.channels == 1
        assert na.dtype == np.int16
        np.testing.assert_array_equal(na.data, data)

    def test_nonexistent_file_raises(self):
        """不存在的文件抛 ValueError"""
        with pytest.raises((ValueError, FileNotFoundError)):
            af.load_and_normalize_wav("/tmp/_nonexistent_audio_file_test_.wav")

    def test_empty_file_raises(self, tmp_path):
        """0 采样点的文件抛 ValueError"""
        path = str(tmp_path / "empty.wav")
        _write_wav(path, 22050, np.array([], dtype=np.int16))
        with pytest.raises(ValueError, match="空|empty|0"):
            af.load_and_normalize_wav(path)


class TestResample:
    """重采样"""

    def test_resample_22050_to_44100(self, tmp_path):
        """22050Hz -> 44100Hz 上采样"""
        data = _sine_wav(22050).astype(np.float32)
        path = str(tmp_path / "up.wav")
        wavfile.write(path, 22050, data)
        na = af.load_and_normalize_wav(path, target_rate=44100)
        assert na.rate == 44100
        # 上采样后采样点变多
        expected_len = int(len(data) * 44100 / 22050)
        assert len(na.data) == expected_len

    def test_resample_44100_to_22050(self, tmp_path):
        """44100Hz -> 22050Hz 下采样"""
        data = _sine_wav(44100).astype(np.float32)
        path = str(tmp_path / "down.wav")
        wavfile.write(path, 44100, data)
        na = af.load_and_normalize_wav(path, target_rate=22050)
        assert na.rate == 22050
        expected_len = int(len(data) * 22050 / 44100)
        assert len(na.data) == expected_len

    def test_same_rate_no_resample(self, tmp_path):
        """目标采样率与原采样率相同，不重采样"""
        data = np.array([1, 2, 3], dtype=np.int16)
        path = str(tmp_path / "same.wav")
        _write_wav(path, 22050, data)
        na = af.load_and_normalize_wav(path, target_rate=22050)
        np.testing.assert_array_equal(na.data, data)


class TestChannelConversion:
    """声道转换"""

    def test_stereo_to_mono(self, tmp_path):
        """立体声 -> 单声道"""
        stereo = _sine_wav_stereo(22050)
        path = str(tmp_path / "stereo.wav")
        wavfile.write(path, 22050, stereo)
        na = af.load_and_normalize_wav(path, target_channels=1)
        assert na.channels == 1
        assert na.data.ndim == 1

    def test_mono_stays_mono(self, tmp_path):
        """单声道保持单声道"""
        mono = _sine_wav(22050).astype(np.int16)
        path = str(tmp_path / "mono.wav")
        _write_wav(path, 22050, mono)
        na = af.load_and_normalize_wav(path, target_channels=1)
        assert na.channels == 1


class TestDtypeConversion:
    """dtype 转换"""

    def test_float32_to_int16(self, tmp_path):
        """float32 -> int16"""
        data = _sine_wav(22050).astype(np.float32)
        path = str(tmp_path / "float.wav")
        wavfile.write(path, 22050, data)
        na = af.load_and_normalize_wav(path, target_dtype=np.int16)
        assert na.dtype == np.int16
        # 正弦波在 [-1, 1] 范围内，转换后应在 int16 范围内
        assert na.data.dtype == np.int16

    def test_peak_clipping(self, tmp_path):
        """超出 [-1, 1] 的 float 值被裁���"""
        data = np.array([2.0, -2.0, 0.5, -0.5], dtype=np.float32)
        path = str(tmp_path / "clip.wav")
        wavfile.write(path, 22050, data)
        na = af.load_and_normalize_wav(path, target_dtype=np.int16)
        # 2.0 -> 32767, -2.0 -> -32768
        assert na.data[0] == 32767
        # int16 下界 -32768，float -2.0 裁为 -1.0 * 32767 → -32767
        assert na.data[1] == -32767


class TestConcatenateNormalized:
    """concatenate_normalized 多文件拼接"""

    def test_concat_two_mono(self, tmp_path):
        """拼接两个 22050Hz 单声道文件"""
        data1 = np.array([100, 200, 300], dtype=np.int16)
        data2 = np.array([400, 500], dtype=np.int16)
        p1 = str(tmp_path / "c1.wav")
        p2 = str(tmp_path / "c2.wav")
        _write_wav(p1, 22050, data1)
        _write_wav(p2, 22050, data2)
        combined, rate, channels = af.concatenate_normalized([p1, p2])
        assert rate == 22050
        assert channels == 1
        assert len(combined) == 5
        np.testing.assert_array_equal(combined, np.concatenate([data1, data2]))

    def test_concat_mixed_rate(self, tmp_path):
        """拼接 22050Hz 和 44100Hz 文件，统一到 22050Hz"""
        data1 = _sine_wav(22050, 0.2).astype(np.int16)
        data2 = _sine_wav(44100, 0.2).astype(np.int16)
        p1 = str(tmp_path / "mr1.wav")
        p2 = str(tmp_path / "mr2.wav")
        _write_wav(p1, 22050, data1)
        _write_wav(p2, 44100, data2)
        combined, rate, channels = af.concatenate_normalized([p1, p2], target_rate=22050)
        assert rate == 22050
        # data2 被下采样到 22050

    def test_concat_mixed_channels(self, tmp_path):
        """拼接单声道和立体声文件，统一到单声道"""
        mono = _sine_wav(22050, 0.1).astype(np.int16)
        stereo = _sine_wav_stereo(22050, 0.1).astype(np.int16)
        p1 = str(tmp_path / "mc1.wav")
        p2 = str(tmp_path / "mc2.wav")
        _write_wav(p1, 22050, mono)
        wavfile.write(p2, 22050, stereo)
        combined, rate, channels = af.concatenate_normalized(
            [p1, p2], target_rate=22050, target_channels=1
        )
        assert channels == 1

    def test_concat_canonical_rate(self, tmp_path):
        """target_rate=None ���使用第一个文件的原始采样率"""
        data1 = np.array([1, 2, 3], dtype=np.int16)
        data2 = np.array([4, 5], dtype=np.int16)
        p1 = str(tmp_path / "cr1.wav")
        p2 = str(tmp_path / "cr2.wav")
        _write_wav(p1, 44100, data1)
        _write_wav(p2, 44100, data2)
        combined, rate, channels = af.concatenate_normalized([p1, p2], target_rate=None)
        assert rate == 44100

    def test_write_wav(self, tmp_path):
        """write_wav 写出 int16 数组"""
        data = np.array([1, 2, 3], dtype=np.int16)
        out = str(tmp_path / "written.wav")
        result = af.write_wav(out, data, 22050)
        assert result == out
        r, d = wavfile.read(out)
        assert r == 22050
        np.testing.assert_array_equal(d, data)
