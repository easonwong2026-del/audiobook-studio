"""Durable, source-fingerprinted checkpoint for book-wide consolidation."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domain.v4.character_consolidation import CharacterConsolidationResponse
from repositories.v4_atomic import atomic_write_json

CHECKPOINT_SCHEMA = "character-consolidation-checkpoint-v1"


class CharacterConsolidationCheckpointRepository:
    def __init__(self, path: str | Path):
        raw = Path(path)
        self.path = raw if raw.suffix else raw / "consolidation.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(
        self,
        *,
        source_sha256: str,
        input_fingerprint: str,
        allowed_candidate_ids: set[str],
    ) -> CharacterConsolidationResponse | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != CHECKPOINT_SCHEMA:
                return None
            if data.get("source_sha256") != source_sha256:
                return None
            if data.get("input_fingerprint") != input_fingerprint:
                return None
            if data.get("status") != "completed":
                return None
            response = data.get("response")
            if not isinstance(response, dict):
                return None
            return CharacterConsolidationResponse.from_dict(
                response, allowed_candidate_ids=allowed_candidate_ids
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def save(
        self,
        *,
        source_sha256: str,
        input_fingerprint: str,
        provider: str,
        model: str,
        response: CharacterConsolidationResponse,
        allowed_candidate_ids: set[str] | None = None,
    ) -> None:
        allowed = allowed_candidate_ids or (
            {item for character in response.characters for item in character.candidate_ids}
            | {item for group in response.unresolved_groups for item in group.candidate_ids}
        )
        response.validate(allowed)
        data: dict[str, Any] = {
            "schema_version": CHECKPOINT_SCHEMA,
            "source_sha256": source_sha256,
            "input_fingerprint": input_fingerprint,
            "provider": provider,
            "model": model,
            "status": "completed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "response": response.to_dict(),
        }
        atomic_write_json(self.path, data)
