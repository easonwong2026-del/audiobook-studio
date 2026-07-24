"""音频后处理：响度归一（LUFS-16）+ 轻量人声均衡（默认关闭）。

全部基于 numpy / scipy / pyloudnorm，CPU 可跑，不加载任何 TTS 模型。
模块在 audio_pipeline.export_book 中被懒加载调用，顶层不引入重依赖。
"""
from __future__ import annotations

import logging

import numpy as np
from scipy.io import wavfile

logger = logging.getLogger(__name__)


def normalize_loudness(wav_path: str, target_lufs: float = -16.0,
                       tp: float = -1.5, lra: float = 11.0) -> str:
    """将 WAV 响度归一化到 target_lufs（默认 -16 LUFS）。

    用 pyloudnorm 量测当前响度，按差值做单遍增益，并把峰值限制在 TP 上限，
    使多角色 / 多批次合成音频音量统一。原地写回 wav_path，返回该路径。

    Args:
        wav_path: 输入/输出 WAV 路径。
        target_lufs: 目标集成响度，默认 -16.0（有声书常用标准）。
        tp: True Peak 上限（线性 dB），默认 -1.5，防止增益后削波。
        lra: 预留参数（响度范围），当前单遍增益不使用，保留以便后续扩展。

    Returns:
        写回后的 wav_path。
    """
    import pyloudnorm as pyln

    rate, data = wavfile.read(wav_path)
    if data.dtype == np.int16:
        audio = data.astype(np.float64) / 32768.0
        is_int = True
    elif np.issubdtype(data.dtype, np.floating):
        audio = data.astype(np.float64)
        is_int = False
    else:
        audio = data.astype(np.float64)
        is_int = False

    # pyloudnorm 需要至少约 0.4s 音频；过短或退化信号直接走软归一分支。
    min_samples = int(rate * 0.4)
    loudness = None
    if audio.shape[0] >= min_samples:
        try:
            meter = pyln.Meter(rate)
            loudness = meter.integrated_loudness(audio)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("响度量测失败，跳过归一：%s", exc)
            loudness = None

    if loudness is not None and np.isfinite(loudness) and loudness > -60.0:
        # 单遍增益到目标响度；限制增益范围，避免极端信号爆音。
        delta = target_lufs - loudness
        delta = min(max(delta, -30.0), 20.0)
        audio = audio * (10.0 ** (delta / 20.0))
        # True Peak 限制：增益后若超过 TP 上限则整体缩放。
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        tp_lin = 10.0 ** (tp / 20.0)
        if peak > tp_lin:
            audio = audio / peak * tp_lin
    else:
        # 退化 / 近静音信号（纯直流或全静音）不强行提增益，仅做峰值软归一。
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0:
            audio = audio / peak * 0.9
        logger.debug("响度归一跳过（当前=%s，视为退化信号）", loudness)

    audio = np.clip(audio, -1.0, 1.0)
    if is_int:
        out = (audio * 32767.0).astype(np.int16)
    else:
        out = audio.astype(data.dtype)
    wavfile.write(wav_path, rate, out)
    return wav_path


def apply_eq(wav_path: str, enable: bool = False,
             highpass_hz: float = 80.0, lowpass_hz: float = 12000.0) -> str:
    """轻量人声均衡：高通 + 低通补偿（scipy biquad，纯 numpy）。

    用于补偿不同参考音色之间的频响差异。默认关闭（enable=False 时直接
    返回原路径，不改动文件），保证零回归。

    Args:
        wav_path: 输入/输出 WAV 路径。
        enable: 是否启用均衡，默认 False。
        highpass_hz: 高通截止频率，默认 80Hz（去掉低频隆隆声）。
        lowpass_hz: 低通截止频率，默认 12000Hz（削弱齿音/高频毛刺）。

    Returns:
        写回后的 wav_path（未启用时即原路径）。
    """
    from scipy.signal import butter, sosfilt

    if not enable:
        return wav_path

    rate, data = wavfile.read(wav_path)
    if data.dtype == np.int16:
        audio = data.astype(np.float64) / 32768.0
        is_int = True
    elif np.issubdtype(data.dtype, np.floating):
        audio = data.astype(np.float64)
        is_int = False
    else:
        audio = data.astype(np.float64)
        is_int = False

    nyq = rate / 2.0
    # 频率必须落在 (0, nyquist) 内，做安全夹紧。
    hp = min(max(highpass_hz, 1.0), nyq * 0.98)
    lp = min(lowpass_hz, nyq * 0.98)
    if lp <= hp:
        lp = nyq * 0.98

    # 2 阶 Butterworth 高通 + 低通（sos 形式，数值稳定）。
    hp_sos = butter(2, hp / nyq, btype="high", output="sos")
    lp_sos = butter(2, lp / nyq, btype="low", output="sos")
    processed = sosfilt(hp_sos, audio, axis=0)
    processed = sosfilt(lp_sos, processed, axis=0)

    peak = float(np.max(np.abs(processed))) if processed.size else 0.0
    if peak > 1.0:
        processed = processed / peak * 0.99
    processed = np.clip(processed, -1.0, 1.0)

    if is_int:
        out = (processed * 32767.0).astype(np.int16)
    else:
        out = processed.astype(data.dtype)
    wavfile.write(wav_path, rate, out)
    return wav_path
