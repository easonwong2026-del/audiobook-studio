"""运行环境检查：路径全部配置化，给出可操作诊断（无 GUI 依赖，可单测）。

路径优先级统一为：环境变量 > config.json > 自动探测 > UI 首次配置。
本模块集中实现「模型 / ffmpeg / Python / CUDA」四项检查，供启动器 / app 在
启动期给出明确提示，避免模型缺失时崩溃或 ffmpeg 缺失时静默改格式。
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config as _cfg

logger = logging.getLogger(__name__)
PROGRAM_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PythonResolution:
    """无副作用的 Python 解释器解析结果。"""

    executable: str | None
    source: str
    warnings: list[str]


def resolve_python_interpreter() -> PythonResolution:
    """按 launcher 顺序解析解释器，不打印、不退出、不启动进程。"""
    warnings: list[str] = []
    configured = os.environ.get("AUDIOBOOK_STUDIO_PYTHON")
    if configured:
        if os.path.isfile(configured):
            return PythonResolution(configured, "environment", warnings)
        warnings.append(f"AUDIOBOOK_STUDIO_PYTHON 指向的文件不存在：{configured}")

    venv = PROGRAM_DIR.parent / "index-tts" / ".venv"
    for candidate in (
        venv / "Scripts" / "python.exe",
        venv / "bin" / "python",
    ):
        if candidate.is_file():
            return PythonResolution(str(candidate), "sibling_venv", warnings)

    for command in ("python", "python3"):
        found = shutil.which(command)
        if found:
            return PythonResolution(found, "path", warnings)
    return PythonResolution(None, "missing", warnings)


@dataclass
class EnvironmentCheckResult:
    """运行环境检查结果（方案 §5.7）。

    Attributes:
        python_ok: Python 解释器可用。
        model_ok: IndexTTS2 模型目录存在且含 config.yaml。
        ffmpeg_ok: ffmpeg 系统二进制可用（mp3 / m4b 导出需要）。
        cuda_ok: CUDA 可用（GPU 推理）。
        messages: 可读诊断信息列表（供 UI / 启动器展示）。
    """

    python_ok: bool
    model_ok: bool
    ffmpeg_ok: bool
    cuda_ok: bool
    messages: list[str]


def _check_python() -> bool:
    """Python 解释器总是可用（本进程即 Python），返回 True。"""
    return True


def _check_model() -> bool:
    """模型目录存在且含 config.yaml 才算就绪。"""
    model_dir = _cfg.get_model_dir()
    return os.path.isdir(model_dir) and os.path.isfile(
        os.path.join(model_dir, "config.yaml")
    )


def _check_ffmpeg() -> bool:
    """ffmpeg 在 PATH 或环境变量指定路径可用。"""
    return shutil.which(_cfg.get_ffmpeg_path()) is not None


def _check_cuda() -> bool:
    """CUDA 可用性（无 torch 时视为 False，不报错）。"""
    try:
        import torch
    except Exception:  # pylint: disable=broad-except
        return False
    try:
        return bool(getattr(torch.cuda, "is_available", lambda: False)())
    except Exception:  # pylint: disable=broad-except
        return False


def check_environment() -> EnvironmentCheckResult:
    """运行完整环境检查，返回结构化结果（含可读提示）。"""
    python_ok = _check_python()
    model_ok = _check_model()
    ffmpeg_ok = _check_ffmpeg()
    cuda_ok = _check_cuda()

    messages: list[str] = []
    if not python_ok:
        messages.append("Python 解释器不可用（不应发生）。")
    if model_ok:
        messages.append(f"✅ 模型目录就绪：{_cfg.get_model_dir()}")
    else:
        messages.append(
            "⚠ 模型目录未找到。请设置环境变量 AUDIOBOOK_STUDIO_MODEL_DIR、"
            "config.json 的 model_dir，或在 UI 中首次配置模型路径。"
        )
    if ffmpeg_ok:
        messages.append(f"✅ ffmpeg 就绪：{_cfg.get_ffmpeg_path()}")
    else:
        messages.append(
            "⚠ 未检测到 ffmpeg（mp3/m4b 导出需要）。请安装并加入 PATH，"
            "或设置环境变量 AUDIOBOOK_STUDIO_FFMPEG。WAV 导出不受影响。"
        )
    if cuda_ok:
        messages.append("✅ CUDA 可用（GPU 推理）。")
    else:
        messages.append("⚠ 未检测到 CUDA（将不可用 GPU 推理；仅检查，不影响启动）。")
    return EnvironmentCheckResult(
        python_ok=python_ok,
        model_ok=model_ok,
        ffmpeg_ok=ffmpeg_ok,
        cuda_ok=cuda_ok,
        messages=messages,
    )
