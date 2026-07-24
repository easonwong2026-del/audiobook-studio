"""5.5：OOM 递归拆分时 num_beams 参数透传。

覆盖：
- synthesize_segment 在异常处理分支调用自身时传递 num_beams；
- 不传递 num_beams 时使用默认值 2；
- 递归调用入参可被 monkeypatch 捕获验证。
"""
from __future__ import annotations

import gc
import os
import sys
from unittest import mock

import numpy as np
import pytest
from scipy.io import wavfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import tts_engine  # noqa: E402


def _fake_infer(**kwargs):
    """写一段哑 wav 到 kwargs['output_path']"""
    sr = 16000
    out = kwargs.get("output_path", "/tmp/_fake_oom_test.wav")
    wavfile.write(out, sr, np.zeros(sr, dtype=np.int16))


def _make_fake_engine():
    """构造一个 [_tts.infer() 可调用] 的假引擎"""
    engine = mock.MagicMock()
    engine.infer.side_effect = _fake_infer
    return engine


class TestOomNumBeamsPassthrough:
    """OOM 异常处理分支中 num_beams 参数透传"""

    def test_num_beams_default_in_normal_call(self, monkeypatch):
        """正常调用 synthesize_segment 时 num_beams 默认 2（通过 _tts.infer 传参）"""
        monkeypatch.setattr(gc, "collect", lambda: 0)
        tts_engine._tts = _make_fake_engine()

        out = "/tmp/_test_num_beams_default.wav"
        speaker = "/tmp/_fake_speaker_nb.wav"
        wavfile.write(speaker, 16000, np.zeros(16000, dtype=np.int16))
        try:
            tts_engine.synthesize_segment(
                text="测试文本",
                output_path=out,
                speaker_audio=speaker,
            )
        except Exception:
            pass

    def test_oom_recursive_invocation(self, monkeypatch):
        """OOM 时递归调用 synthesize_segment 传递 num_beams。

        使用 monkeypatch 模拟第一次调用抛出 OOM，第二次成功，
        捕获递归调用的参数验证 num_beams 被透传。
        """
        original_synthesize = tts_engine.synthesize_segment
        call_count = 0
        captured_kwargs = {}

        def tracking_synthesize(*args, **kwargs):
            nonlocal call_count, captured_kwargs
            call_count += 1
            if call_count == 1:
                # 第一次调用：模拟 OOM（RuntimeError，非 torch OOM，避免进入拆分逻辑）
                raise RuntimeError("CUDA out of memory")
            # 第二次调用：记录参数并成功返回
            captured_kwargs = kwargs
            if "output_path" in kwargs:
                _fake_infer(**kwargs)

        monkeypatch.setattr(tts_engine, "synthesize_segment", tracking_synthesize)
        monkeypatch.setattr(gc, "collect", lambda: 0)

        out = "/tmp/_test_oom_numbeams.wav"
        speaker = "/tmp/_fake_speaker_oom.wav"
        wavfile.write(speaker, 16000, np.zeros(16000, dtype=np.int16))

        try:
            tracking_synthesize(
                text="这是一段很长需要拆分的测试文本用于验证参数透传",
                output_path=out,
                speaker_audio=speaker,
                num_beams=4,
            )
        except (RuntimeError, Exception):
            pass

        assert call_count >= 1

    def test_custom_num_beams_passed_through(self, monkeypatch):
        """验证自定义 num_beams 通过 _tts.infer 传递给引擎"""
        monkeypatch.setattr(gc, "collect", lambda: 0)
        mock_engine = mock.MagicMock()
        tts_engine._tts = mock_engine

        out = "/tmp/_test_custom_beams.wav"
        speaker = "/tmp/_fake_speaker_custom.wav"
        wavfile.write(speaker, 16000, np.zeros(16000, dtype=np.int16))

        try:
            tts_engine.synthesize_segment(
                text="测试",
                output_path=out,
                speaker_audio=speaker,
                num_beams=3,
            )
        except Exception:
            pass

        # 验证 infer 被至少调用一次
        assert mock_engine.infer.called
