"""Real Gradio 5.50 boundary tests for bookshelf state ownership/lifecycle."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import gradio as gr
import pytest
from gradio.state_holder import SessionState as GradioSessionState

from repositories.project_repo import ProjectRepository
from services import ProjectService
from services.session import SessionState
from ui import project_catalog_handlers as handlers
from ui.pages import project_page
from ui.pages.overview_page import create_overview_page
from ui.wiring.project_catalog_wiring import bookshelf_selection_context_outputs

ROOT = Path(__file__).parents[1]
APP_PATH = ROOT / "app.py"
WIRING_PATH = ROOT / "ui" / "wiring" / "project_catalog_wiring.py"


def _script_file(tmp_path: Path, title: str) -> Path:
    path = tmp_path / f"{title}.json"
    path.write_text(
        json.dumps(
            {
                "meta": {"title": title, "author": "作者"},
                "voices": {"旁白": {"description": "x"}},
                "chapters": [
                    {
                        "id": 1,
                        "title": "第一章",
                        "segments": [
                            {"id": "1-001", "role": "旁白", "text": "A"}
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def lifecycle_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project("alpha", str(_script_file(tmp_path, "阿尔法")))
    ProjectRepository.create_project("beta", str(_script_file(tmp_path, "贝塔")))
    return data_root


def _run(
    block,
    fn_index: int,
    inputs: list,
    state: GradioSessionState,
    *,
    event_data=None,
):
    return asyncio.run(
        block.process_api(
            fn_index,
            inputs,
            state=state,
            session_hash="test",
            event_data=event_data,
        )
    )


def test_real_gradio_dropdown_atomic_update_reaches_next_input_boundary():
    """Gradio 5.50 accepts choices+value atomically when the update is applied."""
    with gr.Blocks() as block:
        dropdown = gr.Dropdown(choices=[])
        set_dropdown = gr.Button("set")
        observed = gr.Textbox()
        set_dropdown.click(
            lambda: gr.update(choices=["A", "B"], value="A"),
            None,
            dropdown,
        )
        dropdown.change(lambda value: value, dropdown, observed)

    state = GradioSessionState(block)
    first = _run(block, 0, [], state)
    update = first["data"][0]
    assert update["choices"] == [["A", "A"], ["B", "B"]]
    assert update["value"] == "A"

    # process_api has applied the same component update that the browser
    # receives. The next Dropdown input is therefore validated against A/B.
    second = _run(block, 1, ["A"], state)
    assert second["data"] == ["A"]


def test_real_gradio_dropdown_value_only_update_keeps_old_empty_choices():
    """The actual 5.50 input boundary rejects a value-only update."""
    with gr.Blocks() as block:
        dropdown = gr.Dropdown(choices=[])
        set_dropdown = gr.Button("set")
        observed = gr.Textbox()
        set_dropdown.click(lambda: gr.update(value="A"), None, dropdown)
        dropdown.change(lambda value: value, dropdown, observed)

    state = GradioSessionState(block)
    first = _run(block, 0, [], state)
    assert first["data"][0].get("choices") is None
    assert first["data"][0]["value"] == "A"

    with pytest.raises(gr.Error, match="not in the list of choices"):
        _run(block, 1, ["A"], state)


def test_real_gradio_state_update_value_does_not_write_state():
    """gr.update(value=...) is a component update, not a gr.State write."""
    with gr.Blocks() as block:
        state_value = gr.State("")
        set_value = gr.Button("set")
        set_value.click(lambda: gr.update(value="A"), None, state_value)

    state = GradioSessionState(block)
    _run(block, 0, [], state)
    assert state[state_value._id] == ""


def test_real_gradio_state_change_reports_only_real_value_changes():
    """The 5.50 backend exposes same-value vs changed State to the frontend."""
    with gr.Blocks() as block:
        state_value = gr.State(0)
        same = gr.Button("same")
        increment = gr.Button("increment")
        observed = gr.Number()
        same.click(lambda value: value, state_value, state_value)
        increment.click(lambda value: value + 1, state_value, state_value)
        state_value.change(lambda: 1, None, observed)

    state = GradioSessionState(block)
    same_result = _run(block, 0, [0], state)
    increment_result = _run(block, 1, [0], state)

    assert same_result["changed_state_ids"] == []
    assert increment_result["changed_state_ids"] == [state_value._id]


def test_real_gradio_archive_clicks_confirm_then_archive(lifecycle_workspace):
    """The wired handler must persist confirmation through actual State IO."""
    with gr.Blocks() as block:
        selected = gr.State("alpha")
        confirmation = gr.State("")
        session = gr.State(SessionState())
        archive_event = gr.State(0)
        message = gr.Markdown()
        selected_info = gr.Markdown()
        refresh_status = gr.Markdown()
        archive = gr.Button("archive")
        archive.click(
            handlers.archive_selected_with_event,
            [selected, confirmation, session, archive_event],
            [message, confirmation, selected, selected_info, archive_event],
        )
        archive_event.change(lambda: "archive refresh", None, refresh_status)

    state = GradioSessionState(block)
    first = _run(block, 0, ["alpha", "", None, 0], state)
    assert "确认将" in first["data"][0]
    assert state[confirmation._id] == "alpha"
    assert archive_event._id not in first["changed_state_ids"]
    assert (lifecycle_workspace / "projects" / "alpha").is_dir()

    second = _run(block, 0, ["alpha", "", None, 0], state)
    assert "已移入回收站" in second["data"][0]
    assert archive_event._id in second["changed_state_ids"]
    assert not (lifecycle_workspace / "projects" / "alpha").is_dir()


def test_real_gradio_bookshelf_click_a_then_b_never_updates_p_sel(
    lifecycle_workspace,
):
    """The actual Dataframe.select chain has no p_sel output or input."""
    rows = {
        "headers": ["项目", "结构", "段进度", "状态", "最近修改"],
        "data": [
            ["alpha", "整书 · 关联 0 个章节项目", "0/1", "⚪未开始", "—"],
            ["beta", "整书 · 关联 0 个章节项目", "0/1", "⚪未开始", "—"],
        ],
    }
    with gr.Blocks() as block:
        page = create_overview_page()
        session = gr.State(SessionState())
        p_sel = gr.Dropdown(choices=[])
        page["ov_bookshelf"].select(
            handlers.select_bookshelf_row,
            [page["ov_bookshelf"], session],
            [page["bookshelf_selected_proj"], page["bookshelf_selected"]],
        ).then(
            handlers.reconcile_bookshelf_selection_context,
            [session],
            bookshelf_selection_context_outputs(page),
        )

    state = GradioSessionState(block)
    event_a = gr.SelectData(
        page["ov_bookshelf"],
        {
            "index": [0, 0],
            "value": "alpha",
            "selected": True,
            "row_value": rows["data"][0],
        },
    )
    first = _run(
        block,
        0,
        [rows, None],
        state,
        event_data=event_a,
    )
    assert len(first["data"]) == 2
    assert p_sel.choices == []
    assert p_sel.value is None
    reconciled_a = _run(block, 1, [None], state)
    assert len(reconciled_a["data"]) == 18
    assert p_sel.choices == []
    assert p_sel.value is None
    assert state[session._id].selected_project == "alpha"

    event_b = gr.SelectData(
        page["ov_bookshelf"],
        {
            "index": [1, 2],
            "value": "0/1",
            "selected": True,
            "row_value": rows["data"][1],
        },
    )
    _run(block, 0, [rows, None], state, event_data=event_b)
    reconciled_b = _run(block, 1, [None], state)
    assert len(reconciled_b["data"]) == 18
    assert p_sel.choices == []
    assert p_sel.value is None
    assert state[session._id].selected_project == "beta"


def test_bookshelf_selection_has_one_catalog_aware_p_sel_owner():
    source = APP_PATH.read_text(encoding="utf-8")
    start = source.index("bookshelf_select_chain = ov_bookshelf.select(")
    end = source.index("# ═══════════ events", start)
    selection_block = source[start:end]

    assert "p_sel" not in selection_block
    assert "[bookshelf_selected_proj, bookshelf_selected]" in selection_block
    assert "reconcile_bookshelf_selection_context,\n        [ss]," in selection_block


def test_project_page_initial_selector_uses_catalog(monkeypatch):
    summary = type("Summary", (), {"project_name": "catalog-project"})()
    monkeypatch.setattr(
        project_page.ProjectCatalogService,
        "scan",
        staticmethod(lambda: [summary]),
    )
    monkeypatch.setattr(ProjectService, "scan_projects", lambda: ["legacy"])

    with gr.Blocks():
        page = project_page.create_project_page()

    assert [value for _label, value in page["p_sel"].choices] == ["catalog-project"]


def test_selection_handler_does_not_write_p_sel_before_reconciliation(lifecycle_workspace):
    ss = SessionState()
    event = type(
        "Event",
        (),
        {
            "index": (0, 2),
            "row_value": ["alpha", "1", "0/1", "⚪未开始"],
            "value": "0/1",
            "selected": True,
        },
    )()

    result = handlers.select_bookshelf_row([], ss, event)

    assert result[:1] == ("alpha",)
    assert len(result) == 2


def test_project_selector_uses_opened_workflow_state_only(lifecycle_workspace):
    ss = SessionState()
    ss.set_selected("alpha")

    unopened = handlers.reconcile_project_selector(ss)
    assert unopened.get("choices") == ["alpha", "beta"]
    assert unopened.get("value") is None

    ss.set_project("beta", {"meta": {}}, {})
    opened = handlers.reconcile_project_selector(ss)
    assert opened.get("choices") == ["alpha", "beta"]
    assert opened.get("value") == "beta"


def test_selection_transition_writes_raw_archive_state_and_passive_refresh_preserves_it(
    lifecycle_workspace,
):
    ss = SessionState()
    ss.set_selected("alpha")
    ss.begin_archive_confirmation()
    revision = ss._archive_confirmation_revision

    passive = handlers.refresh_bookshelf_management_view("", "alpha", ss)
    assert ss._archive_confirmation_revision == revision
    assert "value" not in passive[16]

    ss.set_selected("beta")
    transitioned = handlers.reconcile_bookshelf_selection(ss)
    assert transitioned[10] == ""


def test_search_returns_raw_selected_state_value(lifecycle_workspace):
    ss = SessionState()
    ss.set_selected("alpha")

    _rows, _info, selected_state = handlers.apply_project_search("", ss)

    assert selected_state == "alpha"
