"""Sequential whole-book assembly orchestration.

This module is intentionally one layer above the existing Chapter merge
workflow.  It owns discovery, ordering, aggregate status, confirmation, and
the sequential control loop; :class:`ChapterMergePlanner` remains the only
eligibility planner and :class:`ChapterMergeExecutor` remains the only code
that mutates a project tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from lib import config
from lib.types import ProjectSummary
from repositories.project_repo import ProjectRepository
from services.chapter_merge_executor import (
    MERGE_FAILED_ROLLBACK_FAILED,
    MERGE_FAILED_ROLLED_BACK,
    ChapterMergeExecutor,
    MergeExecutionError,
    MergeExecutionResult,
)
from services.chapter_merge_planner import (
    BLOCKED as MERGE_BLOCKED,
)
from services.chapter_merge_planner import (
    PLANNING_ALLOWED,
    ChapterMergePlanner,
    MergeConflict,
    MergePlan,
)
from services.chapter_merge_planner import (
    READY as MERGE_READY,
)
from services.chapter_merge_planner import (
    READY_WITH_WARNINGS as MERGE_READY_WITH_WARNINGS,
)
from services.project_catalog import (
    RELATION_INVALID,
    RELATION_STANDALONE,
    RELATION_VALID,
    CatalogHierarchy,
    ProjectCatalogService,
)
from services.project_storage import ProjectStorageService

ASSEMBLY_SCHEMA_VERSION = "whole-book-assembly-v1"

ASSEMBLY_READY = "READY"
ASSEMBLY_READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
ASSEMBLY_BLOCKED = "BLOCKED"

ASSEMBLY_SUCCEEDED = "SUCCEEDED"
ASSEMBLY_PARTIAL_SUCCESS_STOPPED = "PARTIAL_SUCCESS_STOPPED"
ASSEMBLY_VALIDATION_FAILED = "VALIDATION_FAILED"
ASSEMBLY_CANCELLED = "CANCELLED"
ASSEMBLY_CRITICAL_FAILURE = "CRITICAL_FAILURE"

CHAPTER_MERGED = "MERGED"
CHAPTER_ALREADY_MERGED = "ALREADY_MERGED"
CHAPTER_SKIPPED = "SKIPPED"
CHAPTER_BLOCKED = "BLOCKED"
CHAPTER_FAILED = "FAILED"
CHAPTER_FAILED_ROLLED_BACK = "FAILED_ROLLED_BACK"
CHAPTER_FAILED_ROLLBACK_FAILED = "FAILED_ROLLBACK_FAILED"
CHAPTER_NOT_ATTEMPTED = "NOT_ATTEMPTED"

WHOLE_BOOK_SELECTION_POLICY = "WHOLE_BOOK_ASSEMBLY"

_VOICE_RESOLUTION_CODES = frozenset(
    {"SOURCE_ONLY_ROLE", "VOICE_BINDING_CONFLICT"}
)


class WholeBookAssemblyError(RuntimeError):
    """Structured error for planning and confirmation boundaries."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.details = dict(details or {})


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _data_root() -> str:
    return os.path.realpath(config.get_data_dir())


def _tree_digest(path: str) -> str:
    """Digest a project tree without including timestamps or symlink targets."""
    root = os.path.realpath(path)
    digest = hashlib.sha256()
    if not os.path.isdir(root):
        return ""
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if not os.path.islink(os.path.join(current, name))
        )
        for name in sorted(files):
            file_path = os.path.join(current, name)
            if os.path.islink(file_path) or not os.path.isfile(file_path):
                continue
            relative = os.path.relpath(file_path, root).replace(os.sep, "/")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            try:
                with open(file_path, "rb") as file:
                    for chunk in iter(lambda: file.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                return ""
            digest.update(b"\0")
    return digest.hexdigest()


def _summary_name(value: Any) -> str:
    return str(
        getattr(value, "project_name", None)
        or (value.get("project_name") if isinstance(value, Mapping) else "")
        or (value.get("name") if isinstance(value, Mapping) else "")
        or value
        or ""
    ).strip()


def _normalise_resolution_document(value: Any) -> dict[str, Any]:
    """Normalize supported per-Chapter resolution input without global leaks."""
    if not isinstance(value, Mapping):
        return {"chapters": {}}
    if isinstance(value.get("chapters"), Mapping):
        source = value["chapters"]
    elif isinstance(value.get("by_chapter"), Mapping):
        source = value["by_chapter"]
    elif "voice_conflicts" in value:
        return {"chapters": {"__default__": {"voice_conflicts": dict(value.get("voice_conflicts") or {})}}}
    else:
        source = value
    chapters: dict[str, dict[str, Any]] = {}
    for key, resolution in source.items():
        if not isinstance(resolution, Mapping):
            continue
        if "voice_conflicts" in resolution:
            chapters[str(key)] = {
                "voice_conflicts": dict(resolution.get("voice_conflicts") or {})
            }
        else:
            chapters[str(key)] = {"voice_conflicts": dict(resolution)}
    return {"chapters": chapters}


def _resolution_for(
    document: Mapping[str, Any], chapter: BookAssemblyChapterPlan, *, default: bool
) -> dict[str, Any]:
    chapters = document.get("chapters")
    if not isinstance(chapters, Mapping):
        return {"voice_conflicts": {}}
    for key in (
        chapter.chapter_project_id,
        chapter.chapter_project_name,
        str(chapter.order),
    ):
        value = chapters.get(str(key))
        if isinstance(value, Mapping):
            return dict(value)
    if default and isinstance(chapters.get("__default__"), Mapping):
        return dict(chapters["__default__"])
    return {"voice_conflicts": {}}


def _conflict_from_merge(
    conflict: MergeConflict,
    chapter: Any,
) -> BookAssemblyConflict:
    return BookAssemblyConflict(
        code=conflict.code,
        severity=conflict.severity,
        message=conflict.message,
        blocking=conflict.blocking,
        chapter_project_id=chapter.chapter_project_id,
        chapter_project_name=chapter.chapter_project_name,
        details=dict(conflict.details),
    )


@dataclass(frozen=True)
class BookAssemblyConflict:
    code: str
    severity: str
    message: str
    blocking: bool = False
    chapter_project_id: str = ""
    chapter_project_name: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "blocking": self.blocking,
            "chapter_project_id": self.chapter_project_id,
            "chapter_project_name": self.chapter_project_name,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class BookAssemblyChapterPlan:
    order: int
    chapter_project_id: str
    chapter_project_name: str
    chapter_title: str
    chapter_order: int | None
    relation_status: str
    initial_plan_status: str
    merge_plan: MergePlan | None = field(default=None, repr=False, compare=False)
    conflicts: tuple[BookAssemblyConflict, ...] = ()
    warnings: tuple[str, ...] = ()
    source_state_fingerprint: str = ""
    already_merged: bool = False

    @property
    def plan(self) -> MergePlan | None:
        """Short alias for callers that use the domain term ``plan``."""
        return self.merge_plan

    @property
    def eligible(self) -> bool:
        return self.relation_status == RELATION_VALID and not any(
            conflict.blocking for conflict in self.conflicts
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "chapter_project_id": self.chapter_project_id,
            "chapter_project_name": self.chapter_project_name,
            "chapter_title": self.chapter_title,
            "chapter_order": self.chapter_order,
            "relation_status": self.relation_status,
            "initial_plan_status": self.initial_plan_status,
            "merge_plan": self.merge_plan.as_dict() if self.merge_plan else None,
            "conflicts": [item.as_dict() for item in self.conflicts],
            "warnings": list(self.warnings),
            "source_state_fingerprint": self.source_state_fingerprint,
            "already_merged": self.already_merged,
            "eligible": self.eligible,
        }


@dataclass(frozen=True)
class BookAssemblyPlan:
    target_book_id: str
    target_book_name: str
    ordered_chapters: tuple[BookAssemblyChapterPlan, ...]
    aggregate_status: str
    blocking_conflicts: tuple[BookAssemblyConflict, ...]
    warnings: tuple[str, ...]
    assembly_token: str
    structure_token: str
    target_state_fingerprint: str
    target_tree_fingerprint: str
    data_root: str
    total_segment_count: int = 0
    total_audio_count: int = 0
    assembly_conflicts: tuple[BookAssemblyConflict, ...] = ()
    resolution_fingerprint: str = ""

    @property
    def fingerprint(self) -> str:
        return self.assembly_token

    @property
    def chapter_plans(self) -> tuple[BookAssemblyChapterPlan, ...]:
        return self.ordered_chapters

    @property
    def plan_status(self) -> str:
        return self.aggregate_status

    @property
    def already_merged_chapters(self) -> tuple[BookAssemblyChapterPlan, ...]:
        return tuple(item for item in self.ordered_chapters if item.already_merged)

    @property
    def pending_chapters(self) -> tuple[BookAssemblyChapterPlan, ...]:
        return tuple(
            item
            for item in self.ordered_chapters
            if item.relation_status == RELATION_VALID and not item.already_merged
        )

    @property
    def ordered_chapter_ids(self) -> tuple[str, ...]:
        return tuple(item.chapter_project_id for item in self.ordered_chapters)

    @property
    def ordered_chapter_names(self) -> tuple[str, ...]:
        return tuple(item.chapter_project_name for item in self.ordered_chapters)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ASSEMBLY_SCHEMA_VERSION,
            "target_book_id": self.target_book_id,
            "target_book_name": self.target_book_name,
            "ordered_chapters": [item.as_dict() for item in self.ordered_chapters],
            "chapter_plans": [item.as_dict() for item in self.ordered_chapters],
            "aggregate_status": self.aggregate_status,
            "plan_status": self.aggregate_status,
            "blocking_conflicts": [item.as_dict() for item in self.blocking_conflicts],
            "assembly_conflicts": [item.as_dict() for item in self.assembly_conflicts],
            "warnings": list(self.warnings),
            "assembly_token": self.assembly_token,
            "fingerprint": self.assembly_token,
            "structure_token": self.structure_token,
            "target_state_fingerprint": self.target_state_fingerprint,
            "target_tree_fingerprint": self.target_tree_fingerprint,
            "data_root": self.data_root,
            "total_segment_count": self.total_segment_count,
            "total_audio_count": self.total_audio_count,
            "already_merged_chapters": len(self.already_merged_chapters),
            "pending_chapters": len(self.pending_chapters),
            "resolution_fingerprint": self.resolution_fingerprint,
        }

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)


@dataclass(frozen=True)
class BookAssemblyConfirmation:
    schema_version: str
    confirmation_token: str
    assembly_token: str
    target_book_id: str
    target_book_name: str
    ordered_chapter_ids: tuple[str, ...]
    resolution_fingerprint: str
    structure_token: str
    selection_revision: int
    selected_project: str
    opened_project: str
    data_root: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ordered_chapter_ids"] = list(self.ordered_chapter_ids)
        return payload


@dataclass(frozen=True)
class BookAssemblyChapterResult:
    order: int
    chapter_project_id: str
    chapter_project_name: str
    initial_plan_status: str
    final_plan_status: str
    execution_result: str
    imported_segment_count: int = 0
    imported_audio_count: int = 0
    warnings: tuple[str, ...] = ()
    blocking_conflict: str = ""
    backup_reference: str = ""
    transaction_id: str = ""
    journal_reference: str = ""
    integrity: Mapping[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error: str = ""

    @property
    def status(self) -> str:
        return self.execution_result

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        payload["integrity"] = dict(self.integrity)
        return payload


@dataclass(frozen=True)
class BookAssemblyExecutionResult:
    success: bool
    status: str
    assembly_id: str
    target_book_id: str
    target_book_name: str
    assembly_token: str
    started_at: str
    finished_at: str
    chapter_results: tuple[BookAssemblyChapterResult, ...] = ()
    final_integrity: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error_code: str = ""
    error: str = ""
    merged_this_run: int = 0
    already_merged: int = 0
    blocked: int = 0
    failed: int = 0
    not_attempted: int = 0
    total_segments_added: int = 0
    total_audio_copied: int = 0

    @property
    def report(self) -> dict[str, Any]:
        return self.as_dict()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ASSEMBLY_SCHEMA_VERSION,
            "success": self.success,
            "status": self.status,
            "assembly_id": self.assembly_id,
            "target_book_id": self.target_book_id,
            "target_book_name": self.target_book_name,
            "assembly_token": self.assembly_token,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "chapter_results": [item.as_dict() for item in self.chapter_results],
            "final_integrity": dict(self.final_integrity),
            "warnings": list(self.warnings),
            "error_code": self.error_code,
            "error": self.error,
            "merged_this_run": self.merged_this_run,
            "already_merged": self.already_merged,
            "blocked": self.blocked,
            "failed": self.failed,
            "not_attempted": self.not_attempted,
            "total_segments_added": self.total_segments_added,
            "total_audio_copied": self.total_audio_copied,
        }

    to_dict = as_dict


BookAssemblyResult = BookAssemblyExecutionResult


def _session_values(session: Any) -> tuple[str, str, int]:
    selected = str(getattr(session, "selected_project", "") or "") if session else ""
    opened = str(getattr(session, "project", "") or "") if session else ""
    try:
        revision = int(getattr(session, "selection_revision", 0) or 0) if session else 0
    except (TypeError, ValueError):
        revision = 0
    return selected, opened, revision


class WholeBookAssemblyService:
    """Book-level orchestration over the existing Chapter merge contracts."""

    @staticmethod
    def _resolve_target(
        hierarchy: CatalogHierarchy, value: Any
    ) -> ProjectSummary | None:
        requested = _summary_name(value)
        if not requested:
            return None
        exact = next(
            (item for item in hierarchy.projects if item.project_name == requested), None
        )
        if exact is not None:
            return exact
        matches = [item for item in hierarchy.projects if item.project_id == requested]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _related_chapters(
        hierarchy: CatalogHierarchy, target_id: str
    ) -> list[ProjectSummary]:
        """Use Catalog presentation order; do not define a second sort policy."""
        return [
            item
            for item in hierarchy.projects
            if item.project_kind == "chapter"
            and str(item.parent_project_id or "") == target_id
        ]

    @staticmethod
    def _structure_scope(
        target_id: str, related: Iterable[ProjectSummary]
    ) -> list[dict[str, Any]]:
        return [
            {
                "project_id": item.project_id or "",
                "project_name": item.project_name,
                "project_kind": item.project_kind,
                "parent_project_id": item.parent_project_id or "",
                "chapter_order": item.chapter_order,
                "relation_status": item.relation_status,
            }
            for item in related
        ]

    @staticmethod
    def _status_for_plan(
        plan: MergePlan,
        resolution: Mapping[str, Any],
    ) -> tuple[str, list[MergeConflict], list[str], bool]:
        """Apply only the Executor's explicit Voice Cast choices."""
        voice_choices = resolution.get("voice_conflicts")
        voice_choices = voice_choices if isinstance(voice_choices, Mapping) else {}
        blockers: list[MergeConflict] = []
        warnings: list[str] = []
        already = False
        source_changed_after_merge = False
        for conflict in plan.conflicts:
            if conflict.code == "ALREADY_MERGED":
                already = True
                continue
            if conflict.code == "SOURCE_CHANGED_AFTER_PREVIOUS_MERGE":
                source_changed_after_merge = True
            if not conflict.blocking:
                warnings.append(conflict.message)
                continue
            if conflict.code in _VOICE_RESOLUTION_CODES:
                role_key = str(conflict.details.get("role_key") or "")
                choice = str(voice_choices.get(role_key) or "").upper()
                allowed = (
                    "ADD_SOURCE_ROLE"
                    if conflict.code == "SOURCE_ONLY_ROLE"
                    else "KEEP_TARGET"
                )
                if choice == allowed:
                    continue
            blockers.append(conflict)
        if already and not source_changed_after_merge:
            # The planner can also observe the segment IDs now present in the
            # target after the historical merge.  Exact merge history is the
            # authoritative idempotency signal, so those target-side
            # collisions must not turn a safe no-op into a new blocker.
            return CHAPTER_ALREADY_MERGED, [], warnings, True
        if plan.planning_status != PLANNING_ALLOWED and not blockers:
            blockers.append(
                MergeConflict(
                    code="PLANNING_BLOCKED",
                    severity="ERROR",
                    domain="validation",
                    message="Chapter MergePlanner 未允许进入执行规划",
                    blocking=True,
                )
            )
        if blockers:
            return MERGE_BLOCKED, blockers, warnings, False
        return (
            MERGE_READY_WITH_WARNINGS if warnings else MERGE_READY,
            blockers,
            warnings,
            False,
        )

    @staticmethod
    def _voice_conflict_resolved(
        conflict: BookAssemblyConflict, resolution: Mapping[str, Any]
    ) -> bool:
        if conflict.code not in _VOICE_RESOLUTION_CODES:
            return False
        choices = resolution.get("voice_conflicts")
        choices = choices if isinstance(choices, Mapping) else {}
        role_key = str(conflict.details.get("role_key") or "")
        choice = str(choices.get(role_key) or "").upper()
        allowed = (
            "ADD_SOURCE_ROLE"
            if conflict.code == "SOURCE_ONLY_ROLE"
            else "KEEP_TARGET"
        )
        return choice == allowed

    @classmethod
    def _effective_assembly_blockers(
        cls,
        plan: BookAssemblyPlan,
        resolution_document: Mapping[str, Any],
    ) -> list[BookAssemblyConflict]:
        blockers: list[BookAssemblyConflict] = []
        for conflict in plan.blocking_conflicts:
            chapter = next(
                (
                    item
                    for item in plan.ordered_chapters
                    if item.chapter_project_id == conflict.chapter_project_id
                    or item.chapter_project_name == conflict.chapter_project_name
                ),
                None,
            )
            resolution = (
                _resolution_for(
                    resolution_document,
                    chapter,
                    default=len(plan.ordered_chapters) == 1,
                )
                if chapter is not None
                else {"voice_conflicts": {}}
            )
            if not cls._voice_conflict_resolved(conflict, resolution):
                blockers.append(conflict)
        return blockers

    @staticmethod
    def _assembly_conflict(
        code: str,
        message: str,
        *,
        blocking: bool,
        chapter: ProjectSummary | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> BookAssemblyConflict:
        return BookAssemblyConflict(
            code=code,
            severity="ERROR" if blocking else "WARNING",
            message=message,
            blocking=blocking,
            chapter_project_id=str(getattr(chapter, "project_id", "") or ""),
            chapter_project_name=str(getattr(chapter, "project_name", "") or ""),
            details=dict(details or {}),
        )

    @classmethod
    def plan_assembly(
        cls,
        target_book: Any,
        *,
        resolutions: Any = None,
        session: Any = None,
        opened_projects: Iterable[str] | None = None,
    ) -> BookAssemblyPlan:
        hierarchy = ProjectCatalogService.scan_hierarchy()
        target = cls._resolve_target(hierarchy, target_book)
        requested_name = _summary_name(target_book)
        resolution_document = _normalise_resolution_document(resolutions)
        resolution_fingerprint = _digest(resolution_document)
        data_root = _data_root()
        assembly_conflicts: list[BookAssemblyConflict] = []

        if target is None:
            assembly_conflicts.append(
                cls._assembly_conflict(
                    "TARGET_NOT_FOUND",
                    "目标 Book 不存在或 project_id 不唯一",
                    blocking=True,
                    details={"requested": requested_name},
                )
            )
            target_id = ""
            target_name = requested_name
            related: list[ProjectSummary] = []
        else:
            target_id = str(target.project_id or "")
            target_name = target.project_name
            if target.project_kind != "book":
                assembly_conflicts.append(
                    cls._assembly_conflict(
                        "TARGET_NOT_BOOK",
                        "整书装配的目标必须是 Book 项目",
                        blocking=True,
                        chapter=target,
                    )
                )
            if not target_id:
                assembly_conflicts.append(
                    cls._assembly_conflict(
                        "TARGET_ID_MISSING",
                        "目标 Book 缺少稳定 project_id",
                        blocking=True,
                        chapter=target,
                    )
                )
            if target_id in set(hierarchy.duplicate_project_ids):
                assembly_conflicts.append(
                    cls._assembly_conflict(
                        "DUPLICATE_PROJECT_ID",
                        "目标 Book 的 project_id 重复，无法安全装配",
                        blocking=True,
                        chapter=target,
                        details={"project_id": target_id},
                    )
                )
            if target.relation_status != RELATION_STANDALONE:
                assembly_conflicts.append(
                    cls._assembly_conflict(
                        "TARGET_RELATION_UNSAFE",
                        "目标 Book 的层级关系无效",
                        blocking=True,
                        chapter=target,
                        details={"relation_status": target.relation_status},
                    )
                )
            selected, opened, _revision = _session_values(session)
            if (
                session is not None
                and target.project_kind == "book"
                and selected != target_name
            ):
                assembly_conflicts.append(
                    cls._assembly_conflict(
                        "SELECTED_TARGET_CHANGED",
                        "当前 bookshelf selected_project 不是目标 Book",
                        blocking=True,
                        chapter=target,
                        details={"selected_project": selected, "target": target_name},
                    )
                )
            related = cls._related_chapters(hierarchy, target_id)

        structure_scope = cls._structure_scope(target_id, related)
        structure_token = _digest(
            {"schema_version": ASSEMBLY_SCHEMA_VERSION, "target_id": target_id, "related": structure_scope}
        )

        selected, opened, _revision = _session_values(session)
        if target is not None and opened == target_name:
            assembly_conflicts.append(
                cls._assembly_conflict(
                    "TARGET_OPENED",
                    "目标 Book 当前处于 opened production session，拒绝后台改盘",
                    blocking=True,
                    chapter=target,
                )
            )

        chapter_plans: list[BookAssemblyChapterPlan] = []
        total_segments = 0
        total_audio = 0
        target_state_fingerprint = ""
        for index, chapter in enumerate(related, start=1):
            title = chapter.chapter_title or chapter.title or chapter.project_name
            if (
                chapter.relation_status != RELATION_VALID
                or not chapter.project_id
                or chapter.project_id in set(hierarchy.duplicate_project_ids)
            ):
                code = (
                    "INVALID_RELATED_CHAPTER"
                    if chapter.relation_status == RELATION_INVALID
                    else "ORPHAN_RELATED_CHAPTER"
                )
                conflict = cls._assembly_conflict(
                    code,
                    chapter.relation_message or "关联 Chapter 的层级身份不安全",
                    blocking=True,
                    chapter=chapter,
                    details={"relation_status": chapter.relation_status},
                )
                chapter_plans.append(
                    BookAssemblyChapterPlan(
                        order=index,
                        chapter_project_id=str(chapter.project_id or ""),
                        chapter_project_name=chapter.project_name,
                        chapter_title=title,
                        chapter_order=chapter.chapter_order,
                        relation_status=chapter.relation_status,
                        initial_plan_status=ASSEMBLY_BLOCKED,
                        conflicts=(conflict,),
                    )
                )
                assembly_conflicts.append(conflict)
                continue

            plan = ChapterMergePlanner.plan_merge(
                chapter.project_name,
                target_name,
                session=session,
                opened_projects=opened_projects,
            )
            resolution = _resolution_for(
                resolution_document,
                BookAssemblyChapterPlan(
                    order=index,
                    chapter_project_id=str(chapter.project_id),
                    chapter_project_name=chapter.project_name,
                    chapter_title=title,
                    chapter_order=chapter.chapter_order,
                    relation_status=chapter.relation_status,
                    initial_plan_status=plan.execution_eligibility,
                ),
                default=len(related) == 1,
            )
            status, blockers, warnings, already = cls._status_for_plan(plan, resolution)
            entry_context = BookAssemblyChapterPlan(
                order=index,
                chapter_project_id=str(chapter.project_id),
                chapter_project_name=chapter.project_name,
                chapter_title=title,
                chapter_order=chapter.chapter_order,
                relation_status=chapter.relation_status,
                initial_plan_status=status,
                merge_plan=plan,
            )
            chapter_conflicts = tuple(
                _conflict_from_merge(item, entry_context) for item in blockers
            )
            entry = BookAssemblyChapterPlan(
                order=entry_context.order,
                chapter_project_id=entry_context.chapter_project_id,
                chapter_project_name=entry_context.chapter_project_name,
                chapter_title=entry_context.chapter_title,
                chapter_order=entry_context.chapter_order,
                relation_status=entry_context.relation_status,
                initial_plan_status=entry_context.initial_plan_status,
                merge_plan=plan,
                conflicts=chapter_conflicts,
                warnings=tuple(warnings),
                source_state_fingerprint=str(
                    plan.token_scope.get("source_state_fingerprint") or ""
                ),
                already_merged=already,
            )
            chapter_plans.append(entry)
            if target_state_fingerprint == "":
                target_state_fingerprint = str(
                    plan.token_scope.get("target_state_fingerprint") or ""
                )
            total_segments += int(plan.source_inventory.total_segments or 0)
            total_audio += int(
                (plan.source_inventory.audio or {}).get("file_count") or 0
            )
            for conflict in chapter_conflicts:
                if conflict.blocking:
                    assembly_conflicts.append(conflict)

        if target is not None and not related:
            empty_warning = cls._assembly_conflict(
                "NO_CHAPTERS",
                "目标 Book 当前没有合法的关联 Chapter；本次不会修改目标",
                blocking=False,
                chapter=target,
            )
            assembly_conflicts.append(empty_warning)

        if target is not None and not target_state_fingerprint:
            target_state_fingerprint = _digest(
                {"target_tree": _tree_digest(ProjectRepository.get_project_dir(target_name))}
            )
        target_tree_fingerprint = (
            _tree_digest(ProjectRepository.get_project_dir(target_name))
            if target is not None
            else ""
        )
        blocking = tuple(item for item in assembly_conflicts if item.blocking)
        warning_messages = tuple(
            item.message
            for item in assembly_conflicts
            if not item.blocking
        ) + tuple(
            warning
            for item in chapter_plans
            for warning in item.warnings
        )
        if blocking:
            aggregate_status = ASSEMBLY_BLOCKED
        elif warning_messages:
            aggregate_status = ASSEMBLY_READY_WITH_WARNINGS
        else:
            aggregate_status = ASSEMBLY_READY
        assembly_scope = {
            "schema_version": ASSEMBLY_SCHEMA_VERSION,
            "target_book_id": target_id,
            "target_book_name": target_name,
            "ordered_chapters": structure_scope,
            "source_state_fingerprints": {
                item.chapter_project_id: item.source_state_fingerprint
                for item in chapter_plans
                if item.chapter_project_id
            },
            "target_state_fingerprint": target_state_fingerprint,
            "target_tree_fingerprint": target_tree_fingerprint,
            "data_root": data_root,
            "resolution_fingerprint": resolution_fingerprint,
        }
        return BookAssemblyPlan(
            target_book_id=target_id,
            target_book_name=target_name,
            ordered_chapters=tuple(chapter_plans),
            aggregate_status=aggregate_status,
            blocking_conflicts=blocking,
            warnings=warning_messages,
            assembly_token=_digest(assembly_scope),
            structure_token=structure_token,
            target_state_fingerprint=target_state_fingerprint,
            target_tree_fingerprint=target_tree_fingerprint,
            data_root=data_root,
            total_segment_count=total_segments,
            total_audio_count=total_audio,
            assembly_conflicts=tuple(assembly_conflicts),
            resolution_fingerprint=resolution_fingerprint,
        )

    @classmethod
    def _current_structure_token(cls, plan: BookAssemblyPlan) -> str:
        hierarchy = ProjectCatalogService.scan_hierarchy()
        target = cls._resolve_target(hierarchy, plan.target_book_id) or cls._resolve_target(
            hierarchy, plan.target_book_name
        )
        if target is None:
            return ""
        related = cls._related_chapters(hierarchy, str(target.project_id or ""))
        scope = cls._structure_scope(str(target.project_id or ""), related)
        return _digest(
            {
                "schema_version": ASSEMBLY_SCHEMA_VERSION,
                "target_id": str(target.project_id or ""),
                "related": scope,
            }
        )

    @classmethod
    def is_plan_current(cls, plan: BookAssemblyPlan) -> bool:
        """Check the initial plan before the first assembly mutation."""
        if not isinstance(plan, BookAssemblyPlan) or plan.data_root != _data_root():
            return False
        if cls._current_structure_token(plan) != plan.structure_token:
            return False
        if not plan.target_book_name:
            return False
        try:
            current_tree = _tree_digest(
                ProjectRepository.get_project_dir(plan.target_book_name)
            )
        except (OSError, ValueError):
            return False
        if current_tree != plan.target_tree_fingerprint:
            return False
        for chapter in plan.ordered_chapters:
            if chapter.merge_plan is None:
                continue
            if not ChapterMergePlanner.is_plan_current(chapter.merge_plan):
                return False
        return True

    @classmethod
    def _confirmation_scope(
        cls,
        plan: BookAssemblyPlan,
        resolutions: Any,
        session: Any,
    ) -> dict[str, Any]:
        selected, opened, revision = _session_values(session)
        document = _normalise_resolution_document(resolutions)
        return {
            "schema_version": ASSEMBLY_SCHEMA_VERSION,
            "assembly_token": plan.assembly_token,
            "target_book_id": plan.target_book_id,
            "target_book_name": plan.target_book_name,
            "ordered_chapter_ids": list(plan.ordered_chapter_ids),
            "resolution_fingerprint": _digest(document),
            "structure_token": plan.structure_token,
            "selection_revision": revision,
            "selected_project": selected,
            "opened_project": opened,
            "data_root": _data_root(),
        }

    @classmethod
    def prepare_confirmation(
        cls,
        plan: BookAssemblyPlan,
        resolutions: Any = None,
        *,
        session: Any = None,
    ) -> BookAssemblyConfirmation:
        if not isinstance(plan, BookAssemblyPlan):
            raise WholeBookAssemblyError("PLAN_REQUIRED", "必须先生成 BookAssemblyPlan")
        document = _normalise_resolution_document(resolutions)
        if not cls.is_plan_current(plan):
            raise WholeBookAssemblyError(
                "STALE_ASSEMBLY_PLAN",
                "整书装配计划已过期，请重新分析",
            )
        for chapter in plan.ordered_chapters:
            if chapter.merge_plan is None:
                continue
            resolution = _resolution_for(
                document, chapter, default=len(plan.ordered_chapters) == 1
            )
            status, blockers, _warnings, _already = cls._status_for_plan(
                chapter.merge_plan, resolution
            )
            if status == MERGE_BLOCKED or blockers:
                raise WholeBookAssemblyError(
                    "ASSEMBLY_BLOCKED",
                    "仍存在未解决的 Chapter blocking conflict",
                    details={
                        "chapter": chapter.chapter_project_name,
                        "conflicts": [item.code for item in blockers],
                    },
                )
        effective_assembly_blockers = cls._effective_assembly_blockers(
            plan, document
        )
        if effective_assembly_blockers:
            raise WholeBookAssemblyError(
                "ASSEMBLY_BLOCKED",
                "整书装配存在 hierarchy / runtime blocking conflict",
                details={
                    "conflicts": [item.code for item in effective_assembly_blockers]
                },
            )
        selected, _opened, _revision = _session_values(session)
        if session is not None and selected != plan.target_book_name:
            raise WholeBookAssemblyError(
                "SELECTED_TARGET_CHANGED",
                "必须保持目标 Book 为当前 bookshelf selection",
            )
        scope = cls._confirmation_scope(plan, document, session)
        return BookAssemblyConfirmation(
            schema_version=ASSEMBLY_SCHEMA_VERSION,
            confirmation_token=_digest(scope),
            assembly_token=plan.assembly_token,
            target_book_id=plan.target_book_id,
            target_book_name=plan.target_book_name,
            ordered_chapter_ids=plan.ordered_chapter_ids,
            resolution_fingerprint=str(scope["resolution_fingerprint"]),
            structure_token=plan.structure_token,
            selection_revision=int(scope["selection_revision"]),
            selected_project=str(scope["selected_project"]),
            opened_project=str(scope["opened_project"]),
            data_root=str(scope["data_root"]),
        )

    @classmethod
    def _confirmation_is_current(
        cls,
        plan: BookAssemblyPlan,
        resolutions: Any,
        confirmation: Any,
        session: Any,
    ) -> tuple[bool, str, str]:
        if isinstance(confirmation, BookAssemblyConfirmation):
            current = confirmation
        elif isinstance(confirmation, Mapping):
            try:
                current = BookAssemblyConfirmation(
                    schema_version=str(confirmation.get("schema_version") or ""),
                    confirmation_token=str(confirmation.get("confirmation_token") or ""),
                    assembly_token=str(confirmation.get("assembly_token") or ""),
                    target_book_id=str(confirmation.get("target_book_id") or ""),
                    target_book_name=str(confirmation.get("target_book_name") or ""),
                    ordered_chapter_ids=tuple(
                        str(item) for item in confirmation.get("ordered_chapter_ids") or ()
                    ),
                    resolution_fingerprint=str(
                        confirmation.get("resolution_fingerprint") or ""
                    ),
                    structure_token=str(confirmation.get("structure_token") or ""),
                    selection_revision=int(confirmation.get("selection_revision") or 0),
                    selected_project=str(confirmation.get("selected_project") or ""),
                    opened_project=str(confirmation.get("opened_project") or ""),
                    data_root=str(confirmation.get("data_root") or ""),
                )
            except (TypeError, ValueError):
                return False, "CONFIRMATION_REQUIRED", "必须先生成新的 assembly confirmation"
        else:
            return False, "CONFIRMATION_REQUIRED", "必须先生成新的 assembly confirmation"
        try:
            expected = cls.prepare_confirmation(plan, resolutions, session=session)
        except WholeBookAssemblyError as exc:
            # After an earlier Chapter succeeds, the target tree is expected
            # to change.  Confirmation identity still remains valid if runtime
            # selection/data-root state is unchanged; child plans are freshly
            # replanned by execute_assembly below.
            if exc.code not in {"STALE_ASSEMBLY_PLAN", "ASSEMBLY_BLOCKED"}:
                return False, exc.code, str(exc)
            document = _normalise_resolution_document(resolutions)
            scope = cls._confirmation_scope(plan, document, session)
            expected = BookAssemblyConfirmation(
                schema_version=ASSEMBLY_SCHEMA_VERSION,
                confirmation_token=_digest(scope),
                assembly_token=plan.assembly_token,
                target_book_id=plan.target_book_id,
                target_book_name=plan.target_book_name,
                ordered_chapter_ids=plan.ordered_chapter_ids,
                resolution_fingerprint=str(scope["resolution_fingerprint"]),
                structure_token=plan.structure_token,
                selection_revision=int(scope["selection_revision"]),
                selected_project=str(scope["selected_project"]),
                opened_project=str(scope["opened_project"]),
                data_root=str(scope["data_root"]),
            )
        if current != expected:
            return False, "STALE_ASSEMBLY_CONFIRMATION", "assembly confirmation 已过期"
        if cls._current_structure_token(plan) != plan.structure_token:
            return False, "ASSEMBLY_STRUCTURE_CHANGED", "Book→Chapter membership/order 已变化"
        if plan.data_root != _data_root():
            return False, "DATA_ROOT_CHANGED", "data-dir 已变化"
        return True, "", ""

    @staticmethod
    def _result_from_executor(
        chapter: BookAssemblyChapterPlan,
        fresh_plan: MergePlan,
        result: MergeExecutionResult,
        warnings: Iterable[str],
    ) -> BookAssemblyChapterResult:
        if result.success:
            status = CHAPTER_MERGED
        elif result.status == MERGE_FAILED_ROLLED_BACK:
            status = CHAPTER_FAILED_ROLLED_BACK
        elif result.status == MERGE_FAILED_ROLLBACK_FAILED:
            status = CHAPTER_FAILED_ROLLBACK_FAILED
        else:
            status = CHAPTER_FAILED
        blocking = str(result.error_code or "")
        return BookAssemblyChapterResult(
            order=chapter.order,
            chapter_project_id=chapter.chapter_project_id,
            chapter_project_name=chapter.chapter_project_name,
            initial_plan_status=chapter.initial_plan_status,
            final_plan_status=fresh_plan.execution_eligibility,
            execution_result=status,
            imported_segment_count=result.imported_segment_count,
            imported_audio_count=result.imported_audio_count,
            warnings=tuple(warnings) + tuple(result.warnings),
            blocking_conflict=blocking,
            backup_reference=result.backup_path,
            transaction_id=result.transaction_id,
            journal_reference=result.journal_path,
            integrity=result.integrity,
            error_code=result.error_code,
            error=result.error,
        )

    @staticmethod
    def _fault_for_chapter(
        fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None,
        chapter: BookAssemblyChapterPlan,
    ) -> Mapping[str, Any] | Callable[[str, str], None] | None:
        if not isinstance(fault_injection, Mapping):
            return fault_injection
        by_chapter = fault_injection.get("by_chapter")
        if isinstance(by_chapter, Mapping):
            value = by_chapter.get(chapter.chapter_project_name)
            if value is None:
                value = by_chapter.get(chapter.chapter_project_id)
            if value is not None:
                return value
        return fault_injection

    @classmethod
    def execute_assembly(
        cls,
        plan: BookAssemblyPlan,
        resolutions: Any = None,
        confirmation: Any = None,
        *,
        session: Any = None,
        fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None = None,
    ) -> BookAssemblyExecutionResult:
        assembly_id = f"assembly-{uuid.uuid4().hex}"
        started_at = _utc_now()
        resolution_document = _normalise_resolution_document(resolutions)
        chapter_results: list[BookAssemblyChapterResult] = []
        warnings = list(plan.warnings) if isinstance(plan, BookAssemblyPlan) else []

        def finish(
            status: str,
            *,
            error_code: str = "",
            error: str = "",
            final_integrity: Mapping[str, Any] | None = None,
        ) -> BookAssemblyExecutionResult:
            merged = sum(item.execution_result == CHAPTER_MERGED for item in chapter_results)
            already = sum(item.execution_result == CHAPTER_ALREADY_MERGED for item in chapter_results)
            blocked = sum(item.execution_result == CHAPTER_BLOCKED for item in chapter_results)
            failed = sum(
                item.execution_result in {
                    CHAPTER_FAILED,
                    CHAPTER_FAILED_ROLLED_BACK,
                    CHAPTER_FAILED_ROLLBACK_FAILED,
                }
                for item in chapter_results
            )
            not_attempted = sum(
                item.execution_result == CHAPTER_NOT_ATTEMPTED
                for item in chapter_results
            )
            return BookAssemblyExecutionResult(
                success=status == ASSEMBLY_SUCCEEDED,
                status=status,
                assembly_id=assembly_id,
                target_book_id=getattr(plan, "target_book_id", ""),
                target_book_name=getattr(plan, "target_book_name", ""),
                assembly_token=getattr(plan, "assembly_token", ""),
                started_at=started_at,
                finished_at=_utc_now(),
                chapter_results=tuple(chapter_results),
                final_integrity=dict(final_integrity or {}),
                warnings=tuple(warnings),
                error_code=error_code,
                error=error,
                merged_this_run=merged,
                already_merged=already,
                blocked=blocked,
                failed=failed,
                not_attempted=not_attempted,
                total_segments_added=sum(item.imported_segment_count for item in chapter_results),
                total_audio_copied=sum(item.imported_audio_count for item in chapter_results),
            )

        if not isinstance(plan, BookAssemblyPlan):
            return finish(
                ASSEMBLY_VALIDATION_FAILED,
                error_code="PLAN_REQUIRED",
                error="必须接收 BookAssemblyPlan",
            )

        valid_confirmation, code, message = cls._confirmation_is_current(
            plan, resolutions, confirmation, session
        )
        if not valid_confirmation:
            return finish(ASSEMBLY_VALIDATION_FAILED, error_code=code, error=message)

        # Validate the initial aggregate using current resolutions.  Once a
        # Chapter commits, its target tree changes and the initial child plans
        # are intentionally no longer treated as current.
        aggregate_blockers: list[BookAssemblyConflict] = cls._effective_assembly_blockers(
            plan, resolution_document
        )
        for chapter in plan.ordered_chapters:
            if chapter.merge_plan is None:
                continue
            resolution = _resolution_for(
                resolution_document, chapter, default=len(plan.ordered_chapters) == 1
            )
            status, blockers, chapter_warnings, _already = cls._status_for_plan(
                chapter.merge_plan, resolution
            )
            warnings.extend(chapter_warnings)
            if status == MERGE_BLOCKED:
                aggregate_blockers.extend(
                    _conflict_from_merge(item, chapter) for item in blockers
                )
        if aggregate_blockers:
            for chapter in plan.ordered_chapters:
                if chapter.relation_status != RELATION_VALID:
                    result_status = CHAPTER_BLOCKED
                    conflict = chapter.conflicts[0].code if chapter.conflicts else "ASSEMBLY_BLOCKED"
                    chapter_results.append(
                        BookAssemblyChapterResult(
                            order=chapter.order,
                            chapter_project_id=chapter.chapter_project_id,
                            chapter_project_name=chapter.chapter_project_name,
                            initial_plan_status=chapter.initial_plan_status,
                            final_plan_status=ASSEMBLY_BLOCKED,
                            execution_result=result_status,
                            blocking_conflict=conflict,
                        )
                    )
                else:
                    chapter_results.append(
                        BookAssemblyChapterResult(
                            order=chapter.order,
                            chapter_project_id=chapter.chapter_project_id,
                            chapter_project_name=chapter.chapter_project_name,
                            initial_plan_status=chapter.initial_plan_status,
                            final_plan_status=chapter.initial_plan_status,
                            execution_result=CHAPTER_NOT_ATTEMPTED,
                        )
                    )
            return finish(
                ASSEMBLY_BLOCKED,
                error_code=aggregate_blockers[0].code,
                error=aggregate_blockers[0].message,
            )

        successful_mutations = 0
        for index, chapter in enumerate(plan.ordered_chapters):
            if chapter.relation_status != RELATION_VALID or not chapter.merge_plan:
                chapter_results.append(
                    BookAssemblyChapterResult(
                        order=chapter.order,
                        chapter_project_id=chapter.chapter_project_id,
                        chapter_project_name=chapter.chapter_project_name,
                        initial_plan_status=chapter.initial_plan_status,
                        final_plan_status=ASSEMBLY_BLOCKED,
                        execution_result=CHAPTER_BLOCKED,
                        blocking_conflict=chapter.conflicts[0].code if chapter.conflicts else "ASSEMBLY_BLOCKED",
                    )
                )
                for remaining in plan.ordered_chapters[index + 1 :]:
                    chapter_results.append(
                        BookAssemblyChapterResult(
                            order=remaining.order,
                            chapter_project_id=remaining.chapter_project_id,
                            chapter_project_name=remaining.chapter_project_name,
                            initial_plan_status=remaining.initial_plan_status,
                            final_plan_status=remaining.initial_plan_status,
                            execution_result=CHAPTER_NOT_ATTEMPTED,
                        )
                    )
                return finish(
                    ASSEMBLY_PARTIAL_SUCCESS_STOPPED
                    if successful_mutations
                    else ASSEMBLY_BLOCKED,
                    error_code="ASSEMBLY_STRUCTURE_CHANGED",
                    error="关联 Chapter 结构在装配期间不可执行",
                )

            current_structure = cls._current_structure_token(plan)
            if current_structure != plan.structure_token:
                chapter_results.append(
                    BookAssemblyChapterResult(
                        order=chapter.order,
                        chapter_project_id=chapter.chapter_project_id,
                        chapter_project_name=chapter.chapter_project_name,
                        initial_plan_status=chapter.initial_plan_status,
                        final_plan_status=ASSEMBLY_BLOCKED,
                        execution_result=CHAPTER_BLOCKED,
                        blocking_conflict="ASSEMBLY_STRUCTURE_CHANGED",
                    )
                )
                for remaining in plan.ordered_chapters[index + 1 :]:
                    chapter_results.append(
                        BookAssemblyChapterResult(
                            order=remaining.order,
                            chapter_project_id=remaining.chapter_project_id,
                            chapter_project_name=remaining.chapter_project_name,
                            initial_plan_status=remaining.initial_plan_status,
                            final_plan_status=remaining.initial_plan_status,
                            execution_result=CHAPTER_NOT_ATTEMPTED,
                        )
                    )
                return finish(
                    ASSEMBLY_PARTIAL_SUCCESS_STOPPED
                    if successful_mutations
                    else ASSEMBLY_BLOCKED,
                    error_code="ASSEMBLY_STRUCTURE_CHANGED",
                    error="Book→Chapter membership/order 已变化，已停止后续 Chapter",
                )

            valid_confirmation, code, message = cls._confirmation_is_current(
                plan, resolutions, confirmation, session
            )
            if not valid_confirmation:
                chapter_results.append(
                    BookAssemblyChapterResult(
                        order=chapter.order,
                        chapter_project_id=chapter.chapter_project_id,
                        chapter_project_name=chapter.chapter_project_name,
                        initial_plan_status=chapter.initial_plan_status,
                        final_plan_status=ASSEMBLY_BLOCKED,
                        execution_result=CHAPTER_BLOCKED,
                        blocking_conflict=code,
                        error_code=code,
                        error=message,
                    )
                )
                for remaining in plan.ordered_chapters[index + 1 :]:
                    chapter_results.append(
                        BookAssemblyChapterResult(
                            order=remaining.order,
                            chapter_project_id=remaining.chapter_project_id,
                            chapter_project_name=remaining.chapter_project_name,
                            initial_plan_status=remaining.initial_plan_status,
                            final_plan_status=remaining.initial_plan_status,
                            execution_result=CHAPTER_NOT_ATTEMPTED,
                        )
                    )
                return finish(
                    ASSEMBLY_PARTIAL_SUCCESS_STOPPED
                    if successful_mutations
                    else ASSEMBLY_VALIDATION_FAILED,
                    error_code=code,
                    error=message,
                )

            fresh_plan = ChapterMergePlanner.plan_merge(
                chapter.chapter_project_name,
                plan.target_book_name,
                session=session,
            )
            resolution = _resolution_for(
                resolution_document, chapter, default=len(plan.ordered_chapters) == 1
            )
            fresh_status, fresh_blockers, fresh_warnings, already = cls._status_for_plan(
                fresh_plan, resolution
            )
            warnings.extend(fresh_warnings)
            if already:
                chapter_results.append(
                    BookAssemblyChapterResult(
                        order=chapter.order,
                        chapter_project_id=chapter.chapter_project_id,
                        chapter_project_name=chapter.chapter_project_name,
                        initial_plan_status=chapter.initial_plan_status,
                        final_plan_status=CHAPTER_ALREADY_MERGED,
                        execution_result=CHAPTER_ALREADY_MERGED,
                        warnings=tuple(fresh_warnings),
                    )
                )
                continue
            if fresh_status == MERGE_BLOCKED or fresh_blockers:
                code = fresh_blockers[0].code if fresh_blockers else "CHAPTER_BLOCKED_AFTER_REPLAN"
                message = fresh_blockers[0].message if fresh_blockers else "fresh Chapter plan 已阻塞"
                chapter_results.append(
                    BookAssemblyChapterResult(
                        order=chapter.order,
                        chapter_project_id=chapter.chapter_project_id,
                        chapter_project_name=chapter.chapter_project_name,
                        initial_plan_status=chapter.initial_plan_status,
                        final_plan_status=MERGE_BLOCKED,
                        execution_result=CHAPTER_BLOCKED,
                        warnings=tuple(fresh_warnings),
                        blocking_conflict=code,
                        error_code=code,
                        error=message,
                    )
                )
                for remaining in plan.ordered_chapters[index + 1 :]:
                    chapter_results.append(
                        BookAssemblyChapterResult(
                            order=remaining.order,
                            chapter_project_id=remaining.chapter_project_id,
                            chapter_project_name=remaining.chapter_project_name,
                            initial_plan_status=remaining.initial_plan_status,
                            final_plan_status=remaining.initial_plan_status,
                            execution_result=CHAPTER_NOT_ATTEMPTED,
                        )
                    )
                return finish(
                    ASSEMBLY_PARTIAL_SUCCESS_STOPPED
                    if successful_mutations
                    else ASSEMBLY_BLOCKED,
                    error_code=code,
                    error=message,
                )

            try:
                child_confirmation = ChapterMergeExecutor.prepare_confirmation(
                    fresh_plan,
                    resolution,
                    session=session,
                    selection_policy=WHOLE_BOOK_SELECTION_POLICY,
                    assembly_token=plan.assembly_token,
                )
                executor_result = ChapterMergeExecutor.execute(
                    fresh_plan,
                    resolution,
                    child_confirmation,
                    session=session,
                    fault_injection=cls._fault_for_chapter(fault_injection, chapter),
                    selection_policy=WHOLE_BOOK_SELECTION_POLICY,
                    assembly_token=plan.assembly_token,
                )
            except MergeExecutionError as exc:
                chapter_results.append(
                    BookAssemblyChapterResult(
                        order=chapter.order,
                        chapter_project_id=chapter.chapter_project_id,
                        chapter_project_name=chapter.chapter_project_name,
                        initial_plan_status=chapter.initial_plan_status,
                        final_plan_status=MERGE_BLOCKED,
                        execution_result=CHAPTER_BLOCKED,
                        blocking_conflict=exc.code,
                        error_code=exc.code,
                        error=str(exc),
                    )
                )
                for remaining in plan.ordered_chapters[index + 1 :]:
                    chapter_results.append(
                        BookAssemblyChapterResult(
                            order=remaining.order,
                            chapter_project_id=remaining.chapter_project_id,
                            chapter_project_name=remaining.chapter_project_name,
                            initial_plan_status=remaining.initial_plan_status,
                            final_plan_status=remaining.initial_plan_status,
                            execution_result=CHAPTER_NOT_ATTEMPTED,
                        )
                    )
                return finish(
                    ASSEMBLY_PARTIAL_SUCCESS_STOPPED
                    if successful_mutations
                    else ASSEMBLY_VALIDATION_FAILED,
                    error_code=exc.code,
                    error=str(exc),
                )
            chapter_result = cls._result_from_executor(
                chapter, fresh_plan, executor_result, fresh_warnings
            )
            chapter_results.append(chapter_result)
            if executor_result.success:
                successful_mutations += 1
                continue
            for remaining in plan.ordered_chapters[index + 1 :]:
                chapter_results.append(
                    BookAssemblyChapterResult(
                        order=remaining.order,
                        chapter_project_id=remaining.chapter_project_id,
                        chapter_project_name=remaining.chapter_project_name,
                        initial_plan_status=remaining.initial_plan_status,
                        final_plan_status=remaining.initial_plan_status,
                        execution_result=CHAPTER_NOT_ATTEMPTED,
                    )
                )
            return finish(
                ASSEMBLY_CRITICAL_FAILURE
                if executor_result.status == MERGE_FAILED_ROLLBACK_FAILED
                else ASSEMBLY_PARTIAL_SUCCESS_STOPPED
                if successful_mutations
                else ASSEMBLY_VALIDATION_FAILED,
                error_code=executor_result.error_code,
                error=executor_result.error,
            )

        final_integrity = ProjectStorageService.check_integrity(plan.target_book_name)
        if not bool(final_integrity.get("ok")):
            return finish(
                ASSEMBLY_CRITICAL_FAILURE,
                error_code="FINAL_TARGET_INTEGRITY_FAILED",
                error="整书装配完成后目标 Book 完整性校验失败",
                final_integrity=final_integrity,
            )
        return finish(ASSEMBLY_SUCCEEDED, final_integrity=final_integrity)


def plan_assembly(target_book: Any, **kwargs: Any) -> BookAssemblyPlan:
    """Functional convenience wrapper."""
    return WholeBookAssemblyService.plan_assembly(target_book, **kwargs)


def execute_assembly(
    plan: BookAssemblyPlan,
    resolutions: Any = None,
    confirmation: Any = None,
    **kwargs: Any,
) -> BookAssemblyExecutionResult:
    """Functional convenience wrapper."""
    return WholeBookAssemblyService.execute_assembly(
        plan, resolutions, confirmation, **kwargs
    )


__all__ = [
    "ASSEMBLY_BLOCKED",
    "ASSEMBLY_CANCELLED",
    "ASSEMBLY_CRITICAL_FAILURE",
    "ASSEMBLY_PARTIAL_SUCCESS_STOPPED",
    "ASSEMBLY_READY",
    "ASSEMBLY_READY_WITH_WARNINGS",
    "ASSEMBLY_SCHEMA_VERSION",
    "ASSEMBLY_SUCCEEDED",
    "ASSEMBLY_VALIDATION_FAILED",
    "CHAPTER_ALREADY_MERGED",
    "CHAPTER_BLOCKED",
    "CHAPTER_FAILED",
    "CHAPTER_FAILED_ROLLBACK_FAILED",
    "CHAPTER_FAILED_ROLLED_BACK",
    "CHAPTER_MERGED",
    "CHAPTER_NOT_ATTEMPTED",
    "CHAPTER_SKIPPED",
    "WHOLE_BOOK_SELECTION_POLICY",
    "BookAssemblyChapterPlan",
    "BookAssemblyChapterResult",
    "BookAssemblyConfirmation",
    "BookAssemblyConflict",
    "BookAssemblyExecutionResult",
    "BookAssemblyPlan",
    "BookAssemblyResult",
    "WholeBookAssemblyError",
    "WholeBookAssemblyService",
    "execute_assembly",
    "plan_assembly",
]
