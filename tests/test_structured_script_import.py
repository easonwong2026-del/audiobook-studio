"""Offline structured_script import/preview/atomic creation coverage."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import project_paths
from repositories.project_repo import ProjectRepository
from services.structured_script_import import StructuredScriptImportService

ROOT = Path(__file__).resolve().parents[1]
VALID = ROOT / "tests" / "fixtures" / "structured_script_valid.json"


@pytest.fixture
def isolated_projects(tmp_path):
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    yield tmp_path
    (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    ) = original


def write_payload(tmp_path, payload, name="script.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def payload(tmp_path):
    return json.loads(VALID.read_text(encoding="utf-8"))


def test_inspect_is_offline_and_does_not_create_project(isolated_projects):
    preview = StructuredScriptImportService.inspect(str(VALID), "新项目")
    assert preview.valid
    assert preview.title == "外部 Agent 示例"
    assert preview.author == "测试作者"
    assert (preview.chapter_count, preview.segment_count, preview.role_count) == (2, 4, 2)
    assert preview.narrator_defined
    assert preview.slot_status == "available"
    assert ProjectRepository.scan_projects() == []


def test_explicit_project_name_and_meta_title_are_used(isolated_projects, tmp_path):
    raw = payload(tmp_path)
    raw["project_name"] = "明确项目名"
    raw["meta"]["title"] = "作品展示名"
    path = write_payload(tmp_path, raw)
    preview = StructuredScriptImportService.inspect(str(path))
    assert preview.suggested_project_name == "明确项目名"
    assert preview.title == "作品展示名"


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda raw: raw.pop("meta"), "meta: 缺少必填对象"),
        (lambda raw: raw.pop("voices"), "voices: 未定义任何角色"),
        (lambda raw: raw.pop("chapters"), "chapters: 未定义任何章节"),
        (lambda raw: raw["chapters"][0]["segments"].clear(), "segments: 不能为空"),
        (lambda raw: raw["chapters"][0]["segments"][1].update({"id": "1-001"}), "片段 ID 重复"),
        (lambda raw: raw["chapters"][0]["segments"][0].update({"speaker": "未知角色"}), "speaker: 角色“未知角色”未在 voices 中定义"),
        (lambda raw: raw["chapters"][0]["segments"][0].update({"emotion": "not-real"}), "emotion: 不支持的情绪"),
        (lambda raw: raw["chapters"][0]["segments"][0].update({"speech_rate": 2.0}), "speech_rate: 数值"),
        (lambda raw: raw["chapters"][0]["segments"][0].update({"delivery": {"pitch": 99}}), "pitch: 数值"),
        (lambda raw: raw["chapters"][0]["segments"][0].update({"emo_alpha": 2}), "emo_alpha: 数值"),
        (lambda raw: raw["chapters"][0]["segments"][0].update({"pause_after": 3001}), "pause_after: 数值"),
        (lambda raw: raw["meta"].update({"total_segments": 999}), "meta.total_segments"),
    ],
)
def test_invalid_json_payloads_block_import(isolated_projects, tmp_path, mutate, needle):
    raw = payload(tmp_path)
    mutate(raw)
    preview = StructuredScriptImportService.inspect(str(write_payload(tmp_path, raw)))
    assert not preview.valid
    assert any(needle in error for error in preview.errors), preview.errors


def test_warning_allows_creation(isolated_projects, tmp_path):
    raw = payload(tmp_path)
    raw["voices"]["未使用"] = {"description": "备用"}
    raw["chapters"][0]["segments"][0]["text"] = "短"
    path = write_payload(tmp_path, raw)
    preview = StructuredScriptImportService.inspect(str(path), "warning-project")
    assert preview.valid
    assert preview.warnings
    result = StructuredScriptImportService.create("warning-project", str(path))
    assert result.warnings
    assert "warning-project" in ProjectRepository.scan_projects()


def test_creation_initializes_project_snapshot_and_bindings(isolated_projects):
    result = StructuredScriptImportService.create("created", str(VALID))
    project_dir = Path(ProjectRepository.get_project_dir(result.project_name))
    assert result.segment_count == 4
    assert Path(project_paths.project_file(str(project_dir), "project_meta")).is_file()
    assert Path(project_paths.project_file(str(project_dir), "structured_script")).is_file()
    bindings = json.loads(Path(project_paths.project_file(str(project_dir), "voice_bindings")).read_text(encoding="utf-8"))
    assert bindings["bindings"] == {"旁白": None, "小雨": None}
    snapshot = ProjectRepository.load_snapshot("created")
    assert snapshot.meta.total_chapters == 2
    assert snapshot.meta.total_segments == 4
    assert set(snapshot.meta.segments_status) == {"1-001", "1-002", "2-001", "2-002"}


def test_existing_and_incomplete_slots_are_not_overwritten(isolated_projects, tmp_path):
    StructuredScriptImportService.create("occupied", str(VALID))
    with pytest.raises(ValueError, match="已存在"):
        StructuredScriptImportService.create("occupied", str(VALID))

    incomplete = Path(ProjectRepository.WORKSPACE_ROOT) / "incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "project.json").write_text("{}", encoding="utf-8")
    preview = StructuredScriptImportService.inspect(str(VALID), "incomplete")
    assert preview.slot_status == "incomplete"
    with pytest.raises(ValueError, match="不完整"):
        StructuredScriptImportService.create("incomplete", str(VALID))


def test_invalid_json_syntax_is_reported(tmp_path, isolated_projects):
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        StructuredScriptImportService.inspect(str(path))


def test_json_top_level_must_be_an_object(isolated_projects, tmp_path):
    path = write_payload(tmp_path, ["not-an-object"])
    preview = StructuredScriptImportService.inspect(str(path))
    assert not preview.valid
    assert any("顶层结构必须是 JSON 对象" in error for error in preview.errors)


@pytest.mark.parametrize(
    ("pause_value", "needle"),
    [
        (None, ".pauses: 必须是数组"),
        (["not-an-object"], ".pauses[0]: 必须是对象"),
        ([{"position": "1", "duration": 100}], ".pauses[0].position: 必须是整数"),
        ([{"position": 999, "duration": 100}], ".pauses[0].position: 数值"),
        ([{"position": 1, "duration": "100"}], ".pauses[0].duration: 必须是整数"),
        ([{"position": 1, "duration": 3001}], ".pauses[0].duration: 数值"),
        ([{"position": 1, "duration": 100, "type": "not-a-pause"}], ".pauses[0].type: 不支持"),
    ],
)
def test_pause_errors_keep_the_precise_json_path(
    isolated_projects, tmp_path, pause_value, needle
):
    raw = payload(tmp_path)
    raw["chapters"][0]["segments"][0]["pauses"] = pause_value
    preview = StructuredScriptImportService.inspect(str(write_payload(tmp_path, raw)))
    assert not preview.valid
    assert any(needle in error for error in preview.errors), preview.errors


def test_role_and_speaker_mismatch_is_rejected(isolated_projects, tmp_path):
    raw = payload(tmp_path)
    raw["chapters"][0]["segments"][0].update({"role": "旁白", "speaker": "小雨"})
    preview = StructuredScriptImportService.inspect(str(write_payload(tmp_path, raw)))
    assert not preview.valid
    assert any("role 与 speaker 不一致" in error for error in preview.errors)


def test_alias_collections_are_normalized_for_creation(isolated_projects, tmp_path):
    raw = payload(tmp_path)
    raw["characters"] = raw.pop("voices")
    raw["sections"] = raw.pop("chapters")
    path = write_payload(tmp_path, raw)
    preview = StructuredScriptImportService.inspect(str(path), "alias-project")
    assert preview.valid
    assert (preview.chapter_count, preview.segment_count, preview.role_count) == (2, 4, 2)

    result = StructuredScriptImportService.create("alias-project", str(path))
    snapshot = ProjectRepository.load_snapshot(result.project_name)
    assert snapshot.meta.total_chapters == 2
    assert snapshot.meta.total_segments == 4
    assert set(snapshot.bindings) == {"旁白", "小雨"}
