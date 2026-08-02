"""Transactional, text-free checkpoints for resumable speaker routing."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repositories.runtime_repository import RuntimeRepository


@dataclass(frozen=True)
class RoutingBatch:
    batch_id: str
    segment_ids: list[str]
    assignments: list[dict[str, Any]]
    status: str
    attempts: int


class RoutingCheckpointRepository:
    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        RuntimeRepository(self.path).initialize()

    def prepare(
        self,
        *,
        source_sha256: str,
        script_revision: int,
        provider: str,
        model: str,
        batches: list[list[str]],
    ) -> list[RoutingBatch]:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for segment_ids in batches:
                batch_id = self._batch_id(
                    source_sha256, script_revision, provider, model, segment_ids
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO routing_batches(
                        batch_id, source_sha256, script_revision, provider, model,
                        segment_ids_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        batch_id,
                        source_sha256,
                        script_revision,
                        provider,
                        model,
                        json.dumps(segment_ids, separators=(",", ":")),
                    ),
                )
            connection.commit()
        current = self.list_current(
            source_sha256=source_sha256,
            script_revision=script_revision,
            provider=provider,
            model=model,
        )
        by_segments = {tuple(item.segment_ids): item for item in current}
        return [by_segments[tuple(segment_ids)] for segment_ids in batches]

    def list_current(
        self,
        *,
        source_sha256: str,
        script_revision: int,
        provider: str,
        model: str,
    ) -> list[RoutingBatch]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT batch_id, segment_ids_json, assignments_json, status, attempts
                  FROM routing_batches
                 WHERE source_sha256 = ? AND script_revision = ?
                   AND provider = ? AND model = ?
                 ORDER BY created_at, batch_id
                """,
                (source_sha256, script_revision, provider, model),
            ).fetchall()
        return [
            RoutingBatch(
                batch_id=row[0],
                segment_ids=json.loads(row[1]),
                assignments=json.loads(row[2]),
                status=row[3],
                attempts=row[4],
            )
            for row in rows
        ]

    def mark_running(self, batch_id: str) -> None:
        self._update(
            batch_id,
            """
            status = 'running', attempts = attempts + 1,
            error_type = NULL, error_message = NULL
            """,
        )

    def mark_completed(
        self, batch_id: str, assignments: list[dict[str, Any]]
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE routing_batches
                   SET status = 'completed', assignments_json = ?,
                       error_type = NULL, error_message = NULL,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE batch_id = ?
                """,
                (json.dumps(assignments, ensure_ascii=False), batch_id),
            )
            connection.commit()

    def mark_failed(self, batch_id: str, error: Exception) -> None:
        safe_message = str(error).replace("\n", " ")[:500]
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE routing_batches
                   SET status = 'failed', error_type = ?, error_message = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE batch_id = ?
                """,
                (type(error).__name__, safe_message, batch_id),
            )
            connection.commit()

    def cancel_pending(self) -> int:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE routing_batches
                   SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                 WHERE status IN ('pending', 'failed')
                """
            )
            connection.commit()
            return cursor.rowcount

    def recover_running(self) -> int:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE routing_batches
                   SET status = 'pending', error_type = 'interrupted',
                       error_message = 'previous process exited during routing',
                       updated_at = CURRENT_TIMESTAMP
                 WHERE status = 'running'
                """
            )
            connection.commit()
            return cursor.rowcount

    def is_cancelled(self, batch_id: str) -> bool:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT status FROM routing_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return bool(row and row[0] == "cancelled")

    def _update(self, batch_id: str, assignments: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                UPDATE routing_batches
                   SET {assignments}, updated_at = CURRENT_TIMESTAMP
                 WHERE batch_id = ?
                """,
                (batch_id,),
            )
            connection.commit()

    @staticmethod
    def _batch_id(
        source_sha256: str,
        script_revision: int,
        provider: str,
        model: str,
        segment_ids: list[str],
    ) -> str:
        payload: dict[str, Any] = {
            "source_sha256": source_sha256,
            "script_revision": script_revision,
            "provider": provider,
            "model": model,
            "segment_ids": segment_ids,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
        return f"routing_{digest}"
