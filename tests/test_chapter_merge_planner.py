"""Phase C.1 Chapter → Book read-only merge planner coverage."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import gradio as gr
import pytest

from lib import project_paths
from repositories.project_repo import ProjectRepository
from services.chapter_merge_planner import (
    BLOCKED,
    COMPLETE_AUDIO,
    NO_AUDIO,
    NO_COLLISION,
    PARTIAL_AUDIO,
    PLANNING_ALLOWED,
    UNRESOLVABLE_COLLISION,
    ChapterMergePlanner,
)
from services.session import SessionState
from ui.chapter_merge_handlers import analyze_merge_plan, refresh_merge_planner_controls
from ui.pages.overview_page import create_overview_page
from ui.wiring.project_catalog_wiring import bookshelf_management_outputs


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


@pytest.fixture
def planner_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True

    shared_voice = tmp_path / "shared-voice.wav"
    shared_voice.write_bytes(b"shared voice")

    def create(name: str, segment_ids=("1-001",), roles=("旁白",)) -> str:
        return ProjectRepository.create_project_from_data(
            name, _script(name, tuple(segment_ids), roles)
        )

    def read_meta(name: str) -> dict:
        path = Path(
            project_paths.project_file(ProjectRepository.get_project_dir(name), "project_meta")
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def write_meta(name: str, **updates) -> None:
        path = Path(
            project_paths.project_file(ProjectRepository.get_project_dir(name), "project_meta")
        )
        value = read_meta(name)
        value.update(updates)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def bind_shared_voice(name: str, path: Path | None = None) -> None:
        project_dir = ProjectRepository.get_project_dir(name)
        binding_path = Path(project_paths.project_file(project_dir, "voice_bindings"))
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

    def set_pair(
        source_ids=("c-001",),
        target_ids=("b-001",),
        *,
        source_order=2,
        source_roles=("旁白",),
        target_roles=("旁白",),
    ) -> None:
        create("book", target_ids, target_roles)
        create("chapter", source_ids, source_roles)
        book_id = read_meta("book")["project_id"]
        write_meta(
            "chapter",
            project_kind="chapter",
            parent_project_id=book_id,
            chapter_title="规划章节",
            chapter_order=source_order,
        )
        bind_shared_voice("book")
        bind_shared_voice("chapter")

    return {
        "root": data_root,
        "create": create,
        "read_meta": read_meta,
        "write_meta": write_meta,
        "bind_shared_voice": bind_shared_voice,
        "add_audio": add_audio,
        "set_pair": set_pair,
        "shared_voice": shared_voice,
    }


def _conflict_codes(plan) -> set[str]:
    return {item.code for item in plan.conflicts}


def _tree_snapshot(name: str) -> dict[str, tuple[int, int, str]]:
    root = Path(ProjectRepository.get_project_dir(name))
    result: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        stat = path.stat()
        result[str(path.relative_to(root))] = (
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return result


def test_p1_healthy_pair_generates_a_deterministic_plan(planner_workspace):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")

    plan = ChapterMergePlanner.plan_merge("chapter", "book")

    assert plan.planning_status == PLANNING_ALLOWED
    assert plan.source_project.project_kind == "chapter"
    assert plan.target_project.project_kind == "book"
    assert plan.source_inventory.segment_ids == ("c-001",)
    assert plan.segment_remap.policy == NO_COLLISION
    assert plan.source_inventory.audio["coverage"] == COMPLETE_AUDIO
    assert plan.plan_token
    assert not hasattr(ChapterMergePlanner, "execute_merge")


@pytest.mark.parametrize(
    ("source", "target", "code"),
    [
        ("book", "book2", "SOURCE_NOT_CHAPTER"),
        ("chapter", "chapter2", "TARGET_NOT_BOOK"),
        ("chapter", "chapter", "SOURCE_TARGET_SAME"),
        ("missing", "book", "SOURCE_NOT_FOUND"),
        ("chapter", "missing", "TARGET_NOT_FOUND"),
    ],
)
def test_p2_to_p6_invalid_refs_are_structured_and_blocked(
    planner_workspace, source, target, code
):
    planner_workspace["set_pair"]()
    planner_workspace["create"]("book2", ("b2-001",))
    planner_workspace["create"]("chapter2", ("c2-001",))
    if target == "chapter2":
        planner_workspace["write_meta"]("chapter2", project_kind="chapter")
    plan = ChapterMergePlanner.plan_merge(source, target)
    assert plan.execution_eligibility == BLOCKED
    assert code in _conflict_codes(plan)
    assert plan.planning_status == "PLANNING_BLOCKED"


def test_p7_duplicate_project_identity_is_never_guessed(planner_workspace):
    planner_workspace["set_pair"]()
    planner_workspace["write_meta"](
        "chapter", project_id=planner_workspace["read_meta"]("book")["project_id"]
    )
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    assert plan.execution_eligibility == BLOCKED
    assert "DUPLICATE_PROJECT_ID" in _conflict_codes(plan)


def test_p8_p9_plan_and_token_are_stable_without_changes(planner_workspace):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")
    first = ChapterMergePlanner.plan_merge("chapter", "book")
    second = ChapterMergePlanner.plan_merge("chapter", "book")
    assert first.plan_token == second.plan_token
    assert first.as_dict() == second.as_dict()


def test_p10_script_change_invalidates_the_token(planner_workspace):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")
    before = ChapterMergePlanner.plan_merge("chapter", "book")
    script_path = Path(
        project_paths.project_file(
            ProjectRepository.get_project_dir("chapter"), "structured_script"
        )
    )
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["chapters"][0]["segments"][0]["text"] = "changed"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    after = ChapterMergePlanner.plan_merge("chapter", "book")
    assert before.plan_token != after.plan_token
    assert not ChapterMergePlanner.is_plan_current(before)


def test_s2_collision_is_explicitly_unresolvable_without_a_remap_contract(
    planner_workspace,
):
    planner_workspace["set_pair"](
        source_ids=("same-001",), target_ids=("same-001",)
    )
    planner_workspace["add_audio"]("chapter", "same-001")
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    assert plan.segment_remap.policy == UNRESOLVABLE_COLLISION
    assert plan.segment_remap.collisions == ("same-001",)
    assert "SEGMENT_ID_COLLISION" in _conflict_codes(plan)
    assert plan.execution_eligibility == BLOCKED


def test_s5_order_and_s6_partial_audio_are_reported(planner_workspace):
    planner_workspace["set_pair"](
        source_ids=("c-001", "c-002"), target_ids=("b-001",)
    )
    planner_workspace["add_audio"]("chapter", "c-001")
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    assert [item["segment_id"] for item in plan.source_inventory.ordered_segments] == [
        "c-001",
        "c-002",
    ]
    assert plan.source_inventory.audio["coverage"] == PARTIAL_AUDIO
    assert "c-002" in plan.source_inventory.audio["missing_segment_ids"]


def test_s6_no_audio_has_a_distinct_blocking_policy(planner_workspace):
    planner_workspace["set_pair"]()
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    assert plan.source_inventory.audio["coverage"] == NO_AUDIO
    assert "SOURCE_AUDIO_MISSING" in _conflict_codes(plan)
    assert plan.execution_eligibility == BLOCKED


def test_s7_unexpected_wav_is_not_counted_as_a_segment(planner_workspace):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")
    planner_workspace["add_audio"]("chapter", "manual-orphan")
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    unexpected = plan.source_inventory.audio["unexpected_files"]
    assert any(item["path"].endswith("manual-orphan.wav") for item in unexpected)
    assert plan.source_inventory.audio["present_count"] == 1


def test_v1_and_v4_same_or_different_voice_are_classified(planner_workspace, tmp_path):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")
    compatible = ChapterMergePlanner.plan_merge("chapter", "book")
    assert compatible.voice_compatibility.roles[0]["status"] == "COMPATIBLE"

    other_voice = tmp_path / "other-voice.wav"
    other_voice.write_bytes(b"different voice")
    planner_workspace["bind_shared_voice"]("book", other_voice)
    conflict = ChapterMergePlanner.plan_merge("chapter", "book")
    assert "VOICE_BINDING_CONFLICT" in _conflict_codes(conflict)
    assert conflict.execution_eligibility == BLOCKED


def test_v2_source_only_and_target_only_roles_are_visible(planner_workspace):
    planner_workspace["set_pair"](
        source_roles=("旁白", "角色A"), target_roles=("旁白", "角色B")
    )
    planner_workspace["add_audio"]("chapter", "c-001")
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    statuses = {item["display_name"]: item["status"] for item in plan.voice_compatibility.roles}
    assert statuses["角色A"] == "SOURCE_ONLY"
    assert statuses["角色B"] == "TARGET_ONLY"


def test_q1_r1_qa_and_revision_records_are_audited_not_merged(planner_workspace):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")
    quality_path = Path(
        project_paths.project_file(
            ProjectRepository.get_project_dir("chapter"), "quality_state", create=True
        )
    )
    quality_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revisions": {
                    "rev-1": {
                        "revision_id": "rev-1",
                        "segment_id": "c-001",
                        "relative_path": "02_生成音频/分段音频/c-001.wav",
                        "params": {},
                    }
                },
                "active_revisions": {"c-001": "rev-1"},
                "technical_qa": {"rev-1": {"outcome": "pass"}},
                "human_reviews": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    assert plan.qa_inventory["record_count"] == 1
    assert plan.revision_inventory["record_count"] == 1
    assert plan.qa_inventory["transfer_policy"] == "EXCLUDED_FROM_EXECUTION_PLAN"
    assert "UNSUPPORTED_REVISION_MAPPING" in _conflict_codes(plan)


def test_t1_active_task_is_non_transferable_and_blocks_execution(planner_workspace):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")
    task_path = project_paths.project_file(
        ProjectRepository.get_project_dir("chapter"), "task_db", create=True
    )
    connection = sqlite3.connect(task_path)
    connection.execute(
        "CREATE TABLE production_tasks ("
        "task_id TEXT, task_type TEXT, project TEXT, status TEXT, "
        "scope_json TEXT, options_json TEXT, created_at TEXT, updated_at TEXT)"
    )
    connection.execute(
        "INSERT INTO production_tasks VALUES (?,?,?,?,?,?,?,?)",
        ("task-1", "synthesis", "chapter", "running", "{}", "{}", "", ""),
    )
    connection.commit()
    connection.close()
    plan = ChapterMergePlanner.plan_merge("chapter", "book")
    assert plan.task_state["source"]["active_count"] == 1
    assert "ACTIVE_TASK" in _conflict_codes(plan)
    assert plan.execution_eligibility == BLOCKED


def test_a1_a4_opened_session_blocks_future_execution_without_switching_state(
    planner_workspace,
):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")
    ss = SessionState(project="book")
    ss.set_selected("chapter")
    plan = ChapterMergePlanner.plan_merge("chapter", "book", session=ss)
    assert ss.project == "book"
    assert ss.selected_project == "chapter"
    assert "TARGET_OPENED" in _conflict_codes(plan)
    text, returned = analyze_merge_plan("chapter", "book", ss)
    assert returned is not None
    assert "Plan token" in text
    assert ss.project == "book"
    assert ss.selected_project == "chapter"


def test_ro1_to_ro7_planning_does_not_mutate_either_project(planner_workspace):
    planner_workspace["set_pair"]()
    planner_workspace["add_audio"]("chapter", "c-001")
    before_source = _tree_snapshot("chapter")
    before_target = _tree_snapshot("book")
    ss = SessionState(project="other-opened")
    ss.set_selected("chapter")
    ChapterMergePlanner.plan_merge("chapter", "book", session=ss)
    assert _tree_snapshot("chapter") == before_source
    assert _tree_snapshot("book") == before_target
    assert ss.project == "other-opened"
    assert ss.selected_project == "chapter"


def test_u1_u3_u4_u9_dedicated_ui_state_has_no_catalog_arity_growth(
    planner_workspace,
):
    planner_workspace["set_pair"]()
    with gr.Blocks():
        page = create_overview_page()
    assert {
        "merge_source_chapter",
        "merge_target_book",
        "merge_analyze",
        "merge_plan_result",
        "merge_plan_state",
    } <= page.keys()
    assert len(bookshelf_management_outputs(page, page["merge_plan_state"], include_hierarchy=True)) == 33

    empty = refresh_merge_planner_controls(SessionState())
    assert len(empty) == 5
    ss = SessionState()
    ss.set_selected("chapter")
    populated = refresh_merge_planner_controls(ss)
    assert populated[0]["value"] == "chapter"
    assert populated[1]["value"] == "book"
    assert populated[2]["interactive"] is True
