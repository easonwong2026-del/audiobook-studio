"""补录 / 临时配音导出命名工具（纯函数，禁止 import gradio）。

统一处理导出 UX 的文件名规则：
- 非法字符清洗：``< > : " / \\ | ? *`` 与控制字符替换为 ``_``；
- 尾部空格 / 尾部 ``.`` 去除；空文件名回退默认名；
- 扩展名归一：用户输入 ``abc`` + MP3 → ``abc.mp3``；输入 ``abc.wav`` 但选
  MP3 → ``abc.mp3``（绝不生成 ``abc.wav.mp3``）；
- 重名不静默覆盖：``abc.mp3`` → ``abc_2.mp3`` → ``abc_3.mp3``。
"""
from __future__ import annotations

import os
import re

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_KNOWN_AUDIO_EXTS = {".wav", ".mp3", ".m4b", ".m4a", ".flac", ".ogg", ".aac", ".opus"}

_DEFAULT_NAME = "export"


def sanitize_filename(name: str, fallback: str = _DEFAULT_NAME) -> str:
    """清洗用户输入的导出名称，返回安全的文件名（不含扩展名语义）。

    Args:
        name: 用户输入的名称（可能含非法字符 / 尾部空格 / 尾部点）。
        fallback: 清洗后为空时的回退名。

    Returns:
        清洗后的文件名；保证非空、不以空格或 ``.`` 结尾、不含非法字符。
    """
    text = str(name or "").strip()
    text = _ILLEGAL_CHARS.sub("_", text)
    # Windows 文件名不能以空格或点结尾（且 "." 会被 shell/资源管理器折叠）。
    text = text.rstrip(". ")
    if not text:
        return fallback
    return text


def strip_extension(name: str) -> str:
    """去掉尾部已知音频扩展名（用于扩展名归一）。

    ``abc.wav`` → ``abc``；``abc.wav.mp3`` → ``abc.wav``（只剥最后一层）；
    无已知音频扩展名时原样返回，避免误伤含点的普通名称。
    """
    base, ext = os.path.splitext(str(name or "").strip())
    if ext.lower() in _KNOWN_AUDIO_EXTS:
        return base
    return str(name or "").strip()


def normalize_export_name(name: str, fmt: str, fallback: str = _DEFAULT_NAME) -> str:
    """把用户输入名称归一为 ``<base>.<fmt>``。

    Args:
        name: 用户输入名称（可为 ``abc`` 或 ``abc.wav`` 等）。
        fmt: 目标格式（``wav`` / ``mp3`` / ``m4b``，含点与否均可）。
        fallback: 空名回退。

    Returns:
        仅文件名（不含目录），如 ``abc.mp3``。
    """
    safe = sanitize_filename(name, fallback=fallback)
    base = strip_extension(safe)
    ext = str(fmt or "").strip().lower().lstrip(".")
    if ext not in {"wav", "mp3", "m4b"}:
        ext = "wav"
    return f"{base}.{ext}"


def build_export_path(out_dir: str, name: str, fmt: str,
                      fallback: str = _DEFAULT_NAME) -> str:
    """生成导出文件路径：``<out_dir>/<normalize_export_name>``。

    仅做路径拼接，不处理重名（重名由 ``unique_path`` 处理）。
    """
    return os.path.join(out_dir, normalize_export_name(name, fmt, fallback=fallback))


def unique_path(path: str) -> str:
    """重名自动加后缀：``abc.mp3`` → ``abc_2.mp3`` → ``abc_3.mp3``。

    已存在目标时依次尝试 ``<base>_2<ext>``、``<base>_3<ext>`` …，
    绝不静默覆盖既有文件。
    """
    candidate = os.path.abspath(str(path or ""))
    if not os.path.exists(candidate):
        return candidate
    directory = os.path.dirname(candidate)
    stem, ext = os.path.splitext(os.path.basename(candidate))
    counter = 2
    while True:
        candidate = os.path.join(directory, f"{stem}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


__all__ = [
    "build_export_path",
    "normalize_export_name",
    "sanitize_filename",
    "strip_extension",
    "unique_path",
]
