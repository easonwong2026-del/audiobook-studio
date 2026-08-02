"""Remote adapter for the strict book-wide character-consolidation protocol."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai.providers import DeepSeekProvider, OpenAIProvider
from domain.v4.character_consolidation import (
    CharacterConsolidationRequest,
    CharacterConsolidationResponse,
)

_SYSTEM_PROMPT = """Unify audiobook character observations across the whole book.
Use only candidate_ids supplied by the user. Merge observations only when the
identity is supported by names, aliases, stable identity/title evidence, and
chapter evidence; name similarity alone is never enough. Keep uncertain or
conflicting observations in unresolved_groups. Do not rename an existing
speaker. Return only strict JSON with schema_version character-consolidation-v1,
characters, and unresolved_groups; no markdown or explanation. Every supplied
candidate_id must appear exactly once in characters or unresolved_groups."""

RequestJson = Callable[[str, str], dict[str, Any]]


class RemoteCharacterConsolidationAdapter:
    name: str
    model: str

    def __init__(self, name: str, model: str, request_json: RequestJson):
        self.name = name
        self.model = model
        self._request_json = request_json

    def consolidate(
        self, request: CharacterConsolidationRequest
    ) -> CharacterConsolidationResponse:
        request.validate()
        raw = self._request_json(
            _SYSTEM_PROMPT,
            json.dumps(request.to_dict(), ensure_ascii=False, separators=(",", ":")),
        )
        return CharacterConsolidationResponse.from_dict(
            raw,
            allowed_candidate_ids={item.candidate_id for item in request.candidates},
        )


def create_character_consolidation_adapter(
    provider_name: str,
    *,
    api_key: str,
    model: str = "",
    base_url: str = "",
    timeout: float = 180.0,
) -> RemoteCharacterConsolidationAdapter:
    normalized = provider_name.strip().lower()
    provider_type = {
        "deepseek": DeepSeekProvider,
        "openai": OpenAIProvider,
    }.get(normalized)
    if provider_type is None:
        raise ValueError(f"unsupported character consolidation provider: {provider_name}")
    provider = provider_type(
        api_key=api_key,
        model=model or None,
        base_url=base_url or None,
        timeout=timeout,
    )
    return RemoteCharacterConsolidationAdapter(
        name=provider.name,
        model=provider.model,
        request_json=provider._request_json,
    )
