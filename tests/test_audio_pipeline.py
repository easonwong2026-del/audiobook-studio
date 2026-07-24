"""单元测试：lib/audio_pipeline.py

验证工程师修复的 BUG：
  - B4: 导出 mp3 时比特率（bitrate）透传给 ffmpeg，而非静默落到默认 192k
  - B8: 段间 / 章首插入静音，且段落顺序与 structured_script.json 一致
  - 缺段健壮性：删除某个段落 wav 后调用 export_book 应抛 RuntimeError（提示未找到段落）
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
    "meta": {"title": "我的有声书"},
    "voices": {"旁白": {"description": "x"}, "小明": {"description": "y"}},
    "chapters": [
        {
            "id": 1, "title": "第一章",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "A", "emotion": "neutral"},
                {"id": "1-002", "role": "小明", "text": "B", "emotion": "neutral"},
            ],
        },
        {
            "id": 2, "title": "第二章",
            "segments": [
                {"id": "2-001", "role": "旁白", "text": "C", "emotion": "neutral"},
            ],
        },
    ],
}


def _write_seg(seg_dir, seg_id, value, n):
    """写一个恒定非零值的 int16 wav，便于之后按“静音切点”还原段落顺序。"""
    data = np.full(n, value, dtype=np.int16)
    wavfile.write(os.path.join(seg_dir, f"{seg_id}.wav"), 16000, data)


def _extract_order(combined):
    """从拼接数组中按“非零恒定段”提取段落顺序（静音为 0 起分隔作用）。"""
    order = []
    prev = None
    for v in combined:
        iv = int(v)
        if iv != 0:
            if prev != iv:
                order.append(iv)
                prev = iv
        else:
            prev = None
    return order


@pytest.fixture
def book(tmp_path):
    proj_dir = tmp_path / "book"
    proj_dir.mkdir()
    seg_dir = proj_dir / "segments"
    seg_dir.mkdir()
    (proj_dir / "structured_script.json").write_text(
        json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8"
    )
    # 不同长度 + 不同恒定值，便于断言顺序与静音
    _write_seg(str(seg_dir), "1-001", value=100, n=100)
    _write_seg(str(seg_dir), "1-002", value=200, n=200)
    _write_seg(str(seg_dir), "2-001", value=300, n=300)
    return str(proj_dir)


def test_export_wav_silence_and_order(book):
    out = ap.export_book(book, format="wav")
    assert os.path.isfile(out)
    rate, data = wavfile.read(out)

    seg_sum = 100 + 200 + 300
    # 1) 采样点数量 > 各段之和（证明插入了静音间隔，B8 仍生效）
    assert len(data) > seg_sum, f"应插入静音，采样点 {len(data)} 应 > {seg_sum}"
    # 2) 段顺序与 JSON 一致：A -> B -> C（单调递增）。
    #    注意：export_book 现在会对 WAV 做 D1 响度归一（改变绝对振幅），
    #    故比较相对顺序而非硬编码数值，B8 段/章顺序不变。
    order = _extract_order(data)
    assert len(order) == 3, f"应有 3 段，实际 {order}"
    assert order == sorted(order), f"段落顺序应递增（A<B<C），实际: {order}"


def test_export_mp3_bitrate_passthrough(book, monkeypatch):
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))
        # 不真正执行 ffmpeg，返回成功对象即可
        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(ap.subprocess, "run", fake_run)

    out = ap.export_book(book, format="mp3", bitrate="320k")
    assert calls, "导出 mp3 时未调用 subprocess.run（ffmpeg）"
    cmd = calls[0]
    # B4 验证：比特率 320k 与编码器 libmp3lame 都进了 ffmpeg 命令行
    assert "320k" in cmd, "比特率未透传给 ffmpeg"
    assert "libmp3lame" in cmd, "未使用 libmp3lame 编码器"


def test_export_missing_segment_raises(book):
    seg_dir = os.path.join(book, "segments")
    os.remove(os.path.join(seg_dir, "1-002.wav"))
    # 缺段场景：删除某个段落 wav 后，导出应抛 RuntimeError 提示未找到段落
    with pytest.raises(RuntimeError):
        ap.export_book(book, format="wav")


def test_export_postprocess_chain_order(book, monkeypatch):
    """集成：export_book 后处理链顺序 + 参数透传（ffmpeg 用 monkeypatch 验参）。"""
    import lib.postprocess as pp

    calls = []

    def fake_eq(p, enable=False):
        calls.append(("eq", enable))
        return p

    def fake_norm(p, target_lufs=-16.0):
        calls.append(("loud", target_lufs))
        return p

    def fake_tags(fmt, out, script, project_dir, markers, rate, logger):
        calls.append(("tags", fmt))

    def fake_run(cmd, *a, **k):
        calls.append(("ffmpeg", list(cmd)))
        class _R:
            returncode = 0
        return _R()

    # postprocess 在 export_book 内 `from . import postprocess` 懒加载，
    # 因此直接 patch lib.postprocess 模块上的函数。
    monkeypatch.setattr(pp, "apply_eq", fake_eq)
    monkeypatch.setattr(pp, "normalize_loudness", fake_norm)
    monkeypatch.setattr(ap, "_write_tags", fake_tags)
    monkeypatch.setattr(ap.subprocess, "run", fake_run)

    out = ap.export_book(book, format="mp3", bitrate="256k", enable_eq=True, target_lufs=-18.0)
    kinds = [c[0] for c in calls]
    # 期望顺序：均衡 → 响度 → ffmpeg 转码 → 写标签
    assert kinds[:4] == ["eq", "loud", "ffmpeg", "tags"], f"后处理链顺序错误: {kinds}"

    # D3 开关透传
    assert calls[0] == ("eq", True)
    # D1 目标响度透传
    assert calls[1] == ("loud", -18.0)
    # B4 比特率 + 编码器透传进 ffmpeg 命令行
    fcmd = calls[2][1]
    assert "256k" in fcmd, "比特率未透传给 ffmpeg"
    assert "libmp3lame" in fcmd, "未使用 libmp3lame 编码器"
    # D2 标签针对 mp3
    assert calls[3] == ("tags", "mp3")
    # ffmpeg 已被 monkeypatch（不产生真实文件）：校验返回的是 mp3 路径
    assert out.endswith(".mp3"), f"导出路径应为 .mp3，实际 {out}"
