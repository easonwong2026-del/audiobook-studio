"""远程 AI 剧本导演 Provider 契约测试（全程 fake transport，不访问网络）。"""
from __future__ import annotations

import json

import pytest

from ai.providers import (
    DeepSeekProvider,
    OpenAIProvider,
    create_provider,
)
from script_director_cli import build_parser
from services.script_director import ScriptDirectorService


def _provider_script():
    return {
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [{
                "speaker": "张三",
                "text": "开始吧。",
                "emotion": "confident",
                "emotion_strength": 0.7,
                "delivery": {
                    "speed": 0.95,
                    "pitch": 0,
                    "intensity": 0.7,
                    "breath": "light",
                },
                "pause_before": 0,
                "pause_after": 800,
                "pauses": [],
            }],
        }]
    }


def test_deepseek_uses_json_output_and_current_endpoint():
    captured = {}

    def transport(url, headers, payload, timeout):
        captured.update(
            url=url,
            headers=headers,
            payload=payload,
            timeout=timeout,
        )
        return {
            "choices": [{
                "message": {"content": json.dumps(_provider_script(), ensure_ascii=False)}
            }]
        }

    provider = DeepSeekProvider(api_key="test-key", transport=transport)
    result = provider.analyze_script("张三说道：“开始吧。”")

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["payload"]["model"] == "deepseek-v4-pro"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert result["provider"] == "deepseek"


def test_openai_uses_responses_json_output():
    captured = {}

    def transport(url, headers, payload, timeout):
        captured.update(url=url, payload=payload)
        return {
            "output": [{
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": json.dumps(_provider_script(), ensure_ascii=False),
                }],
            }]
        }

    provider = OpenAIProvider(api_key="test-key", transport=transport)
    result = provider.analyze_script("张三说道：“开始吧。”")

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["payload"]["model"] == "gpt-5.6"
    assert captured["payload"]["text"]["format"] == {"type": "json_object"}
    assert result["provider"] == "openai"


def test_remote_output_runs_through_common_quality_guards():
    def transport(url, headers, payload, timeout):
        raw = _provider_script()
        raw["chapters"][0]["segments"][0]["delivery"]["speed"] = 1.8
        return {
            "choices": [{
                "message": {"content": json.dumps(raw, ensure_ascii=False)}
            }]
        }

    provider = DeepSeekProvider(api_key="test-key", transport=transport)
    script = ScriptDirectorService(provider).analyze_text("文本")
    segment = script["chapters"][0]["segments"][0]
    assert segment["delivery"]["speed"] == 1.1
    assert segment["role"] == segment["speaker"] == "张三"
    assert script["meta"]["director_provider"] == "deepseek"
    assert script["meta"]["provider_model"] == "deepseek-v4-pro"


def test_provider_requires_environment_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider().analyze_script("文本")


def test_factory_supports_local_openai_and_deepseek():
    assert create_provider("local").name == "local"
    assert create_provider("openai", model="custom-openai").model == "custom-openai"
    assert create_provider("deepseek", model="custom-deepseek").model == "custom-deepseek"
    with pytest.raises(ValueError, match="不支持"):
        create_provider("unknown")


def test_environment_can_override_remote_models(monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_OPENAI_MODEL", "openai-env-model")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DEEPSEEK_MODEL", "deepseek-env-model")
    assert OpenAIProvider(api_key="x").model == "openai-env-model"
    assert DeepSeekProvider(api_key="x").model == "deepseek-env-model"


def test_long_text_is_batched_by_chapter_and_merged():
    calls = []

    def transport(url, headers, payload, timeout):
        calls.append(payload)
        number = len(calls)
        raw = {
            "chapters": [{
                "id": number,
                "title": f"第{number}章",
                "segments": [{
                    "speaker": "旁白",
                    "text": f"批次{number}",
                    "emotion": "neutral",
                }],
            }]
        }
        return {
            "choices": [{
                "message": {"content": json.dumps(raw, ensure_ascii=False)}
            }]
        }

    text = (
        "第一章\n\n" + "甲。" * 140
        + "\n\n第二章\n\n" + "乙。" * 140
    )
    provider = DeepSeekProvider(
        api_key="test-key",
        transport=transport,
        max_input_chars=220,
    )
    result = provider.analyze_script(text)

    assert len(calls) >= 2
    assert result["meta"]["analysis_batches"] == len(calls)
    assert len(result["chapters"]) == len(calls)
    prompts = [call["messages"][1]["content"] for call in calls]
    assert all("第一章" in prompt or "第二章" in prompt for prompt in prompts)


def test_cli_accepts_provider_and_model():
    args = build_parser().parse_args([
        "novel.txt",
        "--provider",
        "deepseek",
        "--model",
        "custom-model",
    ])
    assert args.provider == "deepseek"
    assert args.model == "custom-model"
