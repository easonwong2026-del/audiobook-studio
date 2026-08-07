"""Repository 层：唯一的持久化边界。

所有 JSON 读写通过本层进行，确保原子写入（临时文件 + fsync + os.replace）。
repositories/ 只依赖标准库 + lib/types.py + lib/snapshot.py，不反向依赖 services/ 或 app.py。

分层策略：
  app.py → services/*.py → repositories/*.py → 磁盘 JSON
"""
from __future__ import annotations

from ._atomic import atomic_write
from .config_repo import ConfigRepository, ConfigData
from .project_repo import ProjectRepository
from .project_storage_repo import (
    ArchivedProjectSummary,
    CleanupCandidate,
    CleanupPlan,
    ProjectStorageRepository,
    ProjectStorageSummary,
)
from .binding_repo import BindingRepository
from .task_repo import TaskRepository, TaskRecord
from .exceptions import RepoError, ProjectNotFoundError, ConfigCorruptedError, AtomicWriteError


__all__ = [
    "ConfigRepository", "ConfigData",
    "ProjectRepository",
    "ProjectStorageRepository", "ProjectStorageSummary", "ArchivedProjectSummary", "CleanupPlan", "CleanupCandidate",
    "BindingRepository",
    "TaskRepository", "TaskRecord",
    "RepoError", "ProjectNotFoundError", "ConfigCorruptedError", "AtomicWriteError",
    "atomic_write",
]
