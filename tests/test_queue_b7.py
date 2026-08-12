"""B7 集成：批量合成中参数（emotion）变更触发重新合成、output_path 文件名不同。

自包含：本文件**自行注入假 torch**（不再依赖 test_tts_engine 在集合期向
sys.modules['torch'] 注入的全局泄漏），可单独运行（无 GPU / 无真实 torch）。

用「伪造 torch 之外的假引擎」范式：直接给 tts_engine._tts 注入一个写哑 wav 并计数
infer 调用的假引擎，无需加载 IndexTTS2（无 GPU）。
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

# ── 自包含：自行注入假 torch，彻底不依赖其它测试文件（如 test_tts_engine）──
# 与 test_tts_engine 注入等价的假 torch，使本文件单独运行也能 import torch 并跑通
# synthesize_segment 的桩路径（无 GPU / 无真实 torch）。若环境中已存在真实 torch，
# setdefault 不会覆盖，行为一致。
class _B7FakeTorchTensor:
    """占位类，仅用于兼容 scipy 的 torch-array 检测（与 test_tts_engine 一致）。"""
    pass


# 注意：OutOfMemoryError 必须与真实 torch 一致地设为 RuntimeError 的子类
# （真实 ``torch.cuda.OutOfMemoryError`` 继承 ``RuntimeError``）。这样即使本文件
# 在 test_tts_engine 之前被导入、假 torch 通过 setdefault 抢占 sys.modules，
# test_tts_engine 的 ``FakeOOM(RuntimeError)`` 仍能被 ``synthesize_segment`` 的
# ``except torch.cuda.OutOfMemoryError`` 正确捕获，避免集合期假 torch 互相覆盖
# 导致 OOM 测试误判失败。
_fake_torch = types.SimpleNamespace(
    cuda=types.SimpleNamespace(
        empty_cache=lambda: None,
        OutOfMemoryError=RuntimeError,
    ),
    Tensor=_B7FakeTorchTensor,
)
sys.modules.setdefault("torch", _fake_torch)

import lib.project_manager as pm  # noqa: E402
import lib.queue as synth_queue  # noqa: E402
import lib.tts_engine as tts_engine  # noqa: E402
import lib.audio_pipeline as audio_pipeline  # noqa: E402
from lib import project_paths  # noqa: E402


def _dummy_wav(path, n=800):
    """写一个合法的 int16 哑 wav。"""
    wavfile.write(path, 16000, np.zeros(n, dtype=np.int16))


class _B7FakeEngine:
    """假 IndexTTS2：仅记录 infer 调用次数并写出 output_path。"""

    def __init__(self):
        self.calls = 0

    def infer(self, spk_audio_prompt, text, output_path, use_emo_text, emo_text,
              emo_alpha, max_text_tokens_per_segment, speed=1.0, pinyin_hints=None):
        self.calls += 1
        _dummy_wav(output_path)


SCRIPT = {
    "meta": {"title": "B7书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {
            "id": 1, "title": "一",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段内容", "emotion": "neutral"},
            ],
        }
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """用临时目录作 WORKSPACE_ROOT，建一个 1 段 1 角色项目并绑定参考音频。"""
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(tmp_path))
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    pm.create_project("b7", str(sp))
    d = pm.get_project_dir("b7")
    vo = os.path.join(project_paths.project_dir(d, "voices", create=True), "ref.wav")
    _dummy_wav(vo)
    bp = os.path.join(d, "voice_bindings.json")
    with open(bp, encoding="utf-8") as f:
        bd = json.load(f)
    bd["bindings"]["旁白"] = vo
    with open(bp, "w", encoding="utf-8") as f:
        json.dump(bd, f, ensure_ascii=False, indent=2)
    return "b7"


def _list_wavs(seg_dir):
    return sorted(f for f in os.listdir(seg_dir) if f.endswith(".wav"))


def test_b7_emotion_change_retriggers_synthesis(project, monkeypatch):
    eng = _B7FakeEngine()
    monkeypatch.setattr(tts_engine, "_tts", eng)

    d = pm.get_project_dir(project)
    vo = os.path.join(project_paths.project_dir(d, "voices", create=True), "ref.wav")
    seg_dir = project_paths.project_dir(d, "segments", create=True)

    # 第 1 次：neutral 情感合成
    list(synth_queue.synthesize_project(project, {"旁白": vo}))
    assert eng.calls == 1, "首次应合成 1 次"
    neutral_files = _list_wavs(seg_dir)
    assert len(neutral_files) == 1
    # B7：output_path 已是参数感知缓存键命名（非裸 seg_id.wav）
    assert neutral_files[0].startswith("1-001_"), f"应写缓存键命名，实际 {neutral_files[0]}"

    # 模拟用户清缓存：删除已合成 wav（与生产「删旧缓存后重跑」一致）
    for f in neutral_files:
        os.remove(os.path.join(seg_dir, f))

    # 改 JSON 中该段 emotion -> happy
    sp = os.path.join(d, "structured_script.json")
    with open(sp, encoding="utf-8") as f:
        script = json.load(f)
    script["chapters"][0]["segments"][0]["emotion"] = "happy"
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    # 第 2 次：emotion 变更 + 旧缓存已清 → 应重新合成（旧缓存不被命中）
    list(synth_queue.synthesize_project(project, {"旁白": vo}))
    assert eng.calls == 2, "参数(emotion)变更且旧缓存清除后应重新合成（旧缓存不应被命中）"

    happy_files = _list_wavs(seg_dir)
    assert len(happy_files) == 1
    assert happy_files[0].startswith("1-001_")
    # 不同 emotion → 不同缓存键文件名（含参数哈希）
    assert happy_files[0] != neutral_files[0], "不同 emotion 应生成不同缓存键文件名"


def test_b7_same_params_cache_hit(project, monkeypatch):
    """参数不变 → 第 2 次批量不重新合成（eng.calls 仍为 1）。"""
    eng = _B7FakeEngine()
    monkeypatch.setattr(tts_engine, "_tts", eng)

    d = pm.get_project_dir(project)
    vo = os.path.join(project_paths.project_dir(d, "voices", create=True), "ref.wav")

    list(synth_queue.synthesize_project(project, {"旁白": vo}))
    assert eng.calls == 1
    # 第 2 次：JSON 未改、wav 仍在 → 命中缓存，不再合成
    list(synth_queue.synthesize_project(project, {"旁白": vo}))
    assert eng.calls == 1, "参数不变应命中缓存、不重新合成"


def test_b7_export_no_drop_for_default_pinyin_hints(project, monkeypatch):
    """B7 导出丢段回归（QA 建议集成测试）。

    构造「缺省 pinyin_hints」的小项目——这是真实最常见情况：段落 JSON 没有
    pinyin_hints 字段，``script_loader`` 默认给 ``{}``，而导出侧读 raw JSON 默认
    ``None``。流程：假引擎跑 ``synthesize_project`` → 直接 ``export_book``
    （monkeypatch ffmpeg）→ 断言不抛 "未找到段落" 且产出文件存在。

    修复前：合成侧缓存键用 ``{}``、导出侧用 ``None``，两侧 md5 不同 → 导出查不到
    文件 → ``RuntimeError(未找到段落)`` 丢段。修复后（``segment_cache`` 归一化
    ``{}``/``None``/``""`` + ``queue._seg_cache_key`` 委派同一公式）两侧一致。
    """
    import shutil as _shutil
    import subprocess as _sp

    eng = _B7FakeEngine()
    monkeypatch.setattr(tts_engine, "_tts", eng)

    # 伪造 ffmpeg：把中间 wav 直接拷成输出文件（无需真实 ffmpeg / GPU）。
    def _fake_ffmpeg(cmd, *a, **k):
        i = cmd.index("-i")
        _shutil.copyfile(cmd[i + 1], cmd[-1])
        class _R:
            returncode = 0
        return _R()
    monkeypatch.setattr(_sp, "run", _fake_ffmpeg)

    # 隔离元数据写入（mutagen / 封面与 B7 缓存键无关，避免环境依赖）。
    monkeypatch.setattr(audio_pipeline, "_write_tags", lambda *a, **k: None)

    d = pm.get_project_dir(project)
    vo = os.path.join(project_paths.project_dir(d, "voices", create=True), "ref.wav")

    # 1) 合成：SCRIPT 段落无 pinyin_hints 字段 → script_loader 默认给 {}
    list(synth_queue.synthesize_project(project, {"旁白": vo}))
    assert eng.calls == 1, "应合成 1 次"

    # 2) 直接导出 mp3（走 ffmpeg 路径）—— 不应因缓存键不匹配而丢段
    out = audio_pipeline.export_book(d, format="mp3")
    assert os.path.isfile(out), "导出应产出音频文件，不得因缓存键不匹配而丢段"
