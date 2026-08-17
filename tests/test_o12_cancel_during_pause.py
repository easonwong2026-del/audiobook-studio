from lib import project_paths
"""O12 集成：暂停态可取消（cancel 优先）。

桩 + 注入 state.paused=True（模拟用户已点暂停态）后调 cancel(state)，断言：
1) status=="cancelled"（worker 在暂停循环因 cancel 退出后由 _run 置终态）；
2) completed<total（暂停态可取消、cancel 优先）；
3) paused 标志保留（设计未要求取消时复位，断言不崩即可）。

worker 经 SynthesisService.start 仍在运行，验证「暂停中可取消」的端到端行为。
无需 GPU / 真实模型。
"""
import sys
import os
import json
import time

import numpy as np
from scipy.io import wavfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.project_manager as pm  # noqa: E402
import lib.tts_engine as tts_engine  # noqa: E402
from services.synthesis import SynthesisState, SynthesisService  # noqa: E402


SCRIPT = {
    "meta": {"title": "取消书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {
            "id": 1, "title": "一",
            "segments": [
                {"id": f"1-00{i}", "role": "旁白", "text": f"第{i}段内容", "emotion": "neutral"}
                for i in range(1, 5)  # 4 段
            ],
        }
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(tmp_path))
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    pm.create_project("cd", str(sp))
    d = pm.get_project_dir("cd")
    vo = os.path.join(project_paths.project_dir(d, "project_voices", create=True), "ref.wav")
    _dummy(vo)
    bp = project_paths.project_file(d, "voice_bindings")
    with open(bp, encoding="utf-8") as f:
        bd = json.load(f)
    bd["bindings"]["旁白"] = vo
    with open(bp, "w", encoding="utf-8") as f:
        json.dump(bd, f, ensure_ascii=False, indent=2)
    return "cd"


def _dummy(path, n=800):
    wavfile.write(path, 16000, np.zeros(n, dtype=np.int16))


def _fake_segment_slow(output_path, **kwargs):
    time.sleep(0.15)
    if output_path:
        _dummy(output_path)


def _bindings(proj_dir: str) -> dict:
    return {"旁白": os.path.join(project_paths.project_dir(proj_dir, "project_voices", create=True), "ref.wav")}


def test_cancel_during_pause(project, monkeypatch):
    monkeypatch.setattr(tts_engine, "synthesize_segment", _fake_segment_slow)
    SynthesisService.reset_executor()
    state = SynthesisState(task_id="t1", project=project)
    SynthesisService.start(state, project, _bindings(pm.get_project_dir(project)))

    # 轮询直到至少完成 1 段且仍在运行
    deadline = time.time() + 15
    while (state.completed < 1 or state.status != "running") and time.time() < deadline:
        time.sleep(0.02)
    assert state.status == "running", state.log_lines

    # 注入暂停态（模拟用户已点暂停），随后取消
    state.paused = True
    state.status = "paused"
    SynthesisService.cancel(state)

    # worker 处理取消：暂停循环因 cancel 退出 -> 置 cancelled 返回
    while state.status not in ("done", "cancelled", "error") and time.time() < deadline:
        time.sleep(0.05)

    assert state.cancel is True
    assert state.status == "cancelled", state.log_lines
    assert state.completed < state.total
    # paused 标志保留（设计未要求取消时复位）
    assert state.paused is True
