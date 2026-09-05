"""IndexTTS2 封装 + VRAM 管理 + OOM 自动降级"""
from __future__ import annotations

import gc
import importlib
import inspect
import logging
import os
import shutil
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from importlib import metadata

from . import audio_format as af
from . import config as _cfg
from .failures import PHASE_ENGINE_INFER
from .segment_cache import SpeakerEmbeddingLRU
from .tts_model_layout import model_config_candidates, resolve_model_config_path

logger = logging.getLogger(__name__)

# 懒加载：首次调用才初始化模型
_tts = None
_ENGINE_PROFILE: dict = {}
_LAST_ADAPTER_REPORT: dict = {}
_ACCEL_STATUS: dict = {
    "requested": False,
    "available": False,
    "enabled": False,
    "active": False,
    "fallback": False,
    "reason": "not_initialized",
    "flash_attn_version": "",
    "triton_version": "",
}
_CAPABILITY_ENGINE_ID: int | None = None
_CAPABILITY_ENGINE_REF = None
_INFER_PARAM_NAMES: frozenset[str] = frozenset()
_INFER_HAS_VAR_KEYWORD = False

# 引擎互斥锁（RLock）：保证 init_engine 与 synthesize_segment 串行化，防止多角色 /
# 批量并发调用时引擎内部状态竞争。必须用 RLock——OOM 时 synthesize_segment 会递归
# 调用自身，非重入锁会在同一线程第二次获取时死锁；RLock 允许同一线程重入。
_ENGINE_LOCK = threading.RLock()
_TRITON_CACHE_WORKAROUND_LOCK = threading.RLock()

CHECK_INTERVAL = 10
MIN_FREE_VRAM_BYTES = 2 * 1024**3
MAX_CACHED_GAP_BYTES = int(1.5 * 1024**3)
_successful_segments_since_check = 0

TTS_ENGINE_RUNTIME_FAILURE = "TTS_ENGINE_RUNTIME_FAILURE"
TTS_ENGINE_OOM_EXHAUSTED = "TTS_ENGINE_OOM_EXHAUSTED"

ACCEL_OVERLAY_ENV = "AUDIOBOOK_STUDIO_ACCEL_OVERLAY"
ACCEL_DISABLE_ENV = "AUDIOBOOK_STUDIO_DISABLE_INDEXTTS25_ACCEL"
_TRITON_WINDOWS_VERSION = "3.4.0.post21"

CONDITIONING_CACHE_MAXSIZE = 4
_CONDITIONING_FIELDS = (
    "cache_spk_cond",
    "cache_s2mel_style",
    "cache_s2mel_prompt",
    "cache_spk_audio_prompt",
    "cache_emo_cond",
    "cache_emo_audio_prompt",
    "cache_mel",
)
_CONDITIONING_CACHE: OrderedDict[tuple[str, str], dict[str, object]] = OrderedDict()
_CONDITIONING_CACHE_ENABLED = False


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


def _is_windows() -> bool:
    """Return the platform state without mutating ``os.name`` in tests."""
    return os.name == "nt"


def _store_accel_status(report: Mapping[str, object]) -> dict:
    global _ACCEL_STATUS
    fields = (
        "requested", "available", "enabled", "active", "fallback", "reason",
        "flash_attn_version", "triton_version",
    )
    _ACCEL_STATUS = {field: report.get(field) for field in fields}
    return dict(_ACCEL_STATUS)


def get_acceleration_status() -> dict:
    """Return path-free IndexTTS 2.5 GPT acceleration status."""
    with _ENGINE_LOCK:
        return dict(_ACCEL_STATUS)


def _base_accel_status(*, requested: bool = True, reason: str = "") -> dict:
    return {
        "requested": bool(requested),
        "available": False,
        "enabled": False,
        "active": False,
        "fallback": not requested,
        "reason": reason,
        "flash_attn_version": "",
        "triton_version": "",
    }


def _same_path(left: object, right: object) -> bool:
    try:
        return os.path.abspath(os.fspath(left)) == os.path.abspath(os.fspath(right))
    except (TypeError, ValueError, OSError):
        return False


def _path_is_under(path: object, root: object) -> bool:
    try:
        return os.path.commonpath((os.path.abspath(os.fspath(path)), os.path.abspath(os.fspath(root)))) == os.path.abspath(os.fspath(root))
    except (TypeError, ValueError, OSError):
        return False


def _prepend_accel_overlay() -> tuple[str | None, str | None]:
    raw = os.environ.get(ACCEL_OVERLAY_ENV)
    if not raw:
        return None, None
    overlay = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(overlay):
        logger.warning("IndexTTS 2.5 accel overlay is invalid")
        logger.debug("Invalid IndexTTS 2.5 accel overlay: %s", overlay)
        return None, "overlay_invalid"
    sys.path[:] = [entry for entry in sys.path if not _same_path(entry, overlay)]
    sys.path.insert(0, overlay)
    return overlay, None


def _module_version(module: object) -> str:
    return str(getattr(module, "__version__", "") or "unknown")


def _triton_version(module: object) -> str:
    for distribution in ("triton-windows", "triton"):
        try:
            return metadata.version(distribution)
        except metadata.PackageNotFoundError:
            continue
    return _module_version(module)


def _is_affected_triton_windows(module: object) -> bool:
    try:
        return metadata.version("triton-windows") == _TRITON_WINDOWS_VERSION
    except metadata.PackageNotFoundError:
        # GPU-free tests can provide a module-only fake; real wheels carry
        # distribution metadata even though triton.__version__ is 3.4.0.
        return _module_version(module) == _TRITON_WINDOWS_VERSION


def _resolve_triton_tcc(triton_module: object) -> str | None:
    origin = getattr(triton_module, "__file__", None)
    if not origin:
        try:
            origin = importlib.util.find_spec("triton").origin
        except (AttributeError, ImportError, ModuleNotFoundError, ValueError):
            origin = None
    if not origin:
        return None
    package_dir = os.path.dirname(os.path.abspath(os.fspath(origin)))
    candidate = os.path.join(package_dir, "runtime", "tcc", "tcc.exe")
    return candidate if os.path.isfile(candidate) else None


def _valid_cc(value: object) -> bool:
    if not value:
        return False
    try:
        text = os.path.expanduser(os.fspath(value))
    except (TypeError, ValueError):
        return False
    return os.path.isfile(text) or shutil.which(text) is not None


def _prepare_v25_acceleration(requested: bool | None = None) -> dict:
    """Prepare optional Windows-only GPT acceleration before IndexTTS import."""
    if requested is None:
        requested = bool(_cfg.get_tts_performance("2.5").get("gpt_accel"))
    report = _base_accel_status(requested=bool(requested))
    if os.environ.get(ACCEL_DISABLE_ENV) == "1":
        report["requested"] = False
        report["fallback"] = True
        report["reason"] = "emergency_disabled"
        return _store_accel_status(report)
    if not requested:
        report["requested"] = False
        report["fallback"] = True
        report["reason"] = "user_disabled"
        return _store_accel_status(report)
    if not _is_windows():
        report["fallback"] = True
        report["reason"] = "unsupported_platform"
        return _store_accel_status(report)

    overlay, overlay_error = _prepend_accel_overlay()
    if overlay_error:
        report["fallback"] = True
        report["reason"] = overlay_error
        return _store_accel_status(report)
    try:
        flash_attn = importlib.import_module("flash_attn")
        triton = importlib.import_module("triton")
    except (ImportError, ModuleNotFoundError, OSError):
        logger.warning("IndexTTS 2.5 GPT acceleration unavailable: dependency_missing")
        logger.debug("Accel dependency import failed", exc_info=True)
        report["fallback"] = True
        report["reason"] = "dependency_missing"
        return _store_accel_status(report)
    except Exception:  # noqa: BLE001 - native extension imports can be non-ImportError
        logger.warning("IndexTTS 2.5 GPT acceleration unavailable: dependency_import_failed")
        logger.debug("Accel dependency import failed", exc_info=True)
        report["fallback"] = True
        report["reason"] = "dependency_import_failed"
        return _store_accel_status(report)

    report["flash_attn_version"] = _module_version(flash_attn)
    report["triton_version"] = _triton_version(triton)
    if overlay and not all(
        _path_is_under(getattr(module, "__file__", None), overlay)
        for module in (flash_attn, triton)
    ):
        logger.warning("IndexTTS 2.5 GPT acceleration unavailable: overlay_source_mismatch")
        logger.debug("Accel modules did not resolve from overlay: %s", overlay)
        report["fallback"] = True
        report["reason"] = "overlay_source_mismatch"
        return _store_accel_status(report)

    current_cc = os.environ.get("CC")
    if current_cc and _valid_cc(current_cc):
        logger.debug("Using existing process-local CC for Triton")
    else:
        # An external overlay owns its bundled TinyCC; prefer it so Triton's
        # compiler and runtime support files come from the same package.
        resolved_cc = _resolve_triton_tcc(triton) if overlay else None
        if resolved_cc is None:
            for name in ("cl", "gcc", "clang"):
                resolved_cc = shutil.which(name)
                if resolved_cc:
                    break
        if resolved_cc is None:
            resolved_cc = _resolve_triton_tcc(triton)
        if resolved_cc is None:
            report["fallback"] = True
            report["reason"] = "compiler_missing"
            return _store_accel_status(report)
        os.environ["CC"] = resolved_cc
        logger.debug("Using process-local Triton CC: %s", resolved_cc)

    report["available"] = True
    report["enabled"] = True
    report["reason"] = "runtime_ready"
    report["fallback"] = False
    return _store_accel_status(report)


class _TritonCacheOSProxy:
    """Proxy only for Triton's module-local ``os`` binding."""

    def __init__(self, original):
        self._original = original

    def removedirs(self, path):
        # triton-windows 3.4.0.post21 can remove cache parents via removedirs;
        # the affected temp directory needs one leaf removal only.
        return self._original.rmdir(path)

    def __getattr__(self, name):
        return getattr(self._original, name)


@contextmanager
def _scoped_triton_cache_workaround():
    """Apply the verified Triton cache fix only around ``FileCacheManager.put``."""
    if not _is_windows():
        yield
        return
    cache_module = None
    try:
        triton = importlib.import_module("triton")
        if _is_affected_triton_windows(triton):
            candidate = importlib.import_module("triton.runtime.cache")
            manager = getattr(candidate, "FileCacheManager", None)
            if callable(getattr(manager, "put", None)):
                original_os = getattr(candidate, "os", None)
                if original_os is not None and not isinstance(original_os, _TritonCacheOSProxy):
                    cache_module = candidate
    except (ImportError, ModuleNotFoundError, AttributeError, TypeError) as exc:
        logger.debug("Triton cache workaround not installed: %s", exc)
    if cache_module is None:
        yield
        return
    original_put = manager.put

    def put_with_leaf_cleanup(*args, **kwargs):
        with _TRITON_CACHE_WORKAROUND_LOCK:
            put_os = cache_module.os
            cache_module.os = _TritonCacheOSProxy(put_os)
            try:
                return original_put(*args, **kwargs)
            finally:
                cache_module.os = put_os

    manager.put = put_with_leaf_cleanup
    try:
        yield
    finally:
        manager.put = original_put


def _v25_accel_is_active(engine: object) -> bool:
    """Check the actual IndexTTS 2.5 GPT AccelInferenceEngine state."""
    candidates = (getattr(engine, "gpt", None), engine)
    try:
        return any(getattr(candidate, "accel_engine", None) is not None for candidate in candidates)
    except Exception:  # noqa: BLE001 - defensive around third-party properties
        return False


def _cuda_is_available(torch_module: object) -> bool:
    try:
        cuda = getattr(torch_module, "cuda", None)
        return bool(cuda is not None and cuda.is_available())
    except Exception:  # noqa: BLE001 - capability probes are best effort
        return False


def _performance_status(
    requested: bool,
    effective: bool,
    reason: str,
) -> dict[str, object]:
    return {
        "requested": bool(requested),
        "effective": bool(effective),
        "state": (
            "disabled" if not requested
            else "active" if effective
            else "unavailable"
        ),
        "reason": str(reason or ""),
    }


def _pending_performance_status(requested: bool, reason: str = "capability_available") -> dict[str, object]:
    return {
        "requested": bool(requested),
        "effective": False,
        "state": "pending" if requested else "disabled",
        "reason": reason,
    }


def _prepare_v2_performance(
    requested: Mapping[str, object],
    torch_module: object,
) -> tuple[dict[str, bool], dict[str, dict[str, object]]]:
    """Resolve v2 optional capabilities without touching the v2.5 overlay."""
    values = {
        field: bool(requested.get(field))
        for field in (
            "cuda_kernel", "gpt_accel", "s2mel_compile", "conditioning_cache"
        )
    }
    statuses = {
        field: _pending_performance_status(value)
        for field, value in values.items()
    }
    cuda_available = _cuda_is_available(torch_module)
    if values["cuda_kernel"] and not cuda_available:
        values["cuda_kernel"] = False
        statuses["cuda_kernel"] = _performance_status(
            True, False, "unsupported_device"
        )
    if values["gpt_accel"]:
        if not cuda_available:
            values["gpt_accel"] = False
            statuses["gpt_accel"] = _performance_status(
                True, False, "unsupported_device"
            )
        else:
            try:
                importlib.import_module("flash_attn")
                importlib.import_module("indextts.accel")
            except (ImportError, ModuleNotFoundError, OSError):
                values["gpt_accel"] = False
                statuses["gpt_accel"] = _performance_status(
                    True, False, "dependency_missing"
                )
                logger.warning(
                    "IndexTTS 2 GPT acceleration unavailable: dependency_missing"
                )
            except Exception:  # noqa: BLE001 - native extensions vary by platform
                values["gpt_accel"] = False
                statuses["gpt_accel"] = _performance_status(
                    True, False, "dependency_import_failed"
                )
                logger.warning(
                    "IndexTTS 2 GPT acceleration unavailable: dependency_import_failed"
                )
    if values["s2mel_compile"]:
        if not callable(getattr(torch_module, "compile", None)):
            values["s2mel_compile"] = False
            statuses["s2mel_compile"] = _performance_status(
                True, False, "torch_compile_missing"
            )
        else:
            try:
                importlib.import_module("triton")
            except (ImportError, ModuleNotFoundError, OSError):
                values["s2mel_compile"] = False
                statuses["s2mel_compile"] = _performance_status(
                    True, False, "dependency_missing"
                )
                logger.warning(
                    "IndexTTS 2 s2mel torch.compile unavailable: dependency_missing"
                )
            except Exception:  # noqa: BLE001 - native extensions vary by platform
                values["s2mel_compile"] = False
                statuses["s2mel_compile"] = _performance_status(
                    True, False, "dependency_import_failed"
                )
                logger.warning(
                    "IndexTTS 2 s2mel torch.compile unavailable: dependency_import_failed"
                )
    # Conditioning is a Studio adapter feature.  The actual upstream field
    # check happens after construction, once the concrete engine exists.
    return values, statuses


def _constructor_kwargs_for(cls: object, kwargs: Mapping[str, object]) -> dict[str, object]:
    """Drop optional kwargs when an older installed upstream lacks them."""
    try:
        signature = inspect.signature(cls)
    except (TypeError, ValueError):
        return dict(kwargs)
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return dict(kwargs)
    return {
        key: value
        for key, value in kwargs.items()
        if key in signature.parameters
    }


def _optional_capability_error(exc: BaseException, field: str) -> bool:
    message = str(exc).lower()
    tokens = {
        "gpt_accel": ("accel", "flash_attn", "cuda", "triton"),
        "s2mel_compile": ("compile", "inductor", "triton"),
        "cuda_kernel": ("cuda", "kernel", "activation"),
    }.get(field, ())
    return isinstance(exc, (ImportError, ModuleNotFoundError, AttributeError, RuntimeError, OSError)) and any(
        token in message for token in tokens
    )


def _generic_accel_is_active(engine: object) -> bool:
    candidates = (getattr(engine, "gpt", None), engine)
    try:
        return any(
            hasattr(candidate, "accel_engine")
            and getattr(candidate, "accel_engine", None) is not None
            for candidate in candidates
            if candidate is not None
        )
    except Exception:  # noqa: BLE001 - defensive around third-party properties
        return False


def _conditioning_cache_supported(engine: object) -> bool:
    try:
        return all(hasattr(engine, field) for field in _CONDITIONING_FIELDS)
    except Exception:  # noqa: BLE001 - third-party objects may expose properties
        return False


def _reference_identity(path: object) -> str:
    """Use path + metadata, never basename, for conditioning cache keys."""
    try:
        normalized = os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(path))))
    except (TypeError, ValueError, OSError):
        normalized = str(path or "")
    try:
        stat = os.stat(normalized)
        return f"{normalized}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        return f"{normalized}|missing"


def _conditioning_cache_key(
    speaker_audio: object,
    emotion_audio: object,
) -> tuple[str, str]:
    return _reference_identity(speaker_audio), _reference_identity(emotion_audio)


def _clear_conditioning_cache() -> None:
    _CONDITIONING_CACHE.clear()


def _invalidate_upstream_conditioning(engine: object) -> None:
    for field in _CONDITIONING_FIELDS:
        try:
            setattr(engine, field, None)
        except (AttributeError, TypeError):
            return


def _restore_conditioning_bundle(
    engine: object,
    bundle: Mapping[str, object],
) -> bool:
    try:
        for field in _CONDITIONING_FIELDS:
            setattr(engine, field, bundle[field])
    except (KeyError, AttributeError, TypeError):
        return False
    return True


def _capture_conditioning_bundle(engine: object) -> dict[str, object] | None:
    if not _conditioning_cache_supported(engine):
        return None
    try:
        return {field: getattr(engine, field) for field in _CONDITIONING_FIELDS}
    except (AttributeError, TypeError):
        return None


def _prepare_conditioning_cache(
    engine: object,
    key: tuple[str, str],
) -> bool:
    """Restore a hit or force upstream to recompute a miss."""
    bundle = _CONDITIONING_CACHE.pop(key, None)
    if bundle is not None and _restore_conditioning_bundle(engine, bundle):
        _CONDITIONING_CACHE[key] = bundle
        logger.debug("TTS2 conditioning cache hit")
        return True
    _invalidate_upstream_conditioning(engine)
    logger.debug("TTS2 conditioning cache miss")
    return False


def _store_conditioning_cache(engine: object, key: tuple[str, str]) -> None:
    bundle = _capture_conditioning_bundle(engine)
    if bundle is None:
        return
    _CONDITIONING_CACHE.pop(key, None)
    _CONDITIONING_CACHE[key] = bundle
    while len(_CONDITIONING_CACHE) > CONDITIONING_CACHE_MAXSIZE:
        _CONDITIONING_CACHE.popitem(last=False)


def _finalize_v2_performance(
    engine: object,
    requested: Mapping[str, object],
    statuses: Mapping[str, Mapping[str, object]],
    constructor_kwargs: Mapping[str, object],
) -> tuple[dict[str, bool], dict[str, dict[str, object]]]:
    effective: dict[str, bool] = {}
    final_status: dict[str, dict[str, object]] = {}
    for field in (
        "cuda_kernel", "gpt_accel", "s2mel_compile", "conditioning_cache"
    ):
        is_requested = bool(requested.get(field))
        previous = dict(statuses.get(field) or {})
        if not is_requested:
            effective[field] = False
            final_status[field] = _performance_status(False, False, "disabled")
            continue
        if previous.get("state") == "unavailable":
            effective[field] = False
            final_status[field] = previous
            continue
        if field == "cuda_kernel":
            actual = getattr(engine, "use_cuda_kernel", None)
            actual = bool(actual) if actual is not None else bool(
                constructor_kwargs.get("use_cuda_kernel")
            )
            effective[field] = actual
            final_status[field] = _performance_status(
                True, actual, "enabled" if actual else "kernel_unavailable"
            )
        elif field == "gpt_accel":
            actual = (
                "use_accel" in constructor_kwargs
                and _generic_accel_is_active(engine)
            )
            effective[field] = actual
            final_status[field] = _performance_status(
                True, actual, "enabled" if actual else "accel_inactive"
            )
        elif field == "s2mel_compile":
            compile_engine = getattr(engine, "s2mel", None)
            actual = bool(getattr(engine, "use_torch_compile", False))
            if actual and compile_engine is not None:
                actual = callable(getattr(compile_engine, "enable_torch_compile", None))
            effective[field] = actual
            final_status[field] = _performance_status(
                True, actual, "enabled" if actual else "compile_unavailable"
            )
        else:
            actual = _conditioning_cache_supported(engine)
            effective[field] = actual
            final_status[field] = _performance_status(
                True, actual, "enabled" if actual else "upstream_fields_missing"
            )
    return effective, final_status



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
        use_torch_compile: bool = False,
    ) -> dict[str, object]:
        return {
            "cfg_path": cfg_path,
            "model_dir": model_dir,
            "use_fp16": precision == "FP16",
            "use_cuda_kernel": use_cuda_kernel,
            "use_deepspeed": use_deepspeed,
            "use_accel": use_accel,
            "use_torch_compile": use_torch_compile,
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
    """Native IndexTTS 2.5 adapter with production GPT Accel policy."""

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
        use_accel: bool = False,
    ) -> dict[str, object]:
        return {
            "cfg_path": cfg_path,
            "model_dir": model_dir,
            "use_bf16": precision == "BF16",
            "use_cuda_kernel": False,
            "use_deepspeed": False,
            "use_accel": bool(use_accel),
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
    use_torch_compile: bool = False,
    *,
    profile: Mapping[str, object] | None = None,
):
    global _tts, _ENGINE_PROFILE, _CONDITIONING_CACHE_ENABLED
    with _ENGINE_LOCK:
        resolved = _resolved_profile(profile, model_dir)
        version = str(resolved["engine_version"])
        if profile is None:
            # Preserve the historical positional API while making the new
            # profile path authoritative for production/runtime tasks.
            performance = dict(resolved.get("performance") or {})
            if version == "2":
                performance.update({
                    "cuda_kernel": bool(use_cuda_kernel),
                    "gpt_accel": bool(use_accel),
                    "s2mel_compile": bool(use_torch_compile),
                })
                resolved["performance"] = performance
            if version == "2" and not use_fp16:
                resolved["precision"] = "FP32"
                from .tts_profile import cache_identity

                resolved["cache_identity"] = cache_identity(resolved)
        if _tts is not None:
            if profile and not _profile_matches(_ENGINE_PROFILE, resolved):
                raise RuntimeError(
                    "TTS runtime 已加载另一 engine identity；必须先 recycle 后再切换"
                )
            return
        _clear_conditioning_cache()
        __import__("torch")
        torch_module = sys.modules.get("torch")
        model_dir = str(resolved["model_dir"])
        backend = _backend_for(version)
        accel_report = None
        v2_constructor_values: dict[str, bool] = {}
        v2_status: dict[str, dict[str, object]] = {}
        requested_performance = dict(resolved.get("performance") or {})
        if version == "2":
            v2_constructor_values, v2_status = _prepare_v2_performance(
                requested_performance,
                torch_module,
            )
        if version == "2.5":
            # This must precede ``infer_v2_5`` import: its import graph loads
            # the GPT acceleration dependencies before the constructor runs.
            requested_accel = bool(requested_performance.get("gpt_accel"))
            try:
                accel_report = _prepare_v25_acceleration(requested=requested_accel)
            except TypeError as exc:
                if "requested" not in str(exc):
                    raise
                # Older test/launcher seams monkeypatch the no-argument
                # preparation hook; retain that compatibility path.
                accel_report = _prepare_v25_acceleration()
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
        if version == "2.5":
            common = backend.constructor_kwargs(
                cfg_path=str(cfg_path),
                model_dir=model_dir,
                precision=precision,
                use_accel=bool(accel_report and accel_report.get("available")),
            )
        else:
            common = backend.constructor_kwargs(
                cfg_path=str(cfg_path),
                model_dir=model_dir,
                precision=precision,
                use_cuda_kernel=v2_constructor_values.get("cuda_kernel", False),
                use_deepspeed=use_deepspeed,
                use_accel=v2_constructor_values.get("gpt_accel", False),
                use_torch_compile=v2_constructor_values.get("s2mel_compile", False),
            )
        common = _constructor_kwargs_for(IndexTTS2, common)
        if version == "2":
            for field, constructor_field in (
                ("cuda_kernel", "use_cuda_kernel"),
                ("gpt_accel", "use_accel"),
                ("s2mel_compile", "use_torch_compile"),
            ):
                if requested_performance.get(field) and constructor_field not in common:
                    v2_status[field] = _performance_status(
                        True, False, "constructor_unsupported"
                    )
                    v2_constructor_values[field] = False
        constructor_scope = (
            _scoped_triton_cache_workaround()
            if accel_report and accel_report.get("available")
            else nullcontext()
        )
        try:
            with constructor_scope:
                constructed = IndexTTS2(**common)
        except Exception as exc:
            if version == "2":
                fallback_common = dict(common)
                fallback_fields: list[str] = []
                for field, constructor_field in (
                    ("gpt_accel", "use_accel"),
                    ("s2mel_compile", "use_torch_compile"),
                ):
                    if (
                        bool(requested_performance.get(field))
                        and bool(common.get(constructor_field))
                        and _optional_capability_error(exc, field)
                    ):
                        fallback_common[constructor_field] = False
                        fallback_fields.append(field)
                if fallback_fields:
                    logger.warning(
                        "IndexTTS 2 optional capability unavailable; "
                        "falling back to baseline fields=%s error=%s",
                        ",".join(fallback_fields),
                        str(exc),
                    )
                    with nullcontext():
                        constructed = IndexTTS2(**fallback_common)
                    for field in fallback_fields:
                        v2_status[field] = _performance_status(
                            True, False, "init_failed"
                        )
                        v2_constructor_values[field] = False
                    common = fallback_common
                else:
                    raise
            else:
                if accel_report and accel_report.get("available"):
                    accel_report["reason"] = "accel_init_failed"
                    accel_report["fallback"] = False
                    _store_accel_status(accel_report)
                    logger.exception("IndexTTS 2.5 GPT acceleration initialization failed")
                raise
        if accel_report and accel_report.get("available"):
            if not _v25_accel_is_active(constructed):
                accel_report["reason"] = "accel_init_failed"
                accel_report["fallback"] = False
                _store_accel_status(accel_report)
                raise RuntimeError(
                    "accel_init_failed: IndexTTS 2.5 did not expose AccelInferenceEngine"
                )
            accel_report["active"] = True
            accel_report["reason"] = "accel_active"
            accel_report["fallback"] = False
            _store_accel_status(accel_report)
        _tts = constructed
        _ENGINE_PROFILE = dict(resolved)
        actual_device = getattr(_tts, "device", None)
        if actual_device is not None:
            _ENGINE_PROFILE["device"] = str(actual_device)
        if version == "2":
            effective_performance, performance_status = _finalize_v2_performance(
                constructed,
                requested_performance,
                v2_status,
                common,
            )
        else:
            requested_accel = bool(requested_performance.get("gpt_accel"))
            effective_accel = bool(accel_report and accel_report.get("active"))
            if not requested_accel:
                accel_state = _performance_status(False, False, "disabled")
            elif effective_accel:
                accel_state = _performance_status(True, True, "enabled")
            else:
                accel_state = _performance_status(
                    True,
                    False,
                    str((accel_report or {}).get("reason") or "unavailable"),
                )
            effective_performance = {"gpt_accel": effective_accel}
            performance_status = {"gpt_accel": accel_state}
        _ENGINE_PROFILE["effective_performance"] = effective_performance
        _ENGINE_PROFILE["performance_status"] = performance_status
        _CONDITIONING_CACHE_ENABLED = bool(
            version == "2" and effective_performance.get("conditioning_cache")
        )
        for field, status in performance_status.items():
            if status.get("state") == "unavailable":
                logger.warning(
                    "TTS capability unavailable: version=%s field=%s "
                    "requested=%s effective=%s reason=%s fallback=baseline",
                    version,
                    field,
                    status.get("requested"),
                    status.get("effective"),
                    status.get("reason"),
                )
        _engine_capabilities()
        if version == "2.5" and accel_report:
            logger.info(
                "IndexTTS 2.5 acceleration: requested=%s available=%s active=%s "
                "fallback=%s flash_attn=%s triton=%s reason=%s",
                accel_report.get("requested"),
                accel_report.get("available"),
                accel_report.get("active"),
                accel_report.get("fallback"),
                accel_report.get("flash_attn_version") or "unknown",
                accel_report.get("triton_version") or "unknown",
                accel_report.get("reason"),
            )
        if version == "2":
            logger.info(
                "TTS runtime: version=2 fp16=%s cuda_kernel=%s gpt_accel=%s "
                "s2mel_compile=%s conditioning_cache=%s",
                precision == "FP16",
                effective_performance.get("cuda_kernel", False),
                effective_performance.get("gpt_accel", False),
                effective_performance.get("s2mel_compile", False),
                effective_performance.get("conditioning_cache", False),
            )
        else:
            logger.info(
                "TTS runtime: version=2.5 cuda_kernel=false gpt_accel=%s",
                effective_performance.get("gpt_accel", False),
            )
        logger.info("Model loaded.")


def _profile_matches(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    from .tts_profile import profile_matches

    return profile_matches(left, right)


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
    emo_audio_prompt: str | None = None,
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
        conditioning_key = None
        if version == "2" and _CONDITIONING_CACHE_ENABLED:
            effective_emotion_audio = speaker_audio if use_emo else (
                emo_audio_prompt or speaker_audio
            )
            if _conditioning_cache_supported(_tts):
                conditioning_key = _conditioning_cache_key(
                    speaker_audio,
                    effective_emotion_audio,
                )
                _prepare_conditioning_cache(_tts, conditioning_key)
            else:
                logger.warning(
                    "TTS2 conditioning cache unavailable: upstream_fields_missing"
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
                if (
                    emo_audio_prompt is not None
                    and "emo_audio_prompt" in param_names
                    and not use_emo
                ):
                    infer_kwargs["emo_audio_prompt"] = emo_audio_prompt
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
                    infer_scope = (
                        _scoped_triton_cache_workaround()
                        if version == "2.5" and _ACCEL_STATUS.get("active")
                        else nullcontext()
                    )
                    with infer_scope:
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
                if conditioning_key is not None:
                    _store_conditioning_cache(_tts, conditioning_key)
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
                        emo_audio_prompt=emo_audio_prompt,
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
                        emo_audio_prompt=emo_audio_prompt,
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
    global _tts, _ENGINE_PROFILE, _LAST_ADAPTER_REPORT, _ACCEL_STATUS
    global _CONDITIONING_CACHE_ENABLED
    global _CAPABILITY_ENGINE_ID, _CAPABILITY_ENGINE_REF
    global _INFER_PARAM_NAMES, _INFER_HAS_VAR_KEYWORD
    with _ENGINE_LOCK:
        _tts = None
        _ENGINE_PROFILE = {}
        _LAST_ADAPTER_REPORT = {}
        _ACCEL_STATUS = {
            "requested": False,
            "available": False,
            "enabled": False,
            "active": False,
            "fallback": False,
            "reason": "not_initialized",
            "flash_attn_version": "",
            "triton_version": "",
        }
        _CAPABILITY_ENGINE_ID = None
        _CAPABILITY_ENGINE_REF = None
        _INFER_PARAM_NAMES = frozenset()
        _INFER_HAS_VAR_KEYWORD = False
        _SPEAKER_EMB_CACHE.clear()
        _CONDITIONING_CACHE_ENABLED = False
        _clear_conditioning_cache()
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
