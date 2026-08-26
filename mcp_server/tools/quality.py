"""Thin MCP adapters for revision-safe repair."""
from __future__ import annotations

from typing import Any

from services import RepairService


def regenerate_segments(arguments: dict[str, Any]) -> dict[str, Any]:
    return RepairService.start(
        str(arguments.get("project_name") or ""),
        [str(item) for item in arguments.get("segment_ids", [])],
        emotion=arguments.get("emotion"),
        emo_alpha=arguments.get("emo_alpha"),
        speech_rate=arguments.get("speech_rate"),
        voice_override=arguments.get("voice_override"),
        requested_by="mcp",
        note=str(arguments.get("note") or ""),
        idempotency_key=str(arguments.get("idempotency_key") or ""),
        source="mcp",
    )


def get_repair_task(arguments: dict[str, Any]) -> dict[str, Any]:
    project = str(arguments.get("project_name") or "")
    repair_id = str(arguments.get("repair_id") or "")
    if arguments.get("refresh", True):
        return RepairService.refresh(project, repair_id)
    return RepairService.get(project, repair_id)


def list_repairs(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return {"repairs": RepairService.list(str(arguments.get("project_name") or ""))}


__all__ = ["get_repair_task", "list_repairs", "regenerate_segments"]
