"""Offline import workflow for the V3 ``structured_script.json`` contract.

The service deliberately has only two operations:

``inspect(path, project_name)``
    Read and validate a JSON file, run the existing consistency checks, and
    return a preview.  It never creates a project and never performs I/O over
    the network.

``create(project_name, path)``
    Inspect again, reserve the project slot through the existing creation
    guard, and delegate the atomic directory creation to
    :class:`repositories.project_repo.ProjectRepository`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib import script_loader
from repositories.project_repo import (
    ProjectRepository,
    ProjectSlotInspection,
    sanitize_project_name,
)
from services.script_consistency import check_script_consistency


@dataclass(frozen=True)
class StructuredScriptPreview:
    """User-facing facts collected without creating a project."""

    source_path: str
    suggested_project_name: str
    title: str
    author: str
    chapter_count: int
    segment_count: int
    role_count: int
    narrator_defined: bool
    unknown_roles: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    slot: ProjectSlotInspection | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def slot_status(self) -> str:
        return self.slot.status if self.slot else "unselected"

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable preview for CLI/tests/UI adapters."""
        return {
            "source_path": self.source_path,
            "suggested_project_name": self.suggested_project_name,
            "title": self.title,
            "author": self.author,
            "chapter_count": self.chapter_count,
            "segment_count": self.segment_count,
            "role_count": self.role_count,
            "narrator_defined": self.narrator_defined,
            "unknown_roles": list(self.unknown_roles),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "slot_status": self.slot_status,
            "slot_path": self.slot.path if self.slot else "",
        }


@dataclass(frozen=True)
class StructuredScriptCreationResult:
    project_name: str
    title: str
    chapter_count: int
    segment_count: int
    role_count: int
    warnings: list[str] = field(default_factory=list)


class StructuredScriptImportService:
    """Single source of truth for offline JSON inspection and creation."""

    @staticmethod
    def _raw_from_script(script) -> dict[str, Any]:
        raw = getattr(script, "raw", None)
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _candidate_name(path: str, raw: dict[str, Any]) -> str:
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        candidates = (
            raw.get("project_name"),
            meta.get("project_name"),
            meta.get("name"),
            meta.get("title"),
            Path(path).stem,
            "未命名项目",
        )
        for candidate in candidates:
            if not str(candidate or "").strip():
                continue
            try:
                return sanitize_project_name(str(candidate))
            except ValueError:
                continue
        return "未命名项目"

    @staticmethod
    def _raw_chapters(raw: dict[str, Any]) -> list:
        chapters = raw.get("chapters")
        if chapters is None:
            for alias in ("sections", "episodes", "scenes"):
                if isinstance(raw.get(alias), list):
                    chapters = raw[alias]
                    break
        return chapters if isinstance(chapters, list) else []

    @staticmethod
    def _unknown_roles(raw: dict[str, Any], voices: dict[str, Any]) -> tuple[str, ...]:
        unknown: set[str] = set()
        for chapter in StructuredScriptImportService._raw_chapters(raw):
            if not isinstance(chapter, dict):
                continue
            segments = chapter.get("segments")
            if not isinstance(segments, list):
                continue
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                role = str(segment.get("role") or segment.get("speaker") or "").strip()
                if role and role not in voices:
                    unknown.add(role)
        return tuple(sorted(unknown))

    @staticmethod
    def _slot(project_name: str | None) -> ProjectSlotInspection | None:
        if not str(project_name or "").strip():
            return None
        return ProjectRepository.inspect_project_slot(str(project_name).strip())

    @staticmethod
    def inspect(path: str, project_name: str | None = None) -> StructuredScriptPreview:
        """Inspect a JSON file without creating files in the project workspace."""
        if not path or not os.path.isfile(path):
            raise ValueError(f"剧本文件不存在：{path or '（未选择）'}")

        try:
            script = script_loader.load_script(path)
        except UnicodeDecodeError as exc:
            raise ValueError(f"无法按 UTF-8 读取 JSON：{exc}") from exc
        except OSError as exc:
            raise ValueError(f"无法读取 JSON 文件：{exc}") from exc

        raw = StructuredScriptImportService._raw_from_script(script)
        errors = list(script_loader.validate_script(script))
        consistency = check_script_consistency(raw if raw else None)
        consistency_errors = [
            item["message"] for item in consistency["issues"]
            if item.get("severity") == "error"
        ]
        warnings = [
            item["message"] for item in consistency["issues"]
            if item.get("severity") == "warning"
        ]
        errors = list(dict.fromkeys([*errors, *consistency_errors]))
        raw_meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        chapters = StructuredScriptImportService._raw_chapters(raw)
        segment_count = sum(
            len(chapter.get("segments", []))
            for chapter in chapters
            if isinstance(chapter, dict) and isinstance(chapter.get("segments"), list)
        )
        roles = raw.get("voices") if isinstance(raw.get("voices"), dict) else {}
        if not roles:
            for alias in ("characters", "roles", "cast", "speakers"):
                if isinstance(raw.get(alias), dict):
                    roles = raw[alias]
                    break
        suggested = StructuredScriptImportService._candidate_name(path, raw)
        title = str(raw_meta.get("title") or suggested)
        author = str(raw_meta.get("author") or "未填写")
        selected_name = str(project_name or suggested).strip() or suggested
        try:
            selected_name = sanitize_project_name(selected_name)
            slot = StructuredScriptImportService._slot(selected_name)
        except ValueError as exc:
            errors.append(f"project_name: {exc}")
            slot = None

        return StructuredScriptPreview(
            source_path=os.path.abspath(path),
            suggested_project_name=suggested,
            title=title,
            author=author,
            chapter_count=len(chapters),
            segment_count=segment_count,
            role_count=len(roles),
            narrator_defined="旁白" in roles,
            unknown_roles=StructuredScriptImportService._unknown_roles(raw, roles),
            errors=tuple(dict.fromkeys(errors)),
            warnings=tuple(dict.fromkeys(warnings)),
            slot=slot,
            raw=raw,
        )

    @staticmethod
    def create(project_name: str, path: str) -> StructuredScriptCreationResult:
        """Re-inspect, reserve the slot, and atomically create a V3 project."""
        safe_name = sanitize_project_name(str(project_name or "").strip())
        preview = StructuredScriptImportService.inspect(path, safe_name)
        if preview.errors:
            raise ValueError("JSON 校验失败：\n" + "\n".join(f"- {item}" for item in preview.errors))

        # Keep the existing slot policy as the single guard for valid,
        # legacy, incomplete, temporary, and corrupted directories.
        from services.project_creation import ProjectCreationService

        ProjectCreationService._assert_slot_available(safe_name)
        ProjectRepository.create_project(safe_name, path)
        return StructuredScriptCreationResult(
            project_name=safe_name,
            title=preview.title,
            chapter_count=preview.chapter_count,
            segment_count=preview.segment_count,
            role_count=preview.role_count,
            warnings=list(preview.warnings),
        )


__all__ = [
    "StructuredScriptCreationResult",
    "StructuredScriptImportService",
    "StructuredScriptPreview",
]
