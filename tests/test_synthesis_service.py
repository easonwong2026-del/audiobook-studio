from lib import project_paths
"""SynthesisService 单测：后台队列 + 进度推进 + 单段协作取消。

直接 monkeypatch ``tts_engine.synthesize_segment``（``lib.queue.synthesize_project``
真正调用的函数）为写哑 wav 的桩，无需 GPU / torch / ffmpeg，且本文件**自包含**
（不依赖其它测试文件在集合期注入的全局 ``sys.modules['torch']``，可单独运行）。验证：
1) 完整跑完 -> status=done、progress=1.0、completed==total、日志填充；
2) 跑到一半置 cancel -> status=cancelled、completed 在段边界停止（< total）。
"""
import sys
import os
import json
import threading
import time

import numpy as np
from scipy.io import wavfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.tts_engine as tts_engine  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402
from services.synthesis import SynthesisState, SynthesisService  # noqa: E402


def _fake_segment_fast(output_path, **kwargs):
    """即时桩：写出 output_path 哑 wav，不 sleep（用于「跑到完成」测试）。"""
    if output_path:
        _dummy(output_path)


SCRIPT = {
    "meta": {"title": "合成书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {
            "id": 1, "title": "一",
            "segments": [
                {"id": f"1-00{i}", "role": "旁白", "text": f"第{i}段内容",
                 "emotion": "neutral"}
                for i in range(1, 4)
            ],
        }
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    ProjectRepository.create_project("syn", str(sp))
    d = ProjectRepository.get_project_dir("syn")
    vo = os.path.join(project_paths.project_dir(d, "project_voices", create=True), "ref.wav")
    _dummy(vo)
    bp = project_paths.project_file(d, "voice_bindings")
    with open(bp, encoding="utf-8") as f:
        bd = json.load(f)
    bd["bindings"]["旁白"] = vo
    with open(bp, "w", encoding="utf-8") as f:
        json.dump(bd, f, ensure_ascii=False, indent=2)
    return "syn"


def _bindings(proj_dir: str) -> dict:
    return {"旁白": os.path.join(project_paths.project_dir(proj_dir, "project_voices", create=True), "ref.wav")}


def _dummy(path, n=800):
    wavfile.write(path, 16000, np.zeros(n, dtype=np.int16))


def test_synthesis_runs_to_completion(project, monkeypatch):
    monkeypatch.setattr(tts_engine, "synthesize_segment", _fake_segment_fast)
    cleanup_reasons = []
    monkeypatch.setattr(
        tts_engine,
        "empty_cache",
        lambda reason="manual": cleanup_reasons.append(reason),
    )
    SynthesisService.reset_executor()  # 测试隔离：新线程池

    state = SynthesisState(task_id="t1", project=project)
    SynthesisService.start(state, project, _bindings(ProjectRepository.get_project_dir(project)))

    deadline = time.time() + 15
    while state.status not in ("done", "cancelled", "error") and time.time() < deadline:
        time.sleep(0.02)

    assert state.status == "done", state.log_lines
    assert state.progress == 1.0
    assert state.completed == state.total == 3
    assert any("✅" in line for line in state.log_lines)
    while state.future is not None and not state.future.done():
        time.sleep(0.01)
    assert cleanup_reasons == ["task_end"]


def test_synthesis_cancel_at_segment_boundary(project, monkeypatch):
    segment_release = threading.Event()
    segment_calls = 0

    def _fake_segment_until_cancel(output_path, **kwargs):
        nonlocal segment_calls
        segment_calls += 1
        if segment_calls > 1:
            segment_release.wait(timeout=15)
        if output_path:
            _dummy(output_path)

    monkeypatch.setattr(tts_engine, "synthesize_segment", _fake_segment_until_cancel)
    cleanup_reasons = []
    monkeypatch.setattr(
        tts_engine,
        "empty_cache",
        lambda reason="manual": cleanup_reasons.append(reason),
    )
    SynthesisService.reset_executor()  # 测试隔离：新线程池

    state = SynthesisState(task_id="t2", project=project)
    SynthesisService.start(state, project, _bindings(ProjectRepository.get_project_dir(project)))

    # 轮询直到至少完成 1 段（不提前取消），随后置 cancel
    deadline = time.time() + 15
    while (state.completed < 1
           and state.status not in ("done", "cancelled", "error")
           and time.time() < deadline):
        time.sleep(0.01)
    SynthesisService.cancel(state)
    segment_release.set()

    while state.status not in ("done", "cancelled", "error") and time.time() < deadline:
        time.sleep(0.02)

    assert state.status == "cancelled", state.log_lines
    assert state.completed >= 1
    # 取消只在段边界生效：未完成全部段
    assert state.completed < state.total
    while state.future is not None and not state.future.done():
        time.sleep(0.01)
    assert cleanup_reasons == ["cancel"]
