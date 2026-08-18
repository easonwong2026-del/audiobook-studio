"""Project mutation service guards while production is active."""
from __future__ import annotations
from lib import project_paths
from pathlib import Path

import json
import os

import pytest

from repositories.project_repo import ProjectRepository
from repositories.project_storage_repo import ProjectStorageRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services.project import ProjectMutationBlockedError, ProjectService
from services.project_backup import ProjectBackupService
from services.project_storage import ProjectStorageService
from services.voice_cast import VoiceCastResolver


SCRIPT = {
    "meta": {"title": "Guard"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


@pytest.fixture
def guarded_project(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        ProjectRepository,
        "WORKSPACE_ROOT",
        str(data_dir / "projects"),
    )
    monkeypatch.setattr(
        ProjectRepository,
        "LEGACY_ROOT",
        str(data_dir / "legacy"),
    )
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    ProjectRepository.create_project_from_data("guarded", SCRIPT)
    now = "2026-08-09T00:00:00Z"
    outcome, task = TaskRepository.create_production_task(TaskRecord(
        task_id="task_guarded",
        task_type="synthesis",
        project="guarded",
        status="pending",
        source="web",
        created_at=now,
        updated_at=now,
    ))
    assert outcome == "created"
    source_voice = tmp_path / "voice.wav"
    source_voice.write_bytes(b"voice")
    return data_dir, source_voice, task


def _assert_blocked(error, operation: str) -> None:
    assert isinstance(error.value, ProjectMutationBlockedError)
    assert error.value.code == "PROJECT_HAS_ACTIVE_PRODUCTION"
    assert error.value.operation == operation
    assert error.value.task_id == "task_guarded"
    assert error.value.status == "pending"


def test_project_service_rejects_destructive_mutations(guarded_project):
    data_dir, source_voice, _task = guarded_project
    project_dir = data_dir / "projects" / "guarded"
    bindings_path = Path(project_paths.project_file(str(project_dir), "voice_bindings"))
    before = json.loads(bindings_path.read_text(encoding="utf-8"))

    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectService.bind_voice("guarded", "旁白", str(source_voice))
    _assert_blocked(error, "bind_voice")
    assert json.loads(bindings_path.read_text(encoding="utf-8")) == before

    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectService.update_segment_status("guarded", "001-001", "done")
    _assert_blocked(error, "update_segment_status")

    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectService.delete_project("guarded")
    _assert_blocked(error, "delete_project")
    assert project_dir.is_dir()


def test_data_dir_switch_rejects_any_active_project(guarded_project, tmp_path):
    _data_dir, _source_voice, _task = guarded_project
    before_root = ProjectRepository.WORKSPACE_ROOT
    ProjectStorageRepository.remove_from_list("guarded")
    assert "guarded" not in ProjectRepository.scan_projects()
    target = tmp_path / "other-data"
    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectService.set_data_dir(str(target))
    _assert_blocked(error, "set_data_dir")
    assert ProjectRepository.WORKSPACE_ROOT == before_root
    assert not target.exists()


def test_storage_archive_cleanup_and_repair_are_guarded(guarded_project):
    data_dir, _source_voice, _task = guarded_project
    project_dir = data_dir / "projects" / "guarded"

    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectStorageService.archive("guarded")
    _assert_blocked(error, "archive_project")
    assert project_dir.is_dir()

    candidate = Path(project_paths.project_dir(str(project_dir), "cache", create=True)) / "active.part"
    candidate.write_bytes(b"partial")
    plan = ProjectStorageRepository.scan_cleanup("guarded")
    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectStorageService.execute_cleanup("guarded", plan.token)
    _assert_blocked(error, "execute_cleanup")
    assert candidate.exists()

    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectStorageService.repair_integrity("guarded")
    _assert_blocked(error, "repair_integrity")

    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectBackupService.create_backup("guarded")
    _assert_blocked(error, "create_project_backup")

    with pytest.raises(ProjectMutationBlockedError) as error:
        ProjectStorageService.migrate_to_projects_root(
            "guarded",
            str(data_dir / "migrated"),
        )
    _assert_blocked(error, "migrate_project")


def test_voice_cast_mutations_use_the_same_guard(guarded_project):
    with pytest.raises(ProjectMutationBlockedError) as error:
        VoiceCastResolver.set_character_roster(
            "guarded",
            [{"role_id": "narrator", "name": "旁白"}],
        )
    _assert_blocked(error, "set_character_roster")


@pytest.mark.parametrize("terminal_status", ["done", "error", "cancelled", "interrupted"])
def test_terminal_task_allows_project_binding(
    guarded_project,
    terminal_status,
):
    _data_dir, source_voice, task = guarded_project
    task.status = terminal_status
    task.finished_at = "2026-08-09T00:01:00Z"
    TaskRepository.save_task(task)

    destination = ProjectService.bind_voice(
        "guarded",
        "旁白",
        str(source_voice),
    )
    assert os.path.isfile(destination)
