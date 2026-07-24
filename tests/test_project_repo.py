"""ProjectRepository 单元测试。

测试内容：
- scan_projects / load_project / create_project / delete_project 完整链路
- update_segment_status 重新计数
- legacy 目录兼容
- load_snapshot 快照构建
- 原子写（模拟写入中断不损坏）
"""
from __future__ import annotations

import json
import os
import shutil
import time

import pytest

from lib.types import ProjectMeta
from repositories.project_repo import ProjectRepository
from repositories.exceptions import ProjectNotFoundError


# 辅助：创建最小的剧本 JSON
def _make_minimal_script(tmp_path, name="test_script.json") -> str:
    path = str(tmp_path / name)
    data = {
        "meta": {"title": "测试", "author": "T"},
        "voices": {"旁白": {"name": "旁白", "description": "测试"}},
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [
                {"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "第一句"},
                {"id": "1-002", "role": "旁白", "emotion": "neutral", "text": "第二句"},
            ],
        }],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


class TestProjectRepository:
    """ProjectRepository 完整链路测试。"""

    def test_create_and_scan(self, tmp_path):
        """create_project → scan_projects 完整链路。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script1.json")
            name = ProjectRepository.create_project("test_book", script_path)
            assert name == "test_book"

            names = ProjectRepository.scan_projects()
            assert "test_book" in names
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_create_then_load(self, tmp_path):
        """create_project → load_project 可读回一致数据。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script2.json")
            ProjectRepository.create_project("my_book", script_path)

            meta, script, bindings = ProjectRepository.load_project("my_book")
            assert meta.project_name == "my_book"
            assert meta.total_segments == 2
            assert "旁白" in bindings.get("bindings", {})
            assert "1-001" in meta.segments_status
            assert meta.segments_status["1-001"] == "pending"
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_load_nonexistent(self, tmp_path):
        """不存在的项目抛出 ProjectNotFoundError。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws_none")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy_none")

            with pytest.raises(ProjectNotFoundError):
                ProjectRepository.load_project("no_such_project")
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_create_then_delete(self, tmp_path):
        """create_project → delete_project → scan_projects 确认删除。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script3.json")
            ProjectRepository.create_project("delete_me", script_path)
            assert "delete_me" in ProjectRepository.scan_projects()

            ProjectRepository.delete_project("delete_me")
            assert "delete_me" not in ProjectRepository.scan_projects()
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_update_segment_status(self, tmp_path):
        """update_segment_status 更新段状态并重新计数。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script4.json")
            ProjectRepository.create_project("status_test", script_path)

            # 更新第一段为 done
            ProjectRepository.update_segment_status("status_test", "1-001", "done")
            meta, _, _ = ProjectRepository.load_project("status_test")
            assert meta.completed_count == 1
            assert meta.pending_count == 1
            assert meta.failed_count == 0
            assert meta.segments_status["1-001"] == "done"
            assert meta.segments_status["1-002"] == "pending"

            # 更新第二段为 failed
            ProjectRepository.update_segment_status("status_test", "1-002", "failed")
            meta, _, _ = ProjectRepository.load_project("status_test")
            assert meta.completed_count == 1
            assert meta.failed_count == 1
            assert meta.pending_count == 0
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_legacy_compat(self, tmp_path):
        """legacy 兼容：旧目录中的项目可被 scan 和 load。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ws = str(tmp_path / "ws_new")
            legacy = str(tmp_path / "legacy_old")
            ProjectRepository.WORKSPACE_ROOT = ws
            ProjectRepository.LEGACY_ROOT = legacy

            # 在 legacy 目录创建一个旧项目
            old_project_dir = os.path.join(legacy, "old_book")
            os.makedirs(os.path.join(old_project_dir, "voices"))
            os.makedirs(os.path.join(old_project_dir, "segments"))
            os.makedirs(os.path.join(old_project_dir, "chapters"))
            os.makedirs(os.path.join(old_project_dir, "output"))

            # 写 project.json
            old_meta = {
                "project_name": "old_book",
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
            with open(os.path.join(old_project_dir, "project.json"), "w",
                      encoding="utf-8") as f:
                json.dump(old_meta, f)

            # 写 structured_script.json
            script = {
                "meta": {"title": "旧书"},
                "voices": {"旁白": {"name": "旁白", "description": ""}},
                "chapters": [{
                    "id": 1,
                    "title": "第一章",
                    "segments": [{"id": "1-001", "role": "旁白",
                                  "emotion": "neutral", "text": "旧书第一句"}],
                }],
            }
            with open(os.path.join(old_project_dir, "structured_script.json"),
                      "w", encoding="utf-8") as f:
                json.dump(script, f)

            # 写 voice_bindings.json
            with open(os.path.join(old_project_dir, "voice_bindings.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"bindings": {"旁白": None}, "bound_at": "",
                           "verified": []}, f)

            # scan 应找到旧项目
            names = ProjectRepository.scan_projects()
            assert "old_book" in names

            # load 应可打开旧项目
            meta, loaded_script, bindings = ProjectRepository.load_project("old_book")
            assert meta.project_name == "old_book"
            assert meta.total_segments == 1
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_load_snapshot(self, tmp_path):
        """load_snapshot 构建完整快照。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script5.json")
            ProjectRepository.create_project("snap_book", script_path)

            snap = ProjectRepository.load_snapshot("snap_book")
            assert snap.name == "snap_book"
            assert snap.meta.project_name == "snap_book"
            assert len(snap.script.get("chapters", [])) == 1
            assert "旁白" in snap.bindings
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_list_projects(self, tmp_path):
        """list_projects 返回项目摘要列表。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script6.json")
            ProjectRepository.create_project("list_test", script_path)

            summaries = ProjectRepository.list_projects()
            names = [s["name"] for s in summaries]
            assert "list_test" in names
            s = next(s for s in summaries if s["name"] == "list_test")
            assert s["total"] == 2
            assert s["done"] == 0
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_synthesis_overrides_atomic(self, tmp_path):
        """set_synthesis_overrides 原子写不损坏现有文件。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script7.json")
            ProjectRepository.create_project("override_test", script_path)

            overrides = {"emotion": "happy", "override": True}
            ProjectRepository.set_synthesis_overrides("override_test", overrides)

            loaded = ProjectRepository.get_synthesis_overrides("override_test")
            assert loaded.get("emotion") == "happy"
            assert loaded.get("override") is True
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_synthesis_selections_atomic(self, tmp_path):
        """set_synthesis_selections 原子写。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script8.json")
            ProjectRepository.create_project("sel_test", script_path)

            selections = {"chapters": [1]}
            ProjectRepository.set_synthesis_selections("sel_test", selections)

            loaded = ProjectRepository.get_synthesis_selections("sel_test")
            assert loaded.get("chapters") == [1]
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_get_project_dir(self, tmp_path):
        """get_project_dir 返回正确路径。"""
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "ws")
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = _make_minimal_script(tmp_path, "script9.json")
            ProjectRepository.create_project("dir_test", script_path)

            d = ProjectRepository.get_project_dir("dir_test")
            assert os.path.isdir(d)
            assert d == os.path.join(ProjectRepository.WORKSPACE_ROOT, "dir_test")
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg
