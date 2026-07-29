"""DeepSeek 剧本导演 Provider。"""
from __future__ import annotations

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

    def _request_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
        }
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
