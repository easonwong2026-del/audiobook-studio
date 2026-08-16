"""不执行 GPU 推理的运行环境诊断。"""
from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib import config
from lib import environment as environment_lib
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
            "details": {"path": environment_lib.display_path(p, "model-dir")},
            "suggestion": "创建目录或通过对应 AUDIOBOOK_STUDIO_* 环境变量指定正确位置。",
        }
    if empty_is_warning and not any(p.iterdir()):
        return {
            "status": "warning",
            "message": "目录存在但为空",
            "details": {"path": environment_lib.display_path(p, "model-dir")},
            "suggestion": "从 IndexTTS2 官方渠道下载模型并放入该目录。",
        }
    return {
        "status": "ok",
        "message": "目录存在",
        "details": {"path": environment_lib.display_path(p, "model-dir")},
    }


def _safe_warning(value: str) -> str:
    """Keep diagnostic warnings useful without echoing configured paths."""
    if "AUDIOBOOK_STUDIO_PYTHON" in value:
        return "AUDIOBOOK_STUDIO_PYTHON 指向的文件不存在，已尝试回退解释器。"
    return value if not os.path.isabs(value) else "检测到配置警告（路径已隐藏）。"


def _model_dir_check(
    version: str,
    model_dir: str,
    *,
    source: str,
    selected: bool,
) -> dict[str, Any]:
    state = environment_lib.model_checkpoint_state(version, model_dir)
    detected_version = environment_lib.detect_model_version(model_dir)
    missing = list(state["missing_required"])
    complete = state["directory"] and not missing
    version_match = (
        detected_version == version if detected_version is not None else None
    )
    if not state["directory"]:
        status = "error" if selected else "warning"
        message = "目录不存在"
    elif missing:
        status = "warning"
        message = f"目录存在，缺少 {len(missing)} 组 checkpoint 文件"
    else:
        status = "ok"
        message = "目录存在，核心 checkpoint 文件齐全"
    if selected and version_match is False:
        status = "error"
        message = f"目录存在，但配置声明为 {detected_version}"
    return {
        "status": status,
        "message": message,
        "details": {
            "version": version,
            "selected": selected,
            "source": source,
            "model_dir": environment_lib.display_path(model_dir, "model-dir"),
            "directory_exists": bool(state["directory"]),
            "required_complete": bool(complete),
            "version_match": version_match,
            "config_name": state.get("config_name"),
            "detected_version": detected_version,
            "missing_files": missing,
            "optional_files_present": list(state["optional_present"]),
        },
        "suggestion": (
            "按该版本官方模型目录补齐缺失文件；本诊断不会联网或下载。"
            if missing
            else ""
        ),
    }


def run_environment_diagnostics() -> dict[str, Any]:
    """返回可序列化的本地运行环境诊断对象。"""
    checks: list[dict[str, Any]] = []
    add = lambda name, fn: checks.append(_check(name, fn))

    selection = environment_lib.resolve_engine_selection()
    model_dirs = environment_lib.resolve_model_directories()
    selected_model_dir = model_dirs[selection.version]["path"]
    selected_model_version = environment_lib.detect_model_version(selected_model_dir)
    selected_version_match = (
        selected_model_version == selection.version
        if selected_model_version is not None
        else None
    )

    add("操作系统", lambda: {
        "status": "ok",
        "message": f"{platform.system()} {platform.release()}",
        "details": {"platform": platform.platform()},
    })
    add("Python", lambda: {
        "status": "ok" if sys.version_info[:2] in ((3, 10), (3, 11)) else "warning",
        "message": platform.python_version(),
        "details": {
            "executable": environment_lib.display_path(sys.executable, "python"),
            "supported_range": "3.10-3.11",
        },
        "suggestion": "建议使用 Python 3.10 或 3.11。" if sys.version_info[:2] not in ((3, 10), (3, 11)) else "",
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
            "details": {
                "path": environment_lib.display_path(path, "data-dir"),
                "readable": readable,
                "writable": writable,
            },
            "suggestion": "在设置页选择当前用户有读写权限的数据目录。",
        }
    add("数据目录", data_dir_check)

    # Keep the summary checks on the same version-resolved directory used by
    # the per-version model inspection below.  The legacy config resolver is
    # still honored inside resolve_model_directories() for v2 compatibility.
    model_dir = selected_model_dir
    index_dir = str(Path(model_dir).parent)
    add("IndexTTS2 项目目录", lambda: _path_state(index_dir))

    add("引擎 / 版本", lambda: {
        "status": "warning" if selection.warnings else "ok",
        "message": f"{selection.engine} {selection.version}",
        "details": {
            "engine": selection.engine,
            "version": selection.version,
            "engine_source": selection.engine_source,
            "version_source": selection.version_source,
            "version_match": selected_version_match,
        },
        "suggestion": "；".join(selection.warnings),
    })

    def python_check():
        resolution = resolve_python_interpreter()
        return {
            "status": "ok" if resolution.executable else "error",
            "message": "解释器存在" if resolution.executable else "解释器不存在",
            "details": {
            "executable": resolution.executable,
                "source": resolution.source,
                "warnings": list(resolution.warnings),
            },
            "suggestion": (
                ""
                if resolution.executable
                else "设置 AUDIOBOOK_STUDIO_PYTHON 指向 IndexTTS2 虚拟环境的 Python。"
            ),
        }
    add("IndexTTS2 Python", python_check)
    add("模型目录", lambda: _path_state(model_dir, empty_is_warning=True))

    for version in environment_lib.SUPPORTED_ENGINE_VERSIONS:
        add(
            f"模型目录 {version}",
            lambda version=version: _model_dir_check(
                version,
                model_dirs[version]["path"],
                source=model_dirs[version]["source"],
                selected=version == selection.version,
            ),
        )

    def selected_model_check():
        state = environment_lib.model_checkpoint_state(selection.version, selected_model_dir)
        detected_version = environment_lib.detect_model_version(selected_model_dir)
        version_match = detected_version == selection.version if detected_version else None
        return {
            "status": "ok" if version_match is True else "warning" if version_match is None else "error",
            "message": (
                f"选中 {selection.version} 与模型目录配置匹配"
                if version_match is True
                else "未在模型配置中声明版本，无法完全确认匹配"
                if version_match is None
                else f"选中 {selection.version} 与模型配置 {detected_version} 不匹配"
            ),
            "details": {
                "selected_engine": selection.engine,
                "selected_version": selection.version,
                "selected_model_dir": environment_lib.display_path(selected_model_dir, "model-dir"),
                "config_name": state.get("config_name"),
                "detected_model_version": detected_version,
                "version_match": version_match,
                "missing_files": list(state["missing_required"]),
            },
            "suggestion": "检查 engine version 与对应模型目录；本诊断不会自动切换或下载。" if version_match is not True else "",
        }
    add("选中版本匹配", selected_model_check)

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
            encoding="utf-8", errors="replace",
        )
        first = (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "message": first[:160] or "FFmpeg 可执行",
            "details": {"executable": environment_lib.display_path(found, "ffmpeg")},
            "suggestion": "重新安装可正常执行的 FFmpeg。" if proc.returncode else "",
        }
    add("FFmpeg", ffmpeg_check)

    def nvidia_check():
        binary = shutil.which("nvidia-smi")
        if not binary:
            return {
                "status": "warning",
                "message": "未检测到 nvidia-smi",
                "details": {
                    "gpu_detected": False,
                    "gpu_name": None,
                    "driver_version": None,
                },
                "suggestion": "需要 GPU 推理时，请安装兼容的 NVIDIA 驱动；无 NVIDIA GPU 可忽略。",
            }
        proc = run_no_window(
            [binary, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8, check=False,
            encoding="utf-8", errors="replace",
        )
        names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return {
            "status": "ok" if proc.returncode == 0 and names else "warning",
            "message": ", ".join(names) if names else "nvidia-smi 无法读取 GPU",
            "details": {
                "gpu_detected": bool(names),
                "gpu_name": names[0] if names else None,
                "nvidia_smi_runnable": proc.returncode == 0,
            },
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
        try:
            cuda = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 - CUDA probe is best effort
            cuda = False
        device = None
        if cuda:
            try:
                device = torch.cuda.get_device_name(0)
            except Exception:  # noqa: BLE001 - device name is informational
                device = None
        cuda_version = getattr(getattr(torch, "version", None), "cuda", None)
        bf16_supported = None
        if cuda:
            try:
                bf16_probe = getattr(torch.cuda, "is_bf16_supported", None)
                if callable(bf16_probe):
                    bf16_supported = bool(bf16_probe())
                else:
                    capability_probe = getattr(torch.cuda, "get_device_capability", None)
                    capability = capability_probe(0) if callable(capability_probe) else None
                    bf16_supported = bool(capability and capability[0] >= 8) if capability else None
            except Exception:  # noqa: BLE001 - capability is best effort
                bf16_supported = None
        return {
            "status": "ok" if cuda else "warning",
            "message": f"CUDA 可用：{device}" if cuda else "Torch 可导入，但 CUDA 不可用",
            "details": {
                "importable": True,
                "torch_version": str(torch.__version__),
                "version": str(torch.__version__),  # legacy field
                "cuda_available": cuda,
                "cuda_version": str(cuda_version) if cuda_version else None,
                "gpu_name": device,
                "device": device,  # legacy field
                "bf16_capability": bf16_supported,
            },
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
    return {
        "status": overall,
        "selected_engine": selection.engine,
        "selected_version": selection.version,
        "version_match": selected_version_match,
        "model_dirs": {
            version: {
                "path": environment_lib.display_path(value["path"], "model-dir"),
                "source": value["source"],
            }
            for version, value in model_dirs.items()
        },
        "checks": checks,
    }


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
