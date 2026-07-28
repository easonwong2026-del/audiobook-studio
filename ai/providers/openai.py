"""OpenAI Responses API 剧本导演 Provider。"""
from __future__ import annotations

from typing import Any, Dict, List

from ._remote import RemoteJsonDirectorProvider, parse_json_content


class OpenAIProvider(RemoteJsonDirectorProvider):
    """调用 OpenAI Responses API，并解析 output_text 内容。"""

    name = "openai"
    api_key_env = "OPENAI_API_KEY"
    model_env = "AUDIOBOOK_STUDIO_OPENAI_MODEL"
    base_url_env = "AUDIOBOOK_STUDIO_OPENAI_BASE_URL"
    default_model = "gpt-5.6"
    default_base_url = "https://api.openai.com/v1"

    @staticmethod
    def _extract_output_text(response: Dict[str, Any]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        texts: List[str] = []
        for item in response.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if content.get("type") in {"output_text", "text"} and isinstance(text, str):
                    texts.append(text)
        if not texts:
            raise RuntimeError("OpenAI Responses 响应缺少 output_text")
        return "".join(texts)

    def _request_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "instructions": system_prompt,
            "input": user_prompt,
            "text": {"format": {"type": "json_object"}},
        }
        response = self._transport(
            f"{self.base_url}/responses",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self.timeout,
        )
        return parse_json_content(self._extract_output_text(response))
