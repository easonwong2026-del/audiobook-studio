from lib import project_paths
"""O13 统一试听播放器：章节级合并试听 concat_for_preview（无 GPU）。

验证（设计 §6 O13 / §12.3）：
- concat_for_preview(project_dir, chapter_id, out) 用 monkeypatch 的 _find_segment
  返回单章多段假 wav 后：out 存在，时长 ≈ 各段和 + 段间(段数-1)*SEG_SILENCE_SEC；
- 缺段跳过仍产出；
- 全缺 / 未知章节返回 None。

不依赖真实模型：_find_segment 直接返回预写的假 wav（仿 test_subtitles 手法）。
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

import lib.audio_pipeline as ap  # noqa: E402


SCRIPT = {
    "meta": {"title": "合并书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {"id": 1, "title": "第一章",
         "segments": [
             {"id": "1-001", "role": "旁白", "text": "A", "emotion": "neutral"},
             {"id": "1-002", "role": "旁白", "text": "B", "emotion": "neutral"},
         ]},
        {"id": 2, "title": "第二章",
         "segments": [
             {"id": "2-001", "role": "旁白", "text": "C", "emotion": "neutral"},
         ]},
    ],
}


def _make_book(tmp_path, wav_lengths: dict):
    proj_dir = tmp_path / "book"
    proj_dir.mkdir()
    seg_dir = proj_dir / "segments"
    seg_dir.mkdir()
    (proj_dir / "structured_script.json").write_text(
        json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    paths = {}
    for seg_id, n in wav_lengths.items():
        p = seg_dir / f"{seg_id}.wav"
        wavfile.write(str(p), 16000, np.full(n, 100, dtype=np.int16))
        paths[seg_id] = str(p)
    return str(proj_dir)


def _fake_find(paths):
    def _f(segments_dir, seg_id, *a, **k):
        return paths.get(seg_id)
    return _f


@pytest.fixture
def chapter1_wavs(tmp_path, monkeypatch):
    proj_dir = _make_book(tmp_path, {"1-001": 1600, "1-002": 2400})
    paths = {"1-001": os.path.join(project_paths.project_dir(proj_dir, "segments", create=True), "1-001.wav"),
             "1-002": os.path.join(project_paths.project_dir(proj_dir, "segments", create=True), "1-002.wav")}
    monkeypatch.setattr(ap, "_find_segment", _fake_find(paths))
    out = os.path.join(proj_dir, "chapter_1_preview.wav")
    return proj_dir, out


def test_concat_for_preview_produces_output(chapter1_wavs):
    proj_dir, out = chapter1_wavs
    res = ap.concat_for_preview(proj_dir, 1, out)
    assert res is not None, "合并试听应产出文件路径"
    assert os.path.isfile(res), "输出 wav 应存在"
    rate, data = wavfile.read(res)
    # 时长 = 各段和 + (段数-1)*段间静音
    seg_sum = 1600 + 2400
    silence = int(16000 * ap.SEG_SILENCE_SEC)  # 4800
    expected = seg_sum + (2 - 1) * silence
    assert rate == 16000
    assert len(data) == expected, \
        f"采样点应=各段和+段间静音({expected})，实际 {len(data)}"


def test_concat_for_preview_skips_missing_segment(chapter1_wavs, monkeypatch):
    proj_dir, out = chapter1_wavs
    # 1-002 缺段（_find_segment 返回 None），1-001 仍存在 -> 跳过缺失段
    monkeypatch.setattr(
        ap, "_find_segment",
        lambda sd, seg_id, *a, **k: None if seg_id == "1-002" else os.path.join(sd, "1-001.wav"))
    res = ap.concat_for_preview(proj_dir, 1, out)
    assert res is not None, "缺一段时仍应产出（跳过缺失段）"
    assert os.path.isfile(res)
    rate, data = wavfile.read(res)
    # 仅 1-001（1600 样本），单段无静音
    assert len(data) == 1600, f"缺段跳过应只剩 1-001，实际 {len(data)}"


def test_concat_for_preview_all_missing_returns_none(tmp_path, monkeypatch):
    proj_dir = _make_book(tmp_path, {"1-001": 1600, "1-002": 2400})
    monkeypatch.setattr(ap, "_find_segment", lambda *a, **k: None)
    out = os.path.join(proj_dir, "chapter_1_preview.wav")
    assert ap.concat_for_preview(proj_dir, 1, out) is None, "全缺段应返回 None"


def test_concat_for_preview_unknown_chapter_returns_none(tmp_path, monkeypatch):
    proj_dir = _make_book(tmp_path, {"1-001": 1600, "1-002": 2400})
    paths = {"1-001": os.path.join(project_paths.project_dir(proj_dir, "segments", create=True), "1-001.wav"),
             "1-002": os.path.join(project_paths.project_dir(proj_dir, "segments", create=True), "1-002.wav")}
    monkeypatch.setattr(ap, "_find_segment", _fake_find(paths))
    out = os.path.join(proj_dir, "chapter_99_preview.wav")
    assert ap.concat_for_preview(proj_dir, 99, out) is None, "未知章节应返回 None"
