"""Pure-Python capability and GPU diagnostics for the native TTS runtimes.

This module deliberately does not import IndexTTS2 or torch.  Callers may pass
already-loaded objects when they are available, but merely asking what a
machine can support must not initialize a model, create a CUDA context, or
change the current engine configuration.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Capability:
    """A conservative four-state capability record.

    ``verified`` is intentionally never inferred from importability or a
    signature.  It is evidence supplied by a real-machine benchmark.
    """

    supported: bool = False
    installed: bool = False
    enabled: bool = False
    verified: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "supported": bool(self.supported),
            "installed": bool(self.installed),
            "enabled": bool(self.enabled),
            "verified": bool(self.verified),
        }


def _safe_find_spec(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _safe_signature(value: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(value)
    except (TypeError, ValueError):
        return None


def _parameter_names(value: Any) -> tuple[set[str], bool]:
    signature = _safe_signature(value)
    if signature is None:
        return set(), False
    names = set(signature.parameters)
    has_var_keyword = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    return names, has_var_keyword


def _package_state(
    package_presence: Mapping[str, bool] | None,
    package_name: str,
) -> bool:
    if package_presence is not None and package_name in package_presence:
        return bool(package_presence[package_name])
    loaded = sys.modules.get(package_name)
    return loaded is not None or _safe_find_spec(package_name)


def gpu_snapshot(torch_module: Any | None = None) -> dict[str, Any]:
    """Return a best-effort CUDA memory snapshot without importing torch.

    The no-CUDA contract is deliberately small and stable::

        {"available": False}

    A partially available or broken CUDA runtime also degrades to that shape.
    This function is safe to call at task/chapter boundaries and should not be
    called for every token or segment by default.
    """

    result: dict[str, Any] = {"available": False}
    if torch_module is None:
        torch_module = sys.modules.get("torch")
    if torch_module is None:
        return result
    try:
        cuda = getattr(torch_module, "cuda", None)
        if cuda is None or not bool(getattr(cuda, "is_available", lambda: False)()):
            return result
        free_bytes, total_bytes = cuda.mem_get_info()
        result.update(
            {
                "available": True,
                "allocated": int(cuda.memory_allocated()),
                "reserved": int(cuda.memory_reserved()),
                "max_allocated": int(cuda.max_memory_allocated()),
                "free": int(free_bytes),
                "total": int(total_bytes),
            }
        )
    except Exception as exc:  # noqa: BLE001  # native CUDA boundary is best-effort
        del exc
        return {"available": False}
    return result


def detect_engine_capabilities(
    engine: Any | None = None,
    *,
    constructor: Any | None = None,
    torch_module: Any | None = None,
    package_presence: Mapping[str, bool] | None = None,
    enabled_options: Mapping[str, bool] | None = None,
    verified_options: Mapping[str, bool] | None = None,
) -> dict[str, dict[str, bool]]:
    """Inspect public engine signatures and package presence conservatively.

    ``engine`` is an already-created engine object, useful for tests or for a
    native runtime that has already initialized its model.  ``constructor``
    can be supplied as ``IndexTTS2`` after import to inspect constructor
    options without constructing it.  Neither argument is imported here.

    ``enabled_options`` reflects the caller's current configuration; it is not
    guessed from support.  ``verified_options`` is explicit benchmark evidence
    and defaults to false.  In particular, a Mac with a CPU-only torch build
    can never receive ``verified=true`` automatically.
    """

    enabled_options = enabled_options or {}
    verified_options = verified_options or {}
    infer = getattr(engine, "infer", None) if engine is not None else None
    if infer is None and engine is not None:
        infer = getattr(type(engine), "infer", None)
    infer_names, infer_var_keyword = _parameter_names(infer)

    if constructor is None and engine is not None:
        constructor = type(engine)
    constructor_names, constructor_var_keyword = _parameter_names(constructor)

    torch_installed = (
        torch_module is not None
        or sys.modules.get("torch") is not None
        or _package_state(package_presence, "torch")
    )
    cuda_available = bool(gpu_snapshot(torch_module).get("available", False))
    index_installed = _package_state(package_presence, "indextts")
    flash_installed = _package_state(package_presence, "flash_attn")
    deepspeed_installed = _package_state(package_presence, "deepspeed")

    def capability(
        name: str,
        *,
        supported: bool,
        installed: bool,
        enabled: bool = False,
    ) -> dict[str, bool]:
        verified = bool(verified_options.get(name, False))
        # A real CUDA run is required before any engine/runtime option can be
        # marked verified.  Signature and package checks alone are never a
        # performance result, especially on the Mac/CPU test host.
        return Capability(
            supported=bool(supported),
            installed=bool(installed),
            enabled=bool(enabled),
            verified=verified
            and bool(supported)
            and bool(installed)
            and bool(enabled)
            and cuda_available,
        ).as_dict()

    # ``**generation_kwargs`` is the public route used by current IndexTTS2
    # for num_beams.  The exact option is therefore supported when explicitly
    # named or when the public infer signature exposes VAR_KEYWORD.
    num_beams_supported = "num_beams" in infer_names or infer_var_keyword
    cuda_supported = torch_module is not None and getattr(torch_module, "cuda", None) is not None
    return {
        "index_tts2": capability(
            "index_tts2",
            supported=constructor is not None or infer is not None,
            installed=index_installed or engine is not None,
            enabled=engine is not None,
        ),
        "cuda": capability(
            "cuda",
            supported=cuda_supported,
            installed=torch_installed,
            enabled=cuda_available,
        ),
        "num_beams": capability(
            "num_beams",
            supported=num_beams_supported,
            installed=index_installed or engine is not None,
            enabled=enabled_options.get("num_beams", False),
        ),
        "cuda_kernel": capability(
            "cuda_kernel",
            supported="use_cuda_kernel" in constructor_names or constructor_var_keyword,
            installed=index_installed,
            enabled=enabled_options.get("cuda_kernel", False),
        ),
        "accel": capability(
            "accel",
            supported="use_accel" in constructor_names or constructor_var_keyword,
            installed=flash_installed,
            enabled=enabled_options.get("accel", False),
        ),
        "deepspeed": capability(
            "deepspeed",
            supported="use_deepspeed" in constructor_names or constructor_var_keyword,
            installed=deepspeed_installed,
            enabled=enabled_options.get("deepspeed", False),
        ),
        "torch_compile": capability(
            "torch_compile",
            supported="torch_compile" in constructor_names or "use_torch_compile" in constructor_names,
            installed=torch_installed,
            enabled=enabled_options.get("torch_compile", False),
        ),
    }


def capability_summary(**kwargs: Any) -> dict[str, dict[str, bool]]:
    """Named alias for callers that want a report-shaped API."""

    return detect_engine_capabilities(**kwargs)


__all__ = [
    "Capability",
    "capability_summary",
    "detect_engine_capabilities",
    "gpu_snapshot",
]
