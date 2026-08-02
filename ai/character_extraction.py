"""Remote adapter for the chapter-scoped character-extraction-v1 protocol."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai.providers import DeepSeekProvider, OpenAIProvider
from domain.v4.character_extraction import CharacterExtractionResponse

_SYSTEM_PROMPT = """Extract review candidates for real audiobook characters from one chapter.
This is character identification, not nearby-noun extraction. Keep only a real
person or a stable person-like title used throughout the story. Exclude actions,
emotions, tones, locations, objects, organizations, chapter titles, field names,
pronouns, generic descriptors, quoted terms, terminology, inner thoughts, and
narration. Every true candidate needs one exact source evidence snippet from this
chapter. Do not create a formal speaker. Return only strict JSON with schema_version
character-extraction-v1 and characters; no markdown or explanation. A non-character
observation may use is_character=false and an empty evidence list."""

RequestJson = Callable[[str, str], dict[str, Any]]


class RemoteCharacterExtractionAdapter:
    name: str
    model: str

    def __init__(self, name: str, model: str, request_json: RequestJson):
        self.name = name
        self.model = model
        self._request_json = request_json

    def extract(self, *, chapter_id: str, context: str) -> CharacterExtractionResponse:
        payload = {
            "chapter_id": chapter_id,
            "chapter_text": context,
        }
        raw = self._request_json(
            _SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        return CharacterExtractionResponse.from_dict(
            raw,
            allowed_chapter_id=chapter_id,
            chapter_text=context,
        )


def create_character_extraction_adapter(
    provider_name: str,
    *,
    api_key: str,
    model: str = "",
    base_url: str = "",
    timeout: float = 180.0,
) -> RemoteCharacterExtractionAdapter:
    normalized = provider_name.strip().lower()
    provider_type = {
        "deepseek": DeepSeekProvider,
        "openai": OpenAIProvider,
    }.get(normalized)
    if provider_type is None:
        raise ValueError(f"unsupported character extraction provider: {provider_name}")
    provider = provider_type(
        api_key=api_key,
        model=model or None,
        base_url=base_url or None,
        timeout=timeout,
    )
    return RemoteCharacterExtractionAdapter(
        name=provider.name,
        model=provider.model,
        request_json=provider._request_json,
    )
