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


# ═══════════════════════════════════════════════════════════════
# 临时目录排除与项目扫描验证
# ═══════════════════════════════════════════════════════════════

class TestProjectScanFiltering:
    def test_tmp_dir_not_in_scan(self, tmp_path, monkeypatch):
        """.tmp_ 目录即使包含项目文件也不出现在扫描结果中。"""
        from repositories.project_repo import ProjectRepository
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ws = str(tmp_path / "projects")
            ProjectRepository.WORKSPACE_ROOT = ws
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            # 创建看似合法的 .tmp_ 目录
            tmp_proj = os.path.join(ws, ".tmp_demo_abc")
            os.makedirs(os.path.join(tmp_proj, "voices"))
            for fname in ("project.json", "structured_script.json", "voice_bindings.json"):
                with open(os.path.join(tmp_proj, fname), "w") as f:
                    f.write("{}")

            # 创建一个真正合法的项目
            real_proj = os.path.join(ws, "real_book")
            os.makedirs(os.path.join(real_proj, "voices"))
            for fname in ("project.json", "structured_script.json", "voice_bindings.json"):
                with open(os.path.join(real_proj, fname), "w") as f:
                    f.write("{}")

            names = ProjectRepository.scan_projects()
            assert ".tmp_demo_abc" not in names
            assert "real_book" in names
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_plain_directory_not_in_scan(self, tmp_path, monkeypatch):
        """无项目文件的普通目录不出现在扫描结果中。"""
        from repositories.project_repo import ProjectRepository
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ws = str(tmp_path / "projects")
            ProjectRepository.WORKSPACE_ROOT = ws
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            # 普通目录，无项目文件
            os.makedirs(os.path.join(ws, "random_folder"))
            os.makedirs(os.path.join(ws, "cache"))

            names = ProjectRepository.scan_projects()
            assert "random_folder" not in names
            assert "cache" not in names
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    @pytest.mark.parametrize("missing_file", [
        "project.json",
        "structured_script.json",
        "voice_bindings.json",
    ])
    def test_missing_required_file(self, tmp_path, monkeypatch, missing_file):
        """缺少任一必需项目文件时不被识别。"""
        from repositories.project_repo import ProjectRepository
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ws = str(tmp_path / "projects")
            ProjectRepository.WORKSPACE_ROOT = ws
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            proj = os.path.join(ws, "incomplete")
            os.makedirs(os.path.join(proj, "voices"))
            for fname in ("project.json", "structured_script.json", "voice_bindings.json"):
                if fname != missing_file:
                    with open(os.path.join(proj, fname), "w") as f:
                        f.write("{}")

            names = ProjectRepository.scan_projects()
            assert "incomplete" not in names
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg


class TestTempDirCleanup:
    def test_cleanup_stale_temp_dirs(self, tmp_path, monkeypatch):
        """过期临时目录被清理，返回正确删除数量。"""
        from repositories.project_repo import ProjectRepository
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ws = str(tmp_path / "projects")
            ProjectRepository.WORKSPACE_ROOT = ws
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            # 创建过期的临时目录（修改时间为 7 天前）
            stale_dir = os.path.join(ws, ".tmp_stale_xxx")
            os.makedirs(stale_dir)
            stale_time = time.time() - 7 * 86400
            os.utime(stale_dir, (stale_time, stale_time))

            # 创建合法项目
            valid_dir = os.path.join(ws, "valid_book")
            os.makedirs(os.path.join(valid_dir, "voices"))
            for fname in ("project.json", "structured_script.json", "voice_bindings.json"):
                with open(os.path.join(valid_dir, fname), "w") as f:
                    f.write("{}")

            removed = ProjectRepository.cleanup_stale_project_temp_dirs(max_age_seconds=3600)
            assert removed == 1, f"应删除 1 个过期目录，实际 {removed}"
            assert not os.path.isdir(stale_dir), "过期临时目录应已被删除"
            assert os.path.isdir(valid_dir), "合法项目不受影响"

            # 第二次调用时不再有可删除目录
            assert ProjectRepository.cleanup_stale_project_temp_dirs(max_age_seconds=3600) == 0
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_fresh_temp_dir_not_cleaned(self, tmp_path, monkeypatch):
        """新创建的 .tmp_ 目录不会被默认清理。"""
        from repositories.project_repo import ProjectRepository
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ws = str(tmp_path / "projects")
            ProjectRepository.WORKSPACE_ROOT = ws
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            fresh_dir = os.path.join(ws, ".tmp_fresh_yyy")
            os.makedirs(fresh_dir)
            # 不修改时间，保持当前时间

            removed = ProjectRepository.cleanup_stale_project_temp_dirs(max_age_seconds=86400)
            assert removed == 0
            assert os.path.isdir(fresh_dir), "新临时目录不应被清理"
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg

    def test_no_tmp_after_successful_create(self, tmp_path, monkeypatch):
        """成功创建项目后不残留 .tmp_ 目录。"""
        from repositories.project_repo import ProjectRepository
        orig_ws = ProjectRepository.WORKSPACE_ROOT
        orig_lg = ProjectRepository.LEGACY_ROOT
        try:
            ws = str(tmp_path / "projects")
            ProjectRepository.WORKSPACE_ROOT = ws
            ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")

            script_path = str(tmp_path / "script.json")
            script = {
                "meta": {"title": "T", "total_segments": 1},
                "voices": {"旁白": {}},
                "chapters": [{"id": 1, "title": "第一章",
                              "segments": [{"id": "1-001", "speaker": "旁白",
                                            "role": "旁白", "text": "开始",
                                            "emotion": "neutral", "emotion_strength": 0.4,
                                            "emo_alpha": 0.4, "speech_rate": 1.0,
                                            "delivery": {"speed": 1.0, "pitch": 0,
                                                         "intensity": 0.4, "breath": "light"},
                                            "pause_before": 0, "pause_after": 600, "pauses": []}]}],
            }
            with open(script_path, "w", encoding="utf-8") as f:
                json.dump(script, f, ensure_ascii=False)

            ProjectRepository.create_project("clean_proj", script_path)
            tmp_names = [d for d in os.listdir(ws) if d.startswith(".tmp_")]
            assert len(tmp_names) == 0, f"残留临时目录: {tmp_names}"
        finally:
            ProjectRepository.WORKSPACE_ROOT = orig_ws
            ProjectRepository.LEGACY_ROOT = orig_lg


# ═══════════════════════════════════════════════════════════════
# 原子创建失败清理测试
# ═══════════════════════════════════════════════════════════════

class TestAtomicCreateFailure:
    SCRIPT = {
        "meta": {"title": "T", "total_segments": 2},
        "voices": {"旁白": {}, "小明": {}},
        "chapters": [{"id": 1, "title": "第一章",
                      "segments": [
                          {"id": "1-001", "speaker": "旁白", "role": "旁白",
                           "text": "开始", "emotion": "neutral", "emotion_strength": 0.4,
                           "emo_alpha": 0.4, "speech_rate": 1.0,
                           "delivery": {"speed": 1.0, "pitch": 0, "intensity": 0.4,
                                        "breath": "light"},
                           "pause_before": 0, "pause_after": 600, "pauses": []},
                          {"id": "1-002", "speaker": "小明", "role": "小明",
                           "text": "你好", "emotion": "happy", "emotion_strength": 0.6,
                           "emo_alpha": 0.6, "speech_rate": 1.05,
                           "delivery": {"speed": 1.05, "pitch": 0, "intensity": 0.6,
                                        "breath": "light"},
                           "pause_before": 200, "pause_after": 800, "pauses": []},
                      ]}],
    }

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        from repositories.project_repo import ProjectRepository
        self._orig_ws = ProjectRepository.WORKSPACE_ROOT
        self._orig_lg = ProjectRepository.LEGACY_ROOT
        self._orig_init = ProjectRepository._INITIALIZED
        self.ws = str(tmp_path / "projects")
        ProjectRepository.WORKSPACE_ROOT = self.ws
        ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
        ProjectRepository._INITIALIZED = True  # prevent ensure_roots from overriding
        os.makedirs(self.ws, exist_ok=True)
        self.script_path = str(tmp_path / "script.json")
        with open(self.script_path, "w", encoding="utf-8") as f:
            json.dump(self.SCRIPT, f, ensure_ascii=False)
        yield
        ProjectRepository.WORKSPACE_ROOT = self._orig_ws
        ProjectRepository.LEGACY_ROOT = self._orig_lg
        ProjectRepository._INITIALIZED = self._orig_init

    def _assert_no_project_left(self, name):
        from repositories.project_repo import ProjectRepository
        project_dir = os.path.join(ProjectRepository.WORKSPACE_ROOT, name)
        assert not os.path.isdir(project_dir), f"项目目录不应存在：{project_dir}"
        # 验证无残留 .tmp_ 目录
        if ProjectRepository.WORKSPACE_ROOT and os.path.isdir(ProjectRepository.WORKSPACE_ROOT):
            tmp_dirs = [d for d in os.listdir(ProjectRepository.WORKSPACE_ROOT)
                        if d.startswith(f".tmp_{name}_")]
            assert len(tmp_dirs) == 0, f"残留临时目录：{tmp_dirs}"

    def _assert_valid_project(self, name):
        from repositories.project_repo import ProjectRepository
        project_dir = os.path.join(ProjectRepository.WORKSPACE_ROOT, name)
        assert os.path.isdir(project_dir)
        for marker in ("project.json", "structured_script.json", "voice_bindings.json"):
            assert os.path.isfile(os.path.join(project_dir, marker))
        for sub in ("voices", "segments", "chapters", "output"):
            assert os.path.isdir(os.path.join(project_dir, sub))

    def test_copy2_failure(self, monkeypatch):
        """shutil.copy2 失败时清理临时目录，原异常继续抛出。"""
        from repositories.project_repo import ProjectRepository
        import shutil
        monkeypatch.setattr(shutil, "copy2",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError("copy2 failed")))
        with pytest.raises(OSError):
            ProjectRepository.create_project("copy_fail", self.script_path)
        self._assert_no_project_left("copy_fail")
        monkeypatch.undo()
        ProjectRepository.create_project("copy_fail", self.script_path)
        self._assert_valid_project("copy_fail")

    def test_save_bindings_failure(self, monkeypatch):
        """save_bindings 失败时清理临时目录。"""
        from repositories.project_repo import ProjectRepository
        monkeypatch.setattr(ProjectRepository, "save_bindings",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError("bindings failed")))
        with pytest.raises(OSError):
            ProjectRepository.create_project("bind_fail", self.script_path)
        self._assert_no_project_left("bind_fail")
        monkeypatch.undo()
        ProjectRepository.create_project("bind_fail", self.script_path)
        self._assert_valid_project("bind_fail")

    def test_save_meta_failure(self, monkeypatch):
        """_save_meta 失败时清理临时目录。"""
        from repositories.project_repo import ProjectRepository
        monkeypatch.setattr(ProjectRepository, "_save_meta",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError("meta failed")))
        with pytest.raises(OSError):
            ProjectRepository.create_project("meta_fail", self.script_path)
        self._assert_no_project_left("meta_fail")
        monkeypatch.undo()
        ProjectRepository.create_project("meta_fail", self.script_path)
        self._assert_valid_project("meta_fail")

    def test_os_replace_failure(self, monkeypatch):
        """os.replace 失败时清理临时目录。"""
        from repositories.project_repo import ProjectRepository
        import repositories._atomic as atomic_mod
        call_count = [0]
        orig_replace = os.replace
        def counting_replace(src, dst):
            call_count[0] += 1
            # Let the first N calls (file-level atomic writes) succeed,
            # fail on the final directory-level os.replace
            if call_count[0] >= 3:
                raise OSError("final replace failed")
            return orig_replace(src, dst)

        monkeypatch.setattr(os, "replace", counting_replace)
        from repositories.exceptions import AtomicWriteError
        with pytest.raises((OSError, AtomicWriteError)):
            ProjectRepository.create_project("replace_fail", self.script_path)
        self._assert_no_project_left("replace_fail")
        # Retry with working os.replace
        monkeypatch.undo()
        ProjectRepository.create_project("replace_fail", self.script_path)
        self._assert_valid_project("replace_fail")

    def test_duplicate_name_does_not_create_temp(self, monkeypatch):
        """项目已存在时直接抛出，不创建临时目录。"""
        from repositories.project_repo import ProjectRepository
        ProjectRepository.create_project("dup_test", self.script_path)
        with pytest.raises(FileExistsError):
            ProjectRepository.create_project("dup_test", self.script_path)
        # 不应有残留临时目录
        ws_dir = ProjectRepository.WORKSPACE_ROOT
        if ws_dir and os.path.isdir(ws_dir):
            tmp_dirs = [d for d in os.listdir(ws_dir) if d.startswith(".tmp_")]
            assert len(tmp_dirs) == 0, f"重复创建后残留临时目录：{tmp_dirs}"


class TestProjectSlotInspection:
    @pytest.fixture(autouse=True)
    def _isolated_roots(self, tmp_path):
        self.original = (
            ProjectRepository.WORKSPACE_ROOT,
            ProjectRepository.LEGACY_ROOT,
            ProjectRepository._INITIALIZED,
        )
        self.workspace = tmp_path / "data" / "projects"
        self.legacy = tmp_path / "legacy"
        self.workspace.mkdir(parents=True)
        self.legacy.mkdir()
        ProjectRepository.WORKSPACE_ROOT = str(self.workspace)
        ProjectRepository.LEGACY_ROOT = str(self.legacy)
        ProjectRepository._INITIALIZED = True
        self.script_path = _make_minimal_script(tmp_path, "slot_script.json")
        yield
        (
            ProjectRepository.WORKSPACE_ROOT,
            ProjectRepository.LEGACY_ROOT,
            ProjectRepository._INITIALIZED,
        ) = self.original

    def test_available_and_valid_slots(self):
        assert ProjectRepository.inspect_project_slot("可用项目").status == "available"
        ProjectRepository.create_project("完整项目", self.script_path)
        inspection = ProjectRepository.inspect_project_slot("完整项目")
        assert inspection.status == "valid"
        assert inspection.location == "workspace"

    def test_incomplete_and_corrupted_slots_are_visible(self):
        incomplete = self.workspace / "残留"
        incomplete.mkdir()
        (incomplete / "project.json").write_text("{}", encoding="utf-8")
        corrupted = self.workspace / "损坏"
        corrupted.mkdir()
        for marker in ("project.json", "structured_script.json", "voice_bindings.json"):
            (corrupted / marker).write_text("{bad", encoding="utf-8")

        first = ProjectRepository.inspect_project_slot("残留")
        second = ProjectRepository.inspect_project_slot("损坏")
        assert first.status == "incomplete"
        assert "structured_script.json" in first.missing_files
        assert second.status == "corrupted"
        assert {item.name for item in ProjectRepository.list_abnormal_projects()} == {
            "残留",
            "损坏",
        }
        assert "残留" not in ProjectRepository.scan_projects()

    def test_temporary_and_legacy_statuses(self):
        (self.workspace / ".tmp_书稿_abc").mkdir()
        legacy_project = self.legacy / "旧项目"
        legacy_project.mkdir()
        assert (
            ProjectRepository.inspect_project_slot(".tmp_书稿_abc").status
            == "temporary"
        )
        legacy = ProjectRepository.inspect_project_slot("旧项目")
        assert legacy.status == "legacy"
        assert legacy.location == "legacy"

    def test_archive_orphan_restores_name_and_protects_valid_and_legacy(self):
        orphan = self.workspace / "待恢复"
        orphan.mkdir()
        archived = ProjectRepository.archive_orphan_project("待恢复")
        assert os.path.isdir(archived)
        assert ".trash" in archived
        assert ProjectRepository.inspect_project_slot("待恢复").status == "available"

        ProjectRepository.create_project("合法", self.script_path)
        with pytest.raises(ValueError, match="仅可归档"):
            ProjectRepository.archive_orphan_project("合法")

        (self.legacy / "旧版").mkdir()
        with pytest.raises(ValueError, match="仅可归档"):
            ProjectRepository.archive_orphan_project("旧版")

    def test_trash_directory_never_appears_in_project_scan(self):
        trash = self.workspace / ".trash"
        trash.mkdir()
        for marker in ("project.json", "structured_script.json", "voice_bindings.json"):
            (trash / marker).write_text("{}", encoding="utf-8")
        assert ".trash" not in ProjectRepository.scan_projects()
