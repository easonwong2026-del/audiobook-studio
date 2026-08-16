"""PR B 修复 4：补录导出 UX —— 文件名清洗 / 扩展名归一 / 重名后缀 / 打开文件夹。

覆盖：
- 自定义文件名：``abc`` + MP3 → ``abc.mp3``；``abc.wav`` + MP3 → ``abc.mp3``；
- 非法字符清洗（< > : " / \\ | ? *）与尾部空格 / 尾部点；空名回退；
- 重名不静默覆盖：abc.mp3 → abc_2.mp3 → abc_3.mp3；
- 最终路径正确（保存目录 + 归一文件名 + 唯一后缀）；
- 打开所在文件夹 handler 正确（no-window）。
"""
from __future__ import annotations

import os
from pathlib import Path


from services.export_naming import (
    build_export_path,
    normalize_export_name,
    sanitize_filename,
    strip_extension,
    unique_path,
)


# ── 自定义文件名 / 扩展名归一 ─────────────────────────────────────────────
def test_normalize_plain_name():
    assert normalize_export_name("abc", "mp3") == "abc.mp3"
    assert normalize_export_name("abc", "MP3") == "abc.mp3"
    assert normalize_export_name("abc", ".wav") == "abc.wav"


def test_normalize_strips_known_audio_extension():
    # abc.wav + MP3 → abc.mp3（不生成 abc.wav.mp3）
    assert normalize_export_name("abc.wav", "mp3") == "abc.mp3"
    assert normalize_export_name("abc.mp3", "wav") == "abc.wav"
    assert normalize_export_name("abc.m4b", "wav") == "abc.wav"


def test_normalize_empty_falls_back():
    assert normalize_export_name("", "mp3") == "export.mp3"
    assert normalize_export_name("   ", "mp3") == "export.mp3"
    assert normalize_export_name("...", "mp3") == "export.mp3"


# ── 非法字符清洗 ─────────────────────────────────────────────────────────
def test_sanitize_illegal_chars():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_trailing_space_and_dot():
    assert sanitize_filename("abc  ") == "abc"
    assert sanitize_filename("abc.") == "abc"
    assert sanitize_filename("abc . ") == "abc"


def test_sanitize_empty_falls_back():
    assert sanitize_filename("") == "export"
    assert sanitize_filename(None) == "export"
    assert sanitize_filename("...", fallback="自定义") == "自定义"
    assert sanitize_filename("   ", fallback="自定义") == "自定义"


def test_strip_extension_only_audio():
    assert strip_extension("abc.wav") == "abc"
    assert strip_extension("abc.wav.mp3") == "abc.wav"
    assert strip_extension("my.voice") == "my.voice"


# ── 重名后缀（不静默覆盖）────────────────────────────────────────────────
def test_unique_path_no_conflict(tmp_path):
    target = str(tmp_path / "abc.mp3")
    assert unique_path(target) == target


def test_unique_path_appends_suffix(tmp_path):
    first = tmp_path / "abc.mp3"
    first.write_bytes(b"x")
    second = unique_path(str(first))
    assert second == str(tmp_path / "abc_2.mp3")
    # 第一次导出落盘后，同一名称再导出 → abc_3.mp3
    Path(second).write_bytes(b"x")
    third = unique_path(str(first))
    assert third == str(tmp_path / "abc_3.mp3")


def test_unique_path_skips_existing_suffix(tmp_path):
    first = tmp_path / "abc.mp3"
    first.write_bytes(b"x")
    (tmp_path / "abc_2.mp3").write_bytes(b"x")
    third = unique_path(str(first))
    assert third == str(tmp_path / "abc_3.mp3")


# ── 最终路径 ─────────────────────────────────────────────────────────────
def test_build_export_path_and_unique_combined(tmp_path):
    out_dir = tmp_path / "exports"
    path = build_export_path(str(out_dir), "abc.wav", "mp3")
    assert path == str(out_dir / "abc.mp3")
    final = unique_path(path)
    assert final == str(out_dir / "abc.mp3")
    # 写入后重名 → abc_2.mp3
    os.makedirs(out_dir, exist_ok=True)
    with open(final, "w", encoding="utf-8") as fh:
        fh.write("x")
    assert unique_path(path) == str(out_dir / "abc_2.mp3")


# ── 打开所在文件夹 handler（no-window）────────────────────────────────────
def test_open_folder_handler_uses_no_window(monkeypatch, tmp_path):

    import lib.procutil as procutil

    monkeypatch.setattr(procutil, "_is_windows", lambda: True)
    started: list = []
    monkeypatch.setattr(os, "startfile", lambda path: started.append(path))
    ok = procutil.open_in_folder(str(tmp_path))
    assert ok is True
    # 目录 → os.startfile（本身无 console，不经过 subprocess）
    assert started == [str(tmp_path)]
