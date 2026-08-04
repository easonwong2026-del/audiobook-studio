from __future__ import annotations

from services import ai_settings
from services.ai_analysis_settings import (
    ANALYSIS_PROTOCOL_VERSION,
    CORE_PROMPT,
    AiAnalysisSettingsService,
)


def test_analysis_settings_are_independent_and_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_settings, "_CONFIG_PATH", str(tmp_path / "config.json"))

    defaults = AiAnalysisSettingsService.for_provider("deepseek")
    assert defaults["depth"] == "quick"
    assert defaults["reasoning_mode"] == "high"
    assert defaults["protocol_version"] == ANALYSIS_PROTOCOL_VERSION

    AiAnalysisSettingsService.save_config(
        {
            "depth": "deep",
            "deepseek_reasoning_mode": "max",
            "auto_upgrade_max": False,
            "prompt_supplement": "保留连续对白，不要猜测未知说话人。",
        }
    )
    reloaded = AiAnalysisSettingsService.for_provider("deepseek")
    assert reloaded["depth"] == "deep"
    assert reloaded["reasoning_mode"] == "max"
    assert reloaded["auto_upgrade_max"] is False
    assert "连续对白" in reloaded["prompt_supplement"]
    assert AiAnalysisSettingsService.for_provider("openai")["reasoning_mode"] == "auto"
    assert AiAnalysisSettingsService.prompt_preview("补充") == f"{CORE_PROMPT}\n\n【用户补充】\n补充"

