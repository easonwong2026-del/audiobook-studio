"""Whole-book Assembly orchestration and safety coverage."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gradio as gr
import pytest

from lib import project_paths
from repositories.project_repo import ProjectRepository
from services.chapter_merge_planner import ChapterMergePlanner
from services.session import SessionState
from services.whole_book_assembly import (
    ASSEMBLY_BLOCKED,
    ASSEMBLY_CRITICAL_FAILURE,
    ASSEMBLY_PARTIAL_SUCCESS_STOPPED,
    ASSEMBLY_READY,
    ASSEMBLY_READY_WITH_WARNINGS,
    ASSEMBLY_SUCCEEDED,
    CHAPTER_ALREADY_MERGED,
    CHAPTER_BLOCKED,
    CHAPTER_FAILED_ROLLED_BACK,
    CHAPTER_MERGED,
    CHAPTER_NOT_ATTEMPTED,
    WholeBookAssemblyError,
    WholeBookAssemblyService,
)
from ui.pages.overview_page import create_overview_page
from ui.whole_book_assembly_handlers import (
    prepare_assembly_execution_controls,
    refresh_assembly_workflow_controls,
)
from ui.wiring.whole_book_assembly_wiring import assembly_workflow_outputs


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
def assembly_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    shared_voice = tmp_path / "shared-voice.wav"
    shared_voice.write_bytes(b"shared voice")

    def create(name: str, segment_ids=("1-001",), roles=("旁白",)) -> None:
        ProjectRepository.create_project_from_data(
            name, _script(name, tuple(segment_ids), roles)
        )

    def meta(name: str) -> dict:
        path = Path(
            project_paths.project_file(
                ProjectRepository.get_project_dir(name), "project_meta"
            )
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def write_meta(name: str, **updates) -> None:
        path = Path(
            project_paths.project_file(
                ProjectRepository.get_project_dir(name), "project_meta"
            )
        )
        value = meta(name)
        value.update(updates)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def bind(name: str, path: Path | None = None) -> None:
        binding_path = Path(
            project_paths.project_file(
                ProjectRepository.get_project_dir(name), "voice_bindings"
            )
        )
        value = json.loads(binding_path.read_text(encoding="utf-8"))
        value.setdefault("bindings", {})["旁白"] = str(path or shared_voice)
        binding_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def add_audio(name: str, segment_id: str, payload: bytes = b"wav") -> None:
        directory = Path(
            project_paths.project_dir(
                ProjectRepository.get_project_dir(name), "segments", create=True
            )
        )
        (directory / f"{segment_id}.wav").write_bytes(payload)

    def make_book(specs, *, target_ids=("book-001",)) -> None:
        create("book", target_ids)
        book_id = meta("book")["project_id"]
        bind("book")
        for index, (name, segment_ids) in enumerate(specs, start=1):
            create(name, segment_ids)
            write_meta(
                name,
                project_kind="chapter",
                parent_project_id=book_id,
                chapter_title=name.title(),
                chapter_order=index,
            )
            bind(name)
            for segment_id in segment_ids:
                add_audio(name, segment_id)

    return {
        "root": data_root,
        "create": create,
        "meta": meta,
        "write_meta": write_meta,
        "bind": bind,
        "add_audio": add_audio,
        "make_book": make_book,
        "shared_voice": shared_voice,
    }


def _selected_book() -> SessionState:
    session = SessionState()
    session.set_selected("book")
    return session


def test_p1_to_p3_plan_uses_catalog_order_and_is_deterministic(assembly_workspace):
    assembly_workspace["make_book"](
        [("chapter-z", ("z-001",)), ("chapter-a", ("a-001",)), ("chapter-m", ("m-001",))]
    )
    session = _selected_book()
    first = WholeBookAssemblyService.plan_assembly("book", session=session)
    second = WholeBookAssemblyService.plan_assembly("book", session=session)

    assert first.aggregate_status == ASSEMBLY_READY
    assert first.ordered_chapter_names == ("chapter-z", "chapter-a", "chapter-m")
    assert first.assembly_token == second.assembly_token
    assert first.as_dict() == second.as_dict()


def test_duplicate_order_uses_catalog_stable_fallback(assembly_workspace):
    assembly_workspace["make_book"](
        [("chapter-z", ("z-001",)), ("chapter-a", ("a-001",))]
    )
    book_id = assembly_workspace["meta"]("book")["project_id"]
    for name in ("chapter-z", "chapter-a"):
        assembly_workspace["write_meta"](
            name, parent_project_id=book_id, chapter_order=1
        )
    plan = WholeBookAssemblyService.plan_assembly("book", session=_selected_book())
    assert plan.ordered_chapter_names == ("chapter-a", "chapter-z")


def test_orphan_is_not_a_healthy_child_and_invalid_related_is_reported(
    assembly_workspace,
):
    assembly_workspace["make_book"]([("chapter", ("c-001",))])
    assembly_workspace["create"]("orphan", ("o-001",))
    assembly_workspace["write_meta"](
        "orphan", project_kind="chapter", parent_project_id="missing-parent"
    )
    plan = WholeBookAssemblyService.plan_assembly("book", session=_selected_book())
    assert "orphan" not in plan.ordered_chapter_names

    duplicate_id = assembly_workspace["meta"]("chapter")["project_id"]
    assembly_workspace["create"]("invalid", ("i-001",))
    assembly_workspace["write_meta"](
        "invalid",
        project_kind="chapter",
        parent_project_id=assembly_workspace["meta"]("book")["project_id"],
        project_id=duplicate_id,
    )
    invalid_plan = WholeBookAssemblyService.plan_assembly(
        "book", session=_selected_book()
    )
    assert invalid_plan.aggregate_status == ASSEMBLY_BLOCKED
    assert "INVALID_RELATED_CHAPTER" in {
        item.code for item in invalid_plan.blocking_conflicts
    }


def test_empty_book_and_chapter_target_are_safe(assembly_workspace):
    assembly_workspace["make_book"]([])
    empty = WholeBookAssemblyService.plan_assembly("book", session=_selected_book())
    assert empty.aggregate_status == ASSEMBLY_READY_WITH_WARNINGS
    assert "NO_CHAPTERS" in {item.code for item in empty.assembly_conflicts}

    assembly_workspace["create"]("standalone-chapter", ("s-001",))
    assembly_workspace["write_meta"](
        "standalone-chapter", project_kind="chapter", parent_project_id=None
    )
    chapter = WholeBookAssemblyService.plan_assembly(
        "standalone-chapter", session=_selected_book()
    )
    assert chapter.aggregate_status == ASSEMBLY_BLOCKED
    assert "TARGET_NOT_BOOK" in {item.code for item in chapter.blocking_conflicts}


def test_e1_to_e8_executes_sequentially_and_preserves_sources(assembly_workspace):
    assembly_workspace["make_book"](
        [("chapter-1", ("c1-001",)), ("chapter-2", ("c2-001",)), ("chapter-3", ("c3-001",))]
    )
    source_before = {name: _tree_snapshot(name) for name in ("chapter-1", "chapter-2", "chapter-3")}
    session = _selected_book()
    plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(
        plan, {}, session=session
    )
    result = WholeBookAssemblyService.execute_assembly(
        plan, {}, confirmation, session=session
    )

    assert result.status == ASSEMBLY_SUCCEEDED
    assert [item.execution_result for item in result.chapter_results] == [
        CHAPTER_MERGED,
        CHAPTER_MERGED,
        CHAPTER_MERGED,
    ]
    assert all(item.transaction_id for item in result.chapter_results)
    assert result.merged_this_run == 3
    assert result.final_integrity["ok"] is True
    assert {name: _tree_snapshot(name) for name in source_before} == source_before

    target_script = json.loads(
        Path(
            project_paths.project_file(
                ProjectRepository.get_project_dir("book"), "structured_script"
            )
        ).read_text(encoding="utf-8")
    )
    assert [chapter["id"] for chapter in target_script["chapters"]] == [
        "book-chapter",
        "chapter-1-chapter",
        "chapter-2-chapter",
        "chapter-3-chapter",
    ]


def test_rerun_classifies_already_merged_without_duplicate_mutation(assembly_workspace):
    assembly_workspace["make_book"](
        [("chapter-1", ("c1-001",)), ("chapter-2", ("c2-001",))]
    )
    session = _selected_book()
    first_plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    first_confirmation = WholeBookAssemblyService.prepare_confirmation(
        first_plan, {}, session=session
    )
    first = WholeBookAssemblyService.execute_assembly(
        first_plan, {}, first_confirmation, session=session
    )
    assert first.status == ASSEMBLY_SUCCEEDED
    before = _tree_snapshot("book")

    second_plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    assert [item.initial_plan_status for item in second_plan.ordered_chapters] == [
        CHAPTER_ALREADY_MERGED,
        CHAPTER_ALREADY_MERGED,
    ]
    second_confirmation = WholeBookAssemblyService.prepare_confirmation(
        second_plan, {}, session=session
    )
    second = WholeBookAssemblyService.execute_assembly(
        second_plan, {}, second_confirmation, session=session
    )
    assert second.status == ASSEMBLY_SUCCEEDED
    assert all(item.execution_result == CHAPTER_ALREADY_MERGED for item in second.chapter_results)
    assert _tree_snapshot("book") == before


def test_source_changed_after_previous_merge_remains_blocked(assembly_workspace):
    assembly_workspace["make_book"]([("chapter-1", ("c1-001",))])
    session = _selected_book()
    plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(plan, {}, session=session)
    first = WholeBookAssemblyService.execute_assembly(
        plan, {}, confirmation, session=session
    )
    assert first.status == ASSEMBLY_SUCCEEDED
    script_path = Path(
        project_paths.project_file(
            ProjectRepository.get_project_dir("chapter-1"), "structured_script"
        )
    )
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["chapters"][0]["segments"][0]["text"] = "changed after merge"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    rerun = WholeBookAssemblyService.plan_assembly("book", session=session)
    assert rerun.aggregate_status == ASSEMBLY_BLOCKED
    assert "SOURCE_CHANGED_AFTER_PREVIOUS_MERGE" in {
        item.code for item in rerun.blocking_conflicts
    }


def test_t1_replans_each_chapter_against_evolving_target(assembly_workspace, monkeypatch):
    assembly_workspace["make_book"](
        [("chapter-1", ("c1-001",)), ("chapter-2", ("c2-001",))]
    )
    seen_target_counts: list[int] = []
    original = ChapterMergePlanner.plan_merge

    def wrapped(_cls, *args, **kwargs):
        plan = original(*args, **kwargs)
        seen_target_counts.append(plan.target_inventory.total_segments)
        return plan

    monkeypatch.setattr(ChapterMergePlanner, "plan_merge", classmethod(wrapped))
    session = _selected_book()
    plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(plan, {}, session=session)
    result = WholeBookAssemblyService.execute_assembly(
        plan, {}, confirmation, session=session
    )
    assert result.status == ASSEMBLY_SUCCEEDED
    assert 1 in seen_target_counts
    assert 2 in seen_target_counts
    assert seen_target_counts[-1] >= 2


def test_t2_collision_after_first_merge_stops_without_mutating_next_chapter(
    assembly_workspace,
):
    assembly_workspace["make_book"](
        [
            ("chapter-1", ("new-001",)),
            ("chapter-2", ("new-001",)),
            ("chapter-3", ("c3-001",)),
        ]
    )
    source_before = _tree_snapshot("chapter-2")
    session = _selected_book()
    plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(plan, {}, session=session)
    result = WholeBookAssemblyService.execute_assembly(
        plan, {}, confirmation, session=session
    )

    assert result.status == ASSEMBLY_PARTIAL_SUCCESS_STOPPED
    assert [item.execution_result for item in result.chapter_results] == [
        CHAPTER_MERGED,
        CHAPTER_BLOCKED,
        CHAPTER_NOT_ATTEMPTED,
    ]
    assert _tree_snapshot("chapter-2") == source_before


def test_ps2_failure_rolls_back_only_current_chapter_and_stops_later(
    assembly_workspace,
):
    assembly_workspace["make_book"](
        [
            ("chapter-1", ("c1-001",)),
            ("chapter-2", ("c2-001",)),
            ("chapter-3", ("c3-001",)),
        ]
    )
    session = _selected_book()
    plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(plan, {}, session=session)
    result = WholeBookAssemblyService.execute_assembly(
        plan,
        {},
        confirmation,
        session=session,
        fault_injection={"by_chapter": {"chapter-2": {"script_commit": True}}},
    )

    assert result.status == ASSEMBLY_PARTIAL_SUCCESS_STOPPED
    assert result.chapter_results[0].execution_result == CHAPTER_MERGED
    assert result.chapter_results[1].execution_result == CHAPTER_FAILED_ROLLED_BACK
    assert result.chapter_results[2].execution_result == CHAPTER_NOT_ATTEMPTED
    target_script = json.loads(
        Path(
            project_paths.project_file(
                ProjectRepository.get_project_dir("book"), "structured_script"
            )
        ).read_text(encoding="utf-8")
    )
    ids = {segment["id"] for chapter in target_script["chapters"] for segment in chapter["segments"]}
    assert "c1-001" in ids
    assert "c2-001" not in ids


def test_rollback_failure_is_critical(assembly_workspace):
    assembly_workspace["make_book"]([("chapter-1", ("c1-001",))])
    session = _selected_book()
    plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(plan, {}, session=session)
    result = WholeBookAssemblyService.execute_assembly(
        plan,
        {},
        confirmation,
        session=session,
        fault_injection={"by_chapter": {"chapter-1": {"script_commit": True, "rollback": True}}},
    )
    assert result.status == ASSEMBLY_CRITICAL_FAILURE


def test_voice_resolution_is_per_chapter_and_changes_confirmation(assembly_workspace, tmp_path):
    assembly_workspace["make_book"]([("chapter-1", ("c1-001",))])
    other_voice = tmp_path / "other.wav"
    other_voice.write_bytes(b"other")
    assembly_workspace["bind"]("chapter-1", other_voice)
    session = _selected_book()
    blocked = WholeBookAssemblyService.plan_assembly("book", session=session)
    assert blocked.aggregate_status == ASSEMBLY_BLOCKED
    controls = prepare_assembly_execution_controls(blocked)
    assert controls[1]["interactive"] is True

    resolutions = {"chapters": {"chapter-1": {"voice_conflicts": {"旁白": "KEEP_TARGET"}}}}
    ready = WholeBookAssemblyService.plan_assembly(
        "book", resolutions=resolutions, session=session
    )
    assert ready.aggregate_status == ASSEMBLY_READY
    confirmation = WholeBookAssemblyService.prepare_confirmation(
        ready, resolutions, session=session
    )
    changed = {"chapters": {"chapter-1": {"voice_conflicts": {"旁白": "ADD_SOURCE_ROLE"}}}}
    result = WholeBookAssemblyService.execute_assembly(
        ready, changed, confirmation, session=session
    )
    assert result.status == "VALIDATION_FAILED"
    assert result.error_code == "STALE_ASSEMBLY_CONFIRMATION"


def test_active_opened_target_and_selection_contract(assembly_workspace):
    assembly_workspace["make_book"]([("chapter-1", ("c1-001",))])
    session = _selected_book()
    session.project = "book"
    blocked = WholeBookAssemblyService.plan_assembly("book", session=session)
    assert blocked.aggregate_status == ASSEMBLY_BLOCKED
    assert "TARGET_OPENED" in {item.code for item in blocked.blocking_conflicts}
    with pytest.raises(WholeBookAssemblyError, match="blocking"):
        WholeBookAssemblyService.prepare_confirmation(blocked, {}, session=session)

    session.project = "unrelated-opened-project"
    plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(plan, {}, session=session)
    result = WholeBookAssemblyService.execute_assembly(plan, {}, confirmation, session=session)
    assert result.status == ASSEMBLY_SUCCEEDED
    assert session.project == "unrelated-opened-project"
    assert session.selected_project == "book"


def test_a_to_b_to_a_selection_revision_invalidates_confirmation(assembly_workspace):
    assembly_workspace["make_book"]([("chapter-1", ("c1-001",))])
    assembly_workspace["create"]("book-b", ("b-001",))
    assembly_workspace["bind"]("book-b")
    session = _selected_book()
    plan = WholeBookAssemblyService.plan_assembly("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(plan, {}, session=session)
    session.set_selected("book-b")
    session.set_selected("book")
    result = WholeBookAssemblyService.execute_assembly(plan, {}, confirmation, session=session)
    assert result.status == "VALIDATION_FAILED"
    assert result.error_code == "STALE_ASSEMBLY_CONFIRMATION"


def test_ui_has_dedicated_assembly_state_without_catalog_arity_growth():
    with gr.Blocks():
        page = create_overview_page()
    assert {
        "assembly_target_book",
        "assembly_analyze",
        "assembly_dashboard",
        "assembly_plan_result",
        "assembly_plan_state",
        "assembly_resolution",
        "assembly_confirm",
        "assembly_confirmation_state",
        "assembly_execute",
        "assembly_execution_result",
        "assembly_transaction_state",
        "assembly_resume",
    } <= page.keys()
    assert len(assembly_workflow_outputs(page)) == 12
    assert len(refresh_assembly_workflow_controls(SessionState())) == 12
