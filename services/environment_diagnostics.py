"""不执行 GPU 推理的运行环境诊断。"""
from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib import config
from lib.environment import resolve_python_interpreter
from lib.procutil import run_no_window

STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}


def _check(name: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """隔离单项异常，确保其余诊断继续。"""
    try:
        result = callback()
        return {
            "name": name,
            "status": result.get("status", "ok"),
            "message": str(result.get("message", "")),
            "details": result.get("details", {}),
            "suggestion": str(result.get("suggestion", "")),
        }
    except Exception as exc:  # noqa: BLE001 - isolate one diagnostic from the rest
        return {
            "name": name,
            "status": "error",
            "message": f"检查失败：{type(exc).__name__}",
            "details": {},
            "suggestion": "请根据该检查项手工核对环境配置后重试。",
        }


def _path_state(path: str, *, empty_is_warning: bool = False) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        return {
            "status": "error",
            "message": "目录不存在",
            "details": {"path": str(p)},
            "suggestion": "创建目录或通过对应 AUDIOBOOK_STUDIO_* 环境变量指定正确位置。",
        }
    if empty_is_warning and not any(p.iterdir()):
        return {
            "status": "warning",
            "message": "目录存在但为空",
            "details": {"path": str(p)},
            "suggestion": "从 IndexTTS2 官方渠道下载模型并放入该目录。",
        }
    return {"status": "ok", "message": "目录存在", "details": {"path": str(p)}}


def run_environment_diagnostics() -> dict[str, Any]:
    """返回可序列化的本地运行环境诊断对象。"""
    checks: list[dict[str, Any]] = []
    add = lambda name, fn: checks.append(_check(name, fn))

    add("操作系统", lambda: {
        "status": "ok",
        "message": f"{platform.system()} {platform.release()}",
        "details": {"platform": platform.platform()},
    })
    add("Python", lambda: {
        "status": "ok" if sys.version_info >= (3, 10) else "warning",
        "message": platform.python_version(),
        "details": {"executable": sys.executable},
        "suggestion": "建议使用 Python 3.10 或更高版本。" if sys.version_info < (3, 10) else "",
    })
    add("Gradio", lambda: {
        "status": "ok" if importlib.metadata.version("gradio").split(".")[0] == "5" else "warning",
        "message": importlib.metadata.version("gradio"),
        "details": {},
        "suggestion": "请安装 requirements.txt 约束的 Gradio >=5.50,<6。",
    })

    def data_dir_check():
        path = Path(config.get_data_dir())
        readable = os.access(path, os.R_OK)
        writable = os.access(path, os.W_OK)
        return {
            "status": "ok" if readable and writable else "error",
            "message": "可读写" if readable and writable else "不可读写",
            "details": {"path": str(path), "readable": readable, "writable": writable},
            "suggestion": "在设置页选择当前用户有读写权限的数据目录。",
        }
    add("数据目录", data_dir_check)

    model_dir = config.get_model_dir()
    index_dir = str(Path(model_dir).parent)
    add("IndexTTS2 项目目录", lambda: _path_state(index_dir))

    def python_check():
        resolution = resolve_python_interpreter()
        return {
            "status": "ok" if resolution.executable else "error",
            "message": "解释器存在" if resolution.executable else "解释器不存在",
            "details": {
                "executable": resolution.executable,
                "source": resolution.source,
                "warnings": resolution.warnings,
            },
            "suggestion": (
                ""
                if resolution.executable
                else "设置 AUDIOBOOK_STUDIO_PYTHON 指向 IndexTTS2 虚拟环境的 Python。"
            ),
        }
    add("IndexTTS2 Python", python_check)
    add("模型目录", lambda: _path_state(model_dir, empty_is_warning=True))

    def ffmpeg_check():
        executable = config.get_ffmpeg_path()
        found = executable if Path(executable).is_file() else shutil.which(executable)
        if not found:
            return {
                "status": "error", "message": "未找到 FFmpeg", "details": {},
                "suggestion": "安装 FFmpeg 并加入 PATH，或设置 AUDIOBOOK_STUDIO_FFMPEG。",
            }
        proc = run_no_window(
            [found, "-version"], capture_output=True, text=True, timeout=5, check=False,
        )
        first = (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "message": first[:160] or "FFmpeg 可执行",
            "details": {"executable": found},
            "suggestion": "重新安装可正常执行的 FFmpeg。" if proc.returncode else "",
        }
    add("FFmpeg", ffmpeg_check)

    def nvidia_check():
        binary = shutil.which("nvidia-smi")
        if not binary:
            return {
                "status": "warning", "message": "未检测到 nvidia-smi", "details": {"gpu_detected": False},
                "suggestion": "需要 GPU 推理时，请安装兼容的 NVIDIA 驱动；无 NVIDIA GPU 可忽略。",
            }
        proc = run_no_window(
            [binary, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8, check=False,
        )
        names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return {
            "status": "ok" if proc.returncode == 0 and names else "warning",
            "message": ", ".join(names) if names else "nvidia-smi 无法读取 GPU",
            "details": {"gpu_detected": bool(names), "nvidia_smi_runnable": proc.returncode == 0},
            "suggestion": "检查 NVIDIA 驱动和 nvidia-smi 是否匹配。",
        }
    add("NVIDIA GPU", nvidia_check)

    def torch_check():
        try:
            import torch
        except ImportError:
            return {
                "status": "warning", "message": "Torch 未安装", "details": {"importable": False},
                "suggestion": "在 IndexTTS2 虚拟环境中按其官方说明安装 Torch；不要安装到仓库。",
            }
        cuda = bool(torch.cuda.is_available())
        device = torch.cuda.get_device_name(0) if cuda else None
        return {
            "status": "ok" if cuda else "warning",
            "message": f"CUDA 可用：{device}" if cuda else "Torch 可导入，但 CUDA 不可用",
            "details": {"importable": True, "version": str(torch.__version__), "cuda_available": cuda, "device": device},
            "suggestion": "检查 Torch/CUDA/驱动版本兼容性。" if not cuda else "",
        }
    add("Torch / CUDA", torch_check)

    def projects_check():
        root = Path(config.get_projects_root())
        entries = list(root.iterdir()) if root.is_dir() else []
        projects = [p for p in entries if p.is_dir() and not p.name.startswith(".tmp_")]
        temporary = [p.name for p in entries if p.is_dir() and p.name.startswith(".tmp_")]
        return {
            "status": "warning" if temporary else "ok",
            "message": f"{len(projects)} 个项目；{len(temporary)} 个临时目录",
            "details": {"project_count": len(projects), "temporary_project_count": len(temporary)},
            "suggestion": "确认没有创建任务运行后，可备份并移除残留 .tmp_ 项目目录。" if temporary else "",
        }
    add("项目目录", projects_check)

    overall = max((item["status"] for item in checks), key=lambda s: STATUS_RANK.get(s, 2))
    return {"status": overall, "checks": checks}


def diagnostics_to_markdown(report: dict[str, Any]) -> str:
    """生成可复制、无密钥内容的 Markdown 报告。"""
    symbols = {"ok": "✅", "warning": "⚠️", "error": "❌"}
    lines = [f"# Audiobook Studio 环境诊断\n\n总体状态：**{report.get('status', 'error')}**", ""]
    for item in report.get("checks", []):
        lines.append(
            f"- {symbols.get(item.get('status'), '❓')} **{item.get('name')}**：{item.get('message', '')}"
        )
        if item.get("suggestion"):
            lines.append(f"  - 建议：{item['suggestion']}")
    return "\n".join(lines)


def diagnostics_table(report: dict[str, Any]) -> list[list[str]]:
    return [
        [item.get("name", ""), item.get("status", ""), item.get("message", ""), item.get("suggestion", "")]
        for item in report.get("checks", [])
    ]
