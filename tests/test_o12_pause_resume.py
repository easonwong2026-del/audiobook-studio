"""O12 集成：段落级暂停 / 恢复（手动驱动生成器，段边界挂起，不杀进行中进程）。

monkeypatch tts_engine.synthesize_segment 为带 sleep 的桩（仿 test_synthesis_service._fake_segment_slow）；
多段项目（仿现有 3~4 段 SCRIPT）。断言：
1) pause(state) -> status=="paused"、completed<total、暂停窗口内（0.7s）completed 不增长
   （段边界挂起，进行中段跑完后停，不杀进程）；
2) resume(state) -> status=="running" -> 最终 status=="done"、completed==total；
3) 暂停期间未反向写 meta：pm.open_project(project).meta.segments_status 仅含 done/pending，
   无 cancelled 等异常态（O12 不污染断点续跑真相源）。

无需 GPU / 真实模型；与现有 107 测试一致，全绿可复现。
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
from lib import progress as prog  # noqa: E402


SCRIPT = {
    "meta": {"title": "暂停书"},
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
    pm.create_project("pr", str(sp))
    d = pm.get_project_dir("pr")
    vo = os.path.join(d, "voices", "ref.wav")
    _dummy(vo)
    bp = os.path.join(d, "voice_bindings.json")
    with open(bp, encoding="utf-8") as f:
        bd = json.load(f)
    bd["bindings"]["旁白"] = vo
    with open(bp, "w", encoding="utf-8") as f:
        json.dump(bd, f, ensure_ascii=False, indent=2)
    return "pr"


def _dummy(path, n=800):
    wavfile.write(path, 16000, np.zeros(n, dtype=np.int16))


def _fake_segment_slow(output_path, **kwargs):
    time.sleep(0.15)
    if output_path:
        _dummy(output_path)


def _bindings(proj_dir: str) -> dict:
    return {"旁白": os.path.join(proj_dir, "voices", "ref.wav")}


def test_pause_resume_full_cycle(project, monkeypatch):
    monkeypatch.setattr(tts_engine, "synthesize_segment", _fake_segment_slow)

    SynthesisService.reset_executor()
    state = SynthesisState(task_id="t1", project=project)
    # O3 数据源：do_synthesis 同款初始化
    state.segment_states = prog.build_segment_states(project)
    SynthesisService.start(state, project, _bindings(pm.get_project_dir(project)))

    # 轮询直到至少完成 1 段且仍在运行
    deadline = time.time() + 15
    while (state.completed < 1 or state.status != "running") and time.time() < deadline:
        time.sleep(0.02)
    assert state.status == "running", state.log_lines

    # 暂停
    SynthesisService.pause(state)
    # 让可能正在进行的段定（段边界挂起）
    time.sleep(0.3)
    assert state.status == "paused", state.log_lines
    assert state.paused is True
    assert state.completed < state.total

    # 暂停窗口内 completed 不应增长（段边界挂起，不杀进行中进程）
    c1 = state.completed
    time.sleep(0.7)
    c2 = state.completed
    assert c1 == c2, f"暂停期间 completed 不应增长: {c1} -> {c2}"

    # meta 未被暂停污染：仅 done/pending，无 cancelled 异常态
    meta, _, _ = pm.open_project(project)
    for seg_id, st in meta.segments_status.items():
        assert st in ("done", "pending"), f"暂停期间 meta 出现非预期态 {seg_id}={st}"
    assert sum(1 for v in meta.segments_status.values() if v == "done") == state.completed

    # 恢复
    SynthesisService.resume(state)
    assert state.paused is False
    assert state.status == "running"

    # 跑到完成
    while state.status not in ("done", "cancelled", "error") and time.time() < deadline:
        time.sleep(0.05)
    assert state.status == "done", state.log_lines
    assert state.completed == state.total == 4


def test_segment_states_data_source_updates_during_run(project, monkeypatch):
    """O3 数据源：运行/完成段态经 cb_seg_state 写入 state.segment_states。"""
    monkeypatch.setattr(tts_engine, "synthesize_segment", _fake_segment_slow)
    SynthesisService.reset_executor()
    state = SynthesisState(task_id="t2", project=project)
    state.segment_states = prog.build_segment_states(project)
    SynthesisService.start(state, project, _bindings(pm.get_project_dir(project)))

    deadline = time.time() + 15
    while state.status not in ("done", "cancelled", "error") and time.time() < deadline:
        time.sleep(0.05)
    assert state.status == "done", state.log_lines
    # 所有段最终都应被标记 done（内存数据源与完成数一致）
    done_states = [s for s in state.segment_states if s["status"] == "done"]
    assert len(done_states) == state.total == 4
