from lib import project_paths
"""单元测试：services.session.SessionState 的快照挂载方法 + 未 import gradio。

验证：
- set_snapshot / ensure_snapshot（干净时返回自身；脏时自动重载并同步会话 mirror）；
- invalidate_snapshot 清空快照；
- services/session.py 源码不得 import gradio（保证可在无 UI 环境单测）。
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
from services.session import SessionState  # noqa: E402


def _make_snap(name="s", project_dir="/tmp/x"):
    bd = {"bindings": {}, "role_categories": {}}
    return ProjectSnapshot.build(name, ProjectMeta(project_name=name, created_at="", updated_at=""), {}, bd, project_dir)


def _create_test_project(tmp_path, monkeypatch, name="rebook"):
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    source = tmp_path / f"{name}.json"
    source.write_text(json.dumps({
        "meta": {"title": "A"},
        "voices": {"旁白": {"description": "x"}},
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [{"id": "1-001", "role": "旁白", "text": "hello"}],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    ProjectRepository.create_project(name, str(source))
    return name


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
    assert ss.project == "rebook"
    assert ss.script == got.script
    assert ss.bindings == got.bindings
    assert ss.script is not got.script
    assert ss.bindings is not got.bindings


def test_stale_reload_synchronizes_script_and_bindings_without_alias(tmp_path, monkeypatch):
    name = _create_test_project(tmp_path, monkeypatch)
    snap = ProjectRepository.load_snapshot(name)
    ss = SessionState()
    ss.apply_project_snapshot(snap, project=name)
    assert ss.script == snap.script
    assert ss.bindings == snap.bindings
    assert ss.script is not snap.script
    assert ss.bindings is not snap.bindings

    project_dir = ProjectRepository.get_project_dir(name)
    script_path = project_paths.project_file(project_dir, "structured_script")
    script = json.loads(open(script_path, encoding="utf-8").read())
    script["meta"]["title"] = "B"
    with open(script_path, "w", encoding="utf-8") as handle:
        json.dump(script, handle, ensure_ascii=False)
    ProjectRepository.save_bindings(
        project_dir,
        {"bindings": {"旁白": "new.wav"}, "role_categories": {}},
    )
    changed_at = snap.loaded_at + 10
    for key in ("structured_script", "voice_bindings"):
        os.utime(project_paths.project_file(project_dir, key), (changed_at, changed_at))

    fresh = ss.ensure_snapshot()

    assert fresh is not None and fresh is not snap
    assert fresh.script["meta"]["title"] == "B"
    assert fresh.bindings["旁白"] == "new.wav"
    assert ss.project == name
    assert ss.project_snapshot is fresh
    assert ss.script == fresh.script
    assert ss.bindings == fresh.bindings
    assert ss.script["meta"]["title"] == "B"
    assert ss.bindings["旁白"] == "new.wav"
    assert ss.script is not fresh.script
    assert ss.bindings is not fresh.bindings


def test_snapshot_identity_mismatch_cannot_serve_opened_project(tmp_path):
    snapshot = _make_snap("A", str(tmp_path))
    ss = SessionState(project="B", script={"meta": {"title": "B"}}, bindings={"旁白": "b.wav"})
    ss.set_snapshot(snapshot)

    assert ss.ensure_snapshot() is None
    assert ss.project == "B"
    assert ss.project_snapshot is None
    assert ss.script == {"meta": {"title": "B"}}
    assert ss.bindings == {"旁白": "b.wav"}


def test_apply_snapshot_identity_mismatch_is_rejected_without_partial_update(tmp_path):
    old = _make_snap("B", str(tmp_path))
    incoming = _make_snap("A", str(tmp_path))
    ss = SessionState()
    ss.apply_project_snapshot(old)
    before = (ss.project, ss.project_snapshot, ss.script, ss.bindings)

    with pytest.raises(ValueError, match="Snapshot identity"):
        ss.apply_project_snapshot(incoming, project="B")

    assert (ss.project, ss.project_snapshot, ss.script, ss.bindings) == before
    assert ss.project_snapshot is old
    assert ss.script is not old.script
    assert ss.bindings is not old.bindings


def test_external_delete_clears_stale_opened_payload(tmp_path, monkeypatch):
    name = _create_test_project(tmp_path, monkeypatch, name="delete-me")
    snapshot = ProjectRepository.load_snapshot(name)
    ss = SessionState()
    ss.apply_project_snapshot(snapshot, project=name)

    ProjectRepository.delete_project(name)

    assert ss.ensure_snapshot() is None
    assert ss.project is None
    assert ss.project_snapshot is None
    assert ss.script is None
    assert ss.bindings == {}


def test_data_root_change_cannot_create_mixed_root_snapshot(tmp_path, monkeypatch):
    root_one = tmp_path / "root-one"
    root_two = tmp_path / "root-two"
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(root_one / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(root_one / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    source_one = tmp_path / "one.json"
    source_one.write_text(json.dumps({
        "meta": {"title": "root one"},
        "voices": {"旁白": {}},
        "chapters": [{"id": 1, "segments": [
            {"id": "1-001", "role": "旁白", "text": "one"},
        ]}],
    }, ensure_ascii=False), encoding="utf-8")
    ProjectRepository.create_project("same", str(source_one))
    snapshot = ProjectRepository.load_snapshot("same")
    ss = SessionState()
    ss.apply_project_snapshot(snapshot, project="same")

    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(root_two / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(root_two / "legacy"))
    source_two = tmp_path / "two.json"
    source_two.write_text(json.dumps({
        "meta": {"title": "root two"},
        "voices": {"旁白": {}},
        "chapters": [{"id": 1, "segments": [
            {"id": "1-001", "role": "旁白", "text": "two"},
        ]}],
    }, ensure_ascii=False), encoding="utf-8")
    ProjectRepository.create_project("same", str(source_two))

    assert ss.ensure_snapshot() is None
    assert ss.project_snapshot is None

    import app

    rebuilt = app._snap(ss)
    assert rebuilt is not None
    assert rebuilt.name == "same"
    assert rebuilt.script["meta"]["title"] == "root two"
    assert ss.script["meta"]["title"] == "root two"
    assert os.path.realpath(rebuilt.project_dir).startswith(os.path.realpath(str(root_two)))
    assert ss.script is not rebuilt.script
    assert ss.bindings is not rebuilt.bindings


def test_invalidate_snapshot_keeps_mirror_until_next_internal_rehydrate(tmp_path, monkeypatch):
    name = _create_test_project(tmp_path, monkeypatch, name="invalidate-book")
    snapshot = ProjectRepository.load_snapshot(name)
    ss = SessionState()
    ss.apply_project_snapshot(snapshot, project=name)
    old_script = ss.script
    old_bindings = ss.bindings

    ss.invalidate_snapshot()

    assert ss.project == name
    assert ss.project_snapshot is None
    assert ss.script is old_script
    assert ss.bindings is old_bindings


def test_apply_project_snapshot_replaces_a_with_b_without_old_payload():
    snapshot_a = ProjectSnapshot.build(
        "A",
        ProjectMeta(project_name="A", created_at="", updated_at=""),
        {"meta": {"title": "A"}},
        {"bindings": {"旁白": "a.wav"}},
        "/tmp/a",
    )
    snapshot_b = ProjectSnapshot.build(
        "B",
        ProjectMeta(project_name="B", created_at="", updated_at=""),
        {"meta": {"title": "B"}},
        {"bindings": {"旁白": "b.wav"}},
        "/tmp/b",
    )
    ss = SessionState()

    ss.apply_project_snapshot(snapshot_a)
    ss.apply_project_snapshot(snapshot_b)

    assert ss.project == "B"
    assert ss.project_snapshot is snapshot_b
    assert ss.script == snapshot_b.script
    assert ss.bindings == snapshot_b.bindings
    assert ss.script["meta"]["title"] != "A"
    assert ss.bindings["旁白"] != "a.wav"
    assert ss.script is not snapshot_b.script
    assert ss.bindings is not snapshot_b.bindings


def test_ui_voice_binding_refreshes_repository_snapshot_and_mirror(tmp_path, monkeypatch):
    name = _create_test_project(tmp_path, monkeypatch, name="voice-book")
    snapshot = ProjectRepository.load_snapshot(name)
    ss = SessionState()
    ss.apply_project_snapshot(snapshot, project=name)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF")

    import app

    app.bind_voice("旁白", str(reference), None, ss)

    durable = ProjectRepository.load_project(name)[2]["bindings"]
    current = ss.project_snapshot
    assert current is not None
    assert durable["旁白"] == current.bindings["旁白"]
    assert ss.bindings == current.bindings
    assert ss.script == current.script
    assert ss.bindings is not current.bindings
    assert ss.script is not current.script


def test_invalidate_snapshot():
    ss = SessionState()
    ss.set_snapshot(_make_snap())
    assert ss.project_snapshot is not None
    ss.invalidate_snapshot()
    assert ss.project_snapshot is None
