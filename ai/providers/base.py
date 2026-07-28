"""AI 剧本分析 Provider 抽象。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class ScriptAnalysisProvider(ABC):
    """把厂商模型调用与剧本导演领域逻辑隔离。

    Provider 可以返回不完全规范的分析结果；字段补全、数值约束、速度平滑和
    structured_script v3 兼容字段生成统一由 ``ScriptDirectorService`` 完成。
    """

    name = "base"

    @abstractmethod
    def analyze_script(
        self,
        text: str,
        *,
        title: str = "",
        author: str = "",
    ) -> Dict[str, Any]:
        """分析完整文本，返回包含章节和 segment 的字典。"""

    @abstractmethod
    def extract_characters(self, text: str) -> List[str]:
        """提取角色名称。"""

    @abstractmethod
    def generate_segments(
        self,
        text: str,
        characters: List[str],
    ) -> List[Dict[str, Any]]:
        """以自然讲话动作为单位生成 segment。"""
