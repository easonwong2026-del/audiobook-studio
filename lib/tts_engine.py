"""IndexTTS2 封装 + VRAM 管理 + OOM 自动降级"""
from __future__ import annotations
import gc
import inspect
import os
import logging
import threading
import time
from collections.abc import Mapping
from . import config as _cfg
from . import audio_format as af
from .segment_cache import SpeakerEmbeddingLRU
from .failures import PHASE_ENGINE_INFER
from .tts_model_layout import model_config_candidates, resolve_model_config_path

logger = logging.getLogger(__name__)

# 懒加载：首次调用才初始化模型
_tts = None
_ENGINE_PROFILE: dict = {}
_LAST_ADAPTER_REPORT: dict = {}
_CAPABILITY_ENGINE_ID: int | None = None
_CAPABILITY_ENGINE_REF = None
_INFER_PARAM_NAMES: frozenset[str] = frozenset()
_INFER_HAS_VAR_KEYWORD = False

# 引擎互斥锁（RLock）：保证 init_engine 与 synthesize_segment 串行化，防止多角色 /
# 批量并发调用时引擎内部状态竞争。必须用 RLock——OOM 时 synthesize_segment 会递归
# 调用自身，非重入锁会在同一线程第二次获取时死锁；RLock 允许同一线程重入。
_ENGINE_LOCK = threading.RLock()

CHECK_INTERVAL = 10
MIN_FREE_VRAM_BYTES = 2 * 1024**3
MAX_CACHED_GAP_BYTES = int(1.5 * 1024**3)
_successful_segments_since_check = 0

TTS_ENGINE_RUNTIME_FAILURE = "TTS_ENGINE_RUNTIME_FAILURE"
TTS_ENGINE_OOM_EXHAUSTED = "TTS_ENGINE_OOM_EXHAUSTED"


class EngineRuntimeFailure(RuntimeError):
    """Stable typed failure raised from the engine adapter layer.

    ``phase == engine_infer`` + ``OSError(errno=22)`` is a *known recoverable
    engine-runtime failure candidate*; the same errno from file publish /
    WAV validation is classified elsewhere and must not trigger an engine
    recycle.  The root cause of sustained Errno-22 remains an open question
    (IndexTTS2 internal state / PyTorch / CUDA native runtime); this class
    deliberately does not claim a specific cause.
    """

    code = TTS_ENGINE_RUNTIME_FAILURE

    def __init__(
        self,
        phase: str,
        message: str,
        *,
        errno: int | None = None,
        recoverable: bool = True,
        code: str | None = None,
        original_exception: BaseException | None = None,
    ) -> None:
        self.phase = str(phase or PHASE_ENGINE_INFER)
        self.errno = errno
        self.code = str(code or self.code)
        self.original_exception = original_exception
        known_fingerprint = self.code == TTS_ENGINE_OOM_EXHAUSTED or (
            self.phase == PHASE_ENGINE_INFER
            and self.errno == 22
            and isinstance(original_exception, OSError)
        )
        # ``recoverable=True`` is advisory only.  The adapter normalizes it
        # against the confirmed fingerprint allow-list so an arbitrary OSError
        # cannot enter the engine-recycle path.
        self.recoverable = bool(recoverable) and known_fingerprint
        detail = f" (errno={errno})" if errno is not None else ""
        super().__init__(f"{self.code} phase={self.phase}{detail}: {message}")



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
    global _CAPABILITY_ENGINE_ID, _CAPABILITY_ENGINE_REF
    global _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD
    engine_id = id(_tts)
    if _CAPABILITY_ENGINE_REF is _tts:
        return _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD
    if _tts is None:
        _CAPABILITY_ENGINE_ID = engine_id
        _CAPABILITY_ENGINE_REF = _tts
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
    _CAPABILITY_ENGINE_REF = _tts
    return _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD


def _resolved_profile(profile=None, model_dir=None) -> dict:
    from .tts_profile import resolve_profile

    # Keep the legacy resolver reference explicit for older launchers and
    # static configuration guards: `_cfg.get_model_dir()` remains the v2
    # compatibility source inside the profile resolver.
    overrides = dict(profile) if isinstance(profile, Mapping) else {}
    if model_dir is not None:
        overrides["model_dir"] = model_dir
        # The legacy positional model_dir API has always meant IndexTTS2.
        if not profile:
            overrides.setdefault("engine_version", "2")
    return resolve_profile(overrides)


class IndexTTS2Backend:
    """Native IndexTTS 2 adapter.

    This concrete adapter keeps the legacy constructor and optional argument
    compatibility in one place.  The application still calls the stable
    ``synthesize_segment`` entry point; it never needs to know these details.
    """

    version = "2"

    @staticmethod
    def load_class():
        from indextts.infer_v2 import IndexTTS2

        return IndexTTS2

    @staticmethod
    def constructor_kwargs(
        *,
        cfg_path: str,
        model_dir: str,
        precision: str,
        use_cuda_kernel: bool,
        use_deepspeed: bool,
        use_accel: bool,
    ) -> dict[str, object]:
        return {
            "cfg_path": cfg_path,
            "model_dir": model_dir,
            "use_fp16": precision == "FP16",
            "use_cuda_kernel": use_cuda_kernel,
            "use_deepspeed": use_deepspeed,
            "use_accel": use_accel,
        }

    @staticmethod
    def emotion_control(emotion: str | None) -> tuple[bool, str | None, dict[str, list]]:
        # Preserve the established v2 behavior: non-neutral canonical labels
        # are passed to its text-emotion path unchanged.
        use_emo = bool(emotion and emotion != "neutral")
        return use_emo, emotion if use_emo else None, {
            "mapped": ["emotion", "emo_alpha"],
            "approximated": [],
            "unsupported": [],
        }

    @staticmethod
    def prepare(
        *,
        text: str,
        pinyin_hints: object,
        speech_rate: float,
        param_names: frozenset[str],
    ) -> tuple[str, dict[str, object], dict[str, list]]:
        kwargs: dict[str, object] = {}
        report: dict[str, list] = {"mapped": ["text"], "approximated": [], "unsupported": [], "ignored": []}
        if speech_rate != 1.0:
            if "speed" in param_names:
                kwargs["speed"] = speech_rate
                report["mapped"].append("speech_rate")
            else:
                report["unsupported"].append({"field": "speech_rate", "reason": "IndexTTS2 infer has no speed parameter"})
        else:
            report["mapped"].append("speech_rate")
        if pinyin_hints:
            if "pinyin_hints" in param_names:
                kwargs["pinyin_hints"] = pinyin_hints
                report["mapped"].append("pinyin_hints")
            else:
                report["unsupported"].append({"field": "pinyin_hints", "reason": "IndexTTS2 infer has no pinyin_hints parameter"})
        else:
            report["ignored"].append({"field": "pinyin_hints", "reason": "no_pinyin_hints"})
        return text, kwargs, report


class IndexTTS25Backend:
    """Native IndexTTS 2.5 adapter with the conservative first baseline."""

    version = "2.5"

    @staticmethod
    def load_class():
        from indextts.infer_v2_5 import IndexTTS2

        return IndexTTS2

    @staticmethod
    def constructor_kwargs(
        *,
        cfg_path: str,
        model_dir: str,
        precision: str,
    ) -> dict[str, object]:
        return {
            "cfg_path": cfg_path,
            "model_dir": model_dir,
            "use_bf16": precision == "BF16",
            "use_cuda_kernel": False,
            "use_deepspeed": False,
            "use_accel": False,
            "use_torch_compile": False,
            "use_qwen_emo": True,
        }

    @staticmethod
    def emotion_control(emotion: str | None) -> tuple[bool, str | None, dict[str, list]]:
        # QwenEmotion's stable labels are the four direct labels below.  The
        # remaining Canonical labels use an explicit approximation or remain
        # unsupported; they are never silently presented as exact mappings.
        canonical = str(emotion or "neutral").strip().lower()
        if canonical == "neutral":
            return False, None, {"mapped": ["emotion", "emo_alpha"], "approximated": [], "unsupported": []}
        direct = {"happy": "happy", "angry": "angry", "sad": "sad", "fearful": "afraid"}
        approximate = {"excited": "happy", "tense": "afraid", "hesitant": "afraid", "cold": "calm", "confident": "calm"}
        if canonical in direct:
            return True, direct[canonical], {"mapped": ["emotion", "emo_alpha"], "approximated": [], "unsupported": []}
        if canonical in approximate:
            return True, approximate[canonical], {
                "mapped": ["emo_alpha"],
                "approximated": [{"field": "emotion", "target": "qwen_emotion", "value": approximate[canonical]}],
                "unsupported": [],
            }
        return False, None, {
            "mapped": ["emo_alpha"],
            "approximated": [],
            "unsupported": [{
                "field": "emotion",
                "reason": "IndexTTS 2.5 QwenEmotion has no stable mapping for this canonical value",
                "value": canonical,
            }],
        }

    @staticmethod
    def prepare(
        *,
        text: str,
        pinyin_hints: object,
        speech_rate: float,
        param_names: frozenset[str],
    ) -> tuple[str, dict[str, object], dict[str, list]]:
        del param_names  # official v2.5 exposes these stable infer parameters
        infer_text, pinyin_report = _pinyin_annotations(text, pinyin_hints)
        duration_factor = duration_factor_for_speech_rate(speech_rate)
        report: dict[str, list] = {
            "mapped": ["text", "lang", "duration_factor"],
            "approximated": [{"field": "speech_rate", "target": "duration_factor", "value": duration_factor}],
            "unsupported": [],
            "ignored": [],
        }
        if pinyin_report.get("status") == "mapped":
            report["mapped"].append("pinyin_hints")
        else:
            report["ignored"].append({"field": "pinyin_hints", "reason": pinyin_report.get("reason")})
        return infer_text, {
            "lang": _normalize_language(_canonical_language_from_text(text)),
            "duration_factor": duration_factor,
        }, report


def _backend_for(version: str):
    return IndexTTS25Backend() if str(version) == "2.5" else IndexTTS2Backend()


def get_engine_profile() -> dict:
    """Return the profile actually attached to this process, path included."""
    with _ENGINE_LOCK:
        return dict(_ENGINE_PROFILE)


def get_public_engine_profile() -> dict:
    """Return path-free identity fields for Web/MCP status responses."""
    from .tts_profile import public_profile

    with _ENGINE_LOCK:
        return public_profile(_ENGINE_PROFILE) if _ENGINE_PROFILE else {}


def last_adapter_report() -> dict:
    with _ENGINE_LOCK:
        return dict(_LAST_ADAPTER_REPORT)


def init_engine(
    model_dir: str = None,
    use_fp16: bool = True,
    use_cuda_kernel: bool = True,
    use_deepspeed: bool = False,
    use_accel: bool = False,
    *,
    profile: Mapping[str, object] | None = None,
):
    global _tts, _ENGINE_PROFILE
    with _ENGINE_LOCK:
        resolved = _resolved_profile(profile, model_dir)
        # Preserve the historical positional API for callers that do not yet
        # pass a frozen profile.  Task/runtime profiles remain authoritative;
        # the legacy switch only affects the v2 default precision.
        if profile is None and resolved.get("engine_version") == "2" and not use_fp16:
            resolved["precision"] = "FP32"
            from .tts_profile import cache_identity

            resolved["cache_identity"] = cache_identity(resolved)
        if _tts is not None:
            if profile and not _profile_matches(_ENGINE_PROFILE, resolved):
                raise RuntimeError(
                    "TTS runtime 已加载另一 engine identity；必须先 recycle 后再切换"
                )
            return
        __import__("torch")
        version = str(resolved["engine_version"])
        model_dir = str(resolved["model_dir"])
        backend = _backend_for(version)
        IndexTTS2 = backend.load_class()

        cfg_path = resolve_model_config_path(version, model_dir)
        if not os.path.isdir(model_dir) or cfg_path is None:
            candidates = ", ".join(
                candidate.name for candidate in model_config_candidates(version, model_dir)
            )
            raise FileNotFoundError(
                f"IndexTTS {version} 模型目录未找到或缺少配置文件（{candidates}）："
                f"{model_dir}\n"
                "请在设置页为该版本配置本地模型目录后重试。"
            )
        # infer_v2_5 may auto-download its auxiliary bundle from the network
        # when files are missing.  Runtime must never turn a local task into a
        # hidden download, so reject an obviously incomplete local bundle
        # before constructing the official class.  Test/fake adapters may use
        # a config-only fixture and are intentionally exempt from this check.
        if version == "2.5" and not _looks_like_local_v25_bundle(model_dir):
            module_name = getattr(IndexTTS2, "__module__", "")
            if str(module_name).startswith("indextts"):
                raise FileNotFoundError(
                    "IndexTTS 2.5 本地模型不完整，已阻止自动下载；请先准备完整 checkpoint 目录"
                )
        precision = str(resolved["precision"])
        logger.info("Loading IndexTTS %s model (%s)...", version, precision)
        # Keep the legacy constructor defaults intact for rollback.  The v2.5
        # adapter below owns its separate conservative baseline and never
        # inherits these optional acceleration flags.
        if version == "2.5":
            common = backend.constructor_kwargs(
                cfg_path=str(cfg_path),
                model_dir=model_dir,
                precision=precision,
            )
        else:
            common = backend.constructor_kwargs(
                cfg_path=str(cfg_path),
                model_dir=model_dir,
                precision=precision,
                use_cuda_kernel=use_cuda_kernel,
                use_deepspeed=use_deepspeed,
                use_accel=use_accel,
            )
        _tts = IndexTTS2(**common)
        _ENGINE_PROFILE = dict(resolved)
        actual_device = getattr(_tts, "device", None)
        if actual_device is not None:
            _ENGINE_PROFILE["device"] = str(actual_device)
        _engine_capabilities()
        logger.info("Model loaded.")


def _profile_matches(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return all(
        str(left.get(key) or "") == str(right.get(key) or "")
        for key in ("engine_identity", "model_identity", "precision")
    )


def _looks_like_local_v25_bundle(model_dir: str) -> bool:
    # Keep the runtime's no-hidden-download guard aligned with the dependency-
    # free diagnostics.  In particular, v2.5 is identified by its actual
    # codec/config-driven asset layout, not by legacy dvae/campplus files.
    from .environment import model_checkpoint_state

    state = model_checkpoint_state("v2.5", model_dir)
    return bool(state.get("directory") and not state.get("missing_required"))


def _normalize_language(value) -> str:
    raw = str(value or "ZH").strip().lower().replace("_", "-")
    aliases = {
        "zh": "ZH", "zh-cn": "ZH", "zh-tw": "ZH", "chinese": "ZH",
        "en": "EN", "en-us": "EN", "en-gb": "EN", "english": "EN",
        "ja": "JA", "ja-jp": "JA", "japanese": "JA",
        "es": "ES", "es-es": "ES", "spanish": "ES",
        "ar": "AR", "ar-sa": "AR", "arabic": "AR",
    }
    return aliases.get(raw, "ZH")


def _canonical_language_from_text(text: str) -> str:
    """Infer only the v2.5 adapter language; Canonical JSON stays untouched."""
    if any("\u3040" <= char <= "\u30ff" for char in str(text or "")):
        return "JA"
    # Chinese is Audiobook Studio's default language.  Keep it for mixed
    # Chinese/Latin text instead of switching the entire v2.5 call to EN.
    if any("\u4e00" <= char <= "\u9fff" for char in str(text or "")):
        return "ZH"
    if any("\u0600" <= char <= "\u06ff" for char in str(text or "")):
        return "AR"
    if any("\u00c0" <= char <= "\u024f" for char in str(text or "") or ""):
        return "ES"
    if any("A" <= char <= "Z" or "a" <= char <= "z" for char in str(text or "")):
        return "EN"
    return "ZH"


def _pinyin_annotations(text: str, hints) -> tuple[str, dict]:
    """Render Canonical pinyin hints into v2.5's adapter syntax.

    The canonical hint object is left untouched.  This adapter accepts both a
    simple ``{"行": "xing2"}`` map and ordered entries with ``start`` offsets,
    which is enough to preserve polyphonic occurrences without changing the
    Structured Script JSON contract.
    """
    if not hints:
        return text, {"status": "ignored", "reason": "no_pinyin_hints"}
    annotations: list[tuple[int, int, str, str]] = []
    if isinstance(hints, Mapping):
        for token, pronunciation in hints.items():
            if isinstance(pronunciation, Mapping):
                pronunciation = pronunciation.get("pinyin") or pronunciation.get("pronunciation")
            if isinstance(pronunciation, (list, tuple)):
                pronunciation = pronunciation[0] if pronunciation else ""
            token = str(token)
            pronunciation = str(pronunciation or "").strip()
            if not token or not pronunciation:
                continue
            start = 0
            while True:
                index = text.find(token, start)
                if index < 0:
                    break
                annotations.append((index, index + len(token), token, pronunciation.upper()))
                start = index + len(token)
    elif isinstance(hints, list):
        for item in hints:
            if not isinstance(item, Mapping):
                continue
            token = str(item.get("text") or item.get("word") or "")
            pronunciation = str(item.get("pinyin") or item.get("pronunciation") or "").strip()
            if not token or not pronunciation:
                continue
            try:
                start = int(item.get("start", item.get("position")))
            except (TypeError, ValueError):
                start = text.find(token)
            if start >= 0 and text[start:start + len(token)] == token:
                annotations.append((start, start + len(token), token, pronunciation.upper()))
    if not annotations:
        return text, {"status": "ignored", "reason": "pinyin_hints_unmatched"}
    annotations.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str, str]] = []
    cursor = -1
    for item in annotations:
        if item[0] >= cursor:
            selected.append(item)
            cursor = item[1]
    rendered: list[str] = []
    cursor = 0
    for start, end, token, pronunciation in selected:
        rendered.append(text[cursor:start])
        rendered.append(f"<{token}|{pronunciation}>")
        cursor = end
    rendered.append(text[cursor:])
    return "".join(rendered), {
        "status": "mapped", "count": len(selected), "syntax": "<text|pronunciation>",
    }


def duration_factor_for_speech_rate(speech_rate: float) -> float:
    """Adapt Canonical speed multiplier to v2.5's duration multiplier."""
    try:
        value = float(speech_rate or 1.0)
    except (TypeError, ValueError):
        value = 1.0
    value = max(value, 0.01)
    return min(max(1.0 / value, 0.5), 2.0)


def _record_adapter_report(report: dict, trace=None) -> None:
    global _LAST_ADAPTER_REPORT
    _LAST_ADAPTER_REPORT = dict(report)
    unsupported = report.get("unsupported") or []
    if unsupported:
        logger.warning("TTS adapter unsupported fields: %s", unsupported)
    diagnostic_keys = ("unsupported", "fallback", "warning", "error")
    if trace is not None and any(report.get(key) for key in diagnostic_keys):
        try:
            trace.record_event("adapter_mapping", data=dict(report))
        except Exception:  # noqa: BLE001
            logger.debug("记录 adapter mapping 失败", exc_info=True)


def record_adapter_report(report: dict, trace=None) -> None:
    """Record adapter handling for Canonical Contract fields."""
    addition = dict(report or {})
    # Directed synthesis reports fields (for example pitch/breath) after the
    # actual infer call.  Merge that report with the adapter's report for the
    # same call so an adapter capability gap cannot overwrite the engine
    # mapping report, and deduplicate repeated list entries.
    merged = dict(_LAST_ADAPTER_REPORT)
    for key in ("mapped", "approximated", "unsupported", "ignored"):
        values = list(merged.get(key) or [])
        for value in addition.get(key) or []:
            if value not in values:
                values.append(value)
        if values:
            merged[key] = values
    for key, value in addition.items():
        if key not in {"mapped", "approximated", "unsupported", "ignored"}:
            merged.setdefault(key, value)
    _record_adapter_report(merged, trace)


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
    trace=None,
    trace_segment_id: str | None = None,
    trace_chapter_id: str | None = None,
    trace_part_index: int | str | None = None,
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
        active_profile = dict(_ENGINE_PROFILE)
        version = str(active_profile.get("engine_version") or "2")
        backend = _backend_for(version)
        # Capability discovery is cached per engine instance.  The concrete
        # adapter uses it only for v2's optional legacy arguments; v2.5's
        # official lang/duration API is explicit and stable.
        param_names, has_var_keyword = _engine_capabilities()
        infer_text, adapter_kwargs, field_report = backend.prepare(
            text=text,
            pinyin_hints=pinyin_hints,
            speech_rate=speech_rate,
            param_names=param_names,
        )
        use_emo, emo_text, emotion_report = backend.emotion_control(emotion)
        adapter_report = {
            "contract": "Structured Script JSON",
            "engine_identity": active_profile.get("engine_identity") or "indextts:2",
            "engine_version": version,
            "mapped": list(field_report.get("mapped") or []),
            "approximated": list(field_report.get("approximated") or []),
            "unsupported": list(field_report.get("unsupported") or []),
            "ignored": list(field_report.get("ignored") or []),
        }
        for key in ("mapped", "approximated", "unsupported", "ignored"):
            adapter_report[key].extend(emotion_report.get(key) or [])
        adapter_report["mapped"].extend(["max_tokens", "num_beams"])
        _record_adapter_report(adapter_report, trace)
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
        last_oom: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            try:
                # 根据 IndexTTS2.infer 实际签名条件透传可选参数，
                # 避免参数名不符时在运行时抛 TypeError。
                infer_kwargs = dict(
                    spk_audio_prompt=speaker_audio,
                    text=infer_text,
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
                # The backend owns all version-specific arguments.  In
                # particular, Canonical pinyin_hints never leaks into v2.5's
                # generation kwargs: it is rendered into infer_text first.
                infer_kwargs.update(adapter_kwargs)

                # num_beams 控制 GPT beam search（默认 2=质量/速度折中；3=质量优先但慢；1=最快但需听测质量）
                # 条件透传：当引擎 infer 签名显式支持 num_beams 或接受 **kwargs（如 **generation_kwargs）时传入；
                # 真实 IndexTTS2 经 **generation_kwargs 接收并在内部 pop 使用；测试桩无 **kwargs 则不接收，避免 TypeError
                generation_kwargs = {}
                if "num_beams" in param_names or has_var_keyword:
                    generation_kwargs["num_beams"] = num_beams
                infer_started = None
                if trace is not None:
                    infer_started = time.perf_counter()
                infer_success = False
                infer_error: BaseException | None = None
                try:
                    _tts.infer(**infer_kwargs, **generation_kwargs)
                    infer_success = True
                except EngineRuntimeFailure as exc:
                    infer_error = exc
                    raise
                except oom_error as exc:
                    infer_error = exc
                    raise
                except OSError as exc:
                    infer_error = exc
                    # Only the observed errno=22 engine-infer fingerprint is
                    # currently approved for automatic engine recycle.  Other
                    # OSErrors remain structured, non-recoverable failures.
                    raise EngineRuntimeFailure(
                        PHASE_ENGINE_INFER,
                        str(exc),
                        errno=getattr(exc, "errno", None),
                        recoverable=getattr(exc, "errno", None) == 22,
                        original_exception=exc,
                    ) from exc
                except Exception as exc:
                    infer_error = exc
                    raise EngineRuntimeFailure(
                        PHASE_ENGINE_INFER,
                        str(exc),
                        recoverable=False,
                        original_exception=exc,
                    ) from exc
                finally:
                    if trace is not None and infer_started is not None:
                        try:
                            trace.record_infer(
                                trace_segment_id or output_path,
                                time.perf_counter() - infer_started,
                                part_index=trace_part_index,
                                chapter_id=trace_chapter_id,
                                success=infer_success,
                                error=infer_error,
                            )
                        except Exception:  # noqa: BLE001  # diagnostics must not alter TTS
                            logger.debug("记录 engine_infer trace 失败", exc_info=True)
                _note_segment_success()
                return output_path

            except oom_error as oom_exc:
                last_oom = oom_exc
                if trace is not None:
                    try:
                        trace.record_boundary("oom")
                        trace.record_event(
                            "oom",
                            data={"segment_id": trace_segment_id or output_path},
                        )
                    except Exception:  # noqa: BLE001  # diagnostics must not alter TTS
                        logger.debug("记录 OOM trace 失败", exc_info=True)
                empty_cache(reason="oom")
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
                        trace=trace,
                        trace_segment_id=trace_segment_id,
                        trace_chapter_id=trace_chapter_id,
                        trace_part_index=trace_part_index,
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
                        trace=trace,
                        trace_segment_id=trace_segment_id,
                        trace_chapter_id=trace_chapter_id,
                        trace_part_index=trace_part_index,
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
                    raise EngineRuntimeFailure(
                        PHASE_ENGINE_INFER,
                        f"OOM after {MAX_RETRIES} retries: {text[:50]}...",
                        recoverable=True,
                        code=TTS_ENGINE_OOM_EXHAUSTED,
                        original_exception=last_oom,
                    )

        return output_path


def _check_cuda_memory() -> None:
    """Check CUDA memory and clear the allocator only past safety thresholds."""
    try:
        snapshot = gpu_snapshot()
        if not snapshot.get("available"):
            return
        free_bytes = int(snapshot["free"])
        allocated_bytes = int(snapshot["allocated"])
        reserved_bytes = int(snapshot["reserved"])
        cached_gap_bytes = reserved_bytes - allocated_bytes
        logger.debug(
            "CUDA memory check allocated_mb=%.1f reserved_mb=%.1f free_mb=%.1f "
            "cached_gap_mb=%.1f",
            allocated_bytes / (1024 * 1024),
            reserved_bytes / (1024 * 1024),
            free_bytes / (1024 * 1024),
            cached_gap_bytes / (1024 * 1024),
        )
        if free_bytes < MIN_FREE_VRAM_BYTES:
            empty_cache(reason="low_free_vram")
        elif cached_gap_bytes > MAX_CACHED_GAP_BYTES:
            empty_cache(reason="cached_gap")
    except Exception:  # telemetry must not alter TTS
        logger.debug("CUDA memory check failed", exc_info=True)


def _note_segment_success() -> None:
    """Record one successful segment and periodically inspect CUDA memory."""
    global _successful_segments_since_check
    _successful_segments_since_check += 1
    if _successful_segments_since_check < CHECK_INTERVAL:
        return
    _successful_segments_since_check = 0
    _check_cuda_memory()


def empty_cache(reason: str = "manual") -> bool:
    """Release unused PyTorch CUDA allocator blocks without unloading the model.

    The guard intentionally checks ``sys.modules`` instead of importing torch:
    a missing/CPU-only/broken CUDA runtime must never turn telemetry or cleanup
    into a synthesis failure.
    """
    import sys as _sys
    if "torch" not in _sys.modules:
        return False
    torch = _sys.modules["torch"]
    try:
        cuda_available = getattr(torch, "cuda", None) is not None
        if cuda_available:
            is_avail = getattr(torch.cuda, "is_available", lambda: False)()
            if is_avail:
                torch.cuda.empty_cache()
                logger.info("CUDA cache cleanup reason=%s", str(reason or "manual"))
                return True
    except Exception:  # pylint: disable=broad-except
        logger.debug(
            "CUDA cache cleanup failed reason=%s",
            str(reason or "manual"),
            exc_info=True,
        )
    return False


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


def engine_is_initialized() -> bool:
    """Return whether an engine instance is currently attached."""
    return _tts is not None


def reset_engine() -> None:
    """Detach the current engine instance and release adapter-level state.

    This is the *only* sanctioned way to drop the in-process model reference
    (``_tts = None``).  It runs under ``_ENGINE_LOCK`` and performs:

    - detach the current ``_tts`` instance (dropping Python references);
    - reset the cached capability inspection;
    - clear the speaker-embedding LRU and adapter-level caches;
    - ``gc.collect()`` and a guarded ``torch.cuda.empty_cache()``.

    Object-level recycle cannot guarantee that every native CUDA context /
    IndexTTS2 internal handle is released; that limitation is documented in
    the runtime lifecycle (process-level recycle happens via runtime restart
    and ownership takeover).
    """
    global _tts, _ENGINE_PROFILE, _LAST_ADAPTER_REPORT
    global _CAPABILITY_ENGINE_ID, _CAPABILITY_ENGINE_REF
    global _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD
    with _ENGINE_LOCK:
        _tts = None
        _ENGINE_PROFILE = {}
        _LAST_ADAPTER_REPORT = {}
        _CAPABILITY_ENGINE_ID = None
        _CAPABILITY_ENGINE_REF = None
        _INFER_PARAM_NAMES = frozenset()
        _INFER_HAS_VAR_KEYWORD = False
        _SPEAKER_EMB_CACHE.clear()
        gc.collect()
        empty_cache(reason="engine_recycle")


def gpu_snapshot() -> dict:
    """Best-effort GPU memory snapshot for diagnostics.

    Never raises and never imports torch: when torch is not loaded or CUDA
    is unavailable it returns ``{"available": False}`` so tests and CPU
    environments remain fully runnable.
    """
    result = {"available": False}
    import sys as _sys

    if "torch" not in _sys.modules:
        return result
    torch = _sys.modules["torch"]
    try:
        if not getattr(torch.cuda, "is_available", lambda: False)():
            return result
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        result.update({
            "available": True,
            "allocated": torch.cuda.memory_allocated(),
            "reserved": torch.cuda.memory_reserved(),
            "max_allocated": torch.cuda.max_memory_allocated(),
            "free": free_bytes,
            "total": total_bytes,
        })
    except Exception:  # pylint: disable=broad-except
        pass
    return result


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
