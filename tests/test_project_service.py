from lib import project_paths
"""ProjectService 单测（纯 Python，假 ffmpeg / 假引擎无关，仅用 lib.project_manager）。

沿用 ``test_queue_b7.py`` 的范式：用 ``tmp_path`` 作 ``WORKSPACE_ROOT``，验证
项目的创建 / 扫描 / 打开 / 剧本校验 / 角色绑定 / 存入音色库。无需 GPU / UI。
"""
import sys
import os
import json

import numpy as np
from scipy.io import wavfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.project import ProjectService  # noqa: E402
import lib.project_manager as pm  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402


SCRIPT = {
    "meta": {"title": "测试书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {
            "id": 1, "title": "一",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段", "emotion": "neutral"},
            ],
        }
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """临时 WORKSPACE_ROOT 下建一个 1 段 1 角色项目。"""
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    ProjectService.create_project("t1", str(sp))
    return str(tmp_path)


def _dummy(path, n=800):
    wavfile.write(path, 16000, np.zeros(n, dtype=np.int16))


def test_create_and_scan(project):
    assert "t1" in ProjectService.scan_projects()


def test_open_project_returns_tuple(project):
    meta, sd, bd = ProjectService.open_project("t1")
    assert meta.project_name == "t1"
    assert sd["meta"]["title"] == "测试书"
    assert "旁白" in bd["bindings"]


def test_validate_script_file_reports_errors(project, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"voices": {}, "chapters": []}, ensure_ascii=False),
                   encoding="utf-8")
    errs = ProjectService.validate_script_file(str(bad))
    # 缺角色 + 缺章节 -> 至少两条错误
    assert len(errs) >= 2


def test_validate_script_file_ok(project, tmp_path):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    assert ProjectService.validate_script_file(str(ok)) == []


def test_bind_voice_mutates_json_and_returns_path(project):
    d = pm.get_project_dir("t1")
    vo = os.path.join(project_paths.project_dir(d, "project_voices", create=True), "ref.wav")
    _dummy(vo)
    dest = ProjectService.bind_voice("t1", "旁白", vo)
    assert os.path.isfile(dest)
    with open(project_paths.project_file(d, "voice_bindings"), encoding="utf-8") as f:
        bd = json.load(f)
    assert bd["bindings"]["旁白"] == dest


def test_save_to_lib(project, tmp_path):
    rec = tmp_path / "rec.wav"
    _dummy(str(rec))
    dest = ProjectService.save_to_lib(str(rec), None, "温柔女声")
    assert os.path.isfile(dest)
    assert os.path.basename(dest).startswith("温柔女声")


def test_save_to_lib_empty_name_raises(project, tmp_path):
    rec = tmp_path / "rec.wav"
    _dummy(str(rec))
    with pytest.raises(ValueError):
        ProjectService.save_to_lib(str(rec), None, "")
