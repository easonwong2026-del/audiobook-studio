"""Thin MCP adapters for the shared ProductionJobService."""
from __future__ import annotations

from typing import Any, Callable

from services import ProductionJobError, ProductionJobService


def _failure(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ProductionJobError):
        return exc.as_payload()
    return {"error": {"code": type(exc).__name__, "message": str(exc)}}


def _call(function: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        return _failure(exc)


def plan_production(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate a production scope and resolve the effective engine.

    ``options`` is forwarded so plan/start agree on the same engine intent
    (explicit ``engine_snapshot`` wins; otherwise Settings current default).
    The plan response includes ``engine`` and ``engine_selection_source``.
    """
    return ProductionJobService.plan(
        str(arguments.get("project_name") or ""),
        arguments.get("scope"),
        arguments.get("options"),
    )


def start_production(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(
        ProductionJobService.start,
        str(arguments.get("project_name") or ""),
        arguments.get("scope"),
        arguments.get("options"),
        source="mcp",
        idempotency_key=str(arguments.get("idempotency_key") or ""),
    )


def get_production_task(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(
        ProductionJobService.get_task_snapshot,
        str(arguments.get("task_id") or ""),
    )


def list_production_tasks(arguments: dict[str, Any] | None = None) -> list[dict[str, Any]] | dict[str, Any]:
    arguments = arguments or {}
    result = _call(
        ProductionJobService.list_tasks,
        arguments.get("project_name"),
        arguments.get("status"),
        arguments.get("source"),
    )
    if isinstance(result, list):
        return {"tasks": result}
    return result


def pause_production(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(ProductionJobService.pause, str(arguments.get("task_id") or ""))


def resume_production(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(ProductionJobService.resume, str(arguments.get("task_id") or ""))


def cancel_production(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(ProductionJobService.cancel, str(arguments.get("task_id") or ""))


def retry_failed_segments(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(
        ProductionJobService.retry_failed_segments,
        str(arguments.get("task_id") or ""),
        str(arguments.get("idempotency_key") or ""),
    )


def get_runtime_health(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """GPU-free runtime health snapshot (never initializes the model)."""
    return _call(ProductionJobService.get_runtime_health)


__all__ = [
    "cancel_production",
    "get_production_task",
    "get_runtime_health",
    "list_production_tasks",
    "pause_production",
    "plan_production",
    "resume_production",
    "retry_failed_segments",
    "start_production",
]
