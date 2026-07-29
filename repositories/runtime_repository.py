"""SQLite-backed high-frequency runtime state for v4."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

RUNTIME_SCHEMA_VERSION = 5

_MIGRATIONS = {
    1: """
    CREATE TABLE IF NOT EXISTS synthesis_tasks (
        task_id TEXT PRIMARY KEY,
        plan_revision TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        speaker_id TEXT,
        cache_key TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('pending','running','completed','failed','stale',
                       'cancelled','skipped')
        ),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        parent_task_id TEXT REFERENCES synthesis_tasks(task_id),
        split_depth INTEGER NOT NULL DEFAULT 0 CHECK (split_depth >= 0),
        output_path TEXT,
        error_type TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_synthesis_tasks_status
        ON synthesis_tasks(status);
    CREATE INDEX IF NOT EXISTS idx_synthesis_tasks_parent
        ON synthesis_tasks(parent_task_id);
    CREATE TABLE IF NOT EXISTS cache_entries (
        cache_key TEXT PRIMARY KEY,
        output_path TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        created_at TEXT NOT NULL,
        last_used_at TEXT NOT NULL
    );
    """,
    2: """
    ALTER TABLE synthesis_tasks ADD COLUMN started_at TEXT;
    ALTER TABLE synthesis_tasks ADD COLUMN completed_at TEXT;
    ALTER TABLE cache_entries ADD COLUMN file_path TEXT NOT NULL DEFAULT '';
    ALTER TABLE cache_entries ADD COLUMN file_sha256 TEXT NOT NULL DEFAULT '';
    ALTER TABLE cache_entries ADD COLUMN duration REAL;
    ALTER TABLE cache_entries ADD COLUMN sample_rate INTEGER;
    ALTER TABLE cache_entries ADD COLUMN channels INTEGER;
    ALTER TABLE cache_entries ADD COLUMN valid INTEGER NOT NULL DEFAULT 1
        CHECK (valid IN (0, 1));
    UPDATE cache_entries
       SET file_path = output_path, file_sha256 = content_sha256
     WHERE file_path = '';
    """,
    3: """
    CREATE TABLE routing_batches (
        batch_id TEXT PRIMARY KEY,
        source_sha256 TEXT NOT NULL,
        script_revision INTEGER NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        segment_ids_json TEXT NOT NULL,
        assignments_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL CHECK (
            status IN ('pending','running','completed','failed','cancelled')
        ),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        error_type TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_routing_batches_resume
        ON routing_batches(source_sha256, script_revision, provider, model, status);
    """,
    4: """
    ALTER TABLE synthesis_tasks ADD COLUMN voice_id TEXT;
    ALTER TABLE synthesis_tasks ADD COLUMN actual_text TEXT;
    ALTER TABLE synthesis_tasks ADD COLUMN input_fingerprint TEXT;
    ALTER TABLE synthesis_tasks ADD COLUMN text_length INTEGER;
    ALTER TABLE synthesis_tasks ADD COLUMN failed_text_length INTEGER;
    CREATE TABLE chapter_outputs (
        chapter_id TEXT PRIMARY KEY,
        plan_revision INTEGER NOT NULL,
        file_path TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        duration REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    5: """
    CREATE TABLE synthesis_metrics (
        metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        text_chars INTEGER NOT NULL,
        text_tokens INTEGER NOT NULL,
        voice_id TEXT,
        auto_emotion INTEGER NOT NULL CHECK (auto_emotion IN (0, 1)),
        elapsed_seconds REAL NOT NULL,
        audio_duration REAL,
        memory_allocated_before_mb REAL,
        memory_allocated_after_mb REAL,
        memory_reserved_before_mb REAL,
        memory_reserved_after_mb REAL,
        max_memory_allocated_mb REAL,
        free_vram_before_mb REAL,
        free_vram_after_mb REAL,
        cache_hit INTEGER NOT NULL CHECK (cache_hit IN (0, 1)),
        error_type TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_synthesis_metrics_task
        ON synthesis_metrics(task_id, metric_id);
    """,
}


class RuntimeRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM migrations")
            }
            for version, sql in sorted(_MIGRATIONS.items()):
                if version in applied:
                    continue
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{sql}\n"
                    f"INSERT INTO migrations(version) VALUES ({int(version)});\n"
                    "COMMIT;\n"
                )
            connection.commit()

    def schema_version(self) -> int:
        if not self.path.exists():
            return 0
        with sqlite3.connect(self.path) as connection:
            try:
                row = connection.execute("SELECT MAX(version) FROM migrations").fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row[0] or 0)

    def recover_interrupted_tasks(self) -> int:
        """Return abandoned running tasks to pending after an unclean shutdown."""
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE synthesis_tasks
                   SET status = 'pending',
                       error_type = 'interrupted',
                       error_message = 'previous process exited while task was running',
                       started_at = NULL,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE status = 'running'
                """
            )
            connection.commit()
            return cursor.rowcount

    def sync_synthesis_plan(
        self,
        plan_revision: int,
        tasks: list[dict[str, Any]],
        removed_task_ids: list[str],
    ) -> None:
        """Register a plan and mark only changed/removed task rows stale."""
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for task_id in removed_task_ids:
                connection.execute(
                    """
                    UPDATE synthesis_tasks
                       SET status = 'stale', updated_at = CURRENT_TIMESTAMP
                     WHERE task_id = ? AND status != 'stale'
                    """,
                    (task_id,),
                )
            for task in tasks:
                connection.execute(
                    """
                    INSERT INTO synthesis_tasks(
                        task_id, plan_revision, chapter_id, speaker_id, cache_key,
                        voice_id, actual_text, input_fingerprint, text_length,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                              CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(task_id) DO UPDATE SET
                        plan_revision = excluded.plan_revision,
                        chapter_id = excluded.chapter_id,
                        speaker_id = excluded.speaker_id,
                        voice_id = excluded.voice_id,
                        actual_text = excluded.actual_text,
                        input_fingerprint = excluded.input_fingerprint,
                        text_length = excluded.text_length,
                        status = CASE
                            WHEN synthesis_tasks.cache_key != excluded.cache_key
                            THEN 'stale'
                            ELSE synthesis_tasks.status
                        END,
                        cache_key = excluded.cache_key,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        task["task_id"],
                        str(plan_revision),
                        task["chapter_id"],
                        task["speaker_id"],
                        task["input_fingerprint"],
                        task.get("voice_id"),
                        task.get("actual_text"),
                        task["input_fingerprint"],
                        task.get("text_length"),
                    ),
                )
            connection.commit()

    def claim_next_task(self) -> dict[str, Any] | None:
        """Atomically claim one pending task; concurrency remains one by policy."""
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM synthesis_tasks
                 WHERE status = 'pending'
                 ORDER BY split_depth, created_at, task_id
                 LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE synthesis_tasks
                   SET status = 'running', attempts = attempts + 1,
                       started_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE task_id = ? AND status = 'pending'
                """,
                (row["task_id"],),
            )
            connection.commit()
            return dict(row)

    def complete_task(self, task_id: str, output_path: str) -> None:
        self._finish_task(task_id, "completed", output_path=output_path)

    def fail_task(
        self,
        task_id: str,
        error_type: str,
        error_message: str,
        failed_text_length: int | None = None,
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE synthesis_tasks
                   SET status = 'failed', error_type = ?, error_message = ?,
                       failed_text_length = ?, completed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE task_id = ?
                """,
                (error_type, error_message[:500], failed_text_length, task_id),
            )
            connection.commit()

    def split_task(
        self,
        parent: dict[str, Any],
        children: list[dict[str, Any]],
        *,
        error_type: str,
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for child in children:
                connection.execute(
                    """
                    INSERT INTO synthesis_tasks(
                        task_id, plan_revision, chapter_id, speaker_id, voice_id,
                        cache_key, input_fingerprint, actual_text, text_length,
                        status, attempts, parent_task_id, split_depth,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?,
                              CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        child["task_id"],
                        parent["plan_revision"],
                        parent["chapter_id"],
                        parent["speaker_id"],
                        parent["voice_id"],
                        child["cache_key"],
                        child["cache_key"],
                        child["actual_text"],
                        child["text_length"],
                        parent["task_id"],
                        int(parent["split_depth"]) + 1,
                    ),
                )
            connection.execute(
                """
                UPDATE synthesis_tasks
                   SET status = 'skipped', error_type = ?,
                       error_message = 'split into child tasks',
                       failed_text_length = text_length,
                       completed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE task_id = ?
                """,
                (error_type, parent["task_id"]),
            )
            connection.commit()

    def task_counts(self) -> dict[str, int]:
        with sqlite3.connect(self.path) as connection:
            return {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT status, COUNT(*) FROM synthesis_tasks GROUP BY status"
                )
            }

    def cancel_pending_tasks(self) -> int:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE synthesis_tasks
                   SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                 WHERE status = 'pending'
                """
            )
            connection.commit()
            return cursor.rowcount

    def record_synthesis_metric(self, metric: dict[str, Any]) -> None:
        """Persist bounded operational telemetry without storing source text."""
        fields = (
            "task_id",
            "attempt",
            "text_chars",
            "text_tokens",
            "voice_id",
            "auto_emotion",
            "elapsed_seconds",
            "audio_duration",
            "memory_allocated_before_mb",
            "memory_allocated_after_mb",
            "memory_reserved_before_mb",
            "memory_reserved_after_mb",
            "max_memory_allocated_mb",
            "free_vram_before_mb",
            "free_vram_after_mb",
            "cache_hit",
            "error_type",
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                INSERT INTO synthesis_metrics({", ".join(fields)})
                VALUES ({", ".join("?" for _ in fields)})
                """,
                tuple(metric.get(field) for field in fields),
            )
            connection.commit()

    def synthesis_metrics(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """Return sanitized runtime metrics for diagnostics and UI summaries."""
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            if task_id is None:
                rows = connection.execute(
                    "SELECT * FROM synthesis_metrics ORDER BY metric_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM synthesis_metrics
                     WHERE task_id = ? ORDER BY metric_id
                    """,
                    (task_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def resolved_audio_paths(self, task_id: str) -> list[str]:
        """Resolve a plan task to its completed leaf outputs after OOM splitting."""
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT status, output_path FROM synthesis_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row[0] == "completed" and row[1]:
                return [row[1]]
            children = [
                item[0]
                for item in connection.execute(
                    """
                    SELECT task_id FROM synthesis_tasks
                     WHERE parent_task_id = ? ORDER BY task_id
                    """,
                    (task_id,),
                )
            ]
        if not children:
            raise RuntimeError(f"task has no completed audio: {task_id}")
        paths: list[str] = []
        for child in children:
            paths.extend(self.resolved_audio_paths(child))
        return paths

    def save_chapter_output(
        self,
        chapter_id: str,
        plan_revision: int,
        file_path: str,
        fingerprint: str,
        duration: float,
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO chapter_outputs(
                    chapter_id, plan_revision, file_path, fingerprint, duration
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chapter_id) DO UPDATE SET
                    plan_revision = excluded.plan_revision,
                    file_path = excluded.file_path,
                    fingerprint = excluded.fingerprint,
                    duration = excluded.duration,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chapter_id, plan_revision, file_path, fingerprint, duration),
            )
            connection.commit()

    def _finish_task(
        self, task_id: str, status: str, *, output_path: str | None = None
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE synthesis_tasks
                   SET status = ?, output_path = ?, error_type = NULL,
                       error_message = NULL, completed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE task_id = ?
                """,
                (status, output_path, task_id),
            )
            connection.commit()
