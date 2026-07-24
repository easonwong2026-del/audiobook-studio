"""工作流测试：音频导出（假 WAV 合成，§10.4）

验证：
  1. ``audio_pipeline.concatenate_normalized()`` 混合采样率归一化拼接
  2. ``audio_pipeline.export_book()`` 导出 WAV 格式（无需 ffmpeg）

所有 WAV 使用标准库 wave + struct 生成，无 ffmpeg / GPU 依赖。
"""
import sys
import os
import json
import struct
import wave
import numpy as np

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import audio_pipeline as ap  # noqa: E402
from lib import audio_format as af  # noqa: E402
from lib import segment_cache  # noqa: E402


# ── 假音频生成 ────────────────────────────────────────────────────────────────

def _make_fake_wav(path: str, sample_rate: int = 22050, duration: float = 0.5,
                   amplitude: int = 5000):
    """生成指定采样率、时长、振幅的 WAV 文件（非静音，便于检测内容）。"""
    n_samples = int(sample_rate * duration)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 生成一个恒定振幅的方波（简单可检测）
    samples = [amplitude if i % 2 == 0 else -amplitude for i in range(n_samples)]
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *samples))


# ── 测试用例 ──────────────────────────────────────────────────────────────────


class TestExportMixedAudio:
    """音频导出工作流测试。"""

    def test_concatenate_normalized_mixed_rates(self, tmp_path):
        """混合采样率（22050 + 44100）能正常归一化拼接。"""
        wav1 = str(tmp_path / "seg1.wav")
        wav2 = str(tmp_path / "seg2.wav")

        # 各采样率 0.3 秒
        _make_fake_wav(wav1, sample_rate=22050, duration=0.3, amplitude=5000)
        _make_fake_wav(wav2, sample_rate=44100, duration=0.3, amplitude=8000)

        # 以 22050 为目标采样率拼接
        data, rate, channels = af.concatenate_normalized(
            [wav1, wav2], target_rate=22050, target_channels=1, target_dtype=np.int16
        )

        # 验证输出
        assert rate == 22050
        assert channels == 1
        assert data.dtype == np.int16
        # 两段各 0.3s * 22050 = 6615 采样点
        expected_len = int(0.3 * 22050) + int(0.3 * 22050)  # 重采样后长度近似
        assert len(data) > 10000, f"拼接后数据太短: {len(data)}"
        # 内容非空（非全零）
        assert np.max(np.abs(data)) > 0, "拼接后数据全零"

    def test_concatenate_normalized_homogeneous_rates(self, tmp_path):
        """同采样率拼接应保持采样率不变。"""
        wav1 = str(tmp_path / "a.wav")
        wav2 = str(tmp_path / "b.wav")
        _make_fake_wav(wav1, sample_rate=44100, duration=0.2, amplitude=3000)
        _make_fake_wav(wav2, sample_rate=44100, duration=0.2, amplitude=6000)

        data, rate, channels = af.concatenate_normalized(
            [wav1, wav2], target_rate=44100, target_channels=1, target_dtype=np.int16
        )
        assert rate == 44100
        assert len(data) > 0

    def test_concatenate_normalized_empty_list_raises(self, tmp_path):
        """空路径列表应抛 ValueError。"""
        with pytest.raises(ValueError):
            af.concatenate_normalized([])

    def test_export_book_wav_format(self, tmp_path):
        """export_book WAV 格式：验证导出文件存在且非空。"""
        proj_dir = tmp_path / "export_book"
        os.makedirs(proj_dir / "segments")
        os.makedirs(proj_dir / "output")

        script = {
            "meta": {"title": "导出测试"},
            "voices": {"旁白": {"description": "x"}},
            "chapters": [
                {
                    "id": 1, "title": "第一章",
                    "segments": [
                        {"id": "s1", "role": "旁白", "text": "第一句。", "emotion": "neutral"},
                        {"id": "s2", "role": "旁白", "text": "第二句。", "emotion": "neutral"},
                    ],
                }
            ],
        }
        script_path = proj_dir / "structured_script.json"
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)

        # 写入参数感知缓存键的 WAV 文件（legacy 裸名也可用）
        seg_dir = str(proj_dir / "segments")
        for seg_id in ("s1", "s2"):
            # 使用旧版裸文件名（_find_segment 兼容回退）
            wav_path = os.path.join(seg_dir, f"{seg_id}.wav")
            _make_fake_wav(wav_path, sample_rate=22050, duration=0.2)

        # 导出 WAV 格式（不需 ffmpeg）
        out = ap.export_book(str(proj_dir), format="wav")
        assert os.path.isfile(out), f"导出文件不存在: {out}"
        assert os.path.getsize(out) > 44, f"导出文件太小（仅 WAV 头）: {out}"

        # 验证输出目录中存在文件
        assert out.startswith(str(proj_dir / "output")), f"导出不在 output 目录: {out}"
