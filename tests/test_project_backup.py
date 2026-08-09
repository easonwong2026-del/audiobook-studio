"""Project ZIP manifest and secure restore regressions."""
from __future__ import annotations

import json
import os
import zipfile

import pytest

from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services.project_backup import ProjectBackupService


def _make_project(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    script = tmp_path / "book.json"
    script.write_text(json.dumps({
        "meta": {"title": "备份书", "author": "测试"},
        "voices": {"旁白": {"description": "测试"}},
        "chapters": [{"id": 1, "title": "第一章", "segments": [
            {"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "测试"},
        ]}],
    }, ensure_ascii=False), encoding="utf-8")
    ProjectRepository.create_project("backup_book", str(script))
    return data_root


def test_backup_manifest_and_restore(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    TaskRepository.save_task(TaskRecord(
        task_id="task_backup",
        task_type="synthesis",
        project="backup_book",
        status="done",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:01:00Z",
        finished_at="2026-08-09T00:01:00Z",
    ))
    repair = QualityRepository.create_history_record(
        "backup_book",
        "repair_history",
        "repair",
        {"status": "done"},
    )
    export = QualityRepository.create_history_record(
        "backup_book",
        "export_jobs",
        "export",
        {"status": "done"},
    )
    manifest_record = QualityRepository.create_history_record(
        "backup_book",
        "delivery_manifests",
        "manifest",
        {"ready": True, "export_id": export["export_id"]},
    )
    archive = ProjectBackupService.create_backup("backup_book")
    assert archive.endswith(".audiobook-project.zip")
    with zipfile.ZipFile(archive) as zip_file:
        manifest = json.loads(zip_file.read("manifest.json"))
        assert manifest["format"] == "audiobook-studio-project"
        assert manifest["file_count"] == len(manifest["files"])

    ProjectRepository.archive_project("backup_book")
    restored = ProjectBackupService.restore_backup(archive)
    assert restored.endswith("backup_book")
    assert "backup_book" in ProjectRepository.scan_projects()
    assert TaskRepository.load_task("task_backup").status == "done"
    state = QualityRepository.load("backup_book")
    assert repair["repair_id"] in state["repair_history"]
    assert export["export_id"] in state["export_jobs"]
    assert manifest_record["manifest_id"] in state["delivery_manifests"]


def test_restore_rejects_zip_slip(tmp_path, monkeypatch):
    _data_root = _make_project(tmp_path, monkeypatch)
    archive_path = tmp_path / "malicious.zip"
    manifest = {
        "format": "audiobook-studio-project",
        "format_version": 1,
        "project_name": "malicious",
        "file_count": 1,
        "files": [{"path": "../outside.txt", "sha256": "0" * 64, "size": 0}],
    }
    with zipfile.ZipFile(archive_path, "w") as zip_file:
        zip_file.writestr("manifest.json", json.dumps(manifest))
        zip_file.writestr("../outside.txt", b"")
    with pytest.raises(ValueError):
        ProjectBackupService.restore_backup(str(archive_path))


def test_restore_normalizes_copied_active_runtime_tasks(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    # The public backup API correctly blocks active work.  Bypass that guard
    # here only to construct a portable fixture containing a stale owner.
    monkeypatch.setattr(
        "services.project.ensure_project_mutation_allowed",
        lambda *_args, **_kwargs: None,
    )
    TaskRepository.save_task(TaskRecord(
        task_id="export_backup_active",
        task_type="export",
        project="backup_book",
        status="running",
        owner_id="old-machine-runtime",
        heartbeat_at="2026-08-09T00:00:00Z",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:01:00Z",
    ))
    archive = ProjectBackupService.create_backup("backup_book")
    ProjectRepository.archive_project("backup_book")
    ProjectBackupService.restore_backup(archive)
    restored = TaskRepository.load_task("export_backup_active")
    assert restored is not None
    assert restored.status == "interrupted"
    assert restored.owner_id == ""


def test_restore_normalization_failure_does_not_publish_project(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "services.project.ensure_project_mutation_allowed",
        lambda *_args, **_kwargs: None,
    )
    archive = ProjectBackupService.create_backup("backup_book")
    ProjectRepository.archive_project("backup_book")

    def fail_normalization(*_args, **_kwargs):
        raise RuntimeError("normalization failed")

    monkeypatch.setattr(
        TaskRepository,
        "normalize_restored_tasks",
        staticmethod(fail_normalization),
    )
    with pytest.raises(RuntimeError, match="normalization failed"):
        ProjectBackupService.restore_backup(archive)

    final_dir = ProjectRepository.get_project_dir("backup_book")
    assert not os.path.exists(final_dir)
    workspace = os.path.dirname(final_dir)
    assert not any(
        name.startswith(".tmp_restore_")
        for name in os.listdir(workspace)
    )
