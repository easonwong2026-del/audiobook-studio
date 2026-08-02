"""Remote AI adapters dedicated to the constrained speaker-routing-v2 protocol."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai.providers import DeepSeekProvider, OpenAIProvider
from domain.v4.routing import SpeakerRoutingResponse

_SYSTEM_PROMPT = """Assign each marked audiobook dialogue segment to a real character.
This task is speaker attribution, not nearby-noun extraction. Exclude actions,
emotions, tones, locations, objects, organizations, chapter titles, field names,
pronouns, generic descriptors, quoted terms, terminology, inner thoughts, and
narration. Choose only from allowed_speakers using speaker_id; never invent a
formal speaker. If the text suggests a new person, return speaker_id null and put
the suspected name in candidate_name. If uncertain or confidence is below 0.75,
return speaker_id null. Return only strict JSON with schema_version
speaker-routing-v2 and assignments; no markdown or explanation."""

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
        allowed_speakers: list[dict[str, Any]] | None = None,
    ) -> SpeakerRoutingResponse:
        payload = {
            "allowed_segment_ids": segment_ids,
            "allowed_speakers": allowed_speakers or [],
            "context": context,
        }
        raw = self._request_json(
            _SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        return SpeakerRoutingResponse.from_dict(
            raw,
            allowed_segment_ids=set(segment_ids),
            allowed_speaker_ids={
                item["speaker_id"]
                for item in (allowed_speakers or [])
                if isinstance(item, dict) and isinstance(item.get("speaker_id"), str)
            },
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
