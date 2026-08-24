"""R3B architecture guards for the remaining project-manager facade.

The compatibility module is intentionally still available for legacy callers,
but canonical production ownership lives in ProjectRepository/ProjectService.
These tests protect that split and the explicit root synchronization boundary.
"""
from __future__ import annotations

import ast
from pathlib import Path

import lib.project_manager as pm
from repositories.project_repo import ProjectRepository
from services.project import ProjectService


ROOT = Path(__file__).resolve().parents[1]
FACADE_PATH = ROOT / "lib" / "project_manager.py"
SERVICE_PATH = ROOT / "services" / "project.py"

SCRIPT = {
    "meta": {"title": "R3B 测试书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {
            "id": 1,
            "title": "第一章",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段"},
            ],
        }
    ],
}


RETAINED_FACADE_FUNCTIONS = {
    "_repository",
    "scan_projects",
    "create_project",
    "open_project",
    "load_snapshot",
    "delete_project",
    "get_project_dir",
    "update_segment_status",
    "get_remaining",
}


def _set_repo_roots(monkeypatch, workspace: Path, legacy: Path) -> None:
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(legacy))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)


def test_facade_surface_is_narrow_and_private_dead_wrappers_stay_gone():
    source = FACADE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert functions == RETAINED_FACADE_FUNCTIONS
    for name in (
        "_resolve_dir",
        "_meta_path",
        "_load_meta",
        "_repair_meta",
        "_save_meta",
        "_project_status",
        "get_synthesis_overrides",
        "set_synthesis_overrides",
        "get_synthesis_selections",
        "set_synthesis_selections",
    ):
        assert f"def {name}" not in source


def test_production_import_graph_keeps_project_manager_at_root_sync_only():
    allowed = SERVICE_PATH
    roots = [
        ROOT / "app.py",
        ROOT / "lib",
        ROOT / "repositories",
        ROOT / "services",
        ROOT / "ui",
        ROOT / "mcp_server",
        ROOT / "scripts",
    ]
    for root in roots:
        if root.is_file():
            paths = [root]
        elif root.is_dir():
            paths = root.rglob("*.py")
        else:
            continue
        for path in paths:
            if path == FACADE_PATH:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name for alias in node.names}
                    assert "lib.project_manager" not in imported or path == allowed
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "lib":
                        imported = {alias.name for alias in node.names}
                        assert "project_manager" not in imported or path == allowed

    service_source = SERVICE_PATH.read_text(encoding="utf-8")
    assert "compatibility_manager.WORKSPACE_ROOT" in service_source
    assert "compatibility_manager.LEGACY_ROOT" in service_source


def test_mutable_pm_roots_only_drive_facade_calls(tmp_path, monkeypatch):
    canonical_workspace = tmp_path / "canonical" / "projects"
    canonical_legacy = tmp_path / "canonical" / "legacy"
    compatibility_workspace = tmp_path / "compatibility" / "projects"
    compatibility_legacy = tmp_path / "compatibility" / "legacy"

    _set_repo_roots(monkeypatch, canonical_workspace, canonical_legacy)
    ProjectService.create_project_from_data("canonical", SCRIPT)

    _set_repo_roots(monkeypatch, compatibility_workspace, compatibility_legacy)
    ProjectService.create_project_from_data("compatibility", SCRIPT)

    _set_repo_roots(monkeypatch, canonical_workspace, canonical_legacy)
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(compatibility_workspace))
    monkeypatch.setattr(pm, "LEGACY_ROOT", str(compatibility_legacy))

    # The repository and service do not read the pm module globals.
    assert ProjectRepository.get_project_dir("canonical") == str(
        canonical_workspace / "canonical"
    )
    assert ProjectService.open_project("canonical")[0].project_name == "canonical"
    assert ProjectRepository.WORKSPACE_ROOT == str(canonical_workspace)

    # A call through the legacy facade explicitly synchronizes its own roots.
    assert pm.get_project_dir("compatibility") == str(
        compatibility_workspace / "compatibility"
    )

    # Restore the canonical repository boundary and confirm the two surfaces
    # remain independently addressable.
    _set_repo_roots(monkeypatch, canonical_workspace, canonical_legacy)
    assert ProjectService.open_project("canonical")[0].project_name == "canonical"
    assert pm.get_project_dir("compatibility") == str(
        compatibility_workspace / "compatibility"
    )


def test_set_data_dir_keeps_legacy_root_sync_contract(tmp_path, monkeypatch):
    workspace = tmp_path / "projects"
    legacy = tmp_path / "legacy"
    monkeypatch.setattr(
        "services.project.ensure_project_mutation_allowed",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "services.project.ConfigRepository.set_data_dir",
        staticmethod(lambda new_dir: str(new_dir)),
    )
    monkeypatch.setattr("services.project.config.get_projects_root", lambda: str(workspace))
    monkeypatch.setattr("services.project.config.get_legacy_dir", lambda: str(legacy))

    ProjectService.set_data_dir(str(tmp_path / "data"))

    assert ProjectRepository.WORKSPACE_ROOT == str(workspace)
    assert ProjectRepository.LEGACY_ROOT == str(legacy)
    assert pm.WORKSPACE_ROOT == str(workspace)
    assert pm.LEGACY_ROOT == str(legacy)
