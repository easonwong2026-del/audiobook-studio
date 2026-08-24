from lib import project_paths
"""单元测试：services.session.SessionState 的快照挂载方法 + 未 import gradio。

验证：
- set_snapshot / ensure_snapshot（干净时返回自身；脏时自动重载并写回会话态）；
- invalidate_snapshot 清空快照；
- services/session.py 源码不得 import gradio（保证可在无 UI 环境单测）。
"""
import sys
import os
import json


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib.snapshot import ProjectSnapshot  # noqa: E402
from lib.types import ProjectMeta  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402
from services.session import SessionState  # noqa: E402


def _make_snap(name="s", project_dir="/tmp/x"):
    bd = {"bindings": {}, "role_categories": {}}
    return ProjectSnapshot.build(name, ProjectMeta(project_name=name, created_at="", updated_at=""), {}, bd, project_dir)


def test_session_py_does_not_import_gradio():
    path = os.path.join(PROJECT_ROOT, "services", "session.py")
    text = open(path, encoding="utf-8").read()
    assert "import gradio" not in text, "services/session.py 不得 import gradio"
    assert "from gradio" not in text, "services/session.py 不得 from gradio import"


def test_set_and_ensure_snapshot_clean(tmp_path):
    ss = SessionState()
    # 用真实存在的临时目录作为 project_dir，且不写入关键 JSON -> is_stale() 为 False
    snap = _make_snap(project_dir=str(tmp_path))
    ss.set_snapshot(snap)
    assert ss.project_snapshot is snap
    got = ss.ensure_snapshot()
    assert got is snap  # 干净时应返回自身（不重建）


def test_ensure_snapshot_rebuilds_when_dirty(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    script_path = tmp_path / "structured_script.json"
    script_path.write_text(json.dumps({
        "meta": {"title": "t"},
        "voices": {"旁白": {"description": "x"}},
        "chapters": [{"id": 1, "title": "一",
                      "segments": [{"id": "1-001", "role": "旁白", "text": "hi"}]}],
    }, ensure_ascii=False), encoding="utf-8")
    ProjectRepository.create_project("rebook", str(script_path))

    ss = SessionState()
    ss.set_project("rebook", None, {})
    snap = ProjectRepository.load_snapshot("rebook")
    ss.set_snapshot(snap)
    # 让 project.json 变新 → 脏
    p = project_paths.project_file(ProjectRepository.get_project_dir("rebook"), "project_meta")
    t = snap.loaded_at + 10
    os.utime(p, (t, t))
    got = ss.ensure_snapshot()
    assert got is not snap
    assert got.name == "rebook"
    assert ss.project_snapshot is got  # 写回会话态


def test_invalidate_snapshot():
    ss = SessionState()
    ss.set_snapshot(_make_snap())
    assert ss.project_snapshot is not None
    ss.invalidate_snapshot()
    assert ss.project_snapshot is None
