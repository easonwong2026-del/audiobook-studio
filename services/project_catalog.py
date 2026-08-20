"""Project Catalog scanning, hierarchy normalization, and search.

The Catalog owns the logical Book → Chapter relationship. Project folders
remain flat and every physical operation continues to receive the existing
``project_name`` key. Legacy metadata is normalized in memory; ordinary
scans never write it back.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from lib.types import ProjectSummary
from repositories.project_repo import ProjectRepository

RELATION_STANDALONE = "standalone"
RELATION_VALID = "valid"
RELATION_ORPHAN = "orphan"
RELATION_INVALID = "invalid"


@dataclass(frozen=True)
class CatalogHierarchy:
    """One normalized, flat snapshot with logical parent/child indexes."""

    projects: tuple[ProjectSummary, ...]
    books: tuple[ProjectSummary, ...]
    chapters: tuple[ProjectSummary, ...]
    orphan_chapters: tuple[ProjectSummary, ...]


class ProjectCatalogService:
    """Catalog domain owner; does not import or depend on Gradio."""

    @staticmethod
    def _normalize_kind(value: object) -> str:
        return "chapter" if str(value or "").strip().lower() == "chapter" else "book"

    @staticmethod
    def _normalize_order(value: object) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _normalize_hierarchy(
        cls, summaries: Iterable[ProjectSummary]
    ) -> CatalogHierarchy:
        raw = list(summaries)
        normalized: list[ProjectSummary] = []
        for summary in raw:
            project_id = str(summary.project_id or "").strip() or None
            parent_id = str(summary.parent_project_id or "").strip() or None
            kind = cls._normalize_kind(summary.project_kind)
            normalized.append(
                replace(
                    summary,
                    project_id=project_id,
                    project_kind=kind,
                    parent_project_id=parent_id,
                    chapter_title=str(summary.chapter_title or "").strip() or None,
                    chapter_order=cls._normalize_order(summary.chapter_order),
                    parent_project_name=None,
                    relation_status=(
                        RELATION_ORPHAN if kind == "chapter" else RELATION_STANDALONE
                    ),
                    relation_message="未找到所属整书" if kind == "chapter" else "",
                )
            )

        by_id: dict[str, ProjectSummary] = {}
        duplicate_ids: set[str] = set()
        for summary in normalized:
            if not summary.project_id:
                continue
            if summary.project_id in by_id:
                duplicate_ids.add(summary.project_id)
            else:
                by_id[summary.project_id] = summary

        def has_cycle(start_id: str) -> bool:
            seen: set[str] = set()
            current = start_id
            while current:
                if current in seen:
                    return True
                seen.add(current)
                current_summary = by_id.get(current)
                if current_summary is None:
                    return False
                current = str(current_summary.parent_project_id or "")
            return False

        resolved: list[ProjectSummary] = []
        for summary in normalized:
            if summary.project_id and summary.project_id in duplicate_ids:
                resolved.append(
                    replace(
                        summary,
                        relation_status=RELATION_INVALID,
                        relation_message="project_id 重复，无法安全建立层级关系",
                    )
                )
                continue

            if summary.project_kind == "book":
                if summary.parent_project_id:
                    resolved.append(
                        replace(
                            summary,
                            relation_status=RELATION_INVALID,
                            relation_message="整书项目不能挂在其他项目下",
                        )
                    )
                else:
                    resolved.append(summary)
                continue

            parent_id = summary.parent_project_id
            if not summary.project_id:
                resolved.append(
                    replace(
                        summary,
                        relation_status=RELATION_ORPHAN,
                        relation_message="章节缺少稳定身份，无法解析所属整书",
                    )
                )
                continue
            if not parent_id:
                resolved.append(
                    replace(
                        summary,
                        relation_status=RELATION_ORPHAN,
                        relation_message="未设置所属整书",
                    )
                )
                continue
            if parent_id == summary.project_id:
                resolved.append(
                    replace(
                        summary,
                        relation_status=RELATION_INVALID,
                        relation_message="章节不能将自己设为所属整书",
                    )
                )
                continue

            parent = by_id.get(parent_id)
            if parent is None:
                resolved.append(
                    replace(
                        summary,
                        relation_status=RELATION_ORPHAN,
                        relation_message="未找到所属整书",
                    )
                )
                continue
            if has_cycle(summary.project_id):
                resolved.append(
                    replace(
                        summary,
                        parent_project_name=parent.project_name,
                        relation_status=RELATION_INVALID,
                        relation_message="层级关系存在循环",
                    )
                )
                continue
            if parent_id in duplicate_ids:
                resolved.append(
                    replace(
                        summary,
                        parent_project_name=parent.project_name,
                        relation_status=RELATION_INVALID,
                        relation_message="所属整书的 project_id 重复",
                    )
                )
                continue
            if (
                parent.project_kind != "book"
                or parent.relation_status != RELATION_STANDALONE
            ):
                resolved.append(
                    replace(
                        summary,
                        parent_project_name=parent.project_name,
                        relation_status=RELATION_INVALID,
                        relation_message="所属项目不是整书项目",
                    )
                )
                continue

            resolved.append(
                replace(
                    summary,
                    parent_project_name=parent.project_name,
                    relation_status=RELATION_VALID,
                    relation_message="",
                )
            )

        books = tuple(
            summary for summary in resolved if summary.project_kind == "book"
        )
        chapters = tuple(
            summary for summary in resolved if summary.project_kind == "chapter"
        )
        children_by_parent: dict[str, list[ProjectSummary]] = {}
        for chapter in chapters:
            if chapter.relation_status == RELATION_VALID and chapter.parent_project_id:
                children_by_parent.setdefault(chapter.parent_project_id, []).append(chapter)

        def chapter_sort_key(summary: ProjectSummary) -> tuple:
            return (
                summary.chapter_order is None,
                summary.chapter_order if summary.chapter_order is not None else 0,
                (summary.chapter_title or summary.title or "").casefold(),
                summary.project_name.casefold(),
            )

        ordered: list[ProjectSummary] = []
        for book in books:
            ordered.append(book)
            ordered.extend(
                sorted(
                    children_by_parent.get(book.project_id or "", []),
                    key=chapter_sort_key,
                )
            )

        # Invalid and orphan chapters stay visible at the top level, after
        # valid book groups. They are never silently dropped or re-parented.
        ordered.extend(
            summary
            for summary in sorted(
                chapters,
                key=lambda item: (
                    item.relation_status == RELATION_VALID,
                    item.project_name.casefold(),
                ),
            )
            if summary.relation_status != RELATION_VALID
        )

        seen_names: set[str] = set()
        deduplicated: list[ProjectSummary] = []
        for summary in ordered:
            if summary.project_name in seen_names:
                continue
            seen_names.add(summary.project_name)
            deduplicated.append(summary)
        if len(deduplicated) != len(resolved):
            deduplicated.extend(
                summary
                for summary in resolved
                if summary.project_name not in seen_names
            )

        orphan_chapters = tuple(
            chapter
            for chapter in chapters
            if chapter.relation_status in {RELATION_ORPHAN, RELATION_INVALID}
        )
        return CatalogHierarchy(
            projects=tuple(deduplicated),
            books=books,
            chapters=chapters,
            orphan_chapters=orphan_chapters,
        )

    @classmethod
    def hierarchy_from_summaries(
        cls, summaries: Iterable[ProjectSummary]
    ) -> CatalogHierarchy:
        """Normalize an already-scanned flat list without disk access."""
        return cls._normalize_hierarchy(summaries)

    @classmethod
    def scan_hierarchy(cls) -> CatalogHierarchy:
        """Scan once and build the complete logical hierarchy in memory."""
        return cls._normalize_hierarchy(ProjectRepository.list_project_summaries())

    @classmethod
    def scan(cls) -> list[ProjectSummary]:
        """Return the normalized flat presentation order for compatibility."""
        return list(cls.scan_hierarchy().projects)

    @classmethod
    def search_projects(cls, query: str = "") -> list[ProjectSummary]:
        """Search a hierarchy-aware normalized Catalog snapshot."""
        return cls.filter_projects(cls.scan(), query)

    @classmethod
    def get_summary(cls, project_name: str) -> ProjectSummary | None:
        """Return one normalized summary without exposing repository details."""
        target = str(project_name or "")
        if not target:
            return None
        return next(
            (summary for summary in cls.scan() if summary.project_name == target),
            None,
        )

    @classmethod
    def filter_projects(
        cls, summaries: Iterable[ProjectSummary], query: str = ""
    ) -> list[ProjectSummary]:
        """Filter while retaining parent context for hierarchy matches."""
        hierarchy = cls._normalize_hierarchy(summaries)
        projects = list(hierarchy.projects)
        q = str(query or "").strip().casefold()
        if not q:
            return projects

        direct_matches = {
            summary.project_name
            for summary in projects
            if q
            in "".join(
                (
                    summary.project_name,
                    summary.title,
                    summary.author,
                    summary.chapter_title or "",
                    summary.parent_project_name or "",
                )
            ).casefold()
        }
        included = set(direct_matches)
        by_name = {summary.project_name: summary for summary in projects}
        children_by_parent: dict[str, list[str]] = {}
        for summary in projects:
            if summary.relation_status == RELATION_VALID and summary.parent_project_name:
                children_by_parent.setdefault(summary.parent_project_name, []).append(
                    summary.project_name
                )

        for name in direct_matches:
            summary = by_name[name]
            if summary.project_kind == "book":
                included.update(children_by_parent.get(name, []))
            elif summary.parent_project_name:
                included.add(summary.parent_project_name)

        return [summary for summary in projects if summary.project_name in included]

    @classmethod
    def book_choices(
        cls,
        summaries: Iterable[ProjectSummary],
        *,
        exclude_name: str | None = None,
    ) -> list[str]:
        """Return safe parent candidates from one normalized snapshot."""
        excluded = str(exclude_name or "")
        return [
            summary.project_name
            for summary in cls._normalize_hierarchy(summaries).books
            if summary.project_name != excluded
        ]

    @classmethod
    def bind_chapter(
        cls,
        chapter_name: str,
        parent_name: str,
        *,
        chapter_title: str | None = None,
        chapter_order: int | None = None,
    ) -> None:
        """Explicitly bind one independent project under a book project."""
        child_name = str(chapter_name or "").strip()
        book_name = str(parent_name or "").strip()
        if not child_name or not book_name:
            raise ValueError("请选择章节项目和所属整书")
        if child_name == book_name:
            raise ValueError("项目不能绑定自己")

        hierarchy = cls.scan_hierarchy()
        by_name = {summary.project_name: summary for summary in hierarchy.projects}
        child = by_name.get(child_name)
        parent = by_name.get(book_name)
        if child is None or parent is None:
            raise ValueError("章节项目或所属整书不存在")
        if parent.project_kind != "book":
            raise ValueError("所属项目必须是整书项目，不能选择章节项目")
        if child.project_kind == "book" and child.project_id:
            existing_children = [
                item
                for item in hierarchy.chapters
                if item.relation_status == RELATION_VALID
                and item.parent_project_id == child.project_id
            ]
            if existing_children:
                raise ValueError(
                    f"项目「{child_name}」仍关联 {len(existing_children)} 个章节，"
                    "请先解除这些章节关系"
                )

        chapter_id = ProjectRepository.ensure_project_id(child_name)
        parent_id = ProjectRepository.ensure_project_id(book_name)
        if chapter_id == parent_id:
            raise ValueError("项目稳定身份冲突，无法建立关系")
        normalized_order = cls._normalize_order(chapter_order)
        title = str(
            chapter_title or child.chapter_title or child.title or child_name
        ).strip()
        ProjectRepository.set_project_relationship(
            child_name,
            parent_id,
            chapter_title=title or None,
            chapter_order=normalized_order,
        )

    @classmethod
    def clear_chapter_parent(cls, chapter_name: str) -> None:
        """Explicitly restore a chapter project to an independent book."""
        name = str(chapter_name or "").strip()
        hierarchy = cls.scan_hierarchy()
        summary = next(
            (item for item in hierarchy.projects if item.project_name == name), None
        )
        if summary is None:
            raise ValueError("项目不存在")
        if summary.project_kind != "chapter":
            raise ValueError("当前项目不是章节项目")
        ProjectRepository.clear_project_relationship(name)

    @classmethod
    def assert_archive_allowed(cls, project_name: str) -> None:
        """Block archiving a book while valid child projects still point to it."""
        name = str(project_name or "").strip()
        hierarchy = cls.scan_hierarchy()
        summary = next(
            (item for item in hierarchy.projects if item.project_name == name), None
        )
        if summary is None:
            raise ValueError("项目不存在")
        if summary.project_kind != "book" or not summary.project_id:
            return
        children = [
            item
            for item in hierarchy.chapters
            if item.relation_status == RELATION_VALID
            and item.parent_project_id == summary.project_id
        ]
        if children:
            raise ValueError(
                f"整书「{name}」仍关联 {len(children)} 个章节，"
                "请先解除章节关系后再归档"
            )

    @staticmethod
    def display_name(summary: ProjectSummary) -> str:
        """Render a hierarchy marker while keeping the physical name recoverable."""
        if summary.project_kind != "chapter":
            return summary.project_name
        label = summary.chapter_title or summary.title or summary.project_name
        marker = "↳" if summary.relation_status == RELATION_VALID else "↳ ⚠"
        return f"{marker} {label} · {summary.project_name}"

    @staticmethod
    def project_name_from_display(value: object) -> str:
        """Recover the canonical project name from a displayed bookshelf cell."""
        text = str(value or "").strip()
        if text.startswith("↳") and " · " in text:
            return text.rsplit(" · ", 1)[-1].strip()
        return text

    @staticmethod
    def display_status(summary: ProjectSummary) -> str:
        if summary.relation_status == RELATION_ORPHAN:
            return f"⚠孤立章节 · {summary.status}"
        if summary.relation_status == RELATION_INVALID:
            return f"⚠关系无效 · {summary.status}"
        if summary.relation_status == RELATION_VALID:
            return f"章节 · {summary.status}"
        return summary.status


__all__ = [
    "RELATION_INVALID",
    "RELATION_ORPHAN",
    "RELATION_STANDALONE",
    "RELATION_VALID",
    "CatalogHierarchy",
    "ProjectCatalogService",
]
