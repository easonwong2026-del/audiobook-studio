"""单元测试：lib/metadata.py（D2 ID3 / M4B 章节标签）

用 mutagen 读回断言标题 / 作者 / 封面存在、m4b 章节存在。
mp3 / m4b 由 ffmpeg 真生成（环境有 ffmpeg 时）；无 ffmpeg 则跳过，不破坏 CI。
"""
import os
import sys
import struct
import zlib

import numpy as np
from scipy.io import wavfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import metadata  # noqa: E402


def _ffmpeg():
    import shutil
    return shutil.which("ffmpeg")


def _make_png(path, color=(120, 80, 200)):
    """写一个合法的 1x1 RGB PNG（纯 Python，不依赖 PIL）。"""
    w = h = 1
    raw = bytes([0]) + bytes(color)
    def _chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw))
    png += _chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def _make_audio_file(path, fmt, rate=44100, dur=2.0):
    """用 ffmpeg 将一段正弦 WAV 转成 mp3 / m4b。"""
    ff = _ffmpeg()
    if not ff:
        pytest.skip("环境无 ffmpeg，跳过 D2 真标签写入测试")
    tmp = path + ".src.wav"
    n = int(rate * dur)
    t = np.linspace(0.0, dur, n, endpoint=False)
    sig = 0.3 * np.sin(2.0 * np.pi * 200.0 * t)
    wavfile.write(tmp, rate, (sig * 32767.0).astype(np.int16))
    if fmt == "mp3":
        cmd = [ff, "-y", "-i", tmp, path]
    else:  # m4b
        cmd = [ff, "-y", "-i", tmp, "-codec:a", "aac", path]
    import subprocess
    subprocess.run(cmd, check=True, capture_output=True)
    os.remove(tmp)


@pytest.fixture
def mp3_file(tmp_path):
    p = str(tmp_path / "book.mp3")
    _make_audio_file(p, "mp3")
    return p


@pytest.fixture
def m4b_file(tmp_path):
    p = str(tmp_path / "book.m4b")
    _make_audio_file(p, "m4b")
    return p


def test_write_mp3_tags_text(mp3_file):
    metadata.write_mp3_tags(mp3_file, "测试书", "佚名", album="专辑X")
    from mutagen.mp3 import MP3
    audio = MP3(mp3_file)
    assert audio.tags is not None
    assert audio.tags.get("TIT2") is not None
    assert "测试书" in str(audio.tags.get("TIT2"))
    assert "佚名" in str(audio.tags.get("TPE1"))
    assert "专辑X" in str(audio.tags.get("TALB"))


def test_write_mp3_tags_with_cover(mp3_file, tmp_path):
    png = str(tmp_path / "cover.png")
    _make_png(png)
    metadata.write_mp3_tags(mp3_file, "书", "作者", cover_path=png)
    from mutagen.mp3 import MP3
    audio = MP3(mp3_file)
    apic = audio.tags.getall("APIC")
    assert apic, "应嵌入封面 APIC 帧"
    assert apic[0].mime == "image/png"


def test_write_m4b_chapters(m4b_file):
    chapters = [(0, "第一章"), (1000, "第二章"), (2000, "第三章")]
    metadata.write_m4b_chapters(m4b_file, "书", "作者", chapters=chapters)
    from mutagen.mp4 import MP4
    audio = MP4(m4b_file)
    got = audio.chapters
    assert got, "m4b 应写入章节"
    assert len(got) == 3, f"章数应为 3，实际 {len(got)}"
    # mutagen 读回章节为 Chapter 对象（.start 为 ms，.title 为标题）
    assert got[0].title == "第一章"
    # mutagen Chapter.start 单位为秒（START=1000 → 1.0s）
    assert abs(got[1].start - 1.0) <= 0.05, f"第二章起点应≈1.0s，实际 {got[1].start}"
    # 文字标签也应保留（ffmpeg 重封装 -c copy 不丢 ©nam/©ART）
    assert audio.get("\xa9nam") == ["书"]
    assert audio.get("\xa9ART") == ["作者"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
