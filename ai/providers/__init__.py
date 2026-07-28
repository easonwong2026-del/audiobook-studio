"""剧本分析 Provider。

第一阶段提供可离线运行的 ``LocalDirectorProvider``。远程模型 Provider 只需实现
``ScriptAnalysisProvider``，不应把厂商 SDK 或鉴权逻辑泄漏到业务服务。
"""

from typing import Optional

from .base import ScriptAnalysisProvider
from .deepseek import DeepSeekProvider
from .local import LocalDirectorProvider
from .openai import OpenAIProvider


def create_provider(name: str, *, model: Optional[str] = None) -> ScriptAnalysisProvider:
    """按 UI / CLI 名称创建 Provider。密钥只从对应环境变量读取。"""
    normalized = (name or "local").strip().lower()
    if normalized == "local":
        return LocalDirectorProvider()
    if normalized == "openai":
        return OpenAIProvider(model=model or None)
    if normalized == "deepseek":
        return DeepSeekProvider(model=model or None)
    raise ValueError(f"不支持的 AI Provider：{name}")


__all__ = [
    "DeepSeekProvider",
    "LocalDirectorProvider",
    "OpenAIProvider",
    "ScriptAnalysisProvider",
    "create_provider",
]
