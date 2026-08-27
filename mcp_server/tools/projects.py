"""Project inspection MCP adapters."""
from __future__ import annotations

import re
from typing import Any

from lib import script_loader
from repositories.project_repo import ProjectRepository
from services import ProjectService
from services.quality import QualityService
from services.voice_cast import VoiceCastError, VoiceCastResolver


_DEFAULT_SEGMENT_LIMIT = 100
_MAX_SEGMENT_LIMIT = 1000


def _project_name(arguments: dict[str, Any]) -> str:
    name = str(arguments.get("project_name") or "").strip()
    if not name:
        raise ValueError("project_name 不能为空")
    return name


def _script_chapters(script: dict[str, Any]) -> list[dict[str, Any]]:
    _voices, raw_chapters = script_loader.resolve_collections(script)
    return [item for item in raw_chapters if isinstance(item, dict)]


def _chapter_id(chapter: dict[str, Any]) -> str:
    """Return the ID accepted by ProductionJobService scope arguments."""
    return str(chapter.get("id") or "").strip()


def _role_info(project_name: str, segment: dict[str, Any]) -> tuple[str | None, str]:
    """Resolve one segment's stable role ID without making query tools brittle.

    A legacy/manual project intentionally has no Character Roster role ID.  In
    that case the public response keeps ``role_id`` null and exposes the
    existing role/speaker name instead of inventing a second identity.
    """
    name = str(segment.get("role") or segment.get("speaker") or "").strip()
    explicit_id = str(segment.get("role_id") or "").strip()
    try:
        resolved = VoiceCastResolver.resolve_role(
            project_name,
            segment,
            role_id=explicit_id or None,
            allow_legacy=True,
        )
    except VoiceCastError:
        return explicit_id or None, name
    except (OSError, TypeError, ValueError, KeyError):
        # Outline/list queries should remain useful for malformed or partially
        # migrated projects; the durable script value is still safe to show.
        return explicit_id or None, name
    role_id = str(resolved.get("role_id") or "").strip() or explicit_id or None
    return role_id, str(resolved.get("name") or name).strip()


def _preview_text(value: Any, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


def list_projects(_arguments: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return resilient structured summaries for active projects.

    The adapter returns an object so ``structuredContent`` stays a JSON
    object per the MCP tool-result contract (a bare list is rejected by
    clients with ``invalid_type structuredContent``).
    """
    return {"projects": ProjectService.list_project_summaries()}


def get_project(arguments: dict[str, Any]) -> dict[str, Any]:
    project_name = _project_name(arguments)
    result = ProjectService.get_project_summary(project_name)
    if arguments.get("include_outline", False):
        result = {
            **result,
            "outline": get_project_outline({"project_name": project_name}),
        }
    return result


def get_project_outline(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, path-free chapter outline from canonical project data."""
    project_name = _project_name(arguments)
    meta, script, _bindings = ProjectRepository.load_project(project_name)
    raw_meta = script.get("meta") if isinstance(script, dict) else None
    title = (
        str(raw_meta.get("title") or project_name)
        if isinstance(raw_meta, dict) else project_name
    )
    chapters: list[dict[str, Any]] = []
    segment_count = 0
    statuses = dict(getattr(meta, "segments_status", {}) or {})

    for chapter in _script_chapters(script):
        chapter_id = _chapter_id(chapter)
        if not chapter_id:
            # Production scopes cannot address a chapter without an ID.  Do
            # not manufacture a new ID in an inspection response.
            continue
        segments = [
            segment for segment in chapter.get("segments", [])
            if isinstance(segment, dict) and str(segment.get("id") or "").strip()
        ]
        completed = sum(
            statuses.get(str(segment.get("id"))) == "done" for segment in segments
        )
        failed = sum(
            statuses.get(str(segment.get("id"))) == "failed" for segment in segments
        )
        total = len(segments)
        required_roles: list[str] = []
        for segment in segments:
            role_id, role_name = _role_info(project_name, segment)
            role = role_id or role_name
            if role and role not in required_roles:
                required_roles.append(role)
        chapters.append({
            "chapter_id": chapter_id,
            "title": str(chapter.get("title") or chapter_id),
            "segment_count": total,
            "completed": completed,
            "failed": failed,
            "pending": max(total - completed - failed, 0),
            "progress": (completed / total) if total else 0.0,
            "required_roles": required_roles,
        })
        segment_count += total

    return {
        "project_name": project_name,
        "title": title,
        "chapter_count": len(chapters),
        "segment_count": segment_count,
        "chapters": chapters,
    }


def list_segments(arguments: dict[str, Any]) -> dict[str, Any]:
    """List compact segment records with deterministic pagination."""
    project_name = _project_name(arguments)
    chapter_filter = str(arguments.get("chapter_id") or "").strip()
    status_filter = str(arguments.get("status") or "").strip()
    try:
        offset = int(arguments.get("offset", 0) or 0)
        limit = int(arguments.get("limit", _DEFAULT_SEGMENT_LIMIT) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset 和 limit 必须是整数") from exc
    if offset < 0:
        raise ValueError("offset 不能小于 0")
    if limit < 0 or limit > _MAX_SEGMENT_LIMIT:
        raise ValueError(f"limit 必须在 0 到 {_MAX_SEGMENT_LIMIT} 之间")

    meta, script, _bindings = ProjectRepository.load_project(project_name)
    statuses = dict(getattr(meta, "segments_status", {}) or {})
    inventory = QualityService.get_active_revision_inventory(project_name)
    audio_by_segment = {
        str(item.get("segment_id") or ""): item
        for item in inventory.get("segments", [])
        if isinstance(item, dict)
    }
    records: list[dict[str, Any]] = []
    for chapter in _script_chapters(script):
        current_chapter_id = _chapter_id(chapter)
        if not current_chapter_id:
            continue
        if chapter_filter and current_chapter_id != chapter_filter:
            continue
        for segment in chapter.get("segments", []):
            if not isinstance(segment, dict):
                continue
            segment_id = str(segment.get("id") or "").strip()
            if not segment_id:
                continue
            synthesis_status = str(statuses.get(segment_id) or "pending")
            audio = audio_by_segment.get(segment_id, {})
            audio_status = str(audio.get("audio_status") or "missing")
            if status_filter and status_filter not in {synthesis_status, audio_status}:
                continue
            role_id, role_name = _role_info(project_name, segment)
            records.append({
                "segment_id": segment_id,
                "chapter_id": current_chapter_id,
                "role": role_name,
                "role_id": role_id,
                "text_preview": _preview_text(segment.get("text")),
                "synthesis_status": synthesis_status,
                "audio_status": audio_status,
                "audio_available": bool(audio.get("audio_valid")),
                "audio_revision": (
                    audio.get("audio_revision") or {}
                ).get("revision_id") if isinstance(audio.get("audio_revision"), dict) else None,
            })

    total = len(records)
    return {
        "project_name": project_name,
        "total": total,
        "offset": offset,
        "limit": limit,
        "segments": records[offset: offset + limit],
    }


__all__ = ["get_project", "get_project_outline", "list_projects", "list_segments"]
