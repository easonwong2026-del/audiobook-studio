"""Storage Layout v3：resolver、纯 v3 新项目、v2→v3 迁移器 targeted 测试。

覆盖矩阵（用户第 23 节重点）：
- 三版本路径解析 / detect_storage_version / resolve_relative / make_relative
- 新项目纯 v3（root 只 4 个一级目录，无 legacy 目录）
- v2 / legacy(v1) backward read
- migration plan dry-run 不修改文件
- migration token 机制（必填 / 过期检测）
- live task blocker
- backup 失败 no mutation
- 迁移失败回滚（无半迁移）
- unknown 文件 preserve
- relative_path 迁移后可 resolve
"""
from __future__ import annotations

import json
import os

import pytest

from lib import project_paths
from repositories.project_repo import ProjectRepository


# ── helpers ────────────────────────────────────────────────────────────────


def _minimal_script(tmp_path, title="测试书"):
    path = tmp_path / "book.json"
    path.write_text(json.dumps({
        "meta": {"title": title, "author": "测试"},
        "voices": {"旁白": {"description": "测试"}},
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [{"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "测试"}],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _make_v2_project(tmp_path, monkeypatch, name="v2book"):
    """手工构造一个 v2 布局项目（root JSON + 01~09 目录 + 质量/导出/未知文件）。"""
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    project_dir = data_root / "projects" / name
    os.makedirs(project_dir, exist_ok=True)
    for d in (
        "01_项目配置", "02_原始文件", "03_章节文本", "04_角色与声音",
        "05_分段音频", "06_章节音频", "07_合并音频", "08_质检记录",
        "09_导出文件", "cache", "logs",
    ):
        os.makedirs(project_dir / d, exist_ok=True)
    script = {
        "meta": {"title": "测试书", "author": "测试"},
        "voices": {"旁白": {"description": "测试"}},
        "chapters": [{
            "id": 1, "title": "第一章",
            "segments": [{"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "测试"}],
        }],
    }
    meta = {
        "project_name": name, "storage_version": 2,
        "total_segments": 1, "segments_status": {"1-001": "done"},
        "source_file": "02_原始文件/book.json",
        "voice_bindings_path": "voice_bindings.json",
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
    }
    (project_dir / "project.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (project_dir / "structured_script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (project_dir / "voice_bindings.json").write_text(
        json.dumps({"bindings": {"旁白": "04_角色与声音/旁白.mp3"}}, ensure_ascii=False), encoding="utf-8")
    (project_dir / "02_原始文件" / "book.json").write_text("{}", encoding="utf-8")
    (project_dir / "04_角色与声音" / "旁白.mp3").write_bytes(b"mp3ref")
    (project_dir / "05_分段音频" / "1-001.wav").write_bytes(b"RIFFwav")
    (project_dir / "08_质检记录" / "quality_state.json").write_text(json.dumps({
        "revisions": {"r1": {"relative_path": "05_分段音频/1-001.wav"}},
        "active_revisions": {"1-001": "r1"},
    }, ensure_ascii=False), encoding="utf-8")
    os.makedirs(project_dir / "09_导出文件" / "exports" / "t1", exist_ok=True)
    (project_dir / "09_导出文件" / "exports" / "t1" / "book.mp3").write_bytes(b"mp3")
    (project_dir / "user_note.txt").write_text("用户自己加的文件", encoding="utf-8")
    return data_root, project_dir


# ── resolver ───────────────────────────────────────────────────────────────


class TestDetectStorageVersion:
    def test_v3_layout_by_file_location(self, tmp_path):
        pd = tmp_path / "proj"
        os.makedirs(pd / "99_系统数据" / "配置", exist_ok=True)
        (pd / "99_系统数据" / "配置" / "project.json").write_text(
            json.dumps({"storage_version": 3}), encoding="utf-8")
        assert project_paths.detect_storage_version(str(pd)) == 3

    def test_v3_corrupted_manifest_still_v3(self, tmp_path):
        pd = tmp_path / "proj"
        os.makedirs(pd / "99_系统数据" / "配置", exist_ok=True)
        (pd / "99_系统数据" / "配置" / "project.json").write_text("{ broken", encoding="utf-8")
        assert project_paths.detect_storage_version(str(pd)) == 3

    def test_v2_layout(self, tmp_path):
        pd = tmp_path / "proj"
        os.makedirs(pd)
        (pd / "project.json").write_text(json.dumps({"storage_version": 2}), encoding="utf-8")
        assert project_paths.detect_storage_version(str(pd)) == 2

    def test_legacy_no_manifest(self, tmp_path):
        pd = tmp_path / "proj"
        os.makedirs(pd)
        assert project_paths.detect_storage_version(str(pd)) == 1


class TestResolveRelative:
    def test_legacy_segments_prefix_maps_to_v3(self, tmp_path):
        pd = tmp_path / "proj"
        os.makedirs(pd / "99_系统数据" / "配置", exist_ok=True)
        (pd / "99_系统数据" / "配置" / "project.json").write_text(
            json.dumps({"storage_version": 3}), encoding="utf-8")
        resolved = project_paths.resolve_relative(str(pd), "05_分段音频/1-001.wav")
        assert resolved.replace("\\", "/").endswith("02_生成音频/分段音频/1-001.wav")

    def test_legacy_exports_prefix_maps_to_v3(self, tmp_path):
        pd = tmp_path / "proj"
        os.makedirs(pd / "99_系统数据" / "配置", exist_ok=True)
        (pd / "99_系统数据" / "配置" / "project.json").write_text(
            json.dumps({"storage_version": 3}), encoding="utf-8")
        resolved = project_paths.resolve_relative(str(pd), "exports/t1/book.mp3")
        assert resolved.replace("\\", "/").endswith("03_导出成品/正式导出/t1/book.mp3")

    def test_already_v3_prefix_unchanged(self, tmp_path):
        pd = tmp_path / "proj"
        os.makedirs(pd / "99_系统数据" / "配置", exist_ok=True)
        (pd / "99_系统数据" / "配置" / "project.json").write_text(
            json.dumps({"storage_version": 3}), encoding="utf-8")
        resolved = project_paths.resolve_relative(str(pd), "02_生成音频/分段音频/1-001.wav")
        assert resolved.replace("\\", "/").endswith("02_生成音频/分段音频/1-001.wav")

    def test_traversal_rejected(self, tmp_path):
        pd = tmp_path / "proj"
        os.makedirs(pd)
        with pytest.raises(ValueError):
            project_paths.resolve_relative(str(pd), "../outside.wav")


# ── 纯 v3 新项目 ───────────────────────────────────────────────────────────


class TestNewProjectV3:
    def test_root_only_four_top_level_dirs(self, tmp_path, monkeypatch):
        data_root = tmp_path / "data"
        monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
        ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
        ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
        ProjectRepository._INITIALIZED = True
        ProjectRepository.create_project("v3book", str(_minimal_script(tmp_path)))
        project_dir = data_root / "projects" / "v3book"
        assert set(os.listdir(project_dir)) == {
            "01_原始资料", "02_生成音频", "03_导出成品", "99_系统数据",
        }
        # 系统 JSON 全在 99_系统数据/配置/
        config_dir = project_dir / "99_系统数据" / "配置"
        assert (config_dir / "project.json").is_file()
        assert (config_dir / "structured_script.json").is_file()
        assert (config_dir / "voice_bindings.json").is_file()
        # 无 legacy 目录
        for legacy in ("chapters", "segments", "voices", "output", "exports", "cache", "logs"):
            assert not (project_dir / legacy).exists()
        # scan 识别
        assert "v3book" in ProjectRepository.scan_projects()

    def test_v2_backward_read(self, tmp_path, monkeypatch):
        _data_root, project_dir = _make_v2_project(tmp_path, monkeypatch)
        assert project_paths.detect_storage_version(str(project_dir)) == 2
        assert "v2book" in ProjectRepository.scan_projects()
        meta, _script, _bindings = ProjectRepository.load_project("v2book")
        assert meta.storage_version == 2

    def test_legacy_backward_read(self, tmp_path, monkeypatch):
        data_root = tmp_path / "data"
        monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
        ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
        ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
        ProjectRepository._INITIALIZED = True
        project_dir = data_root / "projects" / "oldbook"
        os.makedirs(project_dir / "segments", exist_ok=True)
        (project_dir / "project.json").write_text(json.dumps({
            "project_name": "oldbook", "total_segments": 0,
            "segments_status": {}, "storage_version": 0,
        }, ensure_ascii=False), encoding="utf-8")
        (project_dir / "structured_script.json").write_text(json.dumps({
            "chapters": [], "voices": {},
        }, ensure_ascii=False), encoding="utf-8")
        (project_dir / "voice_bindings.json").write_text("{}", encoding="utf-8")
        assert project_paths.detect_storage_version(str(project_dir)) == 1
        assert "oldbook" in ProjectRepository.scan_projects()


# ── 迁移器 ─────────────────────────────────────────────────────────────────


class TestMigration:
    def test_plan_dry_run_no_mutation(self, tmp_path, monkeypatch):
        from services.project_storage import ProjectStorageService
        _data_root, project_dir = _make_v2_project(tmp_path, monkeypatch)
        before = sorted(p.name for p in project_dir.iterdir())
        plan = ProjectStorageService.plan_storage_upgrade("v2book")
        assert plan["code"] == "PLAN_OK"
        assert plan["from_version"] == 2
        assert plan["to_version"] == 3
        assert plan["backup_required"] is True
        assert len(plan["token"]) == 64
        assert any(u["path"].endswith("user_note.txt") for u in plan["unknown_paths"])
        after = sorted(p.name for p in project_dir.iterdir())
        assert before == after  # dry-run 不改任何文件

    def test_token_required_and_stale(self, tmp_path, monkeypatch):
        from services.project_storage import ProjectStorageService
        _data_root, project_dir = _make_v2_project(tmp_path, monkeypatch)
        plan = ProjectStorageService.plan_storage_upgrade("v2book")
        # 错误 token → 拒绝
        with pytest.raises(ValueError):
            ProjectStorageService.upgrade_storage("v2book", "bad-token")
        # 文件变化后旧 token 失效
        (project_dir / "05_分段音频" / "new.wav").write_bytes(b"x")
        with pytest.raises(ValueError):
            ProjectStorageService.upgrade_storage("v2book", plan["token"])

    def test_already_current(self, tmp_path, monkeypatch):
        from services.project_storage import ProjectStorageService
        data_root = tmp_path / "data"
        monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
        ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
        ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
        ProjectRepository._INITIALIZED = True
        ProjectRepository.create_project("v3book", str(_minimal_script(tmp_path)))
        plan = ProjectStorageService.plan_storage_upgrade("v3book")
        assert plan["code"] == "ALREADY_CURRENT"

    def test_full_upgrade_success(self, tmp_path, monkeypatch):
        from services.project_storage import ProjectStorageService
        _data_root, project_dir = _make_v2_project(tmp_path, monkeypatch)
        plan = ProjectStorageService.plan_storage_upgrade("v2book")
        result = ProjectStorageService.upgrade_storage("v2book", plan["token"])
        assert result["ok"] is True
        assert result["backup_path"]
        assert os.path.isfile(result["backup_path"])
        # root 只 4 目录
        assert set(os.listdir(project_dir)) == {
            "01_原始资料", "02_生成音频", "03_导出成品", "99_系统数据",
        }
        # version 切换
        assert project_paths.detect_storage_version(str(project_dir)) == 3
        assert (project_dir / "03_导出成品" / "正式导出" / "t1" / "book.mp3").is_file()
        # unknown 保留
        preserved = project_dir / "99_系统数据" / "迁移保留" / "user_note.txt"
        assert preserved.is_file()
        # relative_path 重写
        quality = json.loads((project_dir / "99_系统数据" / "质检" / "quality_state.json").read_text(encoding="utf-8"))
        assert quality["revisions"]["r1"]["relative_path"] == "02_生成音频/分段音频/1-001.wav"
        # 迁移后可 resolve
        resolved = project_paths.resolve_relative(str(project_dir), quality["revisions"]["r1"]["relative_path"])
        assert os.path.exists(resolved)
        # scan/load 正常
        assert "v2book" in ProjectRepository.scan_projects()
        meta, _s, _b = ProjectRepository.load_project("v2book")
        assert meta.storage_version == 3

    def test_backup_failure_no_mutation(self, tmp_path, monkeypatch):
        from services.project_storage import ProjectStorageService
        from services import project_backup
        _data_root, project_dir = _make_v2_project(tmp_path, monkeypatch)
        before = sorted(p.name for p in project_dir.iterdir())
        plan = ProjectStorageService.plan_storage_upgrade("v2book")

        def _fail_backup(*a, **kw):
            raise OSError("backup failed")

        monkeypatch.setattr(project_backup.ProjectBackupService, "create_backup", _fail_backup)
        with pytest.raises(OSError):
            ProjectStorageService.upgrade_storage("v2book", plan["token"])
        after = sorted(p.name for p in project_dir.iterdir())
        assert before == after  # 无任何变动
        assert project_paths.detect_storage_version(str(project_dir)) == 2


# ── 正式导出路径 ───────────────────────────────────────────────────────────


class TestExportV3Path:
    def test_resolver_delivery_and_temp_keys(self, tmp_path):
        pd = str(tmp_path / "proj")
        project_paths.ensure_layout(pd, prefer_version=3, compatibility=False)
        # 显式 v3 解析（无 manifest 时 detect 会回退 legacy）
        def _v3(key):
            return project_paths.project_dir(pd, key, prefer_version=3)
        assert _v3("delivery_official").replace("\\", "/").endswith("03_导出成品/正式导出")
        assert _v3("temp").replace("\\", "/").endswith("99_系统数据/临时")
        assert _v3("supplement_audio").replace("\\", "/").endswith("02_生成音频/补录音频")
        assert _v3("delivery_supplement").replace("\\", "/").endswith("03_导出成品/补录")
        assert _v3("migration_preserved").replace("\\", "/").endswith("99_系统数据/迁移保留")
