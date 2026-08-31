"""音色库扫描（纯函数，禁止 import gradio）。

O9：在既有 ``v_lib`` 下拉之外，提供一个可搜索 / 可分类的「音色库浏览器」。扫
``config.get_voice_library()`` 目录，按文件名首 ``_`` 前缀推导分类
（如 ``温柔_xxx.wav`` → ``温柔``；无前缀 → ``未分类``），返回结构化列表供
``gr.Dataframe`` 渲染。

本模块只做扫描与归类，不触发任何播放逻辑（播放复用 ``app.py`` 既有
``play_lib_voice`` 与 ``v_lib.change`` 接线）。
"""
from __future__ import annotations

import logging
import os

from . import config as _cfg

logger = logging.getLogger(__name__)

# 支持的音频扩展名（用于过滤非音频文件）
_VOICE_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")


def _category_of(filename: str) -> str:
    """由文件名推导分类：首个 ``_`` 之前的部分；无 ``_`` → ``未分类``。"""
    base = os.path.splitext(filename)[0]
    if "_" in base:
        return base.split("_", 1)[0]
    return "未分类"


def scan_voice_library(search: str = "", category: str = None) -> list[dict]:
    """扫描音色库目录，返回结构化列表（O9 浏览器数据源）。

    每项含 ``name``（文件名）/ ``path``（绝对路径）/ ``size_kb``（大小，KB）/
    ``category``（分类）/ ``ext``（扩展名）。

    Args:
        search: 关键字过滤（匹配文件名或分类，大小写不敏感），默认 "" 不过滤。
        category: 分类过滤（精确匹配），默认 None 不过滤。

    Returns:
        音频 dict 列表（按文件名排序）；目录不存在时返回空列表。
    """
    root = _cfg.get_voice_library()
    results: list[dict] = []
    if not os.path.isdir(root):
        return results
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in _VOICE_EXTS or name.lower().endswith(".reference.wav"):
            continue
        try:
            size_kb = round(os.path.getsize(full) / 1024.0, 1)
        except OSError as exc:
            logger.debug("读取音频文件大小失败: %s", exc)
            size_kb = 0.0
        results.append({
            "name": name,
            "path": full,
            "size_kb": size_kb,
            "category": _category_of(name),
            "ext": ext,
        })
    # 搜索过滤（名称 / 分类含关键字）
    if search:
        s = search.strip().lower()
        if s:
            results = [
                r for r in results
                if s in r["name"].lower() or s in r["category"].lower()
            ]
    # 分类过滤（精确匹配）
    if category:
        results = [r for r in results if r["category"] == category]
    return results


def list_categories() -> list[str]:
    """返回音色库中所有去重分类（按名称排序）。目录不存在时返回空列表。"""
    root = _cfg.get_voice_library()
    cats: set[str] = set()
    if not os.path.isdir(root):
        return []
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in _VOICE_EXTS or name.lower().endswith(".reference.wav"):
            continue
        cats.add(_category_of(name))
    return sorted(cats)


def voice_names(category: str | None = None) -> list[str]:
    """返回下拉框使用的音色文件名，复用统一扫描与扩展名规则。"""
    return [item["name"] for item in scan_voice_library(category=category)]
