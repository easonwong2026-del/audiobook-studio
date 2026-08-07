"""Project creation guards for the V3 JSON import workflow.

The application no longer analyzes source novels.  External agents produce a
single ``structured_script.json`` and this service keeps the existing project
slot policy available to the import service.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from repositories.project_repo import ProjectRepository, sanitize_project_name


@dataclass
class ProjectCreationResult:
    project_name: str
    title: str
    chapter_count: int
    segment_count: int
    role_count: int
    warnings: list[str] = field(default_factory=list)


class ProjectCreationService:
    """Keep project-slot policy and delegate JSON creation to one service."""

    @staticmethod
    def _safe_name(value: str) -> str:
        return sanitize_project_name(value)

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
    def create_from_structured_script(
        project_name: str,
        script_path: str,
    ) -> ProjectCreationResult:
        """Create from one validated JSON file and return production metadata."""
        from services.structured_script_import import StructuredScriptImportService

        result = StructuredScriptImportService.create(project_name, script_path)
        return ProjectCreationResult(
            project_name=result.project_name,
            title=result.title,
            chapter_count=result.chapter_count,
            segment_count=result.segment_count,
            role_count=result.role_count,
            warnings=list(result.warnings),
        )

    @staticmethod
    def create_from_structured_data(
        project_name: str,
        script: dict,
    ) -> ProjectCreationResult:
        """Create from an in-memory script through the same import service."""
        from services.structured_script_import import StructuredScriptImportService

        result = StructuredScriptImportService.create_from_data(project_name, script)
        return ProjectCreationResult(
            project_name=result.project_name,
            title=result.title,
            chapter_count=result.chapter_count,
            segment_count=result.segment_count,
            role_count=result.role_count,
            warnings=list(result.warnings),
        )


__all__ = ["ProjectCreationResult", "ProjectCreationService"]
