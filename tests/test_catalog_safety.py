"""PR C 数据安全边界回归（P0，无 gradio）。

覆盖（设计 B9 / B6-T05）：
1. active production（running/pausing/recovering/cancelling/pending）→
   archive 抛 ProjectMutationBlockedError；
2. 打开项目被移入回收站 → session reset（ss.project=None、snapshot=None、
   synthesis=None）且不再加载剧本；
3. 清理不删 task provenance / quality 记录 / Voice Cast / 正式 wav / exports /
   structured_script（构造项目 → execute_cleanup → 断言 durable 文件仍在）；
4. 恢复备份 / 回收站恢复拒绝重名覆盖（FileExistsError / 明确错误）；
5. 搜索大小写 / 中文 substring；
6. 移入回收站两步确认契约。
"""
from __future__ import annotations
from lib import project_paths

import json
import os

import pytest

from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import ProjectBackupService, ProjectService, ProjectStorageService
from services.project import ProjectMutationBlockedError
from services.project_catalog import ProjectCatalogService
from services.session import SessionState
from ui import project_catalog_handlers as handlers

ACTIVE_STATES = ("pending", "running", "pausing", "recovering", "cancelling")


def _script_file(tmp_path, title="安全书", author="作者"):
    path = tmp_path / "book.json"
    path.write_text(json.dumps({
        "meta": {"title": title, "author": author},
        "voices": {"旁白": {"description": "测试"}},
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [{"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "测试"}],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def safety_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project("safety_book", str(_script_file(tmp_path)))
    return data_root


def _save_active_task(project: str, status: str, task_id: str) -> None:
    TaskRepository.save_task(TaskRecord(
        task_id=task_id,
        task_type="synthesis",
        project=project,
        status=status,
        created_at="2026-08-16T00:00:00Z",
        updated_at="2026-08-16T00:01:00Z",
    ))


# ── 1. active production 禁移入回收站（P0） ──


@pytest.mark.parametrize("status", ACTIVE_STATES)
def test_archive_blocked_during_active_production(safety_workspace, status):
    _save_active_task("safety_book", status, f"task_{status}")
    with pytest.raises(ProjectMutationBlockedError):
        ProjectStorageService.archive("safety_book")
    # 项目仍在原处，未被归档
    assert os.path.isdir(os.path.join(safety_workspace, "projects", "safety_book"))


def test_archive_allowed_when_production_finished(safety_workspace):
    TaskRepository.save_task(TaskRecord(
        task_id="task_done",
        task_type="synthesis",
        project="safety_book",
        status="done",
        created_at="2026-08-16T00:00:00Z",
        updated_at="2026-08-16T00:01:00Z",
        finished_at="2026-08-16T00:01:00Z",
    ))
    target = ProjectStorageService.archive("safety_book")  # 不抛
    assert "safety_book" in target


# ── 2. 打开项目被移入回收站 → session reset ──


def test_archive_opened_project_resets_session(safety_workspace):
    snapshot = ProjectService.open_project_as_snapshot("safety_book")
    ss = SessionState()
    ss.set_project("safety_book", snapshot.script, snapshot.bindings)
    ss.set_snapshot(snapshot)
    ss.synthesis = object()  # 占位合成态

    _msg, _state, _sel, _info = handlers.archive_selected("safety_book", "safety_book", ss)
    assert ss.project is None
    assert ss.script is None
    assert ss.bindings == {}
    assert ss.project_snapshot is None
    assert ss.synthesis is None


# ── 3. 清理不删 durable 产物 ──


def test_cleanup_keeps_durable_artifacts(safety_workspace):
    project_dir = os.path.join(safety_workspace, "projects", "safety_book")
    segment_dir = project_paths.project_dir(project_dir, "segments", create=True)
    os.makedirs(segment_dir, exist_ok=True)
    exports_dir = project_paths.project_dir(project_dir, "delivery_official", create=True)
    os.makedirs(exports_dir, exist_ok=True)

    # durable 产物：task provenance / quality / Voice Cast / 正式 wav / exports
    TaskRepository.save_task(TaskRecord(
        task_id="task_provenance",
        task_type="synthesis",
        project="safety_book",
        status="done",
        created_at="2026-08-16T00:00:00Z",
        updated_at="2026-08-16T00:01:00Z",
        finished_at="2026-08-16T00:01:00Z",
    ))
    QualityRepository.create_history_record(
        "safety_book", "repair_history", "repair", {"status": "done"}
    )
    roster_path = project_paths.project_file(project_dir, "character_roster")
    with open(roster_path, "w", encoding="utf-8") as file:
        json.dump({"mode": "voice_cast", "roles": []}, file, ensure_ascii=False)
    formal_wav = os.path.join(segment_dir, "1-001.wav")
    with open(formal_wav, "wb") as file:
        file.write(b"RIFF-formal-audio")
    export_file = os.path.join(exports_dir, "book.mp3")
    with open(export_file, "wb") as file:
        file.write(b"ID3-export")
    script_path = project_paths.project_file(project_dir, "structured_script")
    assert os.path.isfile(script_path)

    # 同时制造一个可清理的空段音频
    empty_wav = os.path.join(segment_dir, "2-999.wav")
    empty_wav_touch = os.path.join(segment_dir, "empty.part")
    open(empty_wav, "wb").close()
    open(empty_wav_touch, "wb").close()

    plan = ProjectStorageService.scan_cleanup("safety_book")
    result = ProjectStorageService.execute_cleanup("safety_book", plan["token"])
    assert result["ok"] is True

    # durable 产物全部保留
    assert TaskRepository.load_task("task_provenance") is not None
    assert os.path.isfile(roster_path)
    assert os.path.isfile(formal_wav)
    assert os.path.isfile(export_file)
    assert os.path.isfile(script_path)
    state = QualityRepository.load("safety_book")
    assert state.get("repair_history"), "quality 记录应保留"
    # 可清理文件已删除
    assert not os.path.exists(empty_wav)
    assert not os.path.exists(empty_wav_touch)


# ── 4. 恢复拒绝重名覆盖 ──


def test_backup_restore_rejects_name_conflict(safety_workspace, tmp_path):
    archive = ProjectBackupService.create_backup("safety_book")
    ProjectStorageService.archive("safety_book")
    # 同名项目重新出现 → 恢复必须拒绝
    ProjectRepository.create_project("safety_book", str(_script_file(tmp_path)))
    with pytest.raises(FileExistsError):
        ProjectBackupService.restore_backup(archive)


def test_recycle_bin_restore_rejects_name_conflict(safety_workspace):
    ProjectStorageService.archive("safety_book")
    archived = ProjectStorageService.list_archived()
    assert len(archived) == 1
    archive_id = archived[0]["archive_id"]
    ProjectRepository.create_project("safety_book", str(_script_file(safety_workspace)))
    with pytest.raises(FileExistsError):
        ProjectStorageService.restore_archived(archive_id)
    # 回收站条目仍在（未被破坏）
    assert len(ProjectStorageService.list_archived()) == 1


# ── 5. 搜索大小写 / 中文 substring ──


def test_search_case_and_chinese_substring(safety_workspace, tmp_path):
    ProjectRepository.create_project(
        "Another Book", str(_script_file(tmp_path, title="第二本书", author="作者B"))
    )
    assert [s.project_name for s in ProjectCatalogService.search_projects("SAFETY")] == ["safety_book"]
    assert [s.project_name for s in ProjectCatalogService.search_projects("another")] == ["Another Book"]
    assert [s.project_name for s in ProjectCatalogService.search_projects("第二本")] == ["Another Book"]
    assert [s.project_name for s in ProjectCatalogService.search_projects("作者B")] == ["Another Book"]
    assert ProjectCatalogService.search_projects("找不到的xyz") == []


# ── 6. 移入回收站两步确认契约 ──


def test_archive_two_step_confirmation_contract(safety_workspace):
    project_dir = os.path.join(safety_workspace, "projects", "safety_book")
    msg1, state1, _sel1, _info1 = handlers.archive_selected("safety_book", "", None)
    assert "确认将「safety_book」移入回收站" in msg1
    assert state1.get("value") == "safety_book"
    assert os.path.isdir(project_dir)  # 第一次绝不归档

    msg2, state2, sel2, info2 = handlers.archive_selected("safety_book", "safety_book", None)
    assert "已移入回收站" in msg2
    assert state2.get("value") == ""
    assert sel2.get("value") == ""  # 成功后 selected 清空
    assert not os.path.isdir(project_dir)
