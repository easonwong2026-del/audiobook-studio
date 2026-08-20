"""C.2 transactional Chapter → Book merge coverage."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gradio as gr
import pytest

from lib import project_paths
from repositories.project_repo import ProjectRepository
from services.chapter_merge_executor import (
    MERGE_FAILED_ROLLBACK_FAILED,
    MERGE_FAILED_ROLLED_BACK,
    ChapterMergeExecutor,
    MergeExecutionError,
    MergeExecutionStage,
)
from services.chapter_merge_planner import ChapterMergePlanner
from services.session import SessionState
from ui.chapter_merge_handlers import (
    clear_merge_execution_controls,
    refresh_merge_workflow_controls,
)
from ui.pages.overview_page import create_overview_page


def _script(title: str, segment_ids: tuple[str, ...], roles=("旁白",)) -> dict:
    return {
        "meta": {"title": title, "author": "作者"},
        "voices": {role: {"description": role} for role in roles},
        "chapters": [
            {
                "id": f"{title}-chapter",
                "title": "第一章",
                "segments": [
                    {
                        "id": segment_id,
                        "role": roles[index % len(roles)],
                        "text": f"{title}-{segment_id}",
                    }
                    for index, segment_id in enumerate(segment_ids)
                ],
            }
        ],
    }


def _tree_snapshot(name: str) -> dict[str, str]:
    root = Path(ProjectRepository.get_project_dir(name))
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


@pytest.fixture
def executor_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    shared_voice = tmp_path / "shared-voice.wav"
    shared_voice.write_bytes(b"shared voice")

    def create(name: str, segment_ids=("1-001",), roles=("旁白",)) -> None:
        ProjectRepository.create_project_from_data(name, _script(name, tuple(segment_ids), roles))

    def meta(name: str) -> dict:
        path = Path(project_paths.project_file(ProjectRepository.get_project_dir(name), "project_meta"))
        return json.loads(path.read_text(encoding="utf-8"))

    def write_meta(name: str, **updates) -> None:
        path = Path(project_paths.project_file(ProjectRepository.get_project_dir(name), "project_meta"))
        value = meta(name)
        value.update(updates)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def bind(name: str, path: Path | None = None) -> None:
        project_dir = ProjectRepository.get_project_dir(name)
        binding_path = Path(project_paths.project_file(project_dir, "voice_bindings"))
        value = json.loads(binding_path.read_text(encoding="utf-8"))
        value.setdefault("bindings", {})["旁白"] = str(path or shared_voice)
        binding_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def add_audio(name: str, segment_id: str, payload: bytes = b"wav") -> None:
        directory = Path(project_paths.project_dir(ProjectRepository.get_project_dir(name), "segments", create=True))
        (directory / f"{segment_id}.wav").write_bytes(payload)

    def pair(*, source_ids=("c-001",), target_ids=("b-001",), source_order=2, source_roles=("旁白",), target_roles=("旁白",)) -> None:
        create("book", target_ids, target_roles)
        create("chapter", source_ids, source_roles)
        book_id = meta("book")["project_id"]
        write_meta("chapter", project_kind="chapter", parent_project_id=book_id, chapter_title="规划章节", chapter_order=source_order)
        bind("book")
        bind("chapter")

    return {
        "root": data_root,
        "create": create,
        "meta": meta,
        "write_meta": write_meta,
        "bind": bind,
        "add_audio": add_audio,
        "pair": pair,
        "shared_voice": shared_voice,
    }


def _current_plan(workspace) -> tuple:
    workspace["pair"]()
    workspace["add_audio"]("chapter", "c-001")
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    session = SessionState()
    session.set_selected("chapter")
    confirmation = ChapterMergeExecutor.prepare_confirmation(plan, {}, session=session)
    return plan, session, confirmation


def test_e1_to_e10_success_backup_source_preservation_and_idempotency(executor_workspace):
    plan, session, confirmation = _current_plan(executor_workspace)
    source_before = _tree_snapshot("chapter")
    target_before = _tree_snapshot("book")

    result = ChapterMergeExecutor.execute(plan, {}, confirmation, session=session)

    assert result.success is True
    assert result.stage == MergeExecutionStage.SUCCEEDED.value
    assert result.imported_segment_count == 1
    assert result.imported_audio_count == 1
    assert Path(result.backup_path).is_file()
    assert Path(result.journal_path).is_file()
    assert _tree_snapshot("chapter") == source_before
    assert _tree_snapshot("book") != target_before

    merged_script = json.loads(
        Path(project_paths.project_file(ProjectRepository.get_project_dir("book"), "structured_script")).read_text(encoding="utf-8")
    )
    assert [segment["id"] for chapter in merged_script["chapters"] for segment in chapter["segments"]] == ["b-001", "c-001"]
    assert Path(project_paths.project_dir(ProjectRepository.get_project_dir("book"), "segments")) .joinpath("c-001.wav").is_file()
    history_path = Path(project_paths.project_dir(ProjectRepository.get_project_dir("book"), "config")) / "merge_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(history) == 1
    assert history[0]["source_unchanged"] is True

    repeated = ChapterMergePlanner.plan_merge("chapter", "book")
    assert any(item.code == "ALREADY_MERGED" for item in repeated.conflicts)


def test_stale_plan_and_confirmation_never_mutate(executor_workspace):
    plan, session, confirmation = _current_plan(executor_workspace)
    script_path = Path(project_paths.project_file(ProjectRepository.get_project_dir("chapter"), "structured_script"))
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["chapters"][0]["segments"][0]["text"] = "changed after Analyze"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    before = _tree_snapshot("book")

    result = ChapterMergeExecutor.execute(plan, {}, confirmation, session=session)

    assert result.status == "VALIDATION_FAILED"
    assert result.error_code == "STALE_PLAN"
    assert _tree_snapshot("book") == before


def test_changed_source_after_success_is_blocked_as_non_incremental(executor_workspace):
    plan, session, confirmation = _current_plan(executor_workspace)
    result = ChapterMergeExecutor.execute(plan, {}, confirmation, session=session)
    assert result.success is True
    script_path = Path(project_paths.project_file(ProjectRepository.get_project_dir("chapter"), "structured_script"))
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["chapters"][0]["segments"][0]["text"] = "changed after previous merge"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    next_plan = ChapterMergePlanner.plan_merge("chapter", "book")
    assert any(item.code == "SOURCE_CHANGED_AFTER_PREVIOUS_MERGE" for item in next_plan.conflicts)


def test_voice_conflict_requires_explicit_keep_target(executor_workspace):
    executor_workspace["pair"]()
    executor_workspace["add_audio"]("chapter", "c-001")
    other = executor_workspace["root"] / "other-voice.wav"
    other.write_bytes(b"other voice")
    executor_workspace["bind"]("book", other)
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    assert any(item.code == "VOICE_BINDING_CONFLICT" for item in plan.conflicts)
    session = SessionState()
    session.set_selected("chapter")
    with pytest.raises(MergeExecutionError):
        ChapterMergeExecutor.prepare_confirmation(plan, {}, session=session)
    confirmation = ChapterMergeExecutor.prepare_confirmation(
        plan, {"voice_conflicts": {"旁白": "KEEP_TARGET"}}, session=session
    )
    result = ChapterMergeExecutor.execute(
        plan,
        {"voice_conflicts": {"旁白": "KEEP_TARGET"}},
        confirmation,
        session=session,
    )
    assert result.success is True


@pytest.mark.parametrize(
    "fault, expected_stage",
    [
        ({"backup": True}, MergeExecutionStage.BACKUP_FAILED.value),
        ({"stage_copy": True}, MergeExecutionStage.STAGE_FAILED.value),
    ],
)
def test_rb1_backup_and_staging_fail_before_target_mutation(executor_workspace, fault, expected_stage):
    plan, session, confirmation = _current_plan(executor_workspace)
    before = _tree_snapshot("book")
    result = ChapterMergeExecutor.execute(plan, {}, confirmation, session=session, fault_injection=fault)
    assert result.stage == expected_stage
    assert _tree_snapshot("book") == before


@pytest.mark.parametrize(
    "fault",
    [
        {"metadata_write": True},
        {"script_commit": True},
        {"audio_commit": True},
        {"quality_commit": True},
        {"integrity": True},
    ],
)
def test_rb2_to_rb6_commit_or_verify_failure_restores_target_and_source(executor_workspace, fault):
    plan, session, confirmation = _current_plan(executor_workspace)
    target_before = _tree_snapshot("book")
    source_before = _tree_snapshot("chapter")
    result = ChapterMergeExecutor.execute(plan, {}, confirmation, session=session, fault_injection=fault)
    assert result.status == MERGE_FAILED_ROLLED_BACK
    assert result.rollback_status == "ROLLED_BACK"
    assert _tree_snapshot("book") == target_before
    assert _tree_snapshot("chapter") == source_before


def test_rb7_rollback_failure_is_critical(executor_workspace):
    plan, session, confirmation = _current_plan(executor_workspace)
    result = ChapterMergeExecutor.execute(
        plan,
        {},
        confirmation,
        session=session,
        fault_injection={"script_commit": True, "rollback": True},
    )
    assert result.status == MERGE_FAILED_ROLLBACK_FAILED
    assert result.stage == MergeExecutionStage.ROLLBACK_FAILED.value
    assert result.rollback_status == "ROLLBACK_FAILED"


def test_source_only_role_can_be_added_only_with_explicit_resolution(executor_workspace):
    executor_workspace["pair"](source_roles=("旁白", "角色A"))
    executor_workspace["add_audio"]("chapter", "c-001")
    source_dir = Path(ProjectRepository.get_project_dir("chapter"))
    voice_path = Path(project_paths.project_dir(str(source_dir), "project_voices", create=True)) / "role-a.wav"
    voice_path.write_bytes(b"role A voice")
    digest = hashlib.sha256(voice_path.read_bytes()).hexdigest()
    cast_path = Path(project_paths.project_file(str(source_dir), "voice_cast", create=True))
    cast_path.write_text(
        json.dumps({"roles": {"角色A": {"role_id": "角色A", "name": "角色A", "project_voice_path": str(voice_path.relative_to(source_dir)), "voice_sha256": digest}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    session = SessionState()
    session.set_selected("chapter")
    resolution = {"voice_conflicts": {"角色A": "ADD_SOURCE_ROLE"}}
    confirmation = ChapterMergeExecutor.prepare_confirmation(plan, resolution, session=session)
    result = ChapterMergeExecutor.execute(plan, resolution, confirmation, session=session)
    assert result.success is True
    target_cast = json.loads(Path(project_paths.project_file(ProjectRepository.get_project_dir("book"), "voice_cast")).read_text(encoding="utf-8"))
    assert "角色A" in target_cast["roles"]


def test_u5_to_u12_c2_controls_are_separate_from_bookshelf_contract(executor_workspace):
    executor_workspace["pair"]()
    with gr.Blocks():
        page = create_overview_page()
    assert {
        "merge_resolution",
        "merge_confirm",
        "merge_confirmation_state",
        "merge_execute",
        "merge_execution_result",
        "merge_transaction_state",
    } <= page.keys()
    assert len(clear_merge_execution_controls()) == 6
    assert len(refresh_merge_workflow_controls(SessionState())) == 11
