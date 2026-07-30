"""SQLite-backed high-frequency runtime state for v4."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

RUNTIME_SCHEMA_VERSION = 3

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
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP,
                              CURRENT_TIMESTAMP)
                    ON CONFLICT(task_id) DO UPDATE SET
                        plan_revision = excluded.plan_revision,
                        chapter_id = excluded.chapter_id,
                        speaker_id = excluded.speaker_id,
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
                    ),
                )
            connection.commit()
