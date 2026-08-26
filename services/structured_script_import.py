"""Offline import workflow for the V3 ``structured_script.json`` contract.

The service deliberately has only two operations:

``inspect(path, project_name)``
    Read and validate a JSON file, run the existing consistency checks, and
    return a preview.  It never creates a project and never performs I/O over
    the network.

``create(project_name, path)``
    Inspect again, reserve the project slot, and delegate the atomic directory creation to
    :class:`repositories.project_repo.ProjectRepository`.
"""
from __future__ import annotations

import json
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
    def _issue_key(issue: dict[str, Any]) -> tuple:
        """Return a stable key for de-duplicating validation diagnostics."""
        return (
            issue.get("code") or issue.get("type"),
            issue.get("path"),
            issue.get("role"),
            issue.get("id"),
            issue.get("message"),
        )

    @staticmethod
    def _script_summary(raw: dict[str, Any], project_name: str | None = None) -> dict[str, Any]:
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        voices, chapters = script_loader.resolve_collections(raw)
        segment_count = sum(
            len(chapter.get("segments", []))
            for chapter in chapters
            if isinstance(chapter, dict) and isinstance(chapter.get("segments"), list)
        )
        return {
            "title": str(meta.get("title") or project_name or raw.get("project_name") or ""),
            "author": str(meta.get("author") or "未填写"),
            "chapters": len(chapters),
            "segments": segment_count,
            "roles": len(voices),
        }

    @staticmethod
    def inspect_data(
        script: dict[str, Any],
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Validate an in-memory structured script using the file-import rules.

        The returned object is intentionally JSON-serializable so it can be
        returned directly by the MCP adapter.  It does not create a project or
        write a temporary file.
        """
        raw = script
        strict_issues = script_loader.validate_script_issues(raw)
        consistency = check_script_consistency(raw)
        issues: list[dict[str, Any]] = [dict(item) for item in strict_issues]
        # Structural errors are already covered by validate_script_issues.  The
        # consistency pass contributes quality warnings only, keeping one
        # authoritative error for each blocking rule.
        issues.extend(
            dict(item)
            for item in consistency.get("issues", [])
            if item.get("severity") == "warning"
        )
        unique: list[dict[str, Any]] = []
        seen: set[tuple] = set()
        for issue in issues:
            issue.setdefault("code", issue.get("type"))
            issue.setdefault("type", issue.get("code"))
            issue.setdefault("severity", "error")
            issue.setdefault("path", None)
            issue.setdefault("fix_hint", "按 path 定位并重新校验 structured_script。")
            key = StructuredScriptImportService._issue_key(issue)
            if key in seen:
                continue
            seen.add(key)
            unique.append(issue)

        selected_name = str(project_name or "").strip()
        slot = None
        if selected_name:
            try:
                selected_name = sanitize_project_name(selected_name)
                slot = StructuredScriptImportService._slot(selected_name)
            except ValueError as exc:
                unique.append({
                    "code": "invalid_project_name",
                    "type": "invalid_project_name",
                    "severity": "error",
                    "path": "project_name",
                    "message": str(exc),
                    "fix_hint": "使用 Windows 和 macOS 都可用的项目目录名。",
                })

        errors = [item for item in unique if item.get("severity") == "error"]
        warnings = [item for item in unique if item.get("severity") == "warning"]
        report: dict[str, Any] = {
            "valid": not errors,
            "can_create": not errors and (slot is None or slot.status == "available"),
            "project_name": selected_name or None,
            "summary": {"errors": len(errors), "warnings": len(warnings)},
            "errors": errors,
            "warnings": warnings,
            "script_summary": StructuredScriptImportService._script_summary(raw if isinstance(raw, dict) else {}, selected_name),
        }
        if slot is not None:
            report["project_slot"] = {
                "name": slot.name,
                "status": slot.status,
                "path": slot.path,
                "location": slot.location,
                "missing_files": list(slot.missing_files),
                "invalid_files": list(slot.invalid_files),
            }
            if slot.status != "available" and not errors:
                report["can_create"] = False
        return report

    @staticmethod
    def _raw_from_script(script) -> dict[str, Any]:
        raw = getattr(script, "raw", None)
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _candidate_name(path: str, raw: dict[str, Any]) -> str:
        raw = raw if isinstance(raw, dict) else {}
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
        _, chapters = script_loader.resolve_collections(raw)
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
    def _assert_slot_available(project_name: str) -> None:
        inspection = ProjectRepository.inspect_project_slot(project_name)
        if inspection.status == "available":
            return
        if inspection.status == "valid":
            raise ValueError(f"项目「{inspection.name}」已存在，请打开已有项目或更换名称")
        if inspection.status == "legacy":
            raise ValueError(f"项目「{inspection.name}」存在于旧版项目目录，请勿覆盖")
        if inspection.status == "incomplete":
            missing = "、".join(inspection.missing_files) or "未知文件"
            raise ValueError(
                f"发现不完整项目目录「{inspection.name}」；缺失：{missing}。"
                "请先点击“清理残留并重试”"
            )
        if inspection.status == "temporary":
            raise ValueError(
                f"发现临时项目目录「{inspection.name}」，请先归档残留后重试"
            )
        raise ValueError(
            f"项目目录「{inspection.name}」存在，但项目文件损坏。"
            "请先移动到回收站后重试"
        )

    @staticmethod
    def inspect(path: str, project_name: str | None = None) -> StructuredScriptPreview:
        """Inspect a JSON file without creating files in the project workspace."""
        if not path or not os.path.isfile(path):
            raise ValueError(f"剧本文件不存在：{path or '（未选择）'}")

        try:
            with open(path, encoding="utf-8") as file:
                raw = json.load(file)
        except UnicodeDecodeError as exc:
            raise ValueError(f"无法按 UTF-8 读取 JSON：{exc}") from exc
        except OSError as exc:
            raise ValueError(f"无法读取 JSON 文件：{exc}") from exc

        report = StructuredScriptImportService.inspect_data(raw, project_name)
        raw_dict = raw if isinstance(raw, dict) else {}
        raw_meta = raw_dict.get("meta") if isinstance(raw_dict.get("meta"), dict) else {}
        chapters = StructuredScriptImportService._raw_chapters(raw)
        segment_count = sum(
            len(chapter.get("segments", []))
            for chapter in chapters
            if isinstance(chapter, dict) and isinstance(chapter.get("segments"), list)
        )
        roles, _ = script_loader.resolve_collections(raw_dict)
        suggested = StructuredScriptImportService._candidate_name(path, raw_dict)
        title = str(raw_meta.get("title") or suggested)
        author = str(raw_meta.get("author") or "未填写")
        selected_name = str(project_name or suggested).strip() or suggested
        try:
            selected_name = sanitize_project_name(selected_name)
            slot = StructuredScriptImportService._slot(selected_name)
        except ValueError as exc:
            slot = None
            # Keep the old preview contract string while the machine report
            # remains the source of truth for MCP callers.
            report["errors"].append({
                "code": "invalid_project_name",
                "type": "invalid_project_name",
                "severity": "error",
                "path": "project_name",
                "message": f"project_name: {exc}",
                "fix_hint": "使用 Windows 和 macOS 都可用的项目目录名。",
            })
        errors = [item.get("message", "") for item in report["errors"]]
        warnings = [item.get("message", "") for item in report["warnings"]]
        # ``inspect_data`` has already checked the selected slot.  The preview
        # keeps the slot object for the existing Gradio import workbench.
        if slot is None and report.get("project_slot"):
            slot = StructuredScriptImportService._slot(selected_name)

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
            raw=raw_dict,
        )

    @staticmethod
    def create(project_name: str, path: str) -> StructuredScriptCreationResult:
        """Re-inspect, reserve the slot, and atomically create a V3 project."""
        safe_name = sanitize_project_name(str(project_name or "").strip())
        preview = StructuredScriptImportService.inspect(path, safe_name)
        if preview.errors:
            raise ValueError("JSON 校验失败：\n" + "\n".join(f"- {item}" for item in preview.errors))

        StructuredScriptImportService._assert_slot_available(safe_name)
        ProjectRepository.create_project(safe_name, path)
        return StructuredScriptCreationResult(
            project_name=safe_name,
            title=preview.title,
            chapter_count=preview.chapter_count,
            segment_count=preview.segment_count,
            role_count=preview.role_count,
            warnings=list(preview.warnings),
        )

    @staticmethod
    def create_from_data(project_name: str, script: dict[str, Any]) -> StructuredScriptCreationResult:
        """Create a project directly from an in-memory JSON object."""
        safe_name = sanitize_project_name(str(project_name or "").strip())
        report = StructuredScriptImportService.inspect_data(script, safe_name)
        if report["errors"]:
            messages = [item.get("message", "") for item in report["errors"]]
            raise ValueError("JSON 校验失败：\n" + "\n".join(f"- {item}" for item in messages))

        StructuredScriptImportService._assert_slot_available(safe_name)
        ProjectRepository.create_project_from_data(safe_name, script)
        summary = report["script_summary"]
        return StructuredScriptCreationResult(
            project_name=safe_name,
            title=summary["title"] or safe_name,
            chapter_count=summary["chapters"],
            segment_count=summary["segments"],
            role_count=summary["roles"],
            warnings=[item.get("message", "") for item in report["warnings"]],
        )


__all__ = [
    "StructuredScriptCreationResult",
    "StructuredScriptImportService",
    "StructuredScriptPreview",
]
