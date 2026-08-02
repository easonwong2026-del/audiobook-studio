"""DeepSeek 剧本导演 Provider。"""
from __future__ import annotations

import os
from typing import Any, Dict

from ._remote import RemoteJsonDirectorProvider, parse_json_content
from .exceptions import ProviderOutputTruncatedError


class DeepSeekProvider(RemoteJsonDirectorProvider):
    """调用 DeepSeek OpenAI-compatible Chat Completions JSON Output。"""

    name = "deepseek"
    api_key_env = "DEEPSEEK_API_KEY"
    model_env = "AUDIOBOOK_STUDIO_DEEPSEEK_MODEL"
    base_url_env = "AUDIOBOOK_STUDIO_DEEPSEEK_BASE_URL"
    default_model = "deepseek-v4-pro"
    default_base_url = "https://api.deepseek.com"

    def _request_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        task: str = "script_director",
        reasoning: bool | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if self._should_enable_reasoning(task, reasoning):
            payload["thinking"] = {"type": "enabled"}
        elif task == "legacy_script_director":
            # Preserve the V3 protocol contract only.  V4 AI-first calls use
            # explicit task names and never inherit this disabled setting.
            payload["thinking"] = {"type": "disabled"}
        response = self._transport(
            f"{self.base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self.timeout,
        )
        try:
            choice = response["choices"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("DeepSeek 响应缺少 choices[0]") from exc
        if choice.get("finish_reason") == "length":
            raise ProviderOutputTruncatedError("DeepSeek 输出达到长度限制")
        try:
            content = choice["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("DeepSeek 响应缺少 choices[0].message.content") from exc
        return parse_json_content(content)

    def _should_enable_reasoning(
        self, task: str, reasoning: bool | None
    ) -> bool:
        """Only send DeepSeek's thinking field when this model supports it.

        The API rejects the field on non-reasoning models.  Model capability is
        therefore inferred conservatively from the model id, with an explicit
        environment override for compatible gateways.  Ordinary connection and
        formatting calls never opt into reasoning.
        """
        if task in {"connection_test", "format", "simple"}:
            return False
        if reasoning is not None:
            return bool(reasoning)
        override = os.getenv("AUDIOBOOK_STUDIO_DEEPSEEK_REASONING", "auto").lower()
        if override in {"0", "false", "disabled", "off"}:
            return False
        if override in {"1", "true", "enabled", "on"}:
            return True
        model = self.model.lower()
        return any(
            marker in model
            for marker in ("reasoner", "reasoning", "think", "deepseek-r1", "-r1")
        )
