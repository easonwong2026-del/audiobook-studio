"""Windows P0 regressions for the bookshelf archive / project selector flow."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from repositories.project_repo import ProjectRepository
from services.project_storage import ProjectStorageService
from services.session import SessionState
from ui import project_catalog_handlers as handlers


APP_PATH = Path(__file__).parents[1] / "app.py"
WIRING_PATH = (
    Path(__file__).parents[1]
    / "ui"
    / "wiring"
    / "project_catalog_wiring.py"
)


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
def archive_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project("alpha", str(_script_file(tmp_path, "阿尔法")))
    ProjectRepository.create_project("beta", str(_script_file(tmp_path, "贝塔")))
    return data_root


class _SelectEvent:
    def __init__(self, name: str, *, selected: bool = True) -> None:
        self.index = (0, 2)
        self.row_value = [name, "1", "0/1", "⚪未开始"]
        self.value = "0/1"
        self.selected = selected


def _select(ss: SessionState, name: str):
    return handlers.select_bookshelf_row(
        {"data": [[name, "1", "0/1", "⚪未开始"]]},
        ss,
        _SelectEvent(name),
    )


def _assert_dropdown_update_is_valid(update: dict, previous_choices=()) -> None:
    choices = update.get("choices", list(previous_choices))
    value = update.get("value")
    assert value is None or value in choices, (choices, value)


def test_t1_selection_from_empty_dropdown_does_not_emit_invalid_value(
    archive_workspace,
):
    ss = SessionState()
    _name, _info, p_sel_update = _select(ss, "alpha")

    _assert_dropdown_update_is_valid(p_sel_update, previous_choices=[])


def test_t2_selected_a_opened_none_has_legal_value_and_choices(archive_workspace):
    ss = SessionState()
    _select(ss, "alpha")
    updates = handlers.reconcile_bookshelf_selection(ss, "alpha")

    _assert_dropdown_update_is_valid(updates[0], previous_choices=[])
    assert updates[0].get("value") == "alpha"


def test_t3_selected_a_opened_b_keeps_b_in_legal_choices(archive_workspace):
    ss = SessionState(project="beta")
    _select(ss, "alpha")
    updates = handlers.reconcile_bookshelf_selection(ss, "alpha")

    _assert_dropdown_update_is_valid(updates[0], previous_choices=[])
    assert updates[0].get("value") == "beta"


def test_t4_first_archive_click_is_confirmation_only(archive_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    message, confirmation, _selected, _info = handlers.archive_selected(
        "alpha", "", ss
    )

    assert "确认将" in message
    assert confirmation.get("value") == "alpha"
    assert ss.selected_project == "alpha"
    assert (archive_workspace / "projects" / "alpha").is_dir()

    _message, _confirmation, _selected, _info, event = (
        handlers.archive_selected_with_event("alpha", "", ss, 7)
    )
    assert event == 7


def test_t5_guard_block_does_not_increment_archive_event(archive_workspace, monkeypatch):
    class _GuardBlocked(RuntimeError):
        code = "PROJECT_HAS_ACTIVE_PRODUCTION"

    monkeypatch.setattr(
        handlers.ProjectStorageService,
        "archive",
        lambda _name: (_ for _ in ()).throw(_GuardBlocked("blocked")),
    )
    ss = SessionState()
    ss.set_selected("alpha")

    message, _confirmation, _selected, _info, event = (
        handlers.archive_selected_with_event("alpha", "alpha", ss, 7)
    )

    assert "正在生产" in message
    assert event == 7
    assert ss.selected_project == "alpha"
    assert (archive_workspace / "projects" / "alpha").is_dir()


def test_t6_archive_wiring_uses_success_only_reconciliation():
    source = WIRING_PATH.read_text(encoding="utf-8")
    start = source.index('page["bookshelf_archive"].click(')
    end = source.index("# ── 全局：从备份恢复", start)
    archive_block = source[start:end]

    assert 'cb["open_chain_rest"]' not in archive_block
    assert "archive_selected_with_event" in archive_block
    assert 'page["bookshelf_archive_event"].change(' in archive_block


def test_t7_archive_selected_a_opened_b_keeps_b_legal_in_p_sel(archive_workspace):
    ss = SessionState(project="beta", script={"meta": {"title": "贝塔"}})
    ss.set_selected("alpha")

    message, *_ = handlers.archive_selected("alpha", "alpha", ss)
    result = handlers.refresh_bookshelf_management_view("", "alpha", ss)

    assert "已移入回收站" in message
    assert ss.selected_project is None
    assert ss.project == "beta"
    p_sel_update = result[1]
    assert p_sel_update.get("choices") == ["beta"]
    assert p_sel_update.get("value") == "beta"
    _assert_dropdown_update_is_valid(p_sel_update)


def test_t8_archive_selected_and_opened_a_clears_value_safely(archive_workspace):
    ss = SessionState(project="alpha", script={"meta": {"title": "阿尔法"}})
    ss.set_selected("alpha")

    handlers.archive_selected("alpha", "alpha", ss)
    result = handlers.refresh_bookshelf_management_view("", "alpha", ss)

    assert ss.selected_project is None
    assert ss.project is None
    assert result[1].get("choices") == ["beta"]
    assert result[1].get("value") is None
    _assert_dropdown_update_is_valid(result[1])


def test_t9_archive_last_project_emits_empty_choices_and_none_value(archive_workspace):
    ss = SessionState()
    handlers.archive_selected("alpha", "alpha", ss)
    handlers.archive_selected("beta", "beta", ss)

    result = handlers.refresh_bookshelf_management_view("", "beta", ss)
    p_sel_update = result[1]

    assert p_sel_update.get("choices") == []
    assert p_sel_update.get("value") is None
    _assert_dropdown_update_is_valid(p_sel_update)


def test_t10_restore_repopulates_legal_p_sel_choices(archive_workspace):
    handlers.archive_selected("alpha", "alpha", None)
    archived = ProjectStorageService.list_archived()
    assert len(archived) == 1

    handlers.restore_archived_global(archived[0]["archive_id"])
    result = handlers.refresh_project_catalog("", "alpha")
    p_sel_update = result[1]

    assert "alpha" in p_sel_update.get("choices", [])
    assert p_sel_update.get("value") == "alpha"
    _assert_dropdown_update_is_valid(p_sel_update)


def test_t11_a_to_b_to_a_confirmation_remains_stale_protected(archive_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    _message, confirmation, *_ = handlers.archive_selected("alpha", "", ss)

    ss.set_selected("beta")
    ss.set_selected("alpha")
    message, next_confirmation, *_ = handlers.archive_selected(
        "alpha", confirmation.get("value"), ss
    )

    assert "确认将" in message
    assert next_confirmation.get("value") == "alpha"
    assert (archive_workspace / "projects" / "alpha").is_dir()


def test_t12_empty_catalog_selector_contract_is_explicit():
    update = handlers.build_project_selector_update([])

    assert update.get("choices") == []
    assert update.get("value") is None
    _assert_dropdown_update_is_valid(update)


def test_t13_manual_project_refresh_sanitizes_stale_value(monkeypatch):
    import app

    monkeypatch.setattr(app.ProjectService, "scan_projects", lambda: ["beta"])

    update = app.refresh_projects_full("alpha")

    assert update.get("choices") == ["beta"]
    assert update.get("value") is None
    _assert_dropdown_update_is_valid(update)


def test_t14_refresh_p_sel_emits_choices_with_legal_value(monkeypatch):
    import app

    summary = type("Summary", (), {"project_name": "beta"})()
    monkeypatch.setattr(app.ProjectCatalogService, "scan", lambda: [summary])

    update = app.refresh_p_sel("beta")

    assert update.get("choices") == ["beta"]
    assert update.get("value") == "beta"
    _assert_dropdown_update_is_valid(update)
