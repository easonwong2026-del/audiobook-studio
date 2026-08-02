"""Atomic, source-fingerprinted checkpoints for the AI-first V4 stages."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repositories.v4_atomic import atomic_write_json


class AIFirstCheckpointRepository:
    """Keep resumable stage state without storing the original book in logs."""

    def __init__(self, path: str | Path, schema_version: str):
        self.path = Path(path)
        self.schema_version = schema_version
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(
        self, *, source_sha256: str, input_fingerprint: str = ""
    ) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("schema_version") != self.schema_version:
            return None
        if data.get("source_sha256") != source_sha256:
            return None
        if input_fingerprint and data.get("input_fingerprint") != input_fingerprint:
            return None
        return data

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        value = dict(data)
        value["schema_version"] = self.schema_version
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.path, value)
        return value


class BookUnderstandingCheckpointRepository(AIFirstCheckpointRepository):
    def __init__(self, project_path: str | Path):
        super().__init__(
            Path(project_path) / "runtime/ai_first/book_understanding.json",
            "v4-book-understanding-checkpoint-v1",
        )


class ScriptDirectorCheckpointRepository(AIFirstCheckpointRepository):
    def __init__(self, project_path: str | Path):
        super().__init__(
            Path(project_path) / "runtime/ai_first/script_director.json",
            "v4-script-director-checkpoint-v1",
        )


class ScriptReviewCheckpointRepository(AIFirstCheckpointRepository):
    def __init__(self, project_path: str | Path):
        super().__init__(
            Path(project_path) / "runtime/ai_first/script_review.json",
            "v4-script-review-checkpoint-v1",
        )
