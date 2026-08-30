"""单元测试：lib/tts_engine.py

验证工程师修复的 BUG：
  - B1 (Critical): 长文本 OOM 自动拆段，递归调用改用关键字参数（emo_alpha / output_path 不再错位）
  - B2: speech_rate -> speed、pinyin_hints 按 IndexTTS2.infer 实际签名条件透传
  - B9: test_voice 调用 synthesize_segment 使用关键字参数，output_path 收到真实 .wav 路径

运行环境：用 index-tts 的 venv python（无 GPU），通过伪造 torch 与假引擎做单测。
"""
import sys
import os
import types

import numpy as np
from scipy.io import wavfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── 伪造 torch（tts_engine 仅在 synthesize_segment 内部 import torch，顶层不依赖）──
class FakeOOM(RuntimeError):
    pass


class _FakeTorchTensor:
    """仅用于兼容 scipy 1.18 的 torch-array 检测（is_torch_array）。

    scipy 在数组分发路径会做 ``_issubclass_fast(ndarray, torch.Tensor)``；
    若伪造的 torch 没有 ``Tensor`` 属性会在整个测试会话中污染所有 scipy 调用。
    这里给一个 numpy 数组不可能是其子类（也非 torch.Tensor 真身）的占位类。
    """
    pass


fake_torch = types.SimpleNamespace(
    cuda=types.SimpleNamespace(
        empty_cache=lambda: None,
        OutOfMemoryError=FakeOOM,
    ),
    Tensor=_FakeTorchTensor,
)
sys.modules.setdefault("torch", fake_torch)

import lib.tts_engine as tts_engine  # noqa: E402


def _write_dummy_wav(output_path, n=800):
    """写一个合法的 int16 wav：n 个采样点（全 0，时长 n/16000 秒）。"""
    wavfile.write(output_path, 16000, np.zeros(n, dtype=np.int16))


class FakeEngineSupported:
    """签名包含 speed / pinyin_hints 的假 IndexTTS2 引擎（新模型）。"""

    def __init__(self, oom_threshold=8, raise_on_oom=True):
        self.capture = []
        self.oom_threshold = oom_threshold
        self.raise_on_oom = raise_on_oom

    def infer(self, spk_audio_prompt, text, output_path, use_emo_text, emo_text,
              emo_alpha, max_text_tokens_per_segment, speed=1.0, pinyin_hints=None):
        self.capture.append(dict(
            spk_audio_prompt=spk_audio_prompt, text=text, output_path=output_path,
            use_emo_text=use_emo_text, emo_text=emo_text, emo_alpha=emo_alpha,
            max_text_tokens_per_segment=max_text_tokens_per_segment,
            speed=speed, pinyin_hints=pinyin_hints,
        ))
        if self.raise_on_oom and len(text) > self.oom_threshold:
            raise FakeOOM("CUDA out of memory (fake)")
        _write_dummy_wav(output_path)


class FakeEngineUnsupported:
    """签名不含 speed / pinyin_hints 的假 IndexTTS2 引擎（旧模型，向后兼容）。"""

    def __init__(self, oom_threshold=8, raise_on_oom=True):
        self.capture = []
        self.oom_threshold = oom_threshold
        self.raise_on_oom = raise_on_oom

    def infer(self, spk_audio_prompt, text, output_path, use_emo_text, emo_text,
              emo_alpha, max_text_tokens_per_segment):
        self.capture.append(dict(
            spk_audio_prompt=spk_audio_prompt, text=text, output_path=output_path,
            use_emo_text=use_emo_text, emo_text=emo_text, emo_alpha=emo_alpha,
            max_text_tokens_per_segment=max_text_tokens_per_segment,
        ))
        if self.raise_on_oom and len(text) > self.oom_threshold:
            raise FakeOOM("CUDA out of memory (fake)")
        _write_dummy_wav(output_path)


@pytest.fixture
def engine_reset():
    tts_engine._successful_segments_since_check = 0
    yield
    tts_engine._tts = None
    tts_engine._successful_segments_since_check = 0


class TestOOMSplit:
    """B1 Critical：长文本 OOM 自动拆成两半并拼接回原路径，临时文件清理。"""

    def test_oom_split(self, engine_reset, tmp_path, monkeypatch):
        eng = FakeEngineSupported()
        tts_engine._tts = eng
        cleanup_reasons = []
        monkeypatch.setattr(
            tts_engine,
            "empty_cache",
            lambda reason="manual": cleanup_reasons.append(reason),
        )

        out = str(tmp_path / "out.wav")
        # 12 个字符 > 阈值 8，触发 OOM；拆成两半各 6 字符（<=8）不再 OOM
        long_text = "x" * 12
        result = tts_engine.synthesize_segment(
            long_text,
            speaker_audio="x.wav",
            emotion="neutral",
            emo_alpha=1.0,
            output_path=out,
            speech_rate=1.0,
            pinyin_hints=None,
        )

        # 1) 返回原路径且文件存在、合法
        assert result == out
        assert os.path.isfile(out)
        rate, data = wavfile.read(out)
        assert data.dtype == np.int16

        # 2) 总时长 ≈ 两段 halves 之和（证明 _a/_b 被拼接回 output_path）
        #    每半段由假引擎写成 800 采样点，拼接后应为 1600
        assert len(data) == 1600, f"拼接后采样点应为 1600，实际 {len(data)}"

        # 3) 临时文件已清理
        path_a = out.replace(".wav", "_a.wav")
        path_b = out.replace(".wav", "_b.wav")
        assert not os.path.exists(path_a), "临时文件 out_a.wav 未清理"
        assert not os.path.exists(path_b), "临时文件 out_b.wav 未清理"

        # 4) 递归调用已用关键字参数：emo_alpha 仍是 float、output_path 仍是 str，
        #    全程没有因参数错位而抛 TypeError（能走到这里即证明未抛）
        for kw in eng.capture:
            assert isinstance(kw["emo_alpha"], float), "emo_alpha 不应被当成字符串"
            assert isinstance(kw["output_path"], str), "output_path 不应被当成整数"
        assert cleanup_reasons == ["oom", "oom"]


class TestSpeedPinyin:
    """B2：speech_rate / pinyin_hints 条件透传（新模型支持 / 旧模型不支持）。"""

    def test_speed_supported(self, engine_reset, tmp_path):
        eng = FakeEngineSupported()
        tts_engine._tts = eng

        out = str(tmp_path / "o.wav")
        tts_engine.synthesize_segment(
            "短句",
            speaker_audio="x.wav",
            emotion="happy",
            emo_alpha=0.8,
            output_path=out,
            speech_rate=1.25,
            pinyin_hints={"了": "le"},
        )
        assert len(eng.capture) == 1
        kw = eng.capture[0]
        assert kw["speed"] == 1.25
        assert kw["pinyin_hints"] == {"了": "le"}
        assert kw["emo_text"] == "happy"
        assert kw["use_emo_text"] is True
        assert kw["emo_alpha"] == 0.8

    def test_speed_unsupported(self, engine_reset, tmp_path):
        eng = FakeEngineUnsupported()
        tts_engine._tts = eng

        out = str(tmp_path / "o.wav")
        # 旧模型不支持 speed/pinyin_hints，应当不抛异常且 inspect 条件透传生效
        tts_engine.synthesize_segment(
            "短句",
            speaker_audio="x.wav",
            emotion="neutral",
            emo_alpha=1.0,
            output_path=out,
            speech_rate=1.3,
            pinyin_hints={"了": "le"},
        )
        assert len(eng.capture) == 1
        kw = eng.capture[0]
        # 关键：kwargs 中不含 speed / pinyin_hints，证明旧模型不会被多余参数坑崩
        assert "speed" not in kw, "旧模型不应透传 speed"
        assert "pinyin_hints" not in kw, "旧模型不应透传 pinyin_hints"


class TestTestVoiceKwargs:
    """B9：test_voice 内部对 synthesize_segment 的调用改用关键字参数。"""

    def test_test_voice_kwargs(self, monkeypatch):
        calls = []

        def stub(text, speaker_audio, emotion="neutral", emo_alpha=1.0,
                 speech_rate=1.0, output_path="", max_tokens=120, pinyin_hints=None):
            calls.append(dict(
                text=text, speaker_audio=speaker_audio, emotion=emotion,
                output_path=output_path, max_tokens=max_tokens,
            ))
            return output_path

        monkeypatch.setattr(tts_engine, "synthesize_segment", stub)
        outs = tts_engine.test_voice("x.wav")

        # 三句测试句
        assert len(calls) == 3
        assert len(outs) == 3
        for c in calls:
            # output_path 是合法 .wav 字符串路径，而非整数/字符串错位
            assert isinstance(c["output_path"], str)
            assert c["output_path"].endswith(".wav")
            assert c["speaker_audio"] == "x.wav"


class TestTestVoiceD4ThreeSentences:
    """D4 完善：test_voice 合成完整三句测试句（陈述 / 疑问 / 感叹三态）。

    用假引擎记录被合成的文本，验证三态测试句都被送入合成，而非只有第一句。
    """

    def test_three_sentences_synthesized(self, monkeypatch):
        texts = []

        def stub(text, speaker_audio, emotion="neutral", emo_alpha=1.0,
                 speech_rate=1.0, output_path="", max_tokens=120, pinyin_hints=None):
            texts.append(text)
            return output_path

        monkeypatch.setattr(tts_engine, "synthesize_segment", stub)
        outs = tts_engine.test_voice("x.wav")

        # 三句都被合成（D4 三态覆盖）
        assert len(outs) == 3, "test_voice 应返回三句测试句音频路径"
        assert len(texts) == 3, "test_voice 应合成三句测试句（D4 三态）"
        joined = "".join(texts)
        assert "今天天气真不错" in joined, "缺陈述句测试句"
        assert "你确定要这么做吗" in joined, "缺疑问句测试句"
        assert "太好了" in joined, "缺感叹句测试句"


class _VarKeywordOnlyEngine:
    """假引擎：仅接受 **generation_kwargs（VAR_KEYWORD），无显式 spk_embedding 形参。"""

    def __init__(self):
        self.captured = {}

    def infer(self, spk_audio_prompt, text, output_path, use_emo_text, emo_text,
              emo_alpha, max_text_tokens_per_segment, **kwargs):
        self.captured = kwargs
        _write_dummy_wav(output_path)


class _ExplicitEmbeddingEngine:
    """假引擎：显式暴露 spk_embedding 形参（未来引擎若支持时透传）。"""

    def __init__(self):
        self.captured = {}

    def infer(self, spk_audio_prompt, text, output_path, use_emo_text, emo_text,
              emo_alpha, max_text_tokens_per_segment, spk_embedding=None):
        self.captured = dict(spk_embedding=spk_embedding)
        _write_dummy_wav(output_path)


class TestSpeakerEmbeddingGating:
    """2.4 S-1 门控修正独立验证（QA 视角）。

    设计修正：embedding 透传门控从「显式形参 或 VAR_KEYWORD」改为
    「仅显式 spk_embedding 形参名」。若按旧口径（VAR_KEYWORD 即透传），
    spk_embedding 会经 **generation_kwargs 泄漏给下游 gpt，导致**真实推理崩溃**。
    本组测试钉死该修正：有 dummy embedding 时，也绝不走 VAR_KEYWORD 泄漏。
    """

    def test_embedding_not_leaked_via_var_keyword(self, engine_reset, monkeypatch, tmp_path):
        eng = _VarKeywordOnlyEngine()
        tts_engine._tts = eng
        tts_engine._SPEAKER_EMB_CACHE._store.clear()
        # 模拟引擎支持提取 → 返回 dummy embedding（spk_emb 非 None）
        monkeypatch.setattr(tts_engine, "_extract_speaker_embedding", lambda sa: object())
        out = str(tmp_path / "o.wav")
        tts_engine.synthesize_segment("短句", speaker_audio="x.wav", output_path=out)
        assert "spk_embedding" not in eng.captured, \
            "spk_embedding 不应经 VAR_KEYWORD 泄漏（会导致真实推理崩溃）"

    def test_embedding_passed_only_when_explicit_param(self, engine_reset, monkeypatch, tmp_path):
        eng = _ExplicitEmbeddingEngine()
        tts_engine._tts = eng
        tts_engine._SPEAKER_EMB_CACHE._store.clear()
        monkeypatch.setattr(tts_engine, "_extract_speaker_embedding", lambda sa: object())
        out = str(tmp_path / "o.wav")
        tts_engine.synthesize_segment("短句", speaker_audio="x.wav", output_path=out)
        assert eng.captured.get("spk_embedding") is not None, \
            "引擎显式支持 spk_embedding 时，应透传缓存 embedding"

    def test_get_speaker_embedding_degrades_to_none(self, engine_reset, monkeypatch):
        """提取失败（引擎未加载 / 无接口）时降级为 None，始终保留 spk_audio_prompt。"""
        # 隔离模块级 LRU 缓存，避免被同文件其它用例的 dummy embedding 污染
        tts_engine._SPEAKER_EMB_CACHE._store.clear()
        # 引擎未加载 → _extract 抛 RuntimeError → 捕获 → None
        tts_engine._tts = None
        assert tts_engine.get_speaker_embedding("x.wav") is None
        # 引擎已加载但无 embedding 接口 → NotImplementedError → None
        tts_engine._tts = types.SimpleNamespace()  # 无 encode_speaker
        assert tts_engine.get_speaker_embedding("x.wav") is None


class TestEmptyCache:
    """2.4 M-3：empty_cache 守卫式 no-op，无 CUDA / torch 或不可用时绝不抛异常。

    关键契约：不卸载模型（仅 torch.cuda.empty_cache），且测试环境（无 GPU）必过。
    """

    def test_empty_cache_no_raise_in_test_env(self):
        """当前无 GPU 环境调用不应抛异常。"""
        tts_engine.empty_cache()

    def test_empty_cache_no_cuda_attribute(self, monkeypatch):
        """torch 存在但无 cuda 属性（AttributeError）→ 被 except 吞掉，不抛。"""
        fake = types.SimpleNamespace()  # 无 cuda 属性
        monkeypatch.setitem(sys.modules, "torch", fake)
        tts_engine.empty_cache()  # torch.cuda → AttributeError → 守卫吃掉

    def test_empty_cache_cuda_unavailable(self, monkeypatch):
        """cuda.is_available()=False（CPU / 无 GPU）→ 不调用 empty_cache，不抛。"""
        calls = []
        fake = types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: False,
                empty_cache=lambda: calls.append(1),
            )
        )
        monkeypatch.setitem(sys.modules, "torch", fake)
        tts_engine.empty_cache()
        assert calls == [], "无 CUDA 时不应调用 torch.cuda.empty_cache"
