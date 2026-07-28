"""Service 层：纯 Python 业务编排（不依赖 Gradio，可单测）。

导出会话状态、合成服务、项目服务与导出服务，供 ``app.py`` 的 Gradio 事件
处理器调用。本包及其子模块**禁止** ``import gradio``，以保证可被单元测试
直接 import（用假引擎 / 假 ffmpeg 完成，无需 GPU 或 UI 环境）。

模块依赖关系（闭环，无环）：
    session.SessionState  --(前向引用)-->  synthesis.SynthesisState
    synthesis.SynthesisService  --驱动-->  lib.queue / lib.project_manager
    project.ProjectService      --包装-->  lib.project_manager / lib.script_loader
    export.ExportService        --调用-->  lib.audio_pipeline（透传 ExportError）
"""
from __future__ import annotations

from lib.snapshot import ProjectSnapshot

from .export import ExportService
from .project import ProjectService
from .script_director import ScriptDirectorService
from .session import SessionState
from .supplement import SupplementService, SupplementTaskState
from .synthesis import SynthesisService, SynthesisState
from .voice_director import DirectorAuditionService, VoiceDirectorService

__all__ = [
    "SessionState",
    "ProjectSnapshot",
    "SynthesisState",
    "SynthesisService",
    "ProjectService",
    "ExportService",
    "SupplementService",
    "SupplementTaskState",
    "ScriptDirectorService",
    "DirectorAuditionService",
    "VoiceDirectorService",
]
