"""Thin MCP adapters for quality review and revision-safe repair."""
from __future__ import annotations

from typing import Any

from services import QualityService, RepairService


def get_quality_report(arguments: dict[str, Any]) -> dict[str, Any]:
    return QualityService.get_quality_report(
        str(arguments.get("project_name") or "")
    )


def list_review_segments(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    report = get_quality_report(arguments)
    requested = str(arguments.get("status") or "").strip()
    segments = report.get("segments", [])
    if requested:
        segments = [
            item for item in segments
            if item.get("review_status") == requested
            or item.get("quality_status") == requested
            or item.get("technical_outcome") == requested
        ]
    return {"segments": segments}


def get_segment_review(arguments: dict[str, Any]) -> dict[str, Any]:
    return QualityService.get_segment_quality(
        str(arguments.get("project_name") or ""),
        str(arguments.get("segment_id") or ""),
    )


def mark_segment_review(arguments: dict[str, Any]) -> dict[str, Any]:
    return QualityService.mark_review(
        str(arguments.get("project_name") or ""),
        str(arguments.get("segment_id") or ""),
        str(arguments.get("review_status") or ""),
        issue_type=str(arguments.get("issue_type") or ""),
        review_note=str(arguments.get("review_note") or ""),
        reviewed_by=str(arguments.get("reviewed_by") or "mcp"),
    )


def run_technical_qa(arguments: dict[str, Any]) -> dict[str, Any]:
    project = str(arguments.get("project_name") or "")
    segment_ids = arguments.get("segment_ids")
    if isinstance(segment_ids, list) and segment_ids:
        normalized_ids = [str(segment_id) for segment_id in segment_ids]
        if len(normalized_ids) == 1:
            return {
                "project": project,
                "results": [
                    QualityService.run_technical_qa(project, normalized_ids[0])
                ],
            }
        return {
            "project": project,
            "results": QualityService.run_technical_qa_batch(
                project,
                normalized_ids,
            ),
        }
    report = QualityService.get_quality_report(project)
    report_segment_ids = [
        str(item["segment_id"])
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("segment_id") or "").strip()
    ]
    return {
        "project": project,
        "results": QualityService.run_technical_qa_batch(
            project,
            report_segment_ids,
        ),
    }


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


__all__ = [
    "get_quality_report",
    "get_repair_task",
    "get_segment_review",
    "list_repairs",
    "list_review_segments",
    "mark_segment_review",
    "regenerate_segments",
    "run_technical_qa",
]
