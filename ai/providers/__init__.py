"""剧本分析 Provider。

第一阶段提供可离线运行的 ``LocalDirectorProvider``。远程模型 Provider 只需实现
``ScriptAnalysisProvider``，不应把厂商 SDK 或鉴权逻辑泄漏到业务服务。
"""

from typing import Any, Optional

from .base import ScriptAnalysisProvider
from ._remote import SourceChunk
from .deepseek import DeepSeekProvider
from .exceptions import (
    ProviderOutputInvalidJsonError,
    ProviderOutputTruncatedError,
)
from .local import LocalDirectorProvider
from .openai import OpenAIProvider


def create_provider(
    name: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 180.0,
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

    if normalized == "local":
        return LocalDirectorProvider()
    if normalized == "openai":
        return OpenAIProvider(**kwargs)
    if normalized == "deepseek":
        return DeepSeekProvider(**kwargs)
    raise ValueError(f"不支持的 AI Provider：{name}")


__all__ = [
    "DeepSeekProvider",
    "LocalDirectorProvider",
    "OpenAIProvider",
    "ProviderOutputInvalidJsonError",
    "ProviderOutputTruncatedError",
    "ScriptAnalysisProvider",
    "SourceChunk",
    "create_provider",
]
