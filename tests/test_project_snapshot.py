"""单元测试：lib/snapshot.ProjectSnapshot + ProjectRepository.load_snapshot。

验证：
- build：bindings / role_categories 正确从 voice_bindings 的完整 dict 子键拆分；
- is_stale：关键文件 mtime 晚于 loaded_at 时返回 True（目录缺失亦为脏）；
- reload_if_stale：干净时返回自身，脏时重建新实例；
- ProjectRepository.load_snapshot：在临时目录造一个含 3 个 json 的假项目，产出等价快照。
"""
import sys
import os
import json

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib.snapshot import ProjectSnapshot  # noqa: E402
from lib.types import ProjectMeta  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402


SCRIPT = {
    "meta": {"title": "快照测试书"},
    "voices": {"旁白": {"description": "沉稳"}},
    "chapters": [
        {"id": 1, "title": "一",
         "segments": [{"id": "1-001", "role": "旁白", "text": "你好"}]},
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    script_path = tmp_path / "structured_script.json"
    script_path.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    ProjectRepository.create_project("snapbook", str(script_path))
    return "snapbook"


def test_build_splits_bindings_and_role_categories():
    bd = {
        "bindings": {"旁白": "/p/a.wav"},
        "role_categories": {"旁白": "未分类"},
        "bound_at": "t", "verified": [],
    }
    meta = ProjectMeta(project_name="x", created_at="", updated_at="")
    snap = ProjectSnapshot.build("x", meta, {"chapters": []}, bd, "/tmp/x")
    assert snap.bindings == {"旁白": "/p/a.wav"}
    assert snap.role_categories == {"旁白": "未分类"}
    assert snap.name == "x"
    assert snap.project_dir == "/tmp/x"
    assert snap.meta is meta


def test_build_handles_missing_subkeys():
    meta = ProjectMeta(project_name="x", created_at="", updated_at="")
    snap = ProjectSnapshot.build("x", meta, {}, {}, "/tmp/x")
    assert snap.bindings == {}
    assert snap.role_categories == {}


def test_is_stale_true_when_file_newer(tmp_path):
    bd = {"bindings": {}, "role_categories": {}}
    meta = ProjectMeta(project_name="x", created_at="", updated_at="")
    snap = ProjectSnapshot.build("x", meta, {}, bd, str(tmp_path))
    f = tmp_path / "project.json"
    f.write_text("{}", encoding="utf-8")
    os.utime(f, (snap.loaded_at + 10, snap.loaded_at + 10))
    assert snap.is_stale() is True


def test_is_stale_false_when_clean(tmp_path):
    bd = {"bindings": {}, "role_categories": {}}
    meta = ProjectMeta(project_name="x", created_at="", updated_at="")
    snap = ProjectSnapshot.build("x", meta, {}, bd, str(tmp_path))
    assert snap.is_stale() is False


def test_is_stale_true_when_dir_missing():
    meta = ProjectMeta(project_name="x", created_at="", updated_at="")
    snap = ProjectSnapshot.build("x", meta, {}, {}, "/no/such/dir")
    assert snap.is_stale() is True


def test_reload_if_stale_clean_returns_self(tmp_path):
    bd = {"bindings": {}, "role_categories": {}}
    meta = ProjectMeta(project_name="x", created_at="", updated_at="")
    snap = ProjectSnapshot.build("x", meta, {}, bd, str(tmp_path))
    assert snap.reload_if_stale() is snap


def test_reload_if_stale_dirty_rebuilds(project):
    from lib import project_paths
    snap = ProjectRepository.load_snapshot(project)
    p = project_paths.project_file(ProjectRepository.get_project_dir(project), "project_meta")
    t = snap.loaded_at + 10
    os.utime(p, (t, t))
    fresh = snap.reload_if_stale()
    assert fresh is not snap
    assert isinstance(fresh, ProjectSnapshot)
    assert fresh.name == project


def test_load_snapshot_equivalent(project):
    snap = ProjectRepository.load_snapshot(project)
    assert isinstance(snap, ProjectSnapshot)
    assert snap.name == project
    assert snap.project_dir == ProjectRepository.get_project_dir(project)
    # bindings / role_categories 从 voice_bindings.json 正确拆分
    assert snap.bindings == {"旁白": None}
    assert snap.role_categories == {}
