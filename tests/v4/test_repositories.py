from __future__ import annotations

import sqlite3

import pytest

from domain.v4 import ProjectManifest, SourceMetadata, ValidationError
from domain.v4.models import source_sha256
from repositories.project_v4_repository import (
    ProjectV4Repository,
    V4ProjectAlreadyExistsError,
)
from repositories.runtime_repository import RUNTIME_SCHEMA_VERSION, RuntimeRepository
from services.source_segmenter import SourceSegmenter


def _documents(text="他说：“你好。”"):
    segmented = SourceSegmenter().segment(text)
    metadata = SourceMetadata(
        original_filename="book.txt",
        source_format="txt",
        encoding="utf-8",
        normalization="audiobook-normalization-v1",
        char_count=len(text),
        sha256=source_sha256(text),
        imported_at="2026-01-01T00:00:00+00:00",
    )
    manifest = ProjectManifest(
        project_id="project_test",
        name="测试",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    return manifest, metadata, segmented


def test_atomic_project_creation_and_runtime_schema(tmp_path):
    manifest, metadata, segmented = _documents()
    repository = ProjectV4Repository(tmp_path)
    path = repository.create(
        "project", manifest, "他说：“你好。”", metadata,
        segmented.script, segmented.speakers,
    )
    assert repository.load_manifest(path) == manifest
    assert (path / "source/source.txt").read_text(encoding="utf-8") == "他说：“你好。”"
    runtime = RuntimeRepository(path / "runtime/runtime.db")
    assert runtime.schema_version() == RUNTIME_SCHEMA_VERSION
    with sqlite3.connect(runtime.path) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "migrations",
        "synthesis_tasks",
        "cache_entries",
        "synthesis_metrics",
    } <= tables


def test_duplicate_and_windows_style_names_are_rejected(tmp_path):
    manifest, metadata, segmented = _documents()
    repository = ProjectV4Repository(tmp_path)
    repository.create(
        "project", manifest, "他说：“你好。”", metadata,
        segmented.script, segmented.speakers,
    )
    with pytest.raises(V4ProjectAlreadyExistsError):
        repository.create(
            "project", manifest, "他说：“你好。”", metadata,
            segmented.script, segmented.speakers,
        )
    with pytest.raises(ValueError):
        repository.create(
            r"folder\project", manifest, "他说：“你好。”", metadata,
            segmented.script, segmented.speakers,
        )


def test_temporary_cleanup_and_idempotent_migration(tmp_path):
    stale = tmp_path / ".tmp_v4_stale_deadbeef"
    stale.mkdir()
    repository = ProjectV4Repository(tmp_path)
    assert repository.cleanup_temporary_projects() == [stale]
    runtime = RuntimeRepository(tmp_path / "runtime.db")
    runtime.initialize()
    runtime.initialize()
    assert runtime.schema_version() == RUNTIME_SCHEMA_VERSION


def test_failed_creation_removes_temporary_directory(tmp_path, monkeypatch):
    manifest, metadata, segmented = _documents()
    repository = ProjectV4Repository(tmp_path)

    def fail_initialize(_self):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(RuntimeRepository, "initialize", fail_initialize)
    with pytest.raises(RuntimeError, match="simulated"):
        repository.create(
            "project", manifest, "他说：“你好。”", metadata,
            segmented.script, segmented.speakers,
        )
    assert not (tmp_path / "project").exists()
    assert not any(path.name.startswith(".tmp_v4_") for path in tmp_path.iterdir())


def test_runtime_migration_fields_and_interrupted_task_recovery(tmp_path):
    runtime = RuntimeRepository(tmp_path / "runtime.db")
    runtime.initialize()
    with sqlite3.connect(runtime.path) as connection:
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(synthesis_tasks)")
        }
        cache_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(cache_entries)")
        }
        connection.execute(
            """
            INSERT INTO synthesis_tasks(
                task_id, plan_revision, chapter_id, status, created_at, updated_at
            ) VALUES ('task_1', '1', 'chapter_1', 'running', 'now', 'now')
            """
        )
        connection.commit()
    assert {"started_at", "completed_at"} <= task_columns
    assert {
        "file_path", "file_sha256", "duration", "sample_rate", "channels", "valid"
    } <= cache_columns
    assert runtime.recover_interrupted_tasks() == 1
    with sqlite3.connect(runtime.path) as connection:
        status, error_type = connection.execute(
            "SELECT status, error_type FROM synthesis_tasks WHERE task_id = 'task_1'"
        ).fetchone()
    assert (status, error_type) == ("pending", "interrupted")


def test_existing_runtime_v1_database_migrates_to_latest(tmp_path):
    path = tmp_path / "runtime.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO migrations(version) VALUES (1);
            CREATE TABLE synthesis_tasks (
                task_id TEXT PRIMARY KEY,
                plan_revision TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                speaker_id TEXT,
                cache_key TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                parent_task_id TEXT,
                split_depth INTEGER NOT NULL DEFAULT 0,
                output_path TEXT,
                error_type TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE cache_entries (
                cache_key TEXT PRIMARY KEY,
                output_path TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL
            );
            """
        )
    runtime = RuntimeRepository(path)
    runtime.initialize()
    assert runtime.schema_version() == RUNTIME_SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        assert "completed_at" in {
            row[1] for row in connection.execute("PRAGMA table_info(synthesis_tasks)")
        }
        assert "routing_batches" in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "synthesis_metrics" in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def test_v3_manifest_is_not_accepted_as_v4(tmp_path):
    project = tmp_path / "legacy"
    project.mkdir()
    (project / "project.json").write_text(
        '{"schema_version": "audiobook-project-v3", "name": "legacy"}',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="schema mismatch"):
        ProjectV4Repository(tmp_path).load_manifest(project)
