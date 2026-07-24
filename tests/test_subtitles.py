"""O1 集成：lib/audio_pipeline.generate_subtitles 生成 srt / lrc 字幕。

独立验证（QA 视角）：字幕时间戳必须复用导出拼接的统一静音规则
（SEG_SILENCE_SEC=0.3 / CH_SILENCE_SEC=0.8），与 export_book 共用单一真相源，
保证「听书进度」与「字幕对齐」一致。

不依赖 GPU / 真实模型：monkeypatch ``_find_segment`` 返回固定 1000ms 假 wav，
构造临时多章多段 ``structured_script.json``。
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
    "meta": {"title": "字幕书"},
    "voices": {"旁白": {"description": "x"}, "小明": {"description": "y"}},
    "chapters": [
        {
            "id": 1, "title": "第一章",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段内容", "emotion": "neutral"},
                {"id": "1-002", "role": "小明", "text": "第二段内容", "emotion": "neutral"},
            ],
        },
        {
            "id": 2, "title": "第二章",
            "segments": [
                {"id": "2-001", "role": "旁白", "text": "第三段内容", "emotion": "neutral"},
            ],
        },
    ],
}


@pytest.fixture
def sub_project(tmp_path, monkeypatch):
    """构造多章多段项目，并把 _find_segment 替换为返回固定 1000ms 假 wav。

    三段长度一致（各 1000ms），便于精确断言累计时间戳：
      段1: 0 -> 1000
      段2: 1000 + 段间0.3s -> 2300
      段3: 2300 + 章首0.8s -> 4100
    """
    proj_dir = tmp_path / "book"
    proj_dir.mkdir()
    seg_dir = proj_dir / "segments"
    seg_dir.mkdir()
    (proj_dir / "structured_script.json").write_text(
        json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8"
    )
    # 固定时长假 wav：16000 采样点 @16000Hz = 1000ms
    dummy = str(seg_dir / "fixed.wav")
    wavfile.write(dummy, 16000, np.zeros(16000, dtype=np.int16))
    monkeypatch.setattr(ap, "_find_segment", lambda *a, **k: dummy)
    return str(proj_dir)


def test_subtitle_silence_constants_present():
    """静音常量作为单一真相源存在且值正确（与导出拼接共用）。"""
    assert ap.SEG_SILENCE_SEC == 0.3
    assert ap.CH_SILENCE_SEC == 0.8


def test_subtitle_srt_timestamps_cumulative(sub_project):
    """srt 时间戳按 SEG/CH 静音规则累计正确（段间0.3s、章首0.8s）。"""
    out = ap.generate_subtitles(sub_project, formats=("srt",))
    assert isinstance(out, list) and len(out) == 1
    assert out[0].endswith(".srt")
    content = open(out[0], encoding="utf-8").read()

    expected = "\n".join([
        "1",
        "00:00:00,000 --> 00:00:01,000",
        "第一段内容",
        "",
        "2",
        "00:00:01,300 --> 00:00:02,300",
        "第二段内容",
        "",
        "3",
        "00:00:03,100 --> 00:00:04,100",
        "第三段内容",
        "",
    ])
    assert content == expected, f"srt 内容不符合预期:\n{content}"

    # 段间间隔 = SEG_SILENCE_SEC，章首间隔 = CH_SILENCE_SEC（与常量一致）
    seg_gap = int(ap.SEG_SILENCE_SEC * 1000)   # 300
    ch_gap = int(ap.CH_SILENCE_SEC * 1000)     # 800
    assert "00:00:01,300" in content, "段2 起点应为 1000+段间静音(0.3s)"
    # 段3 起点 = 段2结束(2300) + 章首静音(0.8s) = 3100
    assert "00:00:03,100" in content, "段3 起点应为上一章结束 + 章首静音(0.8s)"
    # 反例：若错误地用了段间静音而非章首静音，段3 会停在 2600
    assert "00:00:02,600 -->" not in content, "章首应使用 0.8s 静音，而非段间 0.3s"


def test_subtitle_lrc_format(sub_project):
    """lrc 时间戳格式 [MM:SS.xx]（点分隔百分秒）正确。"""
    out = ap.generate_subtitles(sub_project, formats=("lrc",))
    assert len(out) == 1 and out[0].endswith(".lrc")
    content = open(out[0], encoding="utf-8").read()

    expected = "\n".join([
        "[00:00.00]第一段内容",
        "[00:01.30]第二段内容",
        "[00:03.10]第三段内容",
    ])
    assert content == expected, f"lrc 内容不符合预期:\n{content}"


def test_subtitle_format_filtering(sub_project):
    """formats=('srt',) 只出 srt；formats=('lrc',) 只出 lrc；默认两者都有。"""
    srt_only = ap.generate_subtitles(sub_project, formats=("srt",))
    assert [os.path.basename(p) for p in srt_only] == ["字幕书.srt"]

    lrc_only = ap.generate_subtitles(sub_project, formats=("lrc",))
    assert [os.path.basename(p) for p in lrc_only] == ["字幕书.lrc"]

    both = ap.generate_subtitles(sub_project)  # 默认 ("srt", "lrc")
    names = sorted(os.path.basename(p) for p in both)
    assert names == ["字幕书.lrc", "字幕书.srt"]


def test_subtitle_returns_path_list(sub_project):
    """返回生成文件路径列表（list[str]，且文件真实存在）。"""
    out = ap.generate_subtitles(sub_project, formats=("srt", "lrc"))
    assert isinstance(out, list)
    assert len(out) == 2
    for p in out:
        assert isinstance(p, str) and os.path.isfile(p)


def test_subtitle_no_segments_skips(sub_project, monkeypatch):
    """某段缺音频（_find_segment 返回 None）则跳过；全缺返回 []，不抛异常。"""
    monkeypatch.setattr(ap, "_find_segment", lambda *a, **k: None)
    out = ap.generate_subtitles(sub_project, formats=("srt",))
    assert out == [], "无可用段落时返回空列表，不应抛异常"


def test_subtitle_empty_formats_returns_empty(sub_project):
    """formats 为空 / None 时返回空列表（不生成任何文件）。"""
    assert ap.generate_subtitles(sub_project, formats=()) == []
    assert ap.generate_subtitles(sub_project, formats=None) == []
