"""项目管理：扫描／创建／打开／保存项目

数据目录（项目 / 产物）默认外置于程序目录（见 lib.config），并通过 legacy 目录
向后兼容打开旧版存放在程序内 workspace/projects 的历史项目。
"""
from __future__ import annotations

from .types import ProjectMeta
from .snapshot import ProjectSnapshot
from . import config as _cfg
from repositories.project_repo import ProjectRepository

# WORKSPACE_ROOT 保持为模块级可变变量（测试用 monkeypatch 覆盖）；
# 初值从配置读取，使项目默认存到程序目录之外。
WORKSPACE_ROOT = _cfg.get_projects_root()
# 旧版项目目录（程序目录内），仅用于向后兼容打开，不参与新建。
LEGACY_ROOT = _cfg.get_legacy_dir()


def _repository() -> type[ProjectRepository]:
    """Synchronize mutable compatibility roots and return the canonical repo."""
    ProjectRepository.WORKSPACE_ROOT = WORKSPACE_ROOT
    ProjectRepository.LEGACY_ROOT = LEGACY_ROOT
    ProjectRepository._INITIALIZED = True
    return ProjectRepository


def scan_projects() -> list[str]:
    """Compatibility wrapper for :meth:`ProjectRepository.scan_projects`."""
    return _repository().scan_projects()


def create_project(name: str, script_path: str) -> str:
    """Compatibility wrapper; new disk writes live only in the repository."""
    return _repository().create_project(name, script_path)


def open_project(name: str) -> tuple[ProjectMeta, dict, dict]:
    """Compatibility wrapper for the canonical load path."""
    return _repository().load_project(name)


def load_snapshot(name: str) -> "ProjectSnapshot":
    """Compatibility wrapper for the canonical snapshot path."""
    return _repository().load_snapshot(name)


def delete_project(name: str):
    """Compatibility wrapper for the canonical delete path."""
    return _repository().delete_project(name)


def get_project_dir(name: str) -> str:
    """Compatibility wrapper for the canonical project resolver."""
    return _repository().get_project_dir(name)


def update_segment_status(name: str, seg_id: str, status: str):
    """Compatibility wrapper for the canonical segment status mutation."""
    return _repository().update_segment_status(name, seg_id, status)


def get_remaining(name: str) -> list[str]:
    """Compatibility wrapper for the canonical recovery query."""
    return _repository().get_remaining(name)
