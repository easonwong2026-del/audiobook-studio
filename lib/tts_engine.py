"""IndexTTS2 封装 + VRAM 管理 + OOM 自动降级"""
from __future__ import annotations
import gc
import inspect
import os
import logging
import threading
from . import config as _cfg
from . import audio_format as af
from .segment_cache import SpeakerEmbeddingLRU

logger = logging.getLogger(__name__)

# 懒加载：首次调用才初始化模型
_tts = None
_CAPABILITY_ENGINE_ID: int | None = None
_INFER_PARAM_NAMES: frozenset[str] = frozenset()
_INFER_HAS_VAR_KEYWORD = False

# 引擎互斥锁（RLock）：保证 init_engine 与 synthesize_segment 串行化，防止多角色 /
# 批量并发调用时引擎内部状态竞争。必须用 RLock——OOM 时 synthesize_segment 会递归
# 调用自身，非重入锁会在同一线程第二次获取时死锁；RLock 允许同一线程重入。
_ENGINE_LOCK = threading.RLock()



def engine_lock():
    """返回引擎互斥锁（供测试与调用方查询；业务调用无需自行加锁）。

    引擎互斥的单一真相源是 ``_ENGINE_LOCK``，``synthesize_segment`` / ``init_engine``
    内部已包入此锁；调用方（含补录 handler）切勿在更外层再加全局锁。
    """
    return _ENGINE_LOCK

# 2.4 T-2：speaker embedding 有界 LRU 缓存容器（键=参考音频路径，值=embedding）。
# 容量默认 16（可由 config.json 的 embedding_cache_max 覆盖），超出自动淘汰最久
# 未用，防止多角色长篇小说下 embedding 随角色数线性膨胀占用显存 / 内存。
_SPEAKER_EMB_CACHE = SpeakerEmbeddingLRU(maxsize=_cfg.get_int("embedding_cache_max", 16))


def _engine_capabilities() -> tuple[frozenset[str], bool]:
    """Inspect one engine generation only once, outside the segment hot path."""
    global _CAPABILITY_ENGINE_ID, _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD
    engine_id = id(_tts)
    if _CAPABILITY_ENGINE_ID == engine_id:
        return _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD
    if _tts is None:
        _CAPABILITY_ENGINE_ID = engine_id
        _INFER_PARAM_NAMES = frozenset()
        _INFER_HAS_VAR_KEYWORD = False
        return _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD
    signature = inspect.signature(_tts.infer)
    _INFER_PARAM_NAMES = frozenset(signature.parameters)
    _INFER_HAS_VAR_KEYWORD = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    _CAPABILITY_ENGINE_ID = engine_id
    return _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD


def init_engine(model_dir: str = None, use_fp16: bool = True, use_cuda_kernel: bool = True, use_deepspeed: bool = False, use_accel: bool = False):
    global _tts
    with _ENGINE_LOCK:
        if _tts is not None:
            return
        __import__("torch")
        from indextts.infer_v2 import IndexTTS2

        if model_dir is None:
            model_dir = _cfg.get_model_dir()

        cfg_path = os.path.join(model_dir, "config.yaml")
        if not os.path.isdir(model_dir) or not os.path.isfile(cfg_path):
            raise FileNotFoundError(
                "IndexTTS2 模型目录未找到或缺少 config.yaml："
                f"{model_dir}\n"
                "请通过环境变量 AUDIOBOOK_STUDIO_MODEL_DIR、config.json 的 model_dir "
                "字段，或在 UI 中首次配置模型路径后重试。"
            )
        logger.info("Loading IndexTTS2 model (FP16)...")
        # 速度相关参数实测结论（v2.7 本机 RTX 5070Ti Laptop / Blackwell）：
        # - CUDA 定制 kernel（use_cuda_kernel）：本机无效——该 GPU 不支持该 kernel，
        #   IndexTTS2 会静默回退或报 kernel/算子错误，实际 RTF 并无改善；保持默认开启仅为
        #   兼容其它机器，若出现 CUDA / kernel 编译 / 找不到算子等报错，须回退为 False。
        # - DeepSpeed（use_deepspeed）：本机 `pip show deepspeed` 显示未安装，传 True 也会被
        #   静默忽略、无加速效果，故默认关；启用前需先 `pip install deepspeed` 并确认 GPU/CUDA 兼容。
        # - flash_attn（use_accel）：Windows 无可用 wheel，开启会 ImportError，默认 False 不启用。
        # - 真实可落地的加速杠杆：fp16（下方透传） + beam 设小（1~2，见 synthesize_segment）。
        #   本机单段 RTF≈2 是受 GPU 算力与模型规模决定的物理上限，非参数可调。
        _tts = IndexTTS2(
            cfg_path=cfg_path,
            model_dir=model_dir,
            use_fp16=use_fp16,
            use_deepspeed=use_deepspeed,
            # use_accel 开启需本机已装 flash_attn，否则会 ImportError；默认 False 不启用
            use_accel=use_accel,
            use_cuda_kernel=use_cuda_kernel,
        )
        _engine_capabilities()
        logger.info("Model loaded.")


def synthesize_segment(
    text: str,
    speaker_audio: str,
    emotion: str = "neutral",
    emo_alpha: float = 1.0,
    speech_rate: float = 1.0,
    output_path: str = "",
    max_tokens: int = 120,
    pinyin_hints: dict | None = None,
    # num_beams 控制 GPT beam search 宽度（默认 2=质量/速度平衡）。
    # 3=质量优先但更慢；1=最快但需听测质量；2=默认折中，用户仍可显式传值覆盖。
    num_beams: int = 2,
) -> str:
    # 引擎互斥（RLock）：保证合成与模型加载串行化；OOM 递归调用自身时同一线程
    # 可重入，不会死锁。调用方无需再加锁。
    with _ENGINE_LOCK:
        try:
            import torch
            oom_error = torch.cuda.OutOfMemoryError
        except ModuleNotFoundError:
            # Lightweight adapter tests may inject an engine without installing
            # PyTorch.  A real IndexTTS engine can only be initialized when
            # torch is present, so this fallback never hides a production OOM.
            class _UnavailableCudaOOM(Exception):
                pass

            oom_error = _UnavailableCudaOOM

        if _tts is None:
            raise RuntimeError("TTS engine not initialized. Call init_engine() first.")

        MAX_RETRIES = 3

        # 情感参数：只有非 neutral 时才启用文本情感控制
        use_emo = emotion and emotion != "neutral"
        emo_text = emotion if use_emo else None

        # Capability discovery is cached per engine instance.  Tests and
        # integrations may still replace ``_tts`` directly; the identity check
        # refreshes the cache once for that replacement.
        param_names, has_var_keyword = _engine_capabilities()
        # Only engines that explicitly accept an embedding can benefit from
        # extraction.  Current IndexTTS2 does not, so avoid a guaranteed
        # exception and fallback on every segment.
        spk_emb = (
            get_speaker_embedding(speaker_audio)
            if "spk_embedding" in param_names
            else None
        )

        # 真实 IndexTTS2.infer 用 **generation_kwargs（VAR_KEYWORD）接收 GPT 生成参数（如 num_beams），
        # 因此 param_names 中并不显式包含 num_beams；仅凭 "num_beams" in param_names 判断会恒为 False，
        # 导致 num_beams 默认 2 未生效（引擎走内部默认 3）。这里额外判定签名是否含 VAR_KEYWORD，含则透传 num_beams。
        # 注意：speed / pinyin_hints 不是 GPT 生成参数，透传进 **generation_kwargs 会被 GPT.generate 拒绝（实测 ValueError），
        # 故这两项仅按显式形参判定，不随 has_var_keyword 放开。
        for attempt in range(MAX_RETRIES):
            try:
                # 根据 IndexTTS2.infer 实际签名条件透传可选参数，
                # 避免参数名不符时在运行时抛 TypeError。
                infer_kwargs = dict(
                    spk_audio_prompt=speaker_audio,
                    text=text,
                    output_path=output_path,
                    use_emo_text=use_emo,
                    emo_text=emo_text,
                    emo_alpha=emo_alpha,
                    max_text_tokens_per_segment=max_tokens,
                )
                # 2.4 S-1：仅当引擎签名显式支持 spk_embedding 入参时才透传缓存 embedding。
                # 注意：不能仅凭 VAR_KEYWORD 透传——当前 IndexTTS2 的 **generation_kwargs 会把它
                # 透传给下游 gpt 导致崩溃，且实测该引擎并未暴露 spk_embedding 参数；故以显式形参名为准。
                if spk_emb is not None and "spk_embedding" in param_names:
                    infer_kwargs["spk_embedding"] = spk_emb
                if "speed" in param_names:
                    infer_kwargs["speed"] = speech_rate
                else:
                    logger.debug(
                        "IndexTTS2.infer 不支持 speed 参数，未透传 speech_rate=%s", speech_rate
                    )
                if pinyin_hints:
                    if "pinyin_hints" in param_names:
                        infer_kwargs["pinyin_hints"] = pinyin_hints
                    else:
                        logger.debug(
                            "IndexTTS2.infer 不支持 pinyin_hints 参数，未透传多音字提示"
                        )

                # num_beams 控制 GPT beam search（默认 2=质量/速度折中；3=质量优先但慢；1=最快但需听测质量）
                # 条件透传：当引擎 infer 签名显式支持 num_beams 或接受 **kwargs（如 **generation_kwargs）时传入；
                # 真实 IndexTTS2 经 **generation_kwargs 接收并在内部 pop 使用；测试桩无 **kwargs 则不接收，避免 TypeError
                generation_kwargs = {}
                if "num_beams" in param_names or has_var_keyword:
                    generation_kwargs["num_beams"] = num_beams
                _tts.infer(**infer_kwargs, **generation_kwargs)
                empty_cache()  # 2.4 M-3：段级碎片化显存清理（守卫式，无 CUDA 时 no-op）
                return output_path

            except oom_error:
                empty_cache()
                if attempt == 0:
                    logger.warning("OOM, retrying after cache clear...")
                    continue
                elif attempt == 1:
                    mid = len(text) // 2
                    path_a = output_path.replace(".wav", "_a.wav")
                    path_b = output_path.replace(".wav", "_b.wav")
                    logger.warning("OOM again, splitting segment into two halves...")
                    # 用关键字参数递归调用，确保 emo_alpha / speech_rate / pinyin_hints
                    # / num_beams 正确透传，不会被位置错位。递归在同一线程内重新获取
                    # _ENGINE_LOCK（RLock 可重入，不会死锁）。
                    synthesize_segment(
                        text=text[:mid],
                        speaker_audio=speaker_audio,
                        emotion=emotion,
                        emo_alpha=emo_alpha,
                        speech_rate=speech_rate,
                        output_path=path_a,
                        max_tokens=max_tokens,
                        pinyin_hints=pinyin_hints,
                        num_beams=num_beams,
                    )
                    synthesize_segment(
                        text=text[mid:],
                        speaker_audio=speaker_audio,
                        emotion=emotion,
                        emo_alpha=emo_alpha,
                        speech_rate=speech_rate,
                        output_path=path_b,
                        max_tokens=max_tokens,
                        pinyin_hints=pinyin_hints,
                        num_beams=num_beams,
                    )
                    # 将两段拼接回原 output_path 并清理临时文件
                    _concat_wavs([path_a, path_b], output_path)
                    for tmp in (path_a, path_b):
                        try:
                            os.remove(tmp)
                        except OSError as exc:
                            logger.debug("清理 OOM 临时文件失败: %s", exc)
                    return output_path
                else:
                    raise RuntimeError(f"OOM after {MAX_RETRIES} retries: {text[:50]}...")

        return output_path


def empty_cache() -> None:
    """释放碎片化显存（仅 ``torch.cuda.empty_cache``，不卸载模型）。

    2.4 M-3：用于合成终态 / 取消后 / 批量重合成后释放碎片化 VRAM，降低峰值占用。
    守卫式实现：用 sys.modules 判断 torch 是否已加载，避免 C 扩展 segfault。
    不调用 import torch（C 扩展的 access violation 会绕过 Python 的异常处理）。
    子线程中也安全——全量 try/except 包裹 CUDA 调用。
    """
    import sys as _sys
    if "torch" not in _sys.modules:
        return
    torch = _sys.modules["torch"]
    try:
        cuda_available = getattr(torch, "cuda", None) is not None
        if cuda_available:
            is_avail = getattr(torch.cuda, "is_available", lambda: False)()
            if is_avail:
                torch.cuda.empty_cache()
    except Exception:  # pylint: disable=broad-except
        pass


def _extract_speaker_embedding(speaker_audio: str):
    """尝试从参考音频提取 speaker embedding（需引擎已加载且暴露 embedding 接口）。

    该函数在引擎不支持时抛任意异常，由 ``get_speaker_embedding`` 捕获并降级为 None。
    当前 IndexTTS2 v2.7 的 ``infer`` 不接收 ``spk_embedding`` 参数、也未暴露公开的
    speaker-embedding 提取 API（如 ``encode_speaker``），因此在本环境下会抛
    ``NotImplementedError`` 而降级（行为不变，仍走 ``spk_audio_prompt``）。
    若未来引擎暴露该接口，可在此调用以落地 S-1 的运行时 embedding 复用收益。
    """
    if _tts is None:
        raise RuntimeError("TTS engine not initialized")
    encode = getattr(_tts, "encode_speaker", None)
    if encode is None:
        # 当前 IndexTTS2 未暴露公开的 speaker-embedding 提取接口
        raise NotImplementedError("engine does not expose a speaker-embedding API")
    return encode(speaker_audio)


def get_speaker_embedding(speaker_audio: str):
    """取得参考音频的 speaker embedding（有界 LRU 缓存，S-1 复用）。

    先查 ``_SPEAKER_EMB_CACHE``；未命中则尝试 ``_extract_speaker_embedding`` 提取，
    成功则写入缓存。任何异常（引擎未加载 / 无 embedding 接口 / 提取失败）均返回
    ``None``，由调用方降级为 ``spk_audio_prompt``（行为不变，测试必过）。

    Args:
        speaker_audio: 参考音频路径。

    Returns:
        embedding（任意对象，通常为 tensor）；不可用时返回 None。
    """
    if not speaker_audio:
        return None
    try:
        cached = _SPEAKER_EMB_CACHE.get(speaker_audio)
        if cached is not None:
            return cached
        emb = _extract_speaker_embedding(speaker_audio)
        if emb is not None:
            _SPEAKER_EMB_CACHE.put(speaker_audio, emb)
        return emb
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("speaker embedding 提取失败，降级为 spk_audio_prompt: %s", exc)
        return None


def invalidate_speaker_cache(speaker_audio: str | None = None) -> None:
    """Invalidate one speaker embedding or the whole embedding cache.

    Voice Cast force-rebinds normally use a new project snapshot path, but the
    old path can still be present in the LRU.  Removing it makes the lifecycle
    explicit and keeps this operation testable without loading the TTS model.
    """
    if speaker_audio:
        _SPEAKER_EMB_CACHE.pop(str(speaker_audio), None)
    else:
        _SPEAKER_EMB_CACHE.clear()


def _concat_wavs(paths: list[str], out_path: str) -> None:
    """拼接多个 WAV 文件为一段，写入 out_path。

    复用 ``lib.audio_format.concatenate_normalized`` 统一采样率 / 声道 / dtype，
    避免直接 ``np.concatenate`` 因格式不一致报错。以首个文件采样率为基准（同质
    输入不重采样），OOM 拆出的两段由同一模型产出，天然一致。
    """
    combined, rate, _ = af.concatenate_normalized(
        paths, target_rate=None, target_channels=1, target_dtype=af.DEFAULT_TARGET_DTYPE
    )
    af.write_wav(out_path, combined, rate)

    # 2.4 M-2：拼接写盘后释放中间 numpy 数组，缓解长篇小说拼接峰值内存
    del combined
    gc.collect()


def test_voice(speaker_audio: str, emotion: str = "neutral", max_tokens: int = 120) -> list[str]:
    """用三句测试句试听音色"""
    test_sentences = [
        "今天天气真不错，适合出去走走。",
        "你确定要这么做吗？",
        "太好了！终于等到了这一天！",
    ]
    outputs = []
    # 保存到外置数据目录的 test_output（不再依赖程序目录 workspace/），Gradio 能直接访问
    out_dir = _cfg.get_test_output_dir()
    for i, text in enumerate(test_sentences):
        out = os.path.join(out_dir, f"test_{i+1}.wav")
        # 用关键字参数调用，避免位置参数错位（emo_alpha/output_path 等）
        result = synthesize_segment(
            text=text,
            speaker_audio=speaker_audio,
            emotion=emotion,
            output_path=out,
            max_tokens=max_tokens,
        )
        logger.info(f"Test segment {i+1}: output={result}, exists={os.path.isfile(result)}")
        outputs.append(out)
    return outputs
