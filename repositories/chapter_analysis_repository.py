"""Atomic per-chapter state for the default fast analysis workflow."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repositories.v4_atomic import atomic_write_json

CHAPTER_ANALYSIS_STATE_SCHEMA = "chapter-analysis-state-v1"


class ChapterAnalysisStateRepository:
    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)
        self.directory = self.project_path / "runtime/chapter_analysis"
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, chapter_id: str) -> Path:
        safe = str(chapter_id or "").strip()
        if not safe or safe in {".", ".."} or "/" in safe or "\\" in safe:
            raise ValueError("invalid chapter_id")
        return self.directory / f"{safe}.json"

    def load(self, chapter_id: str) -> dict[str, Any] | None:
        path = self.path_for(chapter_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get("schema_version") != CHAPTER_ANALYSIS_STATE_SCHEMA:
            return None
        if value.get("chapter_id") != chapter_id:
            return None
        return value

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        value = dict(state)
        value["schema_version"] = CHAPTER_ANALYSIS_STATE_SCHEMA
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.path_for(str(value.get("chapter_id") or "")), value)
        return value
