"""Project ZIP manifest and secure restore regressions."""
from __future__ import annotations

import json
import zipfile

import pytest

from repositories.project_repo import ProjectRepository
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
