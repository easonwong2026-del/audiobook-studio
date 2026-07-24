"""角色单独补录 / 补合成导出 —— 合并单测集（P0 完整 + P1 核心）。

覆盖：
- 引擎互斥锁 _ENGINE_LOCK（RLock 递归不死锁、多线程串行化、锁内合成返回路径、
  OOM 递归切分重入锁不死锁）；
- project_manager.build_bound_role_choices（仅返回已绑定角色）；
- audio_pipeline.export_supplement（哑 wav 拼接 + LUFS + ffmpeg 转码；无 ffmpeg
  则 pytest.skip）；
- services.supplement.SupplementService（monkeypatch 假引擎写哑 wav：split_lines /
  synthesize_lines 逐句编排·状态·错误文案、小 JSON 校验诊断 role 未定义 / 缺字段 /
  复用 script_loader、build_small_script / build_output_path、P1 覆盖透传 / 换音色 /
  按标点切分 / m4b 仅写文字标签 / 比特率）。

不初始化 IndexTTS2（无 GPU / 模型）；用假引擎桩替换 tts_engine.synthesize_segment。
"""
from __future__ import annotations

import os
import shutil
import threading
from unittest import mock

import numpy as np
import pytest
from scipy.io import wavfile

from lib import tts_engine
from lib import audio_pipeline
from lib import project_manager as pm
from lib.exceptions import ExportError
from services.supplement import SupplementService


# ───────────────────────── 引擎互斥锁 ─────────────────────────

def _fake_infer(**kwargs):
    sr = 16000
    wavfile.write(kwargs["output_path"], sr, np.zeros(sr, dtype=np.int16))


def test_engine_lock_is_rlock_and_reentrant():
    lock = tts_engine._ENGINE_LOCK
    # RLock 在本环境可能是类或工厂函数；用其实例真实类型判定，避免 isinstance 误报
    assert isinstance(lock, type(threading.RLock()))
    # 关键性质：同一线程可重入（OOM 递归调用自身依赖此特性，非重入锁会死锁）
    with lock:
        with lock:
            pass
    lock.acquire()
    lock.acquire()
    lock.release()
    lock.release()


def test_engine_lock_identity():
    assert tts_engine.engine_lock() is tts_engine._ENGINE_LOCK


def test_lock_serializes_threads():
    lock = tts_engine._ENGINE_LOCK
    counter = {"n": 0}
    in_critical = {"flag": False}
    errors = []

    def worker():
        for _ in range(50):
            with lock:
                if in_critical["flag"]:
                    errors.append("overlap")
                in_critical["flag"] = True
                counter["n"] += 1
                in_critical["flag"] = False

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert counter["n"] == 200


def test_synthesize_segment_under_lock_returns_path(tmp_path, monkeypatch):
    out = str(tmp_path / "seg.wav")
    fake_tts = mock.MagicMock()
    fake_tts.infer = _fake_infer
    monkeypatch.setattr(tts_engine, "_tts", fake_tts)
    monkeypatch.setattr(tts_engine, "get_speaker_embedding", lambda *_a, **_k: None)
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)
    res = tts_engine.synthesize_segment(text="测试。", speaker_audio="x.wav", output_path=out)
    assert res == out and os.path.isfile(out)


# OOM 递归切分依赖 RLock 重入，已在 test_engine_lock_is_rlock_and_reentrant 覆盖；
# torch 在 tts_engine 内为局部 import，无需 GPU/模型即可保证锁重入特性，故不在此集成。



# ───────────────────────── 已绑定角色下拉 ─────────────────────────

def test_build_bound_role_choices_only_bound():
    script = {"voices": {"旁白": {}, "小明": {}, "配角": {}}}
    bindings = {"旁白": "/a.wav", "小明": "", "配角": None}
    choices = pm.build_bound_role_choices(script, bindings)
    assert [v for _, v in choices] == ["旁白"]
    assert all(lbl.startswith("【已绑定】") for lbl, _ in choices)


def test_build_bound_role_choices_empty_when_none():
    script = {"voices": {"A": {}, "B": {}}}
    assert pm.build_bound_role_choices(script, {"A": None}) == []
    assert pm.build_bound_role_choices(script, {}) == []


def test_build_bound_role_choices_order_preserved():
    script = {"voices": {"z": {}, "a": {}, "m": {}}}
    choices = pm.build_bound_role_choices(script, {"z": "1", "a": "2", "m": "3"})
    assert [v for _, v in choices] == ["z", "a", "m"]


# ───────────────────────── export_supplement ─────────────────────────

def _write_sine(path, sr=16000, dur=0.5, freq=220.0):
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    wavfile.write(path, sr, (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16))


def _ffmpeg_present():
    return shutil.which("ffmpeg") is not None


def test_export_empty_paths_raises():
    with pytest.raises(RuntimeError):
        audio_pipeline.export_supplement([], "out.wav")


def test_export_missing_segment_raises(tmp_path):
    with pytest.raises(RuntimeError):
        audio_pipeline.export_supplement([str(tmp_path / "x.wav")], "out.wav")


def test_export_wav_passthrough_and_concat(tmp_path):
    a = str(tmp_path / "a.wav")
    b = str(tmp_path / "b.wav")
    _write_sine(a)
    _write_sine(b)
    out = str(tmp_path / "out.wav")
    res = audio_pipeline.export_supplement([a, b], out, format="wav")
    rate, data = wavfile.read(res)
    assert data.shape[0] > int(16000 * 0.5)
    assert data.shape[0] < int(16000 * 0.5 * 2 + 16000 * 1.0)


def test_export_wav_insert_silence(tmp_path):
    a = str(tmp_path / "a.wav")
    b = str(tmp_path / "b.wav")
    _write_sine(a, dur=0.3)
    _write_sine(b, dur=0.3)
    out = str(tmp_path / "out.wav")
    rate, data = wavfile.read(audio_pipeline.export_supplement(
        [a, b], out, format="wav", insert_silence_sec=0.5))
    assert abs(data.shape[0] / rate - 1.1) < 0.05


def test_export_mp3_happy(tmp_path):
    if not _ffmpeg_present():
        pytest.skip("ffmpeg 未安装")
    a = str(tmp_path / "a.wav")
    _write_sine(a)
    res = audio_pipeline.export_supplement([a], str(tmp_path / "out.mp3"), format="mp3")
    assert res.endswith(".mp3") and os.path.isfile(res)


def test_export_mp3_missing_ffmpeg_raises(tmp_path):
    if _ffmpeg_present():
        pytest.skip("ffmpeg 已安装")
    a = str(tmp_path / "a.wav")
    _write_sine(a)
    with pytest.raises(ExportError):
        audio_pipeline.export_supplement([a], str(tmp_path / "out.mp3"), format="mp3")


def test_export_m4b_text_tags_only(tmp_path):
    if not _ffmpeg_present():
        pytest.skip("ffmpeg 未安装")
    a = str(tmp_path / "a.wav")
    wavfile.write(a, 16000, np.zeros(16000, dtype=np.int16))
    final = audio_pipeline.export_supplement(
        [a], str(tmp_path / "out.m4b"), format="m4b",
        bitrate="128k", title="补录标题", artist="补录作者")
    from mutagen.mp4 import MP4
    audio = MP4(final)
    # mutagen MP4 标签以列表存储
    assert audio.get("\xa9nam") == ["补录标题"]
    assert audio.get("\xa9ART") == ["补录作者"]


# ───────────────────────── SupplementService ─────────────────────────

@pytest.fixture
def fake_synth(tmp_path, monkeypatch):
    def _synth(text, speaker_audio, emotion="neutral", emo_alpha=1.0,
               speech_rate=1.0, output_path="", **kw):
        sr = 16000
        wavfile.write(output_path, sr,
                      (np.sin(np.linspace(0, 10, sr, endpoint=False)) * 3000).astype(np.int16))
        return output_path
    monkeypatch.setattr(tts_engine, "synthesize_segment", _synth)
    return tmp_path


def test_split_lines_basic():
    assert SupplementService.split_lines("a\n\nb\nc") == ["a", "b", "c"]


def test_split_lines_by_punct():
    assert SupplementService.split_lines("你好。世界！我们？", split_long=True) == ["你好。", "世界！", "我们？"]


def test_split_lines_punct_no_empty():
    assert all(SupplementService.split_lines("啊！！！嗯？？", split_long=True))


def test_synthesize_lines_ok(fake_synth):
    res = SupplementService.synthesize_lines("旁白", ["句一", "句二"], "spk.wav", cache_dir=str(fake_synth))
    assert len(res) == 2 and all(r["status"] == "ok" and os.path.isfile(r["wav_path"]) for r in res)


def test_synthesize_lines_partial_failure(tmp_path, monkeypatch):
    def _synth(text, speaker_audio, **kw):
        if "炸" in text:
            raise RuntimeError("boom detail")
        wavfile.write(kw["output_path"], 16000, np.zeros(16000, dtype=np.int16))
        return kw["output_path"]
    monkeypatch.setattr(tts_engine, "synthesize_segment", _synth)
    res = SupplementService.synthesize_lines("旁白", ["好的", "会炸的", "也好的"], "spk.wav", cache_dir=str(tmp_path))
    assert res[1]["status"] == "failed" and res[1]["wav_path"] is None
    assert res[1]["error"].startswith("❌ 句2: ") and "boom detail" in res[1]["error"]
    assert res[0]["status"] == "ok" and res[2]["status"] == "ok"


def test_synthesize_lines_empty_text(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_engine, "synthesize_segment", lambda **kw: "x.wav")
    res = SupplementService.synthesize_lines("旁白", ["  ", "有效"], "spk.wav", cache_dir=str(tmp_path))
    assert "文本为空" in res[0]["error"] and res[1]["status"] == "ok"


def test_synthesize_lines_uses_cache_dir(tmp_path, monkeypatch):
    captured = {"outs": []}
    def _synth(text, speaker_audio, **kw):
        captured["outs"].append(kw["output_path"])
        wavfile.write(kw["output_path"], 16000, np.zeros(16000, dtype=np.int16))
        return kw["output_path"]
    monkeypatch.setattr(tts_engine, "synthesize_segment", _synth)
    cache = tmp_path / "mycache"
    SupplementService.synthesize_lines("旁白", ["a", "b"], "spk.wav", cache_dir=str(cache))
    assert all(str(cache) in o for o in captured["outs"]) and os.path.isdir(cache)


def test_build_small_script():
    script = {"meta": {"title": "书"}, "voices": {"旁白": {"description": "x"}}, "chapters": []}
    small = SupplementService.build_small_script("旁白", ["a", "b"], script)
    assert small["voices"] == {"旁白": {"description": "x"}}
    segs = small["chapters"][0]["segments"]
    assert len(segs) == 2 and segs[0]["id"] == "sup-001" and segs[0]["role"] == "旁白"


def _project_script():
    return {"meta": {"title": "书", "author": "甲"},
            "voices": {"旁白": {}, "小明": {}}, "chapters": []}


def test_validate_small_json_role_miss():
    bad = {"voices": {"不存在": {}},
           "chapters": [{"id": 1, "segments": [{"id": "s1", "role": "不存在", "text": "x"}]}]}
    errs = SupplementService.validate_small_json(bad, _project_script())
    assert any("未在项目剧本 voices 中定义" in e for e in errs)


def test_validate_small_json_missing_voices_diagnostic():
    raw = {"chapters": [{"id": 1, "segments": [{"id": "s1", "role": "旁白", "text": "x"}]}]}
    errs = SupplementService.validate_small_json(raw, _project_script())
    assert any("未定义任何角色" in e for e in errs) and any("诊断信息" in e for e in errs)


def test_validate_small_json_no_lines():
    raw = {"voices": {"旁白": {}}, "chapters": [{"id": 1, "segments": []}]}
    errs = SupplementService.validate_small_json(raw, _project_script())
    assert any("未包含任何段落" in e for e in errs)


def test_validate_small_json_missing_text():
    # 缺 text 字段：script_loader.from_dict 会默认填 ""，validate_script 不报错，
    # 因此必须在 validate_small_json 内显式诊断空文本（QA follow-up 修复）。
    raw = {"voices": {"旁白": {}},
           "chapters": [{"id": 1, "segments": [{"id": "s1", "role": "旁白"}]}]}
    errs = SupplementService.validate_small_json(raw, _project_script())
    assert any("缺少文本内容" in e for e in errs), errs


def test_parse_small_json_ok():
    good = {"voices": {"小明": {}},
            "chapters": [{"id": 1, "segments": [
                {"id": "s1", "role": "小明", "text": "你好"},
                {"id": "s2", "role": "小明", "text": "世界"}]}]}
    role, lines = SupplementService.parse_small_json(good, _project_script())
    assert role == "小明" and lines == ["你好", "世界"]


def test_parse_small_json_raises_on_bad():
    bad = {"voices": {"幽灵": {}},
           "chapters": [{"id": 1, "segments": [{"id": "s1", "role": "幽灵", "text": "x"}]}]}
    with pytest.raises(ValueError):
        SupplementService.parse_small_json(bad, _project_script())


def test_build_output_path(tmp_path):
    p = SupplementService.build_output_path(str(tmp_path), "旁白", "mp3")
    assert p.endswith(".mp3") and "supplement_旁白_" in p and os.path.isdir(os.path.join(str(tmp_path), "output"))


# ───────────────────────── P1 覆盖透传 / 换音色 / 比特率 ─────────────────────────

@pytest.fixture
def capture_synth(tmp_path, monkeypatch):
    captured = {"calls": []}
    def _synth(text, speaker_audio, emotion="neutral", emo_alpha=1.0,
               speech_rate=1.0, output_path="", **kw):
        captured["calls"].append({
            "speaker_audio": speaker_audio, "emotion": emotion,
            "emo_alpha": emo_alpha, "speech_rate": speech_rate, "num_beams": kw.get("num_beams"),
        })
        wavfile.write(output_path, 16000, np.zeros(16000, dtype=np.int16))
        return output_path
    monkeypatch.setattr(tts_engine, "synthesize_segment", _synth)
    return captured


def test_overrides_forwarded(capture_synth, tmp_path):
    SupplementService.synthesize_lines(
        "旁白", ["你好", "世界"], "spk.wav",
        overrides={"emotion": "happy", "emo_alpha": 0.7, "speech_rate": 1.2},
        num_beams=3, cache_dir=str(tmp_path))
    for c in capture_synth["calls"]:
        assert c["emotion"] == "happy" and c["emo_alpha"] == 0.7 and c["speech_rate"] == 1.2 and c["num_beams"] == 3


def test_overrides_none_uses_default(capture_synth, tmp_path):
    SupplementService.synthesize_lines(
        "旁白", ["你好"], "spk.wav",
        overrides={"emotion": None, "emo_alpha": None, "speech_rate": None},
        num_beams=1, cache_dir=str(tmp_path))
    c = capture_synth["calls"][0]
    assert c["emotion"] == "neutral" and c["emo_alpha"] == 1.0 and c["speech_rate"] == 1.0 and c["num_beams"] == 1


def test_voice_swap_override_forwarded(capture_synth, tmp_path):
    SupplementService.synthesize_lines("旁白", ["你好"], "override.wav", cache_dir=str(tmp_path))
    assert capture_synth["calls"][0]["speaker_audio"] == "override.wav"


def test_export_bitrate_option(tmp_path):
    if not _ffmpeg_present():
        pytest.skip("ffmpeg 未安装")
    a = str(tmp_path / "a.wav")
    wavfile.write(a, 16000, np.zeros(16000, dtype=np.int16))
    for br in ("128k", "192k", "320k"):
        final = audio_pipeline.export_supplement([a], str(tmp_path / f"o_{br}.mp3"), format="mp3", bitrate=br)
        assert os.path.isfile(final)
