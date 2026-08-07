"""Structured-script MCP adapters."""
from __future__ import annotations

from typing import Any

from services import ProjectService, StructuredScriptImportService

from ..models import server_info


def _script_argument(arguments: dict[str, Any]) -> dict[str, Any] | Any:
    script = arguments.get("script")
    if script is not None:
        return script
    # Also accept a raw structured_script object for convenient local clients.
    return arguments


def validate_structured_script(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate an in-memory object and return machine-readable diagnostics."""
    script = _script_argument(arguments)
    project_name = arguments.get("project_name") if isinstance(arguments, dict) else None
    return StructuredScriptImportService.inspect_data(script, project_name)


def create_project(arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a project from an already validated in-memory script."""
    script = _script_argument(arguments)
    if not isinstance(script, dict):
        raise ValueError("script 必须是 JSON object")
    project_name = str(arguments.get("project_name") or script.get("project_name") or "").strip()
    if not project_name:
        raise ValueError("project_name 不能为空")
    result = ProjectService.create_project_from_data(project_name, script)
    return {
        "created": True,
        "project_name": result.project_name,
        "title": result.title,
        "chapters": result.chapter_count,
        "segments": result.segment_count,
        "roles": result.role_count,
        "warnings": list(result.warnings),
    }


__all__ = [
    "create_project",
    "server_info",
    "validate_structured_script",
]
