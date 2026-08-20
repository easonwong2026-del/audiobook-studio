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

from .application_lifecycle import (
    ApplicationLifecycleService,
    get_application_lifecycle,
)
from .export import ExportPlanError, ExportService
from .production_jobs import (
    ACTIVE_PRODUCTION_STATES,
    PRODUCTION_STATES,
    ProductionJobError,
    ProductionJobService,
)
from .project import ProjectService
from .project_backup import ProjectBackupService
from .project_catalog import ProjectCatalogService
from .project_storage import ProjectStorageService
from .quality import QualityService
from .quick_tts import QuickTTSBusyError, QuickTTSService
from .repair import RepairError, RepairService
from .runtime_tts import RuntimeTTSBusyError, RuntimeTTSError, RuntimeTTSService
from .session import SessionState
from .structured_script_import import (
    StructuredScriptCreationResult,
    StructuredScriptImportService,
    StructuredScriptPreview,
)
from .supplement import SupplementService, SupplementTaskState
from .synthesis import SynthesisService, SynthesisState
from .voice_assets import VoiceAssetError, VoiceAssetService
from .voice_cast import VoiceCastError, VoiceCastResolver, VoiceCastService
from .whole_book_assembly import WholeBookAssemblyService
from .workflow import WorkflowService

__all__ = [
    "ACTIVE_PRODUCTION_STATES",
    "PRODUCTION_STATES",
    "ApplicationLifecycleService",
    "ExportPlanError",
    "ExportService",
    "ProductionJobError",
    "ProductionJobService",
    "ProjectBackupService",
    "ProjectCatalogService",
    "ProjectService",
    "ProjectSnapshot",
    "ProjectStorageService",
    "QualityService",
    "QuickTTSBusyError",
    "QuickTTSService",
    "RepairError",
    "RepairService",
    "RuntimeTTSBusyError",
    "RuntimeTTSError",
    "RuntimeTTSService",
    "SessionState",
    "StructuredScriptCreationResult",
    "StructuredScriptImportService",
    "StructuredScriptPreview",
    "SupplementService",
    "SupplementTaskState",
    "SynthesisService",
    "SynthesisState",
    "VoiceAssetError",
    "VoiceAssetService",
    "VoiceCastError",
    "VoiceCastResolver",
    "VoiceCastService",
    "WholeBookAssemblyService",
    "WorkflowService",
    "get_application_lifecycle",
]
