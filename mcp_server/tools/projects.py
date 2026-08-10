"""Project inspection MCP adapters."""
from __future__ import annotations

from typing import Any

from services import ProjectService


def list_projects(_arguments: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return resilient structured summaries for active projects.

    The adapter returns an object so ``structuredContent`` stays a JSON
    object per the MCP tool-result contract (a bare list is rejected by
    clients with ``invalid_type structuredContent``).
    """
    return {"projects": ProjectService.list_project_summaries()}


def get_project(arguments: dict[str, Any]) -> dict[str, Any]:
    project_name = str(arguments.get("project_name") or "").strip()
    if not project_name:
        raise ValueError("project_name 不能为空")
    return ProjectService.get_project_summary(project_name)


__all__ = ["get_project", "list_projects"]
