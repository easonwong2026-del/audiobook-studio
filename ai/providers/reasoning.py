"""Provider 推理能力与请求模式的显式注册表。

推理能力属于 Provider/网关能力，不属于模型名称字符串。所有上层阶段都应
传入明确模式，避免通过 ``reasoner``、``r1`` 等名称猜测 API 行为。
"""
from __future__ import annotations

from typing import Final

REASONING_MODES: Final = ("off", "high", "max")
OPENAI_REASONING_MODES: Final = ("auto", "off", "low", "medium", "high")

PROVIDER_REASONING_CAPABILITIES: Final[dict[str, dict[str, object]]] = {
    "local": {"modes": ("off",), "default": "off", "label": "本地离线基线"},
    "deepseek": {
        "modes": REASONING_MODES,
        "default": "high",
        "label": "DeepSeek thinking",
    },
    "openai": {
        "modes": OPENAI_REASONING_MODES,
        "default": "auto",
        "label": "OpenAI reasoning effort",
    },
    "custom": {
        "modes": ("auto", "off"),
        "default": "auto",
        "label": "兼容代理能力",
    },
}

_OPERATIONAL_TASKS = frozenset(
    {"connection_test", "model_list", "format", "simple", "legacy_script_director"}
)


def normalize_reasoning_mode(provider: str, mode: str | bool | None) -> str:
    """Return a supported mode; bool is kept only for old callers."""
    normalized_provider = str(provider or "custom").strip().lower()
    capabilities = PROVIDER_REASONING_CAPABILITIES.get(
        normalized_provider, PROVIDER_REASONING_CAPABILITIES["custom"]
    )
    supported = tuple(str(item) for item in capabilities["modes"])
    if isinstance(mode, bool):
        requested = "high" if mode else "off"
    else:
        requested = str(mode or "").strip().lower()
    if requested in supported:
        return requested
    if requested == "max" and "high" in supported:
        return "high"
    if "auto" in supported:
        return "auto"
    return str(capabilities["default"])


def default_reasoning_mode(provider: str, task: str) -> str:
    """Choose a stage default without inspecting the model ID."""
    normalized_provider = str(provider or "custom").strip().lower()
    if task in _OPERATIONAL_TASKS:
        return "off"
    if task in {"repair", "chapter_analysis_repair", "script_review", "deep_review"}:
        if normalized_provider == "deepseek":
            return "max"
        if normalized_provider == "openai":
            return "high"
    if normalized_provider == "deepseek":
        return "high"
    if normalized_provider == "openai":
        return "auto"
    return "auto"


def resolve_reasoning_mode(
    provider: str,
    task: str,
    requested: str | bool | None = None,
) -> str:
    """Resolve operational safety first, then explicit choice, then stage default."""
    if task in _OPERATIONAL_TASKS:
        return "off"
    if requested is not None:
        return normalize_reasoning_mode(provider, requested)
    return normalize_reasoning_mode(provider, default_reasoning_mode(provider, task))


def is_deepseek_thinking_mode(mode: str) -> bool:
    return mode in {"high", "max"}

