"""OpenAI Responses API 剧本导演 Provider。"""
from __future__ import annotations

from typing import Any, Dict, List

from ._remote import RemoteJsonDirectorProvider, parse_json_content
from .exceptions import ProviderOutputTruncatedError


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
            raise ProviderOutputTruncatedError(
                "OpenAI Responses 请求成功但 output_text 为空"
            )
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
        status = str(response.get("status") or "").lower()
        incomplete = response.get("incomplete_details")
        if status == "incomplete" or incomplete:
            reason = (
                incomplete.get("reason")
                if isinstance(incomplete, dict)
                else "incomplete"
            )
            raise ProviderOutputTruncatedError(
                f"OpenAI Responses 输出未完成（{reason or 'unknown'}）"
            )
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"OpenAI Responses 请求状态异常：{status}")
        for item in response.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "refusal":
                    raise RuntimeError("OpenAI Responses 拒绝处理当前批次")
        return parse_json_content(self._extract_output_text(response))
