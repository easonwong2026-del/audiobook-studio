"""Project Catalog 唯一 live authority 的结构契约。"""
from __future__ import annotations

import ast
from pathlib import Path

from lib.types import ProjectSummary
from repositories.project_repo import ProjectRepository
from services.project import ProjectService
from services.project_catalog import ProjectCatalogService


ROOT = Path(__file__).parents[1]
APP_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
WIRING_TREE = ast.parse(
    (ROOT / "ui" / "wiring" / "project_catalog_wiring.py").read_text(
        encoding="utf-8"
    )
)


def _page_click_callbacks() -> dict[str, str]:
    callbacks: dict[str, str] = {}
    for node in ast.walk(WIRING_TREE):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "click"
            and isinstance(node.func.value, ast.Subscript)
            and node.args
        ):
            continue
        owner = node.func.value
        if not (
            isinstance(owner.value, ast.Name)
            and owner.value.id == "page"
            and isinstance(owner.slice, ast.Constant)
        ):
            continue
        callback = node.args[0]
        if (
            isinstance(callback, ast.Attribute)
            and isinstance(callback.value, ast.Name)
            and callback.value.id == "catalog_handlers"
        ):
            callbacks[str(owner.slice.value)] = callback.attr
    return callbacks


def test_live_bookshelf_scans_only_through_project_catalog(monkeypatch):
    calls = []
    expected = [
        ProjectSummary(
            project_name="authority",
            title="Authority",
            author="作者",
            chapters=1,
            segments=2,
            completed=1,
            modified_at=None,
        )
    ]

    def _summaries():
        calls.append("list_project_summaries")
        return expected

    monkeypatch.setattr(
        ProjectRepository,
        "list_project_summaries",
        staticmethod(_summaries),
    )

    assert [item.project_name for item in ProjectCatalogService.scan()] == [
        "authority"
    ]
    assert calls == ["list_project_summaries"]
    assert not hasattr(ProjectRepository, "list_projects")
    assert not hasattr(ProjectService, "list_projects")


def test_project_catalog_actions_are_wired_to_catalog_handlers():
    callbacks = _page_click_callbacks()
    assert callbacks == {
        "bookshelf_open": "open_selected_project",
        "bookshelf_open_dir": "open_selected_directory",
        "bookshelf_open_audio": "open_selected_generated_audio",
        "bookshelf_open_delivery": "open_selected_deliveries",
        "bookshelf_backup": "create_selected_backup",
        "bookshelf_cleanup": "scan_selected_cleanup",
        "bookshelf_cleanup_confirm": "execute_selected_cleanup",
        "bookshelf_cleanup_cancel": "cancel_selected_cleanup",
        "bookshelf_storage": "scan_selected_storage_upgrade",
        "bookshelf_storage_confirm": "execute_selected_storage_upgrade",
        "bookshelf_storage_cancel": "cancel_selected_storage_upgrade",
        "bookshelf_integrity": "check_selected_integrity",
        "bookshelf_integrity_repair": "repair_selected_integrity",
        "bookshelf_bind_chapter": "bind_selected_chapter",
        "bookshelf_update_chapter": "update_selected_chapter",
        "bookshelf_unbind_chapter": "unbind_selected_chapter",
        "bookshelf_archive": "archive_selected_with_event",
        "bookshelf_restore": "restore_backup_global",
        "bookshelf_trash_refresh": "refresh_archived_projects_global",
        "bookshelf_trash_restore": "restore_archived_global",
        "bookshelf_trash_delete": "permanently_delete_archived_global",
    }
    wiring_source = ast.unparse(WIRING_TREE)
    assert (
        "management_refresh = deps.get('management_refresh', "
        "catalog_handlers.refresh_bookshelf_management_view_with_hierarchy)"
        in wiring_source
    )


def test_app_has_no_duplicate_project_management_handlers():
    definitions = {
        node.name
        for node in APP_TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert definitions.isdisjoint({
        "scan_project_cleanup",
        "execute_project_cleanup",
        "cancel_project_cleanup",
        "check_project_integrity",
        "repair_project_integrity",
        "create_project_backup",
        "restore_project_backup",
        "refresh_archived_projects",
        "restore_archived_project",
        "permanently_delete_archived_project",
        "refresh_bookshelf",
    })
    assert "select_project_from_bookshelf" not in definitions
    assert "render_chapter_tree" not in definitions
