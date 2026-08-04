"""剧本分析 Provider。

第一阶段提供可离线运行的 ``LocalDirectorProvider``。远程模型 Provider 只需实现
``ScriptAnalysisProvider``，不应把厂商 SDK 或鉴权逻辑泄漏到业务服务。
"""
from __future__ import annotations

from typing import Any

from ._remote import SourceChunk
from .base import ScriptAnalysisProvider
from .deepseek import DeepSeekProvider
from .exceptions import (
    ProviderOutputInvalidJsonError,
    ProviderOutputTruncatedError,
)
from .local import LocalDirectorProvider
from .openai import OpenAIProvider
from .reasoning import PROVIDER_REASONING_CAPABILITIES


def create_provider(
    name: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 180.0,
    reasoning_mode: str | bool | None = None,
) -> ScriptAnalysisProvider:
    """按 UI / CLI 名称创建 Provider。传入参数覆盖环境变量默认值。"""
    normalized = (name or "local").strip().lower()
    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    if model:
        kwargs["model"] = model
    kwargs["timeout"] = timeout
    if reasoning_mode is not None and normalized in {"openai", "deepseek"}:
        kwargs["reasoning_mode"] = reasoning_mode

    if normalized == "local":
        return LocalDirectorProvider()
    if normalized == "openai":
        return OpenAIProvider(**kwargs)
    if normalized == "deepseek":
        return DeepSeekProvider(**kwargs)
    raise ValueError(f"不支持的 AI Provider：{name}")


__all__ = [
    "PROVIDER_REASONING_CAPABILITIES",
    "DeepSeekProvider",
    "LocalDirectorProvider",
    "OpenAIProvider",
    "ProviderOutputInvalidJsonError",
    "ProviderOutputTruncatedError",
    "ScriptAnalysisProvider",
    "SourceChunk",
    "create_provider",
]
