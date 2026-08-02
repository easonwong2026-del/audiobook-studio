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


def test_reasoning_is_enabled_only_for_reasoning_models_and_ai_tasks():
    enabled = {}
    DeepSeekProvider(
        api_key="x", model="deepseek-reasoner", transport=_transport(enabled)
    )._request_json("system", "user", task="character_bible")
    assert enabled["thinking"] == {"type": "enabled"}

    disabled = {}
    DeepSeekProvider(
        api_key="x", model="deepseek-chat", transport=_transport(disabled)
    )._request_json("system", "user", task="character_bible")
    assert "thinking" not in disabled

    connection = {}
    DeepSeekProvider(
        api_key="x", model="deepseek-reasoner", transport=_transport(connection)
    )._request_json("system", "user", task="connection_test")
    assert "thinking" not in connection
