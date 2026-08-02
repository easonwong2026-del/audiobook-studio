"""Durable per-chapter checkpoints for character extraction."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repositories.v4_atomic import atomic_write_json

CHECKPOINT_SCHEMA = "character-extraction-checkpoints-v1"


@dataclass(frozen=True)
class CharacterExtractionCheckpoint:
    batch_id: str
    source_sha256: str
    chapter_id: str
    provider: str
    model: str
    response: dict[str, Any]
    status: str
    attempts: int


class CharacterExtractionCheckpointRepository:
    """JSON checkpoints avoid copying source text into the runtime SQLite DB."""

    def __init__(self, path: str | Path):
        raw = Path(path)
        self.path = raw if raw.suffix else raw / "checkpoints.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"schema_version": CHECKPOINT_SCHEMA, "entries": []})

    def prepare(
        self,
        *,
        source_sha256: str,
        provider: str,
        model: str,
        chapter_ids: list[str],
    ) -> list[CharacterExtractionCheckpoint]:
        data = self._read()
        entries = data["entries"]
        by_id = {item["batch_id"]: item for item in entries}
        for chapter_id in chapter_ids:
            batch_id = self._batch_id(source_sha256, provider, model, chapter_id)
            by_id.setdefault(
                batch_id,
                {
                    "batch_id": batch_id,
                    "source_sha256": source_sha256,
                    "chapter_id": chapter_id,
                    "provider": provider,
                    "model": model,
                    "response": {},
                    "status": "pending",
                    "attempts": 0,
                },
            )
        if len(by_id) != len(entries):
            self._write({"schema_version": CHECKPOINT_SCHEMA, "entries": list(by_id.values())})
        current = [
            item
            for item in by_id.values()
            if item["source_sha256"] == source_sha256
            and item["provider"] == provider
            and item["model"] == model
        ]
        current_by_chapter = {item["chapter_id"]: item for item in current}
        return [self._from_dict(current_by_chapter[chapter_id]) for chapter_id in chapter_ids]

    def recover_running(self) -> int:
        data = self._read()
        count = 0
        for entry in data["entries"]:
            if entry["status"] == "running":
                entry["status"] = "pending"
                entry["response"] = {}
                count += 1
        if count:
            self._write(data)
        return count

    def mark_running(self, batch_id: str) -> None:
        self._update(batch_id, status="running", attempts_increment=True)

    def mark_completed(self, batch_id: str, response: dict[str, Any]) -> None:
        self._update(batch_id, status="completed", response=response)

    def mark_failed(self, batch_id: str, error: Exception) -> None:
        self._update(
            batch_id,
            status="failed",
            response={"error_type": type(error).__name__, "error_message": str(error)[:500]},
        )

    def _update(self, batch_id: str, **changes: Any) -> None:
        data = self._read()
        found = False
        for entry in data["entries"]:
            if entry["batch_id"] != batch_id:
                continue
            found = True
            if changes.pop("attempts_increment", False):
                entry["attempts"] = int(entry.get("attempts", 0)) + 1
            entry.update(changes)
            break
        if not found:
            raise KeyError(f"unknown character extraction checkpoint: {batch_id}")
        self._write(data)

    def _read(self) -> dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get("schema_version") != CHECKPOINT_SCHEMA:
            raise ValueError("invalid character extraction checkpoint schema")
        if not isinstance(data.get("entries"), list):
            raise TypeError("character extraction checkpoints must be a list")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.path, data)

    @staticmethod
    def _from_dict(data: dict[str, Any]) -> CharacterExtractionCheckpoint:
        return CharacterExtractionCheckpoint(
            batch_id=data["batch_id"],
            source_sha256=data["source_sha256"],
            chapter_id=data["chapter_id"],
            provider=data["provider"],
            model=data["model"],
            response=data.get("response") or {},
            status=data["status"],
            attempts=int(data.get("attempts", 0)),
        )

    @staticmethod
    def _batch_id(source_sha256: str, provider: str, model: str, chapter_id: str) -> str:
        payload = json.dumps(
            {
                "source_sha256": source_sha256,
                "provider": provider,
                "model": model,
                "chapter_id": chapter_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"character_extraction_{hashlib.sha256(payload.encode()).hexdigest()[:20]}"
