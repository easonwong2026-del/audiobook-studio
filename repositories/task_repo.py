"""TaskRepository：轻量任务状态持久化。

只保存可序列化的轻量任务状态记录（synthesis / supplement），
不保存 Future / 线程 / SynthesisState（内存态不落盘）。
每个任务一个 JSON 文件：<task_dir>/<task_id>.json
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ._atomic import atomic_write as _atomic_write

logger = logging.getLogger(__name__)

# 预览目录的获取推迟到 get_task_dir() 中动态解析
_PROGRAM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_progress() -> dict[str, Any]:
    """Return a fresh, JSON-safe progress payload for Task V2 records."""
    return {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "current_chapter": None,
        "current_segment": None,
    }


@dataclass
class TaskRecord:
    """可序列化的轻量任务状态记录。

    Attributes:
        task_id: 任务标识（uuid4().hex）。
        task_type: 任务类型（"synthesis" | "supplement"）。
        project: 所属项目名。
        status: 状态（"pending" | "running" | "pausing" | "paused" |
            "cancelling" | "cancelled" | "done" | "error" | "interrupted"）。
        artifact_dir: 产物目录（可选）。
        error_summary: 错误摘要（可选）。
        created_at: ISO 8601 时间戳（可选）。
    """
    task_id: str
    task_type: str  # "synthesis" | "supplement"
    project: str
    status: str  # production state machine; supplement keeps legacy values
    artifact_dir: str = ""
    error_summary: str = ""
    created_at: str = ""  # ISO 8601
    # Task V2 fields.  Defaults intentionally keep old task JSON and old
    # positional constructors valid.
    source: str = "system"
    scope: dict[str, Any] = field(default_factory=lambda: {
        "all": True,
        "chapter_ids": [],
        "segment_ids": [],
    })
    options: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=_default_progress)
    failed_segment_ids: list[str] = field(default_factory=list)
    attempt: int = 1
    idempotency_key: str = ""
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    parent_task_id: str = ""
    recovery_of: str = ""

    def to_dict(self) -> dict:
        """序列化为 dict。"""
        scope = {"all": False, "chapter_ids": [], "segment_ids": []}
        if isinstance(self.scope, dict):
            scope.update(self.scope)
        progress = _default_progress()
        if isinstance(self.progress, dict):
            progress.update(self.progress)
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "project": self.project,
            "status": self.status,
            "source": self.source,
            "scope": scope,
            "options": dict(self.options) if isinstance(self.options, dict) else {},
            "progress": progress,
            "failed_segment_ids": [str(item) for item in self.failed_segment_ids],
            "attempt": max(int(self.attempt or 1), 1),
            "idempotency_key": self.idempotency_key,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "artifact_dir": self.artifact_dir,
            "error_summary": self.error_summary,
            "created_at": self.created_at,
            "parent_task_id": self.parent_task_id,
            "recovery_of": self.recovery_of,
        }

    @staticmethod
    def from_dict(data: dict) -> "TaskRecord":
        """从 dict 反序列化，缺省字段使用空值。"""
        raw_scope = data.get("scope")
        scope = {"all": True, "chapter_ids": [], "segment_ids": []}
        if isinstance(raw_scope, dict):
            scope.update(raw_scope)
        progress = _default_progress()
        raw_progress = data.get("progress")
        if isinstance(raw_progress, dict):
            progress.update(raw_progress)
        raw_failed = data.get("failed_segment_ids", [])
        failed = [str(item) for item in raw_failed] if isinstance(raw_failed, list) else []
        try:
            attempt = max(int(data.get("attempt", 1) or 1), 1)
        except (TypeError, ValueError):
            attempt = 1
        return TaskRecord(
            task_id=data.get("task_id", ""),
            task_type=data.get("task_type", ""),
            project=data.get("project", ""),
            status=data.get("status", "pending"),
            artifact_dir=data.get("artifact_dir", ""),
            error_summary=data.get("error_summary", ""),
            created_at=data.get("created_at", ""),
            source=str(data.get("source") or "system"),
            scope=scope,
            options=data.get("options", {}) if isinstance(data.get("options", {}), dict) else {},
            progress=progress,
            failed_segment_ids=failed,
            attempt=attempt,
            idempotency_key=str(data.get("idempotency_key") or ""),
            started_at=str(data.get("started_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            finished_at=str(data.get("finished_at") or ""),
            parent_task_id=str(data.get("parent_task_id") or ""),
            recovery_of=str(data.get("recovery_of") or ""),
        )


class TaskRepository:
    """任务状态仓库：轻量任务状态持久化（JSON 文件）。

    所有方法均为 @staticmethod，无实例状态。
    """

    @staticmethod
    def _resolve_preview_dir() -> str:
        """动态解析 preview 目录（延迟导入 lib.config 避免循环依赖）。"""
        # 使用与 lib/config.py 相同的解析逻辑
        from lib import config as _cfg
        return _cfg.get_preview_dir()

    @staticmethod
    def get_task_dir() -> str:
        """任务状态 JSON 根目录：<preview_dir>/task_records/

        目录不存在时自动创建。
        """
        preview_dir = TaskRepository._resolve_preview_dir()
        task_dir = os.path.join(preview_dir, "task_records")
        os.makedirs(task_dir, exist_ok=True)
        return task_dir

    @staticmethod
    def save_task(record: TaskRecord) -> None:
        """原子写 <task_dir>/<task_id>.json。

        Args:
            record: TaskRecord 实例。

        Raises:
            AtomicWriteError: 写入失败时抛出。
        """
        task_dir = TaskRepository.get_task_dir()
        os.makedirs(task_dir, exist_ok=True)
        path = os.path.join(task_dir, f"{record.task_id}.json")
        _atomic_write(path, record.to_dict())

    @staticmethod
    def load_task(task_id: str) -> Optional[TaskRecord]:
        """读取任务记录。

        Args:
            task_id: 任务 ID。

        Returns:
            TaskRecord 实例，文件不存在时返回 None。
        """
        task_dir = TaskRepository.get_task_dir()
        path = os.path.join(task_dir, f"{task_id}.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return TaskRecord.from_dict(data) if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取任务记录 %s 失败: %s", task_id, exc)
            return None

    @staticmethod
    def list_tasks(project: Optional[str] = None,
                   task_type: Optional[str] = None,
                   status: Optional[str] = None,
                   source: Optional[str] = None) -> list[TaskRecord]:
        """扫描任务记录并按条件过滤。

        Args:
            project: 可选项目名过滤。
            task_type: 可选任务类型过滤（"synthesis" | "supplement"）。
            status: 可选状态过滤。
            source: 可选来源过滤（mcp / web / system / recovery）。

        Returns:
            TaskRecord 列表（按最近更新时间倒序）。
        """
        task_dir = TaskRepository.get_task_dir()
        if not os.path.isdir(task_dir):
            return []
        records: list[TaskRecord] = []
        for name in os.listdir(task_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(task_dir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                record = TaskRecord.from_dict(data) if isinstance(data, dict) else None
                if record is None:
                    continue
                if project and record.project != project:
                    continue
                if task_type and record.task_type != task_type:
                    continue
                if status and record.status != status:
                    continue
                if source and record.source != source:
                    continue
                records.append(record)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("扫描任务记录 %s 失败: %s", name, exc)
                continue
        return sorted(
            records,
            key=lambda r: (r.updated_at or r.created_at or "", r.task_id),
            reverse=True,
        )

    @staticmethod
    def find_by_idempotency(
        project: str,
        task_type: str,
        idempotency_key: str,
    ) -> Optional[TaskRecord]:
        """Find the newest task matching a stable replay key."""
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        for record in TaskRepository.list_tasks(project=project, task_type=task_type):
            if record.idempotency_key == key:
                return record
        return None

    @staticmethod
    def delete_task(task_id: str) -> None:
        """删除任务记录 JSON 文件。

        Args:
            task_id: 任务 ID。
        """
        task_dir = TaskRepository.get_task_dir()
        path = os.path.join(task_dir, f"{task_id}.json")
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as exc:
                logger.warning("删除任务记录 %s 失败: %s", task_id, exc)

    @staticmethod
    def cleanup_old_tasks(max_age_days: int = 7) -> int:
        """删除超期任务记录（按 created_at 或文件 mtime）。

        Args:
            max_age_days: 过期天数阈值（默认 7 天）。

        Returns:
            删除的任务记录数量。
        """
        task_dir = TaskRepository.get_task_dir()
        if not os.path.isdir(task_dir):
            return 0
        cutoff = time.time() - max_age_days * 86400
        cleaned = 0
        for name in os.listdir(task_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(task_dir, name)
            # 优先用 created_at 字段判断
            record = TaskRepository.load_task(name[:-5])  # 去掉 .json
            if record and record.created_at:
                try:
                    # 解析 ISO 8601 时间戳
                    from datetime import datetime
                    dt = datetime.fromisoformat(record.created_at)
                    record_time = dt.timestamp()
                except (ValueError, OSError):
                    record_time = os.path.getmtime(path)
            else:
                record_time = os.path.getmtime(path)
            if record_time < cutoff:
                try:
                    os.remove(path)
                    cleaned += 1
                except OSError as exc:
                    logger.warning("清理任务记录 %s 失败: %s", name, exc)
        return cleaned
