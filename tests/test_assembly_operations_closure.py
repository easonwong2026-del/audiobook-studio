"""Layer 7 Whole-book Assembly operations, restart, and safety coverage."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import gradio as gr
import pytest
from gradio.state_holder import SessionState as GradioSessionState

from lib import project_paths
from lib.types import ProjectSummary
from repositories.project_repo import ProjectRepository
from services.project_catalog import RELATION_STANDALONE, CatalogHierarchy
from services.project_storage import ProjectStorageService
from services.session import SessionState
from services.whole_book_assembly import WholeBookAssemblyService
from services.whole_book_assembly_operations import (
    CHAPTER_CRITICAL_FAILURE,
    CHAPTER_FAILED_ROLLED_BACK_STATE,
    CHAPTER_OPERATIONAL_ALREADY_MERGED,
    CHAPTER_PENDING,
    CHAPTER_SOURCE_CHANGED,
    INTEGRITY_PASS,
    OPS_BLOCKED,
    OPS_COMPLETE,
    OPS_DEGRADED,
    OPS_FAILED,
    OPS_NOT_STARTED,
    OPS_PARTIAL,
    WholeBookAssemblyOperationsService,
)
from ui import project_catalog_handlers as catalog_handlers
from ui.chapter_merge_handlers import refresh_merge_workflow_controls
from ui.pages.overview_page import create_overview_page
from ui.whole_book_assembly_handlers import (
    refresh_assembly_workflow_controls,
    render_assembly_operations,
)
from ui.wiring.project_catalog_wiring import (
    bookshelf_selection_context_outputs,
    hierarchy_outputs,
)
from ui.wiring.whole_book_assembly_wiring import assembly_workflow_outputs


def _script(title: str, segment_id: str) -> dict:
    return {
        "meta": {"title": title, "author": "作者"},
        "voices": {"旁白": {"description": "旁白"}},
        "chapters": [
            {
                "id": f"{title}-chapter",
                "title": "第一章",
                "segments": [
                    {"id": segment_id, "role": "旁白", "text": f"{title}-{segment_id}"}
                ],
            }
        ],
    }


def _assert_dropdown_update_legal(update: dict) -> None:
    """Every returned Dropdown value must be empty or one of its choices."""
    choices = update.get("choices")
    if choices is None:
        return
    values = []
    for choice in choices:
        if isinstance(choice, dict):
            values.append(choice.get("value"))
        elif isinstance(choice, (tuple, list)) and len(choice) >= 2:
            values.append(choice[1])
        else:
            values.append(choice)
    value = update.get("value")
    assert value is None or value in values, (choices, value)


@pytest.fixture
def operations_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voice")

    def create(name: str, segment_id: str) -> None:
        ProjectRepository.create_project_from_data(name, _script(name, segment_id))
        binding_path = Path(
            project_paths.project_file(
                ProjectRepository.get_project_dir(name), "voice_bindings"
            )
        )
        value = json.loads(binding_path.read_text(encoding="utf-8"))
        value.setdefault("bindings", {})["旁白"] = str(voice)
        binding_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        segments = Path(
            project_paths.project_dir(
                ProjectRepository.get_project_dir(name), "segments", create=True
            )
        )
        (segments / f"{segment_id}.wav").write_bytes(b"wav")

    def meta(name: str) -> dict:
        return json.loads(
            Path(
                project_paths.project_file(
                    ProjectRepository.get_project_dir(name), "project_meta"
                )
            ).read_text(encoding="utf-8")
        )

    def update_meta(name: str, **updates) -> None:
        path = Path(
            project_paths.project_file(
                ProjectRepository.get_project_dir(name), "project_meta"
            )
        )
        value = meta(name)
        value.update(updates)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def make_book(names: tuple[str, ...] = ("chapter-1", "chapter-2")) -> None:
        create("book", "book-001")
        book_id = meta("book")["project_id"]
        for index, name in enumerate(names, start=1):
            create(name, f"{name}-001")
            update_meta(
                name,
                project_kind="chapter",
                parent_project_id=book_id,
                chapter_title=name.title(),
                chapter_order=index,
            )

    def selected(name: str = "book") -> SessionState:
        session = SessionState()
        session.set_selected(name)
        return session

    return {
        "root": data_root,
        "create": create,
        "meta": meta,
        "update_meta": update_meta,
        "make_book": make_book,
        "selected": selected,
    }


def _run_confirmed(session: SessionState):
    analysis = WholeBookAssemblyOperationsService.analyze("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(
        analysis.plan, {}, session=session
    )
    return WholeBookAssemblyOperationsService.execute_confirmed(
        analysis.plan, {}, confirmation, session=session
    )


def test_status_reconstructs_not_started_and_is_read_only(operations_workspace):
    operations_workspace["make_book"]()
    session = operations_workspace["selected"]()
    before = {
        path: path.read_bytes()
        for path in Path(ProjectRepository.get_project_dir("book")).rglob("*")
        if path.is_file()
    }

    snapshot = WholeBookAssemblyOperationsService.reconstruct("book", session=session)

    after = {
        path: path.read_bytes()
        for path in Path(ProjectRepository.get_project_dir("book")).rglob("*")
        if path.is_file()
    }
    assert snapshot.overall_status == OPS_NOT_STARTED
    assert snapshot.integrity_status == INTEGRITY_PASS
    assert snapshot.pending_count == 2
    assert snapshot.resume_allowed is True
    assert all(item.status == CHAPTER_PENDING for item in snapshot.chapter_states)
    assert before == after


def test_partial_complete_and_new_membership_invalidate_completion(operations_workspace):
    operations_workspace["make_book"]()
    session = operations_workspace["selected"]()
    first_plan = WholeBookAssemblyOperationsService.analyze("book", session=session)
    first_confirmation = WholeBookAssemblyService.prepare_confirmation(
        first_plan.plan, {}, session=session
    )
    # Execute only the first Chapter through the existing executor contract.
    first = WholeBookAssemblyService.execute_assembly(
        first_plan.plan,
        {},
        first_confirmation,
        session=session,
        fault_injection={"by_chapter": {"chapter-2": {"script_commit": True}}},
    )
    assert first.status == "PARTIAL_SUCCESS_STOPPED"
    partial = WholeBookAssemblyOperationsService.reconstruct("book", session=session)
    assert partial.overall_status == OPS_PARTIAL
    assert partial.merged_count == 1
    assert partial.failed_count == 1

    # The second Chapter can finish the current set; no old plan is reused.
    outcome = _run_confirmed(session)
    assert outcome.snapshot.overall_status == OPS_COMPLETE
    assert outcome.snapshot.merged_count == 2
    assert outcome.snapshot.resume_allowed is False

    operations_workspace["create"]("chapter-3", "chapter-3-001")
    operations_workspace["update_meta"](
        "chapter-3",
        project_kind="chapter",
        parent_project_id=operations_workspace["meta"]("book")["project_id"],
        chapter_title="Chapter 3",
        chapter_order=3,
    )
    changed_membership = WholeBookAssemblyOperationsService.reconstruct(
        "book", session=session
    )
    assert changed_membership.overall_status == OPS_PARTIAL
    assert changed_membership.total_chapters == 3
    assert changed_membership.pending_count == 1


def test_restart_reconstructs_already_merged_pending_and_history(operations_workspace):
    operations_workspace["make_book"]()
    session = operations_workspace["selected"]()
    outcome = _run_confirmed(session)
    assert outcome.execution_result.status == "SUCCEEDED"

    restarted_session = operations_workspace["selected"]()
    snapshot = WholeBookAssemblyOperationsService.reconstruct(
        "book", session=restarted_session
    )
    assert snapshot.overall_status == OPS_COMPLETE
    assert [item.status for item in snapshot.chapter_states] == [
        CHAPTER_OPERATIONAL_ALREADY_MERGED,
        CHAPTER_OPERATIONAL_ALREADY_MERGED,
    ]
    assert all(item.last_transaction_id for item in snapshot.chapter_states)
    assert all(item.latest_backup_reference for item in snapshot.chapter_states)
    assert snapshot.latest_run["status"] == "SUCCEEDED"
    assert snapshot.latest_run["transaction_ids"]
    assert "confirmation_token" not in snapshot.latest_run
    assert "assembly_token" not in snapshot.latest_run


def test_source_change_blocks_current_complete_state(operations_workspace):
    operations_workspace["make_book"](("chapter-1",))
    session = operations_workspace["selected"]()
    _run_confirmed(session)
    script_path = Path(
        project_paths.project_file(
            ProjectRepository.get_project_dir("chapter-1"), "structured_script"
        )
    )
    value = json.loads(script_path.read_text(encoding="utf-8"))
    value["chapters"][0]["segments"][0]["text"] = "changed after merge"
    script_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    snapshot = WholeBookAssemblyOperationsService.reconstruct("book", session=session)
    assert snapshot.overall_status == OPS_BLOCKED
    assert snapshot.chapter_states[0].status == CHAPTER_SOURCE_CHANGED
    assert snapshot.resume_allowed is False
    assert snapshot.latest_run["status"] == "SUCCEEDED"


def test_integrity_failure_is_degraded_and_never_complete(
    operations_workspace, monkeypatch
):
    operations_workspace["make_book"](("chapter-1",))
    session = operations_workspace["selected"]()
    monkeypatch.setattr(
        ProjectStorageService,
        "check_integrity",
        staticmethod(
            lambda _name: {
                "ok": False,
                "issues": [{"severity": "error", "code": "synthetic"}],
            }
        ),
    )
    snapshot = WholeBookAssemblyOperationsService.reconstruct("book", session=session)
    assert snapshot.overall_status == OPS_DEGRADED
    assert snapshot.integrity_status == "FAIL"
    assert snapshot.resume_allowed is False


def test_failed_rolled_back_is_retryable_and_rollback_failure_is_degraded(
    operations_workspace,
):
    operations_workspace["make_book"](("chapter-1",))
    session = operations_workspace["selected"]()
    analysis = WholeBookAssemblyOperationsService.analyze("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(
        analysis.plan, {}, session=session
    )
    rolled_back = WholeBookAssemblyOperationsService.execute_confirmed(
        analysis.plan,
        {},
        confirmation,
        session=session,
        fault_injection={"by_chapter": {"chapter-1": {"script_commit": True}}},
    )
    assert rolled_back.execution_result.status == "VALIDATION_FAILED"
    retryable = WholeBookAssemblyOperationsService.reconstruct("book", session=session)
    assert retryable.overall_status == OPS_FAILED
    assert retryable.chapter_states[0].status == CHAPTER_FAILED_ROLLED_BACK_STATE
    assert retryable.resume_allowed is True
    assert retryable.chapter_states[0].last_transaction_id

    analysis = WholeBookAssemblyOperationsService.analyze("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(
        analysis.plan, {}, session=session
    )
    critical = WholeBookAssemblyOperationsService.execute_confirmed(
        analysis.plan,
        {},
        confirmation,
        session=session,
        fault_injection={
            "by_chapter": {"chapter-1": {"script_commit": True, "rollback": True}}
        },
    )
    assert critical.execution_result.status == "CRITICAL_FAILURE"
    degraded = WholeBookAssemblyOperationsService.reconstruct("book", session=session)
    assert degraded.overall_status == OPS_DEGRADED
    assert degraded.chapter_states[0].status == CHAPTER_CRITICAL_FAILURE
    assert degraded.resume_allowed is False


def test_interrupted_journal_is_detected_and_blocks_resume(operations_workspace):
    operations_workspace["make_book"](("chapter-1",))
    session = operations_workspace["selected"]()
    target_id = operations_workspace["meta"]("book")["project_id"]
    journal_root = operations_workspace["root"] / "runtime" / "merge_transactions"
    journal_root.mkdir(parents=True)
    journal_path = journal_root / "merge-interrupted.json"
    journal_path.write_text(
        json.dumps(
            {
                "schema_version": "chapter-merge-transaction-v1",
                "transaction_id": "merge-interrupted",
                "source_project_id": operations_workspace["meta"]("chapter-1")[
                    "project_id"
                ],
                "target_project_id": target_id,
                "stage": "COMMITTING",
                "started_at": "2026-08-21T00:00:00Z",
                "updated_at": "2026-08-21T00:01:00Z",
                "rollback_status": "NOT_STARTED",
            }
        ),
        encoding="utf-8",
    )
    snapshot = WholeBookAssemblyOperationsService.reconstruct("book", session=session)
    assert snapshot.overall_status == OPS_DEGRADED
    assert snapshot.active_transactions[0].transaction_id == "merge-interrupted"
    assert snapshot.resume_allowed is False
    assert snapshot.chapter_states[0].status == CHAPTER_CRITICAL_FAILURE


def test_historical_unbound_content_is_visible_without_target_cleanup(operations_workspace):
    operations_workspace["make_book"](("chapter-1",))
    session = operations_workspace["selected"]()
    _run_confirmed(session)
    before = {
        path.relative_to(ProjectRepository.get_project_dir("book"))
        for path in Path(ProjectRepository.get_project_dir("book")).rglob("*")
        if path.is_file()
    }
    operations_workspace["update_meta"]("chapter-1", parent_project_id=None)

    snapshot = WholeBookAssemblyOperationsService.reconstruct("book", session=session)
    after = {
        path.relative_to(ProjectRepository.get_project_dir("book"))
        for path in Path(ProjectRepository.get_project_dir("book")).rglob("*")
        if path.is_file()
    }
    assert snapshot.historical_merges[0]["status_code"] == (
        "HISTORICAL_MERGE_NOT_CURRENT_CHILD"
    )
    assert snapshot.overall_status == OPS_NOT_STARTED
    assert before <= after


def test_resume_freshly_skips_merged_chapters_after_restart(operations_workspace):
    operations_workspace["make_book"](("chapter-1", "chapter-2", "chapter-3"))
    session = operations_workspace["selected"]()
    analysis = WholeBookAssemblyOperationsService.analyze("book", session=session)
    confirmation = WholeBookAssemblyService.prepare_confirmation(
        analysis.plan, {}, session=session
    )
    first = WholeBookAssemblyService.execute_assembly(
        analysis.plan,
        {},
        confirmation,
        session=session,
        fault_injection={"by_chapter": {"chapter-3": {"script_commit": True}}},
    )
    assert first.status == "PARTIAL_SUCCESS_STOPPED"

    restarted = operations_workspace["selected"]()
    resumed = WholeBookAssemblyOperationsService.resume(
        "book", {}, confirmed=True, session=restarted
    )
    assert resumed.execution_result.status == "SUCCEEDED"
    assert resumed.execution_result.already_merged == 2
    assert resumed.execution_result.merged_this_run == 1
    assert resumed.snapshot.overall_status == OPS_COMPLETE


def test_operations_ui_dashboard_and_button_contract():
    with gr.Blocks():
        page = create_overview_page()
    assert {"assembly_dashboard", "assembly_resume"} <= page.keys()
    assert len(assembly_workflow_outputs(page)) == 12
    controls = refresh_assembly_workflow_controls(SessionState())
    assert len(controls) == 12
    assert controls[1]["interactive"] is False
    assert controls[-1]["interactive"] is False


def test_eligible_book_target_update_keeps_value_inside_choices(operations_workspace):
    operations_workspace["make_book"](("chapter-1",))
    controls = refresh_assembly_workflow_controls(operations_workspace["selected"]())

    _assert_dropdown_update_legal(controls[0])
    assert controls[0]["choices"] == ["book"]
    assert controls[0]["value"] == "book"
    assert controls[1]["interactive"] is True


def test_chapter_selection_clears_assembly_target_value(operations_workspace):
    operations_workspace["make_book"](("chapter-1",))
    controls = refresh_assembly_workflow_controls(
        operations_workspace["selected"]("chapter-1")
    )

    _assert_dropdown_update_legal(controls[0])
    assert controls[0]["value"] is None
    assert controls[1]["interactive"] is False


def test_selected_assembly_book_missing_from_catalog_clears_target(monkeypatch):
    eligible = ProjectSummary(
        project_name="stale-book",
        title="Stale Book",
        author="作者",
        chapters=1,
        segments=1,
        completed=0,
        modified_at=None,
        project_id="stable-id",
        project_kind="book",
        relation_status=RELATION_STANDALONE,
    )
    empty_hierarchy = CatalogHierarchy(
        projects=(),
        books=(),
        chapters=(),
        orphan_chapters=(),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.ProjectCatalogService.scan_hierarchy",
        staticmethod(lambda: empty_hierarchy),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.ProjectCatalogService.get_summary",
        staticmethod(lambda _name: eligible),
    )

    session = SessionState()
    session.set_selected("stale-book")
    controls = refresh_assembly_workflow_controls(session)

    _assert_dropdown_update_legal(controls[0])
    assert controls[0]["choices"] == []
    assert controls[0]["value"] is None
    assert controls[1]["interactive"] is False


def test_legacy_standalone_book_clears_ineligible_target_update(monkeypatch):
    """A legacy Book stays manageable but is not an Assembly target."""
    legacy = ProjectSummary(
        project_name="legacy-book",
        title="Legacy Book",
        author="作者",
        chapters=1,
        segments=1,
        completed=0,
        modified_at=None,
        project_id=None,
        project_kind="book",
        relation_status=RELATION_STANDALONE,
    )
    hierarchy = CatalogHierarchy(
        projects=(legacy,),
        books=(legacy,),
        chapters=(),
        orphan_chapters=(),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.ProjectCatalogService.scan_hierarchy",
        staticmethod(lambda: hierarchy),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.ProjectCatalogService.scan",
        staticmethod(lambda: [legacy]),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.ProjectCatalogService.get_summary",
        staticmethod(lambda _name: legacy),
    )

    session = SessionState()
    session.set_selected("legacy-book")
    controls = refresh_assembly_workflow_controls(session)
    target_update = controls[0]

    assert target_update["choices"] == []
    assert target_update["value"] is None
    assert controls[1]["interactive"] is False
    assert "尚不具备整书装配资格" in controls[2]

    management = catalog_handlers.reconcile_bookshelf_selection_context(session)
    assert all(update.get("interactive") is True for update in management[:9])


def test_real_gradio_selection_to_assembly_keeps_legacy_target_legal(monkeypatch):
    """The production-shaped downstream boundary accepts the safe empty value."""
    legacy = ProjectSummary(
        project_name="legacy-book",
        title="Legacy Book",
        author="作者",
        chapters=1,
        segments=1,
        completed=0,
        modified_at=None,
        project_id=None,
        project_kind="book",
        relation_status=RELATION_STANDALONE,
    )
    hierarchy = CatalogHierarchy(
        projects=(legacy,),
        books=(legacy,),
        chapters=(),
        orphan_chapters=(),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.ProjectCatalogService.scan_hierarchy",
        staticmethod(lambda: hierarchy),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.ProjectCatalogService.scan",
        staticmethod(lambda: [legacy]),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.ProjectCatalogService.get_summary",
        staticmethod(lambda _name: legacy),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.WholeBookAssemblyOperationsService.reconstruct",
        staticmethod(lambda *_args, **_kwargs: SimpleNamespace(resume_allowed=False)),
    )
    monkeypatch.setattr(
        "ui.whole_book_assembly_handlers.render_assembly_operations",
        lambda _snapshot: "legacy assembly dashboard",
    )

    def select_legacy(session):
        session.set_selected("legacy-book")
        return session

    def run(block, fn_index, inputs, state, *, event_data=None):
        return asyncio.run(
            block.process_api(
                fn_index,
                inputs,
                state=state,
                session_hash="assembly-test",
                event_data=event_data,
            )
        )

    with gr.Blocks() as block:
        page = create_overview_page()
        session = gr.State(SessionState())
        select = gr.Button("select")
        observed = gr.Textbox()
        merge_outputs = [
            page["merge_source_chapter"],
            page["merge_target_book"],
            page["merge_analyze"],
            page["merge_plan_result"],
            page["merge_plan_state"],
            page["merge_resolution"],
            page["merge_confirm"],
            page["merge_confirmation_state"],
            page["merge_execute"],
            page["merge_execution_result"],
            page["merge_transaction_state"],
        ]
        select.click(select_legacy, [session], [session]).then(
            catalog_handlers.reconcile_bookshelf_selection_context,
            [session],
            bookshelf_selection_context_outputs(page),
        ).then(
            catalog_handlers.reconcile_bookshelf_hierarchy_selection,
            [session],
            hierarchy_outputs(page),
        ).then(
            refresh_merge_workflow_controls,
            [session],
            merge_outputs,
        ).then(
            refresh_assembly_workflow_controls,
            [session],
            assembly_workflow_outputs(page),
        )
        page["assembly_target_book"].change(
            lambda value: value,
            page["assembly_target_book"],
            observed,
        )

    state = GradioSessionState(block)
    run(block, 0, [None], state)
    context_result = run(block, 1, [None], state)
    assert all(item["interactive"] is True for item in context_result["data"][:9])

    hierarchy_result = run(block, 2, [None], state)
    _assert_dropdown_update_legal(hierarchy_result["data"][0])
    assert hierarchy_result["data"][0]["value"] is None

    merge_result = run(block, 3, [None], state)
    _assert_dropdown_update_legal(merge_result["data"][0])
    _assert_dropdown_update_legal(merge_result["data"][1])

    assembly_result = run(block, 4, [None], state)
    _assert_dropdown_update_legal(assembly_result["data"][0])
    assert assembly_result["data"][0]["choices"] == []
    assert assembly_result["data"][0]["value"] is None

    observed = run(block, 5, [None], state)
    assert observed["data"] == [None]


def test_dashboard_render_exposes_operational_fields(operations_workspace):
    operations_workspace["make_book"](("chapter-1",))
    snapshot = WholeBookAssemblyOperationsService.reconstruct(
        "book", session=operations_workspace["selected"]()
    )
    rendered = render_assembly_operations(snapshot)
    assert "总体状态" in rendered
    assert "完整性" in rendered
    assert "Chapter-1" in rendered
