"""音频格式统一：采样率重采样 / 声道转换 / dtype 转换 / 空音频检查 / 峰值溢出保护。

所有拼接（整本导出、章节试听、补录导出、TTS OOM 拆段拼接）必须复用本模块，
确保不同来源 WAV（22050/44100、单/双声道、float32/int16）在拼接前被统一到
目标采样率、目标声道数与目标 dtype，杜绝因格式不一致导致的拼接错误或音色突变。

设计要点：
- 纯 NumPy + SciPy（``scipy.io.wavfile``），无 Gradio / torch 依赖，可单测。
- 重采样用线性插值（``np.interp``），对任意（含分段常量）信号单调、无振铃、开销低。
- dtype 转换统一走 int16（PCM 16bit），float 归一化到 [-1, 1]，并做峰值裁剪保护。
- 空音频（0 采样点）显式抛错，避免下游 ``np.concatenate`` 静默产出空数组。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Union

import numpy as np
from scipy.io import wavfile

logger = logging.getLogger(__name__)

# 默认目标规格（与既有导出链路一致：22.05kHz 单声道 int16）。
DEFAULT_TARGET_RATE = 22050
DEFAULT_TARGET_CHANNELS = 1
DEFAULT_TARGET_DTYPE = np.int16

# 峰值溢出保护：float -> int16 时裁剪到 int16 表示范围。
_INT16_MAX = 32767
_INT16_MIN = -32768


@dataclass
class NormalizedAudio:
    """统一规格后的音频数据。

    Attributes:
        data: NumPy 数组；单声道为 1D，多声道为 2D（shape=(n, channels)）。
        rate: 采样率（Hz）。
        channels: 声道数（1=单声道，>1=多声道）。
        dtype: 实际 dtype（统一为 ``target_dtype``）。
    """

    data: np.ndarray
    rate: int
    channels: int
    dtype: np.dtype


def _resample_linear(data: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    """线性插值重采样（保持 1D 单声道或 2D 多声道）。

    用 ``np.interp`` 在统一时间轴上重采样：对分段常量 / 阶跃信号单调、无 Fourier
    振铃，开销低，足够音频拼接前的格式统一使用。
    """
    if orig_rate == target_rate:
        return data
    n = data.shape[0]
    if n == 0:
        return data
    old_t = np.linspace(0.0, (n - 1) / orig_rate, n)
    new_n = max(1, int(round(n * target_rate / orig_rate)))
    new_t = np.linspace(0.0, (n - 1) / orig_rate, new_n)
    if data.ndim == 1:
        return np.interp(new_t, old_t, data)
    out = np.empty((new_n, data.shape[1]), dtype=data.dtype)
    for c in range(data.shape[1]):
        out[:, c] = np.interp(new_t, old_t, data[:, c])
    return out


def _to_mono(data: np.ndarray) -> tuple[np.ndarray, int]:
    """多声道下混为单声道（均值）；返回 (data, channels)。"""
    if data.ndim == 1:
        return data, 1
    channels = data.shape[1]
    if channels == 1:
        return data[:, 0], 1
    # 多声道 -> 单声道：按通道均值下混
    return data.mean(axis=1).astype(data.dtype), 1


def _to_channels(data: np.ndarray, target_channels: int) -> tuple[np.ndarray, int]:
    """按目标声道数调整：单声道复制为 N 通道；多声道下混为 1 通道。"""
    if data.ndim == 1:
        cur = 1
        arr = data
    else:
        cur = data.shape[1]
        arr = data
    if cur == target_channels:
        return arr, cur
    if target_channels == 1 and cur > 1:
        return _to_mono(arr)
    if target_channels > 1 and cur == 1:
        return np.repeat(arr[:, None], target_channels, axis=1), target_channels
    # 多声道 -> 多声道（不一致）：简单下混/上混到目标
    if cur > target_channels:
        return _to_mono(arr)
    return np.repeat(arr[:, None], target_channels, axis=1), target_channels


def _float_to_int16(data: np.ndarray) -> np.ndarray:
    """float（-1..1 或任意范围）裁剪后转 int16（峰值溢出保护）。"""
    clipped = np.clip(data, -1.0, 1.0)
    return (clipped * _INT16_MAX).astype(np.int16)


def _convert_dtype(data: np.ndarray, target_dtype: np.dtype) -> tuple[np.ndarray, np.dtype]:
    """统一 dtype：float <-> int16 双向转换，并做峰值裁剪保护。"""
    if target_dtype == np.int16:
        if np.issubdtype(data.dtype, np.floating):
            return _float_to_int16(data), np.dtype(np.int16)
        if data.dtype == np.int16:
            return data, np.dtype(np.int16)
        # 其它整型 -> int16
        info = np.iinfo(data.dtype) if np.issubdtype(data.dtype, np.integer) else None
        if info is not None and (info.min < _INT16_MIN or info.max > _INT16_MAX):
            # 超出 int16 范围：先归一化到 float 再裁剪
            norm = data.astype(np.float64) / max(abs(float(info.min)), abs(float(info.max)))
            return _float_to_int16(norm), np.dtype(np.int16)
        return data.astype(np.int16), np.dtype(np.int16)

    # 目标为浮点（如 float32）
    if np.issubdtype(data.dtype, np.floating):
        return data.astype(target_dtype), np.dtype(target_dtype)
    # int -> 浮点归一化到 [-1, 1]
    info = np.iinfo(data.dtype)
    norm = data.astype(target_dtype) / max(abs(float(info.min)), abs(float(info.max)))
    return norm, np.dtype(target_dtype)


def load_and_normalize_wav(
    path: str,
    target_rate: int = DEFAULT_TARGET_RATE,
    target_channels: int = DEFAULT_TARGET_CHANNELS,
    target_dtype=np.int16,
) -> NormalizedAudio:
    """加载并统一规范化为目标采样率 / 声道 / dtype。

    Args:
        path: WAV 文件路径。
        target_rate: 目标采样率（Hz），默认 22050。
        target_channels: 目标声道数，默认 1（单声道）。
        target_dtype: 目标 dtype，默认 ``np.int16``。

    Returns:
        ``NormalizedAudio``（data / rate / channels / dtype 均为目标规格）。

    Raises:
        ValueError: 文件不存在、非 WAV、或音频为空（0 采样点）。
        RuntimeError: 读取失败（断损坏等）。
    """
    if not path or not os.path.isfile(path):
        raise ValueError(f"音频文件不存在或路径为空：{path!r}")
    try:
        rate, data = wavfile.read(path)
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"读取 WAV 失败：{path!r}（{exc}）") from exc

    data = np.asarray(data)
    if data.size == 0 or (data.ndim == 1 and data.shape[0] == 0):
        raise ValueError(f"音频为空（0 采样点）：{path!r}")

    # 1) 声道统一
    data, channels = _to_channels(data, target_channels)
    # 2) 采样率重采样
    if rate != target_rate:
        data = _resample_linear(data, rate, target_rate)
        rate = target_rate
    # 3) dtype 转换（峰值溢出保护）
    data, out_dtype = _convert_dtype(data, np.dtype(target_dtype))
    return NormalizedAudio(data=data, rate=rate, channels=channels, dtype=out_dtype)


def concatenate_normalized(
    paths: list[str],
    target_rate: int = DEFAULT_TARGET_RATE,
    target_channels: int = DEFAULT_TARGET_CHANNELS,
    target_dtype=np.int16,
) -> tuple[np.ndarray, int, int]:
    """把多个 WAV 统一规格后拼接为单条数组。

    复用 ``load_and_normalize_wav`` 保证每个文件在拼接前已被统一到目标采样率 /
    声道 / dtype，避免 ``np.concatenate`` 因形状 / dtype 不一致失败。

    Args:
        paths: 按播放顺序的 WAV 路径列表（至少 1 个）。
        target_rate / target_channels / target_dtype: 统一目标规格。

    Returns:
        ``(data, rate, channels)``：拼接后的 NumPy 数组（单声道 1D / 多声道 2D）、
        采样率、声道数。

    Raises:
        ValueError: paths 为空或任一文件缺失 / 为空。
    """
    if not paths:
        raise ValueError("concatenate_normalized 需要至少一个音频路径")
    # target_rate 为 None 时，以首个文件自身采样率为基准（同质输入不重采样，
    # 混合输入统一到首个速率），与导出链路保持一致。
    canonical_rate = target_rate
    if canonical_rate is None and paths:
        r0, _ = wavfile.read(paths[0])
        canonical_rate = int(r0)
    arrays: list[np.ndarray] = []
    rate = canonical_rate
    channels = target_channels
    for p in paths:
        na = load_and_normalize_wav(
            p, target_rate=canonical_rate, target_channels=target_channels,
            target_dtype=target_dtype,
        )
        arrays.append(na.data)
        rate = na.rate
        channels = na.channels
    combined = np.concatenate(arrays)
    return combined, rate, channels


def write_wav(path: str, data: np.ndarray, rate: int) -> str:
    """写 WAV（自动建父目录），返回写入路径。"""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    wavfile.write(path, rate, np.asarray(data))
    return path
