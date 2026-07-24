"""R2 单测：ffmpeg 缺失 / 转码失败时应抛 ``ExportError``（而非静默回退 WAV）。

用 monkeypatch ``subprocess.run`` 抛 ``FileNotFoundError``，断言
``audio_pipeline.export_book`` 与 ``ExportService.export`` 都抛 ``ExportError``，
且错误信息包含已生成的中间 WAV 路径与 ffmpeg 安装链接。成功路径不受影响
（由 ``test_audio_pipeline.py`` 既有用例覆盖）。
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
from services.export import ExportService  # noqa: E402
from lib.exceptions import ExportError  # noqa: E402


SCRIPT = {
    "meta": {"title": "R2书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {
            "id": 1, "title": "一",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段", "emotion": "neutral"},
            ],
        }
    ],
}


@pytest.fixture
def book(tmp_path):
    """准备一个「有 1 段已合成 wav」的项目，使导出能走到 ffmpeg 分支。"""
    proj_dir = tmp_path / "book"
    proj_dir.mkdir()
    seg_dir = proj_dir / "segments"
    seg_dir.mkdir()
    (proj_dir / "structured_script.json").write_text(
        json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8"
    )
    wavfile.write(os.path.join(str(seg_dir), "1-001.wav"), 16000,
                  np.zeros(800, dtype=np.int16))
    return str(proj_dir)


def _raise_file_not_found(*a, **k):
    raise FileNotFoundError("ffmpeg not found")


def _raise_called_process(*a, **k):
    class _CP:
        returncode = 1
    raise __import__("subprocess").CalledProcessError(1, "ffmpeg")


def test_export_book_raises_export_error_when_ffmpeg_missing(book, monkeypatch):
    monkeypatch.setattr(ap.subprocess, "run", _raise_file_not_found)
    with pytest.raises(ExportError) as ei:
        ap.export_book(book, format="mp3")
    msg = str(ei.value)
    # 必须包含中间 WAV 路径与 ffmpeg 安装链接
    assert ".wav" in msg
    assert "https://ffmpeg.org/download.html" in msg
    # 应包含「改用 WAV」建议
    assert "WAV" in msg


def test_export_book_raises_export_error_when_ffmpeg_fails(book, monkeypatch):
    monkeypatch.setattr(ap.subprocess, "run", _raise_called_process)
    with pytest.raises(ExportError) as ei:
        ap.export_book(book, format="m4b")
    msg = str(ei.value)
    assert ".wav" in msg
    assert "https://ffmpeg.org/download.html" in msg


def test_export_service_propagates_export_error(book, monkeypatch):
    monkeypatch.setattr(ap.subprocess, "run", _raise_file_not_found)
    with pytest.raises(ExportError) as ei:
        ExportService.export(book, "mp3")
    assert "https://ffmpeg.org/download.html" in str(ei.value)
