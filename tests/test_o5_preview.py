"""O5 合成前分段预览 + 勾选透传 + 跳过逻辑：纯函数 + FakeEngine 集成（无 GPU）。

验证（设计 §6 O5 / §12.3）：
- prog.build_preview_rows(project) 行数=剧本段数、列=章节/段落/角色/文本；
- pm.get/set_synthesis_selections 读写一致、缺文件返回 {}；
- prog.build_segment_states(project, selected_chapters) 未选章段标 skipped；
- queue.synthesize_project(..., selected_chapters=[选中章]) 用 FakeEngine 桩：
  未选章段 meta.segments_status 标 skipped、选章段被合成（eng.calls==选中段数）。

用 tmp_path + monkeypatch；synthesize 走 b7 同款 FakeEngine（自包含注入假 torch）。
全部基于「全新 / 全 pending 项目」构造，避免依赖 done->skipped 降级（不在本次范围）。
"""
import sys
import os
import json
import types

import numpy as np
from scipy.io import wavfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 自包含注入假 torch（仿 test_queue_b7.py），无 GPU / 无真实 torch
class _FakeTorchTensor:
    pass


_fake_torch = types.SimpleNamespace(
    cuda=types.SimpleNamespace(empty_cache=lambda: None, OutOfMemoryError=RuntimeError),
    Tensor=_FakeTorchTensor,
)
sys.modules.setdefault("torch", _fake_torch)

import lib.project_manager as pm  # noqa: E402
from lib import progress as prog  # noqa: E402
from lib import project_paths  # noqa: E402
import lib.queue as synth_queue  # noqa: E402
import lib.tts_engine as tts_engine  # noqa: E402


SCRIPT = {
    "meta": {"title": "O5书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {"id": 1, "title": "第一章",
         "segments": [
             {"id": "1-001", "role": "旁白", "text": "第一段内容用于预览", "emotion": "neutral"},
             {"id": "1-002", "role": "旁白", "text": "第二段内容", "emotion": "neutral"},
         ]},
        {"id": 2, "title": "第二章",
         "segments": [
             {"id": "2-001", "role": "旁白", "text": "第三段内容", "emotion": "neutral"},
         ]},
    ],
}


def _dummy_wav(path, n=800):
    wavfile.write(path, 16000, np.zeros(n, dtype=np.int16))


class _FakeEngine:
    """假 IndexTTS2：仅记录 infer 调用次数并写出 output_path。"""
    def __init__(self):
        self.calls = 0

    def infer(self, spk_audio_prompt, text, output_path, use_emo_text, emo_text,
              emo_alpha, max_text_tokens_per_segment, speed=1.0, pinyin_hints=None):
        self.calls += 1
        _dummy_wav(output_path)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """用临时目录作 WORKSPACE_ROOT，建一个 3 段 1 角色项目并绑定参考音频。"""
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(tmp_path))
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    pm.create_project("o5", str(sp))
    d = pm.get_project_dir("o5")
    vo = os.path.join(project_paths.project_dir(d, "voices", create=True), "ref.wav")
    _dummy_wav(vo)
    bp = os.path.join(d, "voice_bindings.json")
    with open(bp, encoding="utf-8") as f:
        bd = json.load(f)
    bd["bindings"]["旁白"] = vo
    with open(bp, "w", encoding="utf-8") as f:
        json.dump(bd, f, ensure_ascii=False, indent=2)
    return "o5"


def test_build_preview_rows_matches_script(project):
    rows = prog.build_preview_rows(project)
    # 3 段 -> 3 行
    assert len(rows) == 3, f"预览行数应等于剧本段数，实际 {len(rows)}"
    for r in rows:
        assert len(r) == 4, f"每行应 4 列（章节/段落/角色/文本），实际 {len(r)}: {r}"
    # 章节列显示「第N章 标题」
    assert rows[0][0] == "第1章 第一章"
    # 段落列
    ids = [r[1] for r in rows]
    assert ids == ["1-001", "1-002", "2-001"], ids
    # 角色列
    assert all(r[2] == "旁白" for r in rows)
    # 文本列
    assert rows[0][3] == "第一段内容用于预览"


def test_get_set_synthesis_selections_roundtrip(project):
    pm.set_synthesis_selections(project, {"chapters": ["1", "2"]})
    got = pm.get_synthesis_selections(project)
    assert got == {"chapters": ["1", "2"]}, f"读写应一致，实际 {got}"


def test_get_synthesis_selections_missing_file_returns_empty(project):
    # 从未设置过 selections 的项目 -> 缺文件返回 {}
    assert pm.get_synthesis_selections(project) == {}
    # 从未创建的项目名 -> 同样返回 {}
    assert pm.get_synthesis_selections("never_created") == {}


def test_build_segment_states_marks_skipped(project):
    # selected_chapters=["1"]：第 1 章段 pending、第 2 章段 skipped
    states = prog.build_segment_states(project, selected_chapters=["1"])
    by_id = {s["seg_id"]: s for s in states}
    assert by_id["1-001"]["status"] != prog.SEGMENT_STATUS_SKIPPED
    assert by_id["1-002"]["status"] != prog.SEGMENT_STATUS_SKIPPED
    assert by_id["2-001"]["status"] == prog.SEGMENT_STATUS_SKIPPED, \
        f"未选章段应标 skipped，实际 {by_id['2-001']['status']}"


def test_synthesize_project_skips_unselected(project, monkeypatch):
    """关键：全 pending 项目，勾选第1章 -> 第1章被合成、第2章标 skipped 且不合成。"""
    eng = _FakeEngine()
    monkeypatch.setattr(tts_engine, "_tts", eng)
    d = pm.get_project_dir(project)
    vo = os.path.join(project_paths.project_dir(d, "voices", create=True), "ref.wav")

    list(synth_queue.synthesize_project(
        project, {"旁白": vo}, cb_seg_state=None, selected_chapters=["1"]))

    # 选中章（第1章，2 段）被合成 -> 2 次
    assert eng.calls == 2, f"应只合成选中章的 2 段，实际 eng.calls={eng.calls}"
    # 选章段标 done
    meta, _, _ = pm.open_project(project)
    assert meta.segments_status["1-001"] == "done"
    assert meta.segments_status["1-002"] == "done"
    # 未选章（第2章，1 段）标 skipped、未被合成
    assert meta.segments_status["2-001"] == "skipped", \
        f"未选章段应标 skipped，实际 {meta.segments_status['2-001']}"


def test_synthesize_project_does_not_downgrade_done_segment(project, monkeypatch):
    """安全微调验证：未选中章中已 done(且 wav 存在) 的段不被降级为 skipped。

    先全量合成使 2-001 真正 done 且有 wav；再仅勾选第1章运行，断言 2-001
    仍保持 done（跳过逻辑只对 remaining(pending/failed) 段写 skipped）。
    """
    eng1 = _FakeEngine()
    monkeypatch.setattr(tts_engine, "_tts", eng1)
    d = pm.get_project_dir(project)
    vo = os.path.join(project_paths.project_dir(d, "voices", create=True), "ref.wav")
    # 第1轮：全量合成（selected_chapters 默认 None=全选）
    list(synth_queue.synthesize_project(project, {"旁白": vo}, cb_seg_state=None))
    meta, _, _ = pm.open_project(project)
    assert meta.segments_status["2-001"] == "done", "前置：第2章段应已合成 done"

    # 第2轮：仅勾选第1章；第2章（含已 done 的 2-001）为未选
    eng2 = _FakeEngine()
    monkeypatch.setattr(tts_engine, "_tts", eng2)
    list(synth_queue.synthesize_project(
        project, {"旁白": vo}, cb_seg_state=None, selected_chapters=["1"]))
    meta, _, _ = pm.open_project(project)
    assert meta.segments_status["2-001"] == "done", \
        f"已 done(含wav) 的未选段不应被降级为 skipped，实际 {meta.segments_status['2-001']}"
    # 第1章段上一轮已 done(有wav) -> 不在 remaining -> 不会被重新合成
    assert eng2.calls == 0, f"已 done 段不应被重新合成，eng2.calls={eng2.calls}"
