"""Thin MCP adapters for export jobs and delivery manifests."""
from __future__ import annotations

from typing import Any

from services import ExportService


def _subtitle_formats(value: Any) -> list[str]:
    if isinstance(value, list):
        return [
            str(item).lower()
            for item in value
            if str(item).lower() in {"srt", "lrc"}
        ]
    return []


def plan_export(arguments: dict[str, Any]) -> dict[str, Any]:
    return ExportService.plan_export(
        str(arguments.get("project_name") or ""),
        fmt=str(arguments.get("format") or "m4b"),
        qa_policy=str(arguments.get("qa_policy") or "require_passed"),
        subtitle_formats=_subtitle_formats(arguments.get("subtitle_formats")),
    )


def start_export(arguments: dict[str, Any]) -> dict[str, Any]:
    return ExportService.start_export(
        str(arguments.get("project_name") or ""),
        fmt=str(arguments.get("format") or "m4b"),
        bitrate=str(arguments.get("bitrate") or "192k"),
        qa_policy=str(arguments.get("qa_policy") or "require_passed"),
        subtitle_formats=_subtitle_formats(arguments.get("subtitle_formats")),
        idempotency_key=str(arguments.get("idempotency_key") or ""),
    )


def get_export_task(arguments: dict[str, Any]) -> dict[str, Any]:
    return ExportService.get_export_task(
        str(arguments.get("project_name") or ""),
        str(arguments.get("export_id") or ""),
    )


def list_exports(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return ExportService.list_exports(str(arguments.get("project_name") or ""))


def get_delivery_manifest(arguments: dict[str, Any]) -> dict[str, Any]:
    return ExportService.get_delivery_manifest(
        str(arguments.get("project_name") or ""),
        str(arguments.get("export_id") or ""),
    )


__all__ = [
    "get_delivery_manifest",
    "get_export_task",
    "list_exports",
    "plan_export",
    "start_export",
]
