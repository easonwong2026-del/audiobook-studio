"""Read-only MCP adapter for persisted production performance summaries."""
from __future__ import annotations

import re
import json
import os
from typing import Any

from lib import config
from lib import project_paths
from repositories.task_repo import TaskRepository


_LOCAL_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:Users|home|private|tmp|var|opt)/)[^\s,;)]*"
)


def _public_value(value: Any) -> Any:
    if isinstance(value, str):
        return _LOCAL_PATH_RE.sub("<local-path>", value)
    if isinstance(value, dict):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return value


def get_production_performance(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the latest batch-persisted trace for a production task."""
    task_id = str(arguments.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id 不能为空")
    record = TaskRepository.load_task(task_id)
    if record is None or record.task_type != "synthesis":
        raise ValueError("生产任务不存在")
    path = os.path.join(
        project_paths.project_dir(config.get_data_dir(), "logs", create=True),
        "performance",
        f"{task_id}.json",
    )
    if not os.path.isfile(path):
        return {
            "task_id": task_id,
            "trace_available": False,
            "status": record.status,
        }
    try:
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError("performance trace 无法读取") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("performance trace 格式无效")
    return _public_value(payload)


__all__ = ["get_production_performance"]
