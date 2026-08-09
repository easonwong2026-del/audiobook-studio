"""Thin MCP adapter for the derived book workflow."""
from __future__ import annotations

from typing import Any

from services import WorkflowService


def get_workflow_state(arguments: dict[str, Any]) -> dict[str, Any]:
    return WorkflowService.get_state(str(arguments.get("project_name") or ""))


__all__ = ["get_workflow_state"]
