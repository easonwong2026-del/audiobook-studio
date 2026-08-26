"""Project Catalog scanning, search, and bookshelf display helpers."""
from __future__ import annotations

from typing import Iterable

from lib.types import ProjectSummary
from repositories.project_repo import ProjectRepository


class ProjectCatalogService:
    """Read-only catalog authority for the flat Project/Book bookshelf."""

    @classmethod
    def scan(cls) -> list[ProjectSummary]:
        """Return normal project summaries in stable display order."""
        return sorted(
            ProjectRepository.list_project_summaries(),
            key=lambda item: item.project_name.casefold(),
        )

    @classmethod
    def search_projects(cls, query: str = "") -> list[ProjectSummary]:
        return cls.filter_projects(cls.scan(), query)

    @classmethod
    def get_summary(cls, project_name: str) -> ProjectSummary | None:
        target = str(project_name or "")
        if not target:
            return None
        return next(
            (summary for summary in cls.scan() if summary.project_name == target),
            None,
        )

    @staticmethod
    def filter_projects(
        summaries: Iterable[ProjectSummary], query: str = ""
    ) -> list[ProjectSummary]:
        projects = list(summaries)
        q = str(query or "").strip().casefold()
        if not q:
            return projects
        return [
            summary
            for summary in projects
            if q
            in "".join(
                (summary.project_name, summary.title, summary.author)
            ).casefold()
        ]

    @staticmethod
    def display_name(summary: ProjectSummary) -> str:
        return summary.project_name

    @staticmethod
    def project_name_from_display(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def display_status(summary: ProjectSummary) -> str:
        return summary.status


__all__ = ["ProjectCatalogService"]
