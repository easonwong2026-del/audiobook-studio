"""Remote AI adapters dedicated to the short speaker-routing-v1 protocol."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai.providers import DeepSeekProvider, OpenAIProvider
from domain.v4.routing import SpeakerRoutingResponse

_SYSTEM_PROMPT = """You route audiobook dialogue to speakers.
Return one JSON object with schema_version speaker-routing-v1 and assignments.
Each assignment contains only segment_id and speaker (a name or null).
Never return source text, emotion, TTS settings, explanations, or unknown IDs.
If uncertain, use null. Do not omit schema_version."""

RequestJson = Callable[[str, str], dict[str, Any]]


class RemoteSpeakerRoutingAdapter:
    def __init__(self, name: str, model: str, request_json: RequestJson):
        self.name = name
        self.model = model
        self._request_json = request_json

    def route(
        self,
        *,
        context: str,
        segment_ids: list[str],
    ) -> SpeakerRoutingResponse:
        payload = {
            "allowed_segment_ids": segment_ids,
            "context": context,
        }
        raw = self._request_json(
            _SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        return SpeakerRoutingResponse.from_dict(
            raw, allowed_segment_ids=set(segment_ids)
        )


def create_speaker_routing_adapter(
    provider_name: str,
    *,
    api_key: str,
    model: str = "",
    base_url: str = "",
    timeout: float = 180.0,
) -> RemoteSpeakerRoutingAdapter:
    normalized = provider_name.strip().lower()
    provider_type = {
        "deepseek": DeepSeekProvider,
        "openai": OpenAIProvider,
    }.get(normalized)
    if provider_type is None:
        raise ValueError(f"unsupported speaker routing provider: {provider_name}")
    provider = provider_type(
        api_key=api_key,
        model=model or None,
        base_url=base_url or None,
        timeout=timeout,
    )
    return RemoteSpeakerRoutingAdapter(
        name=provider.name,
        model=provider.model,
        request_json=provider._request_json,
    )
