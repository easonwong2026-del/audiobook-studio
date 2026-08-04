from __future__ import annotations

import json

from ai.providers import DeepSeekProvider


def _transport(captured):
    def transport(url, headers, payload, timeout):
        captured.update(payload)
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"ok": True})},
            }]
        }

    return transport


def test_deepseek_reasoning_modes_are_explicit_and_model_name_is_ignored():
    enabled = {}
    DeepSeekProvider(
        api_key="x", model="deepseek-chat", transport=_transport(enabled)
    )._request_json("system", "user", task="chapter_analysis", reasoning_mode="high")
    assert enabled["thinking"] == {"type": "enabled"}
    assert enabled["reasoning_effort"] == "high"

    disabled = {}
    DeepSeekProvider(
        api_key="x", model="deepseek-reasoner", transport=_transport(disabled)
    )._request_json("system", "user", task="chapter_analysis", reasoning_mode="off")
    assert disabled["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in disabled

    connection = {}
    DeepSeekProvider(
        api_key="x", model="deepseek-reasoner", transport=_transport(connection)
    )._request_json("system", "user", task="connection_test", reasoning_mode="max")
    assert connection["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in connection


def test_deepseek_max_mode_is_explicit():
    captured = {}
    DeepSeekProvider(
        api_key="x", model="custom-gateway-model", transport=_transport(captured)
    )._request_json("system", "user", task="repair", reasoning_mode="max")
    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "max"
