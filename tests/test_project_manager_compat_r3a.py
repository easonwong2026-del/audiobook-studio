"""R3A regression tests for the project-manager compatibility boundary.

These tests keep the old module-level roots and wrappers covered while making
the production read paths point at ``ProjectRepository``.  The compatibility
module remains available for legacy callers; it is no longer a production
ownership boundary.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import lib.project_manager as pm
from lib import progress
from lib.snapshot import ProjectSnapshot
from repositories.project_repo import ProjectRepository
from services.project import ProjectService


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SCRIPT = {
    "meta": {"title": "R3A 测试书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {
            "id": 1,
            "title": "第一章",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段"},
            ],
        },
    ],
}


def _configure_roots(monkeypatch, workspace: Path, legacy: Path) -> None:
    """Keep both the compatibility and canonical mutable roots in sync."""
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(pm, "LEGACY_ROOT", str(legacy))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(legacy))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)


def _create_workspace_project(tmp_path: Path, monkeypatch, name: str = "r3a_book") -> str:
    workspace = tmp_path / "workspace"
    legacy = tmp_path / "legacy"
    _configure_roots(monkeypatch, workspace, legacy)
    source = tmp_path / "source.json"
    source.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    pm.create_project(name, str(source))
    return name


def _write_legacy_project(root: Path, name: str = "legacy_book") -> Path:
    project_dir = root / name
    for directory in ("voices", "segments", "chapters", "output"):
        (project_dir / directory).mkdir(parents=True, exist_ok=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "project_name": name,
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
                "total_chapters": 1,
                "total_segments": 1,
                "completed_count": 0,
                "failed_count": 0,
                "pending_count": 1,
                "segments_status": {"1-001": "pending"},
                "voice_bindings_path": "voice_bindings.json",
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "structured_script.json").write_text(
        json.dumps(SCRIPT, ensure_ascii=False),
        encoding="utf-8",
    )
    (project_dir / "voice_bindings.json").write_text(
        json.dumps({"bindings": {"旁白": None}, "bound_at": "", "verified": []}),
        encoding="utf-8",
    )
    return project_dir


def test_production_readers_no_longer_import_project_manager():
    """The four migrated production paths use the repository directly."""
    for relative in ("app.py", "services/synthesis.py", "lib/progress.py", "lib/snapshot.py"):
        source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        assert "project_manager" not in source, relative

    project_source = (PROJECT_ROOT / "services/project.py").read_text(encoding="utf-8")
    assert "compatibility_manager.WORKSPACE_ROOT" in project_source
    assert "compatibility_manager.LEGACY_ROOT" in project_source


def test_app_synthesis_preferences_route_through_project_service():
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    assert "from lib import project_manager" not in source
    assert "ProjectService.get_synthesis_selections" in source
    assert "ProjectService.set_synthesis_overrides" in source
    assert "ProjectService.set_synthesis_selections" in source


def test_project_manager_facade_preserves_mutable_root_compatibility(tmp_path, monkeypatch):
    name = _create_workspace_project(tmp_path, monkeypatch, "compat_book")

    assert Path(pm.get_project_dir(name)) == tmp_path / "workspace" / name
    assert ProjectRepository.get_project_dir(name) == pm.get_project_dir(name)
    assert name in pm.scan_projects()


def test_migrated_readers_preserve_legacy_root_resolution(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    legacy = tmp_path / "legacy"
    _configure_roots(monkeypatch, workspace, legacy)
    project_dir = _write_legacy_project(legacy)

    meta, script, bindings = ProjectService.open_project("legacy_book")
    assert meta.project_name == "legacy_book"
    assert script["meta"]["title"] == "R3A 测试书"
    assert bindings["bindings"]["旁白"] is None

    states = progress.build_segment_states("legacy_book")
    assert [item["seg_id"] for item in states] == ["1-001"]
    assert progress.build_preview_rows("legacy_book")[0][0] == "第1章 第一章"

    snapshot = ProjectRepository.load_snapshot("legacy_book")
    assert isinstance(snapshot, ProjectSnapshot)
    assert snapshot.project_dir == str(project_dir)
    meta_path = project_dir / "project.json"
    os.utime(meta_path, (snapshot.loaded_at + 10, snapshot.loaded_at + 10))
    refreshed = snapshot.reload_if_stale()
    assert refreshed is not snapshot
    assert refreshed.name == "legacy_book"


def test_project_service_owns_synthesis_preferences(tmp_path, monkeypatch):
    name = _create_workspace_project(tmp_path, monkeypatch)
    overrides = {"emotion": "happy", "override": True, "emo_alpha": 0.8}
    selections = {"mode": "chapters", "chapters": ["1"], "segment_ids": []}

    ProjectService.set_synthesis_overrides(name, overrides)
    ProjectService.set_synthesis_selections(name, selections)

    assert ProjectService.get_synthesis_overrides(name) == overrides
    assert ProjectService.get_synthesis_selections(name) == selections
    assert ProjectRepository.get_synthesis_overrides(name) == overrides
    assert ProjectRepository.get_synthesis_selections(name) == selections
