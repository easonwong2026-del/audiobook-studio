"""远程 AI 剧本导演 Provider 契约测试（全程 fake transport，不访问网络）。"""
from __future__ import annotations

import json
import re

import pytest

from ai.prompts.script_director_v3 import SCHEMA_VERSION
from ai.providers import (
    DeepSeekProvider,
    OpenAIProvider,
    ProviderOutputInvalidJsonError,
    ProviderOutputTruncatedError,
    create_provider,
)
from script_director_cli import build_parser
from services.script_director import ScriptDirectorService


def _prompt_value(prompt: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}：(.+)$", prompt, re.MULTILINE)
    assert match, f"prompt missing {label}"
    return match.group(1).strip()


def _batch_from_prompt(prompt: str, *, speed: float = 0.95):
    source = prompt.split("<novel>\n", 1)[1].rsplit("\n</novel>", 1)[0]
    return {
        "schema_version": SCHEMA_VERSION,
        "batch_id": _prompt_value(prompt, "batch_id"),
        "source_chapter_id": _prompt_value(prompt, "source_chapter_id"),
        "source_chapter_title": _prompt_value(prompt, "source_chapter_title"),
        "segments": [{
                "speaker": "张三",
                "text": source,
                "emotion": "confident",
                "emotion_strength": 0.7,
                "delivery": {
                    "speed": speed,
                    "pitch": 0,
                    "intensity": 0.7,
                    "breath": "light",
                },
                "pause_before": 0,
                "pause_after": 800,
                "pauses": [],
            }],
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
        prompt = payload["messages"][1]["content"]
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(_batch_from_prompt(prompt), ensure_ascii=False)
                },
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
        raw = _batch_from_prompt(payload["input"])
        return {
            "status": "completed",
            "output": [{
                "type": "message",
                "content": [{
                    "type": "output_text",
                        "text": json.dumps(raw, ensure_ascii=False),
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
        raw = _batch_from_prompt(payload["messages"][1]["content"], speed=1.15)
        return {
            "choices": [{
                "finish_reason": "stop",
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
        raw = _batch_from_prompt(payload["messages"][1]["content"])
        raw["segments"][0]["speaker"] = "旁白"
        raw["segments"][0]["emotion"] = "neutral"
        return {
            "choices": [{
                "finish_reason": "stop",
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
    assert len(result["chapters"]) == 2
    assert [chapter["title"] for chapter in result["chapters"]] == ["第一章", "第二章"]
    prompts = [call["messages"][1]["content"] for call in calls]
    assert all("第一章" in prompt or "第二章" in prompt for prompt in prompts)


def test_deepseek_length_finish_reason_raises_specialized_error():
    def transport(url, headers, payload, timeout):
        return {
            "choices": [{
                "finish_reason": "length",
                "message": {"content": "{\"schema_version\":"},
            }]
        }

    provider = DeepSeekProvider(api_key="test-key", transport=transport)
    provider.max_split_depth = 0
    with pytest.raises(ProviderOutputTruncatedError, match="来源章节"):
        provider.analyze_script("无法再拆")


def test_openai_incomplete_raises_specialized_error():
    def transport(url, headers, payload, timeout):
        return {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output_text": "{\"schema_version\":",
        }

    provider = OpenAIProvider(api_key="test-key", transport=transport)
    provider.max_split_depth = 0
    with pytest.raises(ProviderOutputTruncatedError, match="批次"):
        provider.analyze_script("无法再拆")


def test_wrong_batch_schema_is_rejected():
    def transport(url, headers, payload, timeout):
        raw = _batch_from_prompt(payload["messages"][1]["content"])
        raw["schema_version"] = "wrong"
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(raw, ensure_ascii=False)},
            }]
        }

    with pytest.raises(ProviderOutputInvalidJsonError, match="协议版本"):
        DeepSeekProvider(api_key="x", transport=transport).analyze_script("文本")


def test_truncated_chunk_splits_and_successful_child_is_not_repeated():
    calls: list[str] = []

    def transport(url, headers, payload, timeout):
        prompt = payload["messages"][1]["content"]
        batch_id = _prompt_value(prompt, "batch_id")
        calls.append(batch_id)
        if "-split-" not in batch_id:
            return {
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": ""},
                }]
            }
        raw = _batch_from_prompt(prompt)
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(raw, ensure_ascii=False)},
            }]
        }

    result = DeepSeekProvider(api_key="x", transport=transport).analyze_script(
        "第一段内容。\n\n第二段内容。"
    )
    assert len(result["chapters"][0]["segments"]) == 2
    assert len(calls) == len(set(calls)) == 3


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
