"""Windows P0 regressions for the bookshelf archive / project selector flow."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from repositories.project_repo import ProjectRepository
from services.project_storage import ProjectStorageService
from services.session import SessionState
from ui import project_catalog_handlers as handlers


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


def test_t1_selection_from_empty_dropdown_does_not_emit_invalid_value(
    archive_workspace,
):
    ss = SessionState()
    name, info = _select(ss, "alpha")

    assert name == "alpha"
    assert "当前选择" in info


def test_t2_selected_a_opened_none_has_legal_value_and_choices(archive_workspace):
    ss = SessionState()
    _select(ss, "alpha")
    updates = handlers.reconcile_bookshelf_selection(ss)

    assert len(updates) == 18
    assert ss.selected_project == "alpha"
    assert ss.project is None


def test_t3_selected_a_opened_b_keeps_b_in_legal_choices(archive_workspace):
    ss = SessionState(project="beta")
    _select(ss, "alpha")
    updates = handlers.reconcile_bookshelf_selection(ss)

    assert len(updates) == 18
    assert ss.selected_project == "alpha"
    assert ss.project == "beta"


def test_t4_first_archive_click_is_confirmation_only(archive_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    message, confirmation, _selected, _info = handlers.archive_selected(
        "alpha", "", ss
    )

    assert "确认将" in message
    assert confirmation == "alpha"
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


def test_t7_archive_selected_a_preserves_opened_b(archive_workspace):
    ss = SessionState(project="beta", script={"meta": {"title": "贝塔"}})
    ss.set_selected("alpha")

    message, *_ = handlers.archive_selected("alpha", "alpha", ss)
    result = handlers.refresh_bookshelf_management_view("", ss)

    assert "已移入回收站" in message
    assert ss.selected_project is None
    assert ss.project == "beta"
    assert result[0]["data"]
    assert result[4] == ""
    assert ss.project == "beta"


def test_t8_archive_selected_and_opened_a_clears_value_safely(archive_workspace):
    ss = SessionState(project="alpha", script={"meta": {"title": "阿尔法"}})
    ss.set_selected("alpha")

    handlers.archive_selected("alpha", "alpha", ss)
    result = handlers.refresh_bookshelf_management_view("", ss)

    assert ss.selected_project is None
    assert ss.project is None
    assert [row[0] for row in result[0]["data"]] == ["beta"]
    assert result[4] == ""


def test_t9_archive_last_project_emits_empty_choices_and_none_value(archive_workspace):
    ss = SessionState()
    handlers.archive_selected("alpha", "alpha", ss)
    handlers.archive_selected("beta", "beta", ss)

    result = handlers.refresh_bookshelf_management_view("", ss)

    assert result[0]["data"] == []
    assert result[4] == ""


def test_t10_restore_repopulates_catalog(archive_workspace):
    handlers.archive_selected("alpha", "alpha", None)
    archived = ProjectStorageService.list_archived()
    assert len(archived) == 1

    handlers.restore_archived_global(archived[0]["archive_id"])
    result = handlers.refresh_bookshelf_management_view("", SessionState())
    assert {row[0] for row in result[0]["data"]} == {"alpha", "beta"}


def test_t11_a_to_b_to_a_confirmation_remains_stale_protected(archive_workspace):
    ss = SessionState()
    ss.set_selected("alpha")
    _message, confirmation, *_ = handlers.archive_selected("alpha", "", ss)

    ss.set_selected("beta")
    ss.set_selected("alpha")
    message, next_confirmation, *_ = handlers.archive_selected(
        "alpha", confirmation, ss
    )

    assert "确认将" in message
    assert next_confirmation == "alpha"
    assert (archive_workspace / "projects" / "alpha").is_dir()
