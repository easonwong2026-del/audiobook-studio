"""音频文件校验：存在性 / 可读性 / 格式可解码 / 时长合理。

错误统一转换成用户可理解的中文消息，供音色绑定等入口直接展示，
不向界面暴露原始堆栈。
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

# 常见可接受的人声参考音频扩展名（大小写不敏感）
AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma",
}

# 最短合理时长（秒）：过短多半不是有效人声样本
_MIN_DURATION_SECONDS = 0.5


def validate_audio_file(path: str | Path) -> tuple[bool, str]:
    """校验音频文件。

    Args:
        path: 音频文件路径。

    Returns:
        ``(ok, message)``；``ok=True`` 且 ``message`` 为空表示通过，
        ``ok=False`` 时 ``message`` 为可直接展示给用户的错误说明。
    """
    p = Path(path)
    if not p.exists():
        return False, "音频文件不存在，请重新选择参考音频。"
    if not p.is_file():
        return False, "所选路径不是文件，请重新选择参考音频。"
    if not os.access(p, os.R_OK):
        return False, "音频文件不可读，请检查文件权限后重试。"
    ext = p.suffix.lower()
    if ext not in AUDIO_EXTENSIONS:
        label = ext or "（无扩展名）"
        return (
            False,
            f"不支持的音频格式 {label}，请选择 WAV / MP3 / FLAC / M4A 等常见格式。",
        )
    duration = _probe_duration(p)
    if duration is not None and duration < _MIN_DURATION_SECONDS:
        return (
            False,
            f"音频时长过短（约 {duration:.1f} 秒），可能不是有效的人声样本，请换一段更长的录音。",
        )
    return True, ""


def probe_duration(path: str | Path) -> float | None:
    """尽力探测音频时长（秒）；无法探测返回 None（不阻断流程）。"""
    return _probe_duration(Path(path))


def _probe_duration(path: Path) -> float | None:
    """WAV 用标准库解析；其余格式暂返回 None（可后续接 ffprobe）。"""
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return frames / rate
    except (wave.Error, OSError, EOFError, ValueError):
        return None
    return None
