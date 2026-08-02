"""Small atomic repository for the user-visible V4 analysis state."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repositories.v4_atomic import atomic_write_json

ANALYSIS_SCHEMA = "v4-analysis-state-v1"


class V4AnalysisRepository:
    relative_path = Path("runtime/analysis.json")

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)
        self.path = self.project_path / self.relative_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, source_sha256: str | None = None) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("schema_version") != ANALYSIS_SCHEMA:
            return None
        if source_sha256 is not None and data.get("source_sha256") != source_sha256:
            return None
        return data

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        value = dict(state)
        value["schema_version"] = ANALYSIS_SCHEMA
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.path, value)
        return value

    def start(self, source_sha256: str, *, provider: str = "") -> dict[str, Any]:
        return self.save(
            {
                "schema_version": ANALYSIS_SCHEMA,
                "source_sha256": source_sha256,
                "status": "running",
                "current_stage": "import",
                "provider": provider,
                "stages": {},
                "summary": {},
                "errors": [],
            }
        )
