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
from typing import ClassVar, Optional

from ._atomic import atomic_write as _atomic_write
from .exceptions import AtomicWriteError

logger = logging.getLogger(__name__)

# 预览目录的获取推迟到 get_task_dir() 中动态解析
_PROGRAM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class TaskRecord:
    """可序列化的轻量任务状态记录。

    Attributes:
        task_id: 任务标识（uuid4().hex）。
        task_type: 任务类型（"synthesis" | "supplement"）。
        project: 所属项目名。
        status: 状态（"pending" | "running" | "done" | "cancelled" | "error"）。
        artifact_dir: 产物目录（可选）。
        error_summary: 错误摘要（可选）。
        created_at: ISO 8601 时间戳（可选）。
    """
    task_id: str
    task_type: str  # "synthesis" | "supplement"
    project: str
    status: str  # "pending" | "running" | "done" | "cancelled" | "error"
    artifact_dir: str = ""
    error_summary: str = ""
    created_at: str = ""  # ISO 8601

    def to_dict(self) -> dict:
        """序列化为 dict。"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "project": self.project,
            "status": self.status,
            "artifact_dir": self.artifact_dir,
            "error_summary": self.error_summary,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "TaskRecord":
        """从 dict 反序列化，缺省字段使用空值。"""
        return TaskRecord(
            task_id=data.get("task_id", ""),
            task_type=data.get("task_type", ""),
            project=data.get("project", ""),
            status=data.get("status", "pending"),
            artifact_dir=data.get("artifact_dir", ""),
            error_summary=data.get("error_summary", ""),
            created_at=data.get("created_at", ""),
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
                   task_type: Optional[str] = None) -> list[TaskRecord]:
        """扫描任务记录并按条件过滤。

        Args:
            project: 可选项目名过滤。
            task_type: 可选任务类型过滤（"synthesis" | "supplement"）。

        Returns:
            TaskRecord 列表（按 task_id 排序）。
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
                records.append(record)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("扫描任务记录 %s 失败: %s", name, exc)
                continue
        return sorted(records, key=lambda r: r.task_id)

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
