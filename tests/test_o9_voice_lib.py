"""O9 音色库增强：扫描 / 分类 / 搜索（纯函数，无 gradio / 无 torch）。

验证（设计 §6 O9 / §12.3）：
- scan_voice_library(search="温柔") 只返回 温柔_a.wav；
- scan_voice_library(category="温柔") 命中；category="未分类" 命中无前缀文件；
- list_categories() 去重含 温柔/沉稳/未分类；
- 分类由文件名首 _ 前缀推导正确（无前缀 -> 未分类）。

用 tmp_path + monkeypatch(config.get_voice_library -> tmp_path) 写几个假 wav。
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.voice_lib as voice_lib  # noqa: E402
import lib.config as cfg  # noqa: E402


@pytest.fixture
def voice_library(tmp_path, monkeypatch):
    # 把音色库目录重定向到临时目录（voice_lib 经 _cfg.get_voice_library 读取）
    monkeypatch.setattr(voice_lib._cfg, "get_voice_library", lambda: str(tmp_path))
    # 写几个假 wav（不同分类 / 无前缀）
    for name in ("温柔_a.wav", "沉稳_b.wav", "plain.wav"):
        p = tmp_path / name
        p.write_bytes(b"RIFF....WAVE")  # 内容无所谓，仅用于 size_kb / 扫描
    return str(tmp_path)


def test_scan_by_search(voice_library):
    res = voice_lib.scan_voice_library(search="温柔")
    names = [r["name"] for r in res]
    assert names == ["温柔_a.wav"], f"search=温柔 应只命中 温柔_a.wav，实际 {names}"


def test_scan_by_category(voice_library):
    res = voice_lib.scan_voice_library(category="温柔")
    assert [r["name"] for r in res] == ["温柔_a.wav"], \
        f"category=温柔 应命中 温柔_a.wav，实际 {[r['name'] for r in res]}"
    # 无前缀文件 -> 未分类
    res2 = voice_lib.scan_voice_library(category="未分类")
    assert [r["name"] for r in res2] == ["plain.wav"], \
        f"category=未分类 应命中 plain.wav，实际 {[r['name'] for r in res2]}"


def test_scan_returns_structured_fields(voice_library):
    res = voice_lib.scan_voice_library()
    assert len(res) == 3, res
    for r in res:
        for k in ("name", "path", "size_kb", "category", "ext"):
            assert k in r, f"返回项缺少字段 {k}: {r}"


def test_list_categories_dedup(voice_library):
    cats = voice_lib.list_categories()
    assert set(cats) == {"温柔", "沉稳", "未分类"}, f"分类去重错误: {cats}"


def test_category_derivation(voice_library):
    # 分类由首 _ 前缀推导
    res = {r["name"]: r["category"] for r in voice_lib.scan_voice_library()}
    assert res["温柔_a.wav"] == "温柔"
    assert res["沉稳_b.wav"] == "沉稳"
    assert res["plain.wav"] == "未分类"


def test_empty_library_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_lib._cfg, "get_voice_library", lambda: str(tmp_path))
    assert voice_lib.scan_voice_library() == []
    assert voice_lib.list_categories() == []
