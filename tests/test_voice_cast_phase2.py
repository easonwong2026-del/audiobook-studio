"""Phase-2 Character Roster / Voice Cast contract tests."""
from __future__ import annotations

import hashlib
import os

import pytest

from mcp_server.server import handle_request
from repositories.project_repo import ProjectRepository
from services import VoiceAssetService, VoiceCastError, VoiceCastResolver
from services.project import ProjectService


@pytest.fixture
def phase2_project(tmp_path, monkeypatch):
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    data = tmp_path / "data"
    library = data / "voice_library"
    library.mkdir(parents=True)
    (library / "沉稳_01.wav").write_bytes(b"voice-one")
    (library / "清亮_02.wav").write_bytes(b"voice-two")
    (library / "notes.txt").write_text("ignore", encoding="utf-8")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data))
    ProjectRepository.WORKSPACE_ROOT = str(data / "projects")
    ProjectRepository.LEGACY_ROOT = str(data / "legacy")
    ProjectRepository._INITIALIZED = True
    script = {
        "version": "3.0",
        "meta": {"title": "演员表测试", "author": "测试"},
        "voices": {"旁白": {}, "叶文洁": {}},
        "chapters": [{
            "id": "001",
            "title": "第一章",
            "segments": [
                {"id": "001-001", "role": "旁白", "text": "开场"},
                {"id": "001-002", "role": "叶文洁", "text": "你好"},
            ],
        }],
    }
    ProjectService.create_project_from_data("phase2", script)
    yield data
    (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    ) = original


def _roles():
    return [
        {"role_id": "role_narrator", "name": "旁白", "aliases": ["叙述者"]},
        {"role_id": "role_ye_wenjie", "name": "叶文洁", "aliases": ["叶老师", "文洁"]},
    ]


def test_voice_assets_are_content_stable_and_hide_paths(phase2_project):
    items = VoiceAssetService.list_assets()
    assert len(items) == 2
    assert all("path" not in item for item in items)
    first = {item["file_name"]: item for item in items}["沉稳_01.wav"]
    expected = hashlib.sha256(b"voice-one").hexdigest()
    assert first["sha256"] == expected
    assert first["voice_asset_id"] == f"voice_{expected[:12]}"
    assert VoiceAssetService.list_assets()[0]["voice_asset_id"] in {
        item["voice_asset_id"] for item in items
    }


def test_roster_validation_and_additive_update(phase2_project):
    valid = VoiceCastResolver.validate_character_roster("phase2", _roles())
    assert valid["valid"] is True
    created = VoiceCastResolver.set_character_roster("phase2", _roles())
    assert created["success"] is True
    with pytest.raises(VoiceCastError) as duplicate:
        VoiceCastResolver.set_character_roster("phase2", _roles())
    assert duplicate.value.code == "ROSTER_EXISTS"

    added = VoiceCastResolver.add_character_roles(
        "phase2", [{"role_id": "role_ding_yi", "name": "丁仪"}]
    )
    assert "role_ding_yi" in added["roles"]
    with pytest.raises(VoiceCastError) as immutable:
        VoiceCastResolver.update_character_role(
            "phase2", "role_ding_yi", {"role_id": "role_other"}
        )
    assert immutable.value.code == "ROLE_ID_IMMUTABLE"

    conflict = VoiceCastResolver.validate_character_roster("phase2", [
        {"role_id": "a", "name": "甲", "aliases": ["老王"]},
        {"role_id": "b", "name": "乙", "aliases": ["老王"]},
    ])
    assert conflict["valid"] is False
    assert any(item["code"] == "ALIAS_CONFLICT" for item in conflict["errors"])


def test_resolver_priority_and_unknown_role(phase2_project):
    VoiceCastResolver.set_character_roster("phase2", _roles())
    by_id = VoiceCastResolver.resolve_role(
        "phase2", {"role_id": "role_ye_wenjie", "role": "旁白"}
    )
    assert by_id["role_id"] == "role_ye_wenjie"
    assert VoiceCastResolver.resolve_role("phase2", {"role": "叶老师"})["matched_by"] == "alias"
    with pytest.raises(VoiceCastError) as unknown:
        VoiceCastResolver.resolve_role("phase2", {"role": "完全未知"})
    assert unknown.value.code == "ROLE_NOT_IN_ROSTER"


def test_cast_snapshot_lock_and_force_rebind_invalidates_only_one_role(phase2_project):
    VoiceCastResolver.set_character_roster("phase2", _roles())
    assets = VoiceAssetService.list_assets()
    first, second = assets[0]["voice_asset_id"], assets[1]["voice_asset_id"]
    VoiceCastResolver.set_voice_cast("phase2", {
        "role_narrator": {"voice_asset_id": first},
        "role_ye_wenjie": {"voice_asset_id": first},
    })
    validation = VoiceCastResolver.validate_voice_cast("phase2")
    assert validation["ready"] is True
    VoiceCastResolver.finalize_voice_cast("phase2")

    project_dir = ProjectRepository.get_project_dir("phase2")
    bindings = ProjectRepository.load_bindings(project_dir)
    snapshot = bindings["role_bindings"]["role_ye_wenjie"]["project_voice_path"]
    snapshot_path = os.path.join(project_dir, snapshot)
    assert os.path.isfile(snapshot_path)
    # A project snapshot remains usable after the global source is removed.
    library_path = os.path.join(str(phase2_project), "voice_library", "沉稳_01.wav")
    os.remove(library_path)
    assert os.path.isfile(snapshot_path)
    assert VoiceCastResolver.validate_voice_cast("phase2")["ready"] is True

    meta, _, _ = ProjectRepository.load_project("phase2")
    meta.segments_status["001-002"] = "done"
    meta.segments_status["001-001"] = "done"
    ProjectRepository._save_meta(project_dir, meta)
    result = VoiceCastResolver.bind_cast_role(
        "phase2", "role_ye_wenjie", second, force_rebind=True
    )
    assert result["segments_invalidated"] == 1
    refreshed, _, _ = ProjectRepository.load_project("phase2")
    assert refreshed.segments_status["001-002"] == "pending"
    assert refreshed.segments_status["001-001"] == "done"


def test_mcp_phase2_smoke(phase2_project):
    project = "phase2"
    assets = handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "list_voice_assets", "arguments": {}},
    })["result"]["structuredContent"]["items"]
    assert len(assets) == 2
    asset_id = assets[0]["voice_asset_id"]
    assert handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "set_character_roster", "arguments": {
            "project_name": project, "roles": _roles(),
        }},
    })["result"]["structuredContent"]["success"] is True
    cast = {role["role_id"]: {"voice_asset_id": asset_id} for role in _roles()}
    result = handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "set_voice_cast", "arguments": {
            "project_name": project, "roles": cast,
        }},
    })["result"]["structuredContent"]
    assert result["success"] is True
    assert handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "finalize_voice_cast", "arguments": {"project_name": project}},
    })["result"]["structuredContent"]["status"] == "locked"
    check = VoiceCastResolver.check_chapter_roles(project, [{
        "id": "002", "segments": [{"id": "002-001", "role": "叶文洁", "text": "继续"}],
    }])
    assert check["synthesis_ready"] is True
    new = VoiceCastResolver.check_chapter_roles(project, [{
        "id": "052", "segments": [{"id": "052-021", "role": "丁仪", "text": "出现"}],
    }])
    assert new["synthesis_ready"] is False
    assert new["new_roles"][0]["suggested_role_id"] == "role_ding_yi"
