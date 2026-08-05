"""Project storage, safe cleanup and integrity checks."""
from __future__ import annotations

import json
import os

import pytest

from repositories.project_repo import ProjectRepository
from repositories.project_storage_repo import ProjectStorageRepository


def _script_file(tmp_path):
    path = tmp_path / "book.json"
    path.write_text(json.dumps({
        "meta": {"title": "测试书", "author": "测试"},
        "voices": {"旁白": {"description": "测试"}},
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [{"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "测试"}],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def storage_project(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project("storage_book", str(_script_file(tmp_path)))
    return data_root, data_root / "projects" / "storage_book"


def test_summary_is_recursive_and_preview_is_separate(storage_project):
    data_root, project_dir = storage_project
    source_dir = project_dir / "02_原始文件"
    source_dir.joinpath("nested").mkdir()
    source_dir.joinpath("nested", "raw.txt").write_text("raw", encoding="utf-8")
    preview_dir = data_root / "preview" / "storage_book" / "chapters"
    preview_dir.mkdir(parents=True)
    preview_dir.joinpath("preview.wav").write_bytes(b"preview")

    summary = ProjectStorageRepository.summarize("storage_book")
    assert summary.source_bytes >= 3
    assert summary.preview_bytes == len(b"preview")
    assert summary.total_bytes >= summary.source_bytes + summary.preview_bytes
    assert summary.file_count >= 2


def test_cleanup_only_removes_temp_and_empty_segment_files(storage_project):
    _data_root, project_dir = storage_project
    segment_dir = project_dir / "05_分段音频"
    empty = segment_dir / "old.wav"
    empty.touch()
    temporary = project_dir / "cache" / "work.part"
    temporary.write_bytes(b"temporary")
    manual = segment_dir / "manual.wav"
    manual.write_bytes(b"keep")

    plan = ProjectStorageRepository.scan_cleanup("storage_book")
    paths = {item.relative_path for item in plan.candidates}
    assert any(path.endswith("old.wav") for path in paths)
    assert any(path.endswith("work.part") for path in paths)
    assert not any(path.endswith("manual.wav") for path in paths)
    result = ProjectStorageRepository.execute_cleanup("storage_book", plan.token)
    assert result["ok"] is True
    assert not empty.exists()
    assert not temporary.exists()
    assert manual.exists()


def test_cleanup_refuses_stale_token(storage_project):
    _data_root, project_dir = storage_project
    candidate = project_dir / "05_分段音频" / "empty.wav"
    candidate.touch()
    plan = ProjectStorageRepository.scan_cleanup("storage_book")
    candidate.write_bytes(b"changed")
    result = ProjectStorageRepository.execute_cleanup("storage_book", plan.token)
    assert result["stale"] is True
    assert candidate.exists()


def test_integrity_reports_done_audio_missing(storage_project):
    _data_root, _project_dir = storage_project
    ProjectRepository.update_segment_status("storage_book", "1-001", "done")
    report = ProjectRepository.check_project_integrity("storage_book")
    assert report["ok"] is False
    assert any(issue["code"] == "done_audio_missing" for issue in report["issues"])


def test_archive_is_recoverable_and_path_is_bounded(storage_project):
    data_root, project_dir = storage_project
    archived = ProjectStorageRepository.archive_project("storage_book")
    assert not project_dir.exists()
    assert os.path.commonpath([os.path.realpath(archived), os.path.realpath(data_root / ".trash" / "projects")]) == os.path.realpath(data_root / ".trash" / "projects")
    with pytest.raises(ValueError):
        ProjectStorageRepository.archive_project("../storage_book")


def test_list_only_removal_keeps_project_files(storage_project):
    _data_root, project_dir = storage_project
    ProjectStorageRepository.remove_from_list("storage_book")
    assert "storage_book" not in ProjectRepository.scan_projects()
    assert project_dir.is_dir()
    ProjectStorageRepository.restore_to_list("storage_book")
    assert "storage_book" in ProjectRepository.scan_projects()
