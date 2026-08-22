"""Project storage, safe cleanup and integrity checks."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib import project_paths
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


def _write_probe(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def _manual_storage_project(tmp_path, monkeypatch, *, name: str, version: int | None):
    data_root = tmp_path / f"data-{name}"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    project_dir = data_root / "projects" / name
    project_dir.mkdir(parents=True)
    manifest = {"project_name": name}
    if version is not None:
        manifest["storage_version"] = version
    (project_dir / "project.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (project_dir / "structured_script.json").write_text("{}", encoding="utf-8")
    (project_dir / "voice_bindings.json").write_text("{}", encoding="utf-8")
    return data_root, project_dir


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
    baseline = ProjectStorageRepository.summarize("storage_book")
    source_dir = project_paths.project_dir(str(project_dir), "source_book", create=True)
    _write_probe(Path(source_dir) / "nested" / "raw.txt", 3)
    _write_probe(
        Path(project_paths.project_dir(str(project_dir), "chapter_data", create=True))
        / "chapter.json",
        5,
    )
    _write_probe(
        Path(project_paths.project_dir(str(project_dir), "project_voices", create=True))
        / "voice.wav",
        7,
    )
    _write_probe(
        Path(project_paths.project_dir(str(project_dir), "segments", create=True))
        / "segment.wav",
        11,
    )
    _write_probe(
        Path(project_paths.project_dir(str(project_dir), "chapter_audio", create=True))
        / "chapter.wav",
        13,
    )
    _write_probe(
        Path(project_paths.project_dir(str(project_dir), "merged_audio", create=True))
        / "merged.wav",
        17,
    )
    _write_probe(
        Path(project_paths.project_dir(str(project_dir), "delivery_official", create=True))
        / "official.mp3",
        19,
    )
    _write_probe(
        Path(project_paths.project_dir(str(project_dir), "delivery_supplement", create=True))
        / "supplement.mp3",
        23,
    )
    preview_dir = data_root / "preview" / "storage_book" / "chapters"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.joinpath("preview.wav").write_bytes(b"preview")

    summary = ProjectStorageRepository.summarize("storage_book")
    assert summary.source_bytes == baseline.source_bytes + 3 + 5
    assert summary.voices_bytes == baseline.voices_bytes + 7
    assert summary.segments_bytes == baseline.segments_bytes + 11
    assert summary.chapter_audio_bytes == baseline.chapter_audio_bytes + 13
    assert summary.merged_audio_bytes == baseline.merged_audio_bytes + 17
    assert summary.output_bytes == baseline.output_bytes + 19 + 23
    assert summary.preview_bytes == len(b"preview")
    assert summary.total_bytes >= summary.source_bytes + summary.preview_bytes
    assert summary.file_count >= 2


def test_v1_summary_uses_legacy_semantic_paths_without_root_or_alias_duplicates(
    tmp_path, monkeypatch
):
    _data_root, project_dir = _manual_storage_project(
        tmp_path, monkeypatch, name="v1book", version=None
    )
    for directory in ("voices", "segments", "chapters", "output", "cache", "logs"):
        (project_dir / directory).mkdir()
    _write_probe(project_dir / "voices" / "voice.wav", 7)
    _write_probe(project_dir / "segments" / "segment.wav", 11)
    _write_probe(project_dir / "chapters" / "chapter.json", 13)
    _write_probe(project_dir / "output" / "book.mp3", 17)

    summary = ProjectStorageRepository.summarize("v1book")

    assert project_paths.detect_storage_version(str(project_dir)) == 1
    assert summary.source_bytes == 13
    assert summary.voices_bytes == 7
    assert summary.segments_bytes == 11
    assert summary.chapter_audio_bytes == 0
    assert summary.merged_audio_bytes == 0
    assert summary.output_bytes == 17


def test_v2_summary_uses_semantic_aliases_without_regression(tmp_path, monkeypatch):
    _data_root, project_dir = _manual_storage_project(
        tmp_path, monkeypatch, name="v2book", version=2
    )
    for directory in (
        "02_原始文件",
        "03_章节文本",
        "04_角色与声音",
        "05_分段音频",
        "06_章节音频",
        "07_合并音频",
        "09_导出文件",
        "cache",
        "logs",
    ):
        (project_dir / directory).mkdir()
    _write_probe(project_dir / "02_原始文件" / "book.json", 5)
    _write_probe(project_dir / "03_章节文本" / "chapter.json", 7)
    _write_probe(project_dir / "04_角色与声音" / "voice.wav", 11)
    _write_probe(project_dir / "05_分段音频" / "segment.wav", 13)
    _write_probe(project_dir / "06_章节音频" / "chapter.wav", 17)
    _write_probe(project_dir / "07_合并音频" / "merged.wav", 19)
    _write_probe(project_dir / "09_导出文件" / "exports" / "book.mp3", 23)

    summary = ProjectStorageRepository.summarize("v2book")

    assert project_paths.detect_storage_version(str(project_dir)) == 2
    assert summary.source_bytes == 5 + 7
    assert summary.voices_bytes == 11
    assert summary.segments_bytes == 13
    assert summary.chapter_audio_bytes == 17
    assert summary.merged_audio_bytes == 19
    assert summary.output_bytes == 23


def test_cleanup_only_removes_temp_and_empty_segment_files(storage_project):
    _data_root, project_dir = storage_project
    project_dir = str(project_dir)
    segment_dir = project_paths.project_dir(project_dir, "segments", create=True)
    empty = os.path.join(segment_dir, "old.wav")
    open(empty, "wb").close()
    cache_dir = project_paths.project_dir(project_dir, "cache", create=True)
    temporary = os.path.join(cache_dir, "work.part")
    with open(temporary, "wb") as f:
        f.write(b"temporary")
    manual = os.path.join(segment_dir, "manual.wav")
    with open(manual, "wb") as f:
        f.write(b"keep")

    plan = ProjectStorageRepository.scan_cleanup("storage_book")
    paths = {item.relative_path for item in plan.candidates}
    assert any(path.endswith("old.wav") for path in paths)
    assert any(path.endswith("work.part") for path in paths)
    assert not any(path.endswith("manual.wav") for path in paths)
    result = ProjectStorageRepository.execute_cleanup("storage_book", plan.token)
    assert result["ok"] is True
    assert not os.path.exists(empty)
    assert not os.path.exists(temporary)
    assert os.path.exists(manual)


def test_cleanup_refuses_stale_token(storage_project):
    _data_root, project_dir = storage_project
    project_dir = str(project_dir)
    segment_dir = project_paths.project_dir(project_dir, "segments", create=True)
    candidate = os.path.join(segment_dir, "empty.wav")
    open(candidate, "wb").close()
    plan = ProjectStorageRepository.scan_cleanup("storage_book")
    with open(candidate, "wb") as f:
        f.write(b"changed")
    result = ProjectStorageRepository.execute_cleanup("storage_book", plan.token)
    assert result["stale"] is True
    assert os.path.exists(candidate)


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


def test_recycle_bin_restore_rejects_name_conflict_and_permanent_delete_is_scoped(storage_project):
    data_root, _project_dir = storage_project
    ProjectStorageRepository.archive_project("storage_book")
    archived = ProjectStorageRepository.list_archived_projects()
    assert len(archived) == 1
    assert archived[0].original_name == "storage_book"

    # A same-name active project is never overwritten by recovery.
    ProjectRepository.create_project("storage_book", str(_script_file(data_root)))
    with pytest.raises(FileExistsError):
        ProjectStorageRepository.restore_archived_project(archived[0].archive_id)
    assert (data_root / ".trash" / "projects" / archived[0].archive_id).is_dir()
    ProjectRepository.delete_project("storage_book")

    restored = ProjectStorageRepository.restore_archived_project(archived[0].archive_id)
    assert restored["restored"] is True
    assert restored["integrity"]["ok"] is True
    assert (data_root / "projects" / "storage_book").is_dir()

    ProjectStorageRepository.archive_project("storage_book")
    second = ProjectStorageRepository.list_archived_projects()[0]
    with pytest.raises(ValueError, match="只能针对"):
        ProjectStorageRepository.permanently_delete_project("storage_book")
    ProjectStorageRepository.permanently_delete_project(second.archive_id)
    assert ProjectStorageRepository.list_archived_projects() == []


def test_integrity_repair_does_not_delete_valid_audio(storage_project):
    _data_root, project_dir = storage_project
    project_dir = str(project_dir)
    segment_dir = project_paths.project_dir(project_dir, "segments", create=True)
    segment = os.path.join(segment_dir, "1-001.wav")
    with open(segment, "wb") as f:
        f.write(b"valid-audio")
    export_dir = project_paths.project_dir(project_dir, "delivery_official", create=True)
    valid_export = os.path.join(export_dir, "book.mp3")
    with open(valid_export, "wb") as f:
        f.write(b"valid-export")
    empty_export = os.path.join(export_dir, "empty.mp3")
    open(empty_export, "wb").close()

    from services.project_storage import ProjectStorageService

    ProjectRepository.update_segment_status("storage_book", "1-001", "done")
    report = ProjectStorageService.repair_integrity("storage_book")
    assert "移除空输出文件：empty.mp3" in report["repaired"]
    assert os.path.exists(segment)
    assert os.path.exists(valid_export)
    assert not os.path.exists(empty_export)


def test_list_only_removal_keeps_project_files(storage_project):
    _data_root, project_dir = storage_project
    ProjectStorageRepository.remove_from_list("storage_book")
    assert "storage_book" not in ProjectRepository.scan_projects()
    assert project_dir.is_dir()
    ProjectStorageRepository.restore_to_list("storage_book")
    assert "storage_book" in ProjectRepository.scan_projects()
