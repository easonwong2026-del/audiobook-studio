"""AI 剧本分析独立配置、能力与内置提示词信息。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai.providers.reasoning import (
    PROVIDER_REASONING_CAPABILITIES,
    normalize_reasoning_mode,
)

from . import ai_settings

ANALYSIS_PROTOCOL_VERSION = "chapter-analysis-protocol-v1"
CORE_PROMPT_VERSION = "chapter-analysis-core-v1"
ANALYSIS_DEPTHS = ("quick", "standard", "deep")
CORE_PROMPT = (
    "你是章节剧本结构分析器。只分析当前章节，保留原文字符、顺序与覆盖完整性；"
    "区分旁白、对白、内心独白、引用和场景说明；无法确认说话人时保留未知或候选，"
    "不要把低置信度判断改成旁白。"
)

_DEFAULTS: dict[str, Any] = {
    "depth": "quick",
    "deepseek_reasoning_mode": "high",
    "openai_reasoning_mode": "auto",
    "auto_upgrade_max": True,
    "prompt_supplement": "",
    "core_prompt_version": CORE_PROMPT_VERSION,
    "protocol_version": ANALYSIS_PROTOCOL_VERSION,
}


@dataclass(frozen=True)
class AnalysisSettings:
    depth: str = "quick"
    reasoning_mode: str = "high"
    auto_upgrade_max: bool = True
    prompt_supplement: str = ""
    core_prompt_version: str = CORE_PROMPT_VERSION
    protocol_version: str = ANALYSIS_PROTOCOL_VERSION

    def validate(self) -> None:
        if self.depth not in ANALYSIS_DEPTHS:
            raise ValueError("analysis depth must be quick, standard, or deep")
        if not isinstance(self.reasoning_mode, str) or not self.reasoning_mode:
            raise ValueError("analysis reasoning mode is required")
        if not isinstance(self.auto_upgrade_max, bool):
            raise TypeError("auto_upgrade_max must be boolean")


def _stored() -> dict[str, Any]:
    cfg = ai_settings._read_full_config()
    values = cfg.get("ai_analysis", {})
    return dict(values) if isinstance(values, dict) else {}


class AiAnalysisSettingsService:
    """Read/write only analysis preferences; provider secrets stay elsewhere."""

    @staticmethod
    def get_config() -> dict[str, Any]:
        values = dict(_DEFAULTS)
        values.update(_stored())
        if values["depth"] not in ANALYSIS_DEPTHS:
            values["depth"] = "quick"
        values["auto_upgrade_max"] = bool(values.get("auto_upgrade_max", True))
        return values

    @staticmethod
    def save_config(values: dict[str, Any]) -> dict[str, Any]:
        current = AiAnalysisSettingsService.get_config()
        allowed = set(_DEFAULTS)
        current.update({key: value for key, value in values.items() if key in allowed})
        settings = AnalysisSettings(
            depth=str(current["depth"]),
            reasoning_mode=str(
                current.get("deepseek_reasoning_mode", "high")
            ),
            auto_upgrade_max=bool(current["auto_upgrade_max"]),
            prompt_supplement=str(current.get("prompt_supplement") or ""),
            core_prompt_version=str(current["core_prompt_version"]),
            protocol_version=str(current["protocol_version"]),
        )
        settings.validate()
        full = ai_settings._read_full_config()
        full["ai_analysis"] = current
        ai_settings._write_full_config(full)
        return current

    @staticmethod
    def for_provider(provider: str) -> dict[str, Any]:
        values = AiAnalysisSettingsService.get_config()
        normalized = str(provider or "custom").strip().lower()
        if normalized == "deepseek":
            selected = values.get("deepseek_reasoning_mode", "high")
        elif normalized == "openai":
            selected = values.get("openai_reasoning_mode", "auto")
        else:
            selected = "auto"
        values["provider"] = normalized
        values["reasoning_mode"] = normalize_reasoning_mode(normalized, selected)
        values["capabilities"] = PROVIDER_REASONING_CAPABILITIES.get(
            normalized, PROVIDER_REASONING_CAPABILITIES["custom"]
        )
        return values

    @staticmethod
    def prompt_preview(supplement: str = "") -> str:
        extra = str(supplement or "").strip()
        return CORE_PROMPT if not extra else f"{CORE_PROMPT}\n\n【用户补充】\n{extra}"


__all__ = [
    "ANALYSIS_DEPTHS",
    "ANALYSIS_PROTOCOL_VERSION",
    "CORE_PROMPT",
    "CORE_PROMPT_VERSION",
    "AiAnalysisSettingsService",
    "AnalysisSettings",
]
