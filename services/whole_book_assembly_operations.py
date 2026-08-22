"""Restart-safe operational state for Whole-book Assembly.

This module is deliberately an observation and recovery boundary around the
existing :class:`WholeBookAssemblyService`.  It derives current state from
Catalog hierarchy, ChapterMergePlanner, target merge history, executor
transaction journals, and read-only integrity checks.  The small persisted
run records contain audit/progress metadata only; they never become the
authority for whether Chapter content is already merged.
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from lib import config
from repositories._atomic import atomic_write
from repositories.project_repo import ProjectRepository
from services.chapter_merge_executor import (
    MERGE_FAILED_ROLLBACK_FAILED,
    MERGE_FAILED_ROLLED_BACK,
    TRANSACTION_JOURNAL_ACTIVE_STAGES,
    read_transaction_journals,
)
from services.chapter_merge_planner import read_merge_history
from services.project_catalog import (
    RELATION_INVALID,
    RELATION_ORPHAN,
    ProjectCatalogService,
)
from services.project_storage import ProjectStorageService
from services.whole_book_assembly import (
    ASSEMBLY_READY,
    ASSEMBLY_READY_WITH_WARNINGS,
    CHAPTER_ALREADY_MERGED,
    BookAssemblyChapterPlan,
    BookAssemblyExecutionResult,
    BookAssemblyPlan,
    BookAssemblyResult,
    WholeBookAssemblyError,
    WholeBookAssemblyService,
)

OPERATIONS_SCHEMA_VERSION = "whole-book-assembly-operations-v1"
RUN_HISTORY_SCHEMA_VERSION = "whole-book-assembly-run-v1"

OPS_NOT_STARTED = "NOT_STARTED"
OPS_READY = "READY"
OPS_READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
OPS_IN_PROGRESS = "IN_PROGRESS"
OPS_PARTIAL = "PARTIAL"
OPS_BLOCKED = "BLOCKED"
OPS_FAILED = "FAILED"
OPS_DEGRADED = "DEGRADED"
OPS_COMPLETE = "COMPLETE"

CHAPTER_PENDING = "PENDING"
CHAPTER_READY = "READY"
CHAPTER_READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
CHAPTER_OPERATIONAL_ALREADY_MERGED = "ALREADY_MERGED"
CHAPTER_SOURCE_CHANGED = "SOURCE_CHANGED"
CHAPTER_FAILED_ROLLED_BACK_STATE = "FAILED_ROLLED_BACK"
CHAPTER_CRITICAL_FAILURE = "CRITICAL_FAILURE"
CHAPTER_INVALID_RELATIONSHIP = "INVALID_RELATIONSHIP"
CHAPTER_OPERATIONAL_BLOCKED = "BLOCKED"

INTEGRITY_PASS = "PASS"
INTEGRITY_WARN = "WARN"
INTEGRITY_FAIL = "FAIL"
INTEGRITY_UNKNOWN = "UNKNOWN"

_CURRENT_READY_STATES = frozenset(
    {CHAPTER_PENDING, CHAPTER_READY, CHAPTER_READY_WITH_WARNINGS}
)
_SOURCE_CHANGED_CODES = frozenset(
    {"SOURCE_CHANGED_AFTER_PREVIOUS_MERGE", "SOURCE_CHANGED"}
)
_STRUCTURAL_BLOCKER_CODES = frozenset(
    {
        "TARGET_NOT_FOUND",
        "TARGET_NOT_BOOK",
        "TARGET_ID_MISSING",
        "DUPLICATE_PROJECT_ID",
        "TARGET_RELATION_UNSAFE",
        "SELECTED_TARGET_CHANGED",
        "TARGET_OPENED",
        "INVALID_RELATED_CHAPTER",
        "ORPHAN_RELATED_CHAPTER",
        "ASSEMBLY_STRUCTURE_CHANGED",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _timestamp_key(value: Any) -> tuple[int, str]:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(parsed.timestamp()), text
    except (TypeError, ValueError, OverflowError):
        return 0, text


def _json_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _data_root_path() -> str:
    """Return the configured data root without creating it."""
    return os.path.realpath(os.path.dirname(config.get_projects_root()))


def _runtime_runs_root() -> str:
    return os.path.join(_data_root_path(), "runtime", "assembly_runs")


def _run_path(assembly_id: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in str(assembly_id or "")
    ).strip("._")
    return os.path.join(_runtime_runs_root(), f"{safe or 'assembly'}.json")


def _write_run(path: str, record: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, dict(record))


def _read_run_records() -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    root = _runtime_runs_root()
    if not os.path.isdir(root):
        return (), ()
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for filename in sorted(os.listdir(root)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(root, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as file:
                value = json.load(file)
            if not isinstance(value, dict):
                raise TypeError("run history top-level must be an object")
            record = dict(value)
            record.setdefault("assembly_run_id", filename[:-5])
            record["history_path"] = path
            records.append(record)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
    records.sort(
        key=lambda item: (
            _timestamp_key(item.get("updated_at") or item.get("started_at")),
            str(item.get("assembly_run_id") or ""),
        ),
        reverse=True,
    )
    return tuple(records), tuple(errors)


def _minimal_chapter_result(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        get_value = item.get
    else:
        get_value = lambda key, default=None: getattr(item, key, default)
    return {
        "order": int(get_value("order", 0) or 0),
        "chapter_project_id": str(get_value("chapter_project_id", "") or ""),
        "chapter_project_name": str(get_value("chapter_project_name", "") or ""),
        "execution_result": str(get_value("execution_result", "") or ""),
        "transaction_id": str(get_value("transaction_id", "") or ""),
        "backup_reference": str(get_value("backup_reference", "") or ""),
        "journal_reference": str(get_value("journal_reference", "") or ""),
        "blocking_conflict": str(get_value("blocking_conflict", "") or ""),
        "error_code": str(get_value("error_code", "") or ""),
        "error": str(get_value("error", "") or ""),
        "integrity": dict(get_value("integrity", {}) or {}),
    }


def _minimal_execution_result(result: BookAssemblyExecutionResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "success": bool(result.success),
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "chapter_results": [
            _minimal_chapter_result(item) for item in result.chapter_results
        ],
        "merged_this_run": result.merged_this_run,
        "already_merged": result.already_merged,
        "blocked": result.blocked,
        "failed": result.failed,
        "not_attempted": result.not_attempted,
        "total_segments_added": result.total_segments_added,
        "total_audio_copied": result.total_audio_copied,
        "final_integrity": dict(result.final_integrity),
        "error_code": result.error_code,
        "error": result.error,
    }


class _AssemblyRunHistory:
    """Minimal durable audit record; content idempotency remains merge history."""

    def __init__(
        self,
        assembly_id: str,
        target_book_id: str,
        target_book_name: str,
        ordered_chapter_ids: tuple[str, ...],
    ) -> None:
        self.assembly_id = assembly_id
        self.path = _run_path(assembly_id)
        now = _utc_now()
        self.record: dict[str, Any] = {
            "schema_version": RUN_HISTORY_SCHEMA_VERSION,
            "assembly_run_id": assembly_id,
            "target_project_id": target_book_id,
            "target_project_name": target_book_name,
            "ordered_chapter_ids": list(ordered_chapter_ids),
            "started_at": now,
            "updated_at": now,
            "finished_at": "",
            "status": OPS_IN_PROGRESS,
            "current_chapter": {},
            "chapter_results": [],
            "transaction_ids": [],
            "backup_references": [],
            "error_code": "",
            "error": "",
        }
        _write_run(self.path, self.record)

    def update(self, payload: Mapping[str, Any]) -> None:
        event = str(payload.get("event") or "")
        if event == "CHAPTER_STARTED":
            self.record["current_chapter"] = {
                "order": payload.get("chapter_order"),
                "project_id": payload.get("chapter_project_id"),
                "project_name": payload.get("chapter_project_name"),
                "completed_chapters": payload.get("completed_chapters", 0),
                "total_chapters": payload.get("total_chapters", 0),
            }
        elif event == "ASSEMBLY_FINISHED":
            result = payload.get("result")
            if isinstance(result, Mapping):
                self.record.update(
                    {
                        "status": str(result.get("status") or OPS_FAILED),
                        "finished_at": str(
                            result.get("finished_at") or _utc_now()
                        ),
                        "chapter_results": [
                            _minimal_chapter_result(item)
                            for item in _json_list(result.get("chapter_results"))
                        ],
                        "transaction_ids": [
                            str(item.get("transaction_id") or "")
                            for item in _json_list(result.get("chapter_results"))
                            if isinstance(item, Mapping)
                            and str(item.get("transaction_id") or "")
                        ],
                        "backup_references": [
                            str(item.get("backup_reference") or "")
                            for item in _json_list(result.get("chapter_results"))
                            if isinstance(item, Mapping)
                            and str(item.get("backup_reference") or "")
                        ],
                        "result_summary": {
                            key: result.get(key)
                            for key in (
                                "merged_this_run",
                                "already_merged",
                                "blocked",
                                "failed",
                                "not_attempted",
                                "total_segments_added",
                                "total_audio_copied",
                            )
                        },
                        "final_integrity": _json_dict(
                            result.get("final_integrity")
                        ),
                        "error_code": str(result.get("error_code") or ""),
                        "error": str(result.get("error") or ""),
                    }
                )
        self.record["updated_at"] = _utc_now()
        _write_run(self.path, self.record)

    def finish(self, result: BookAssemblyExecutionResult) -> None:
        self.update(
            {
                "event": "ASSEMBLY_FINISHED",
                "result": {
                    **_minimal_execution_result(result),
                    "chapter_results": [
                        _minimal_chapter_result(item)
                        for item in result.chapter_results
                    ],
                },
            }
        )

    def fail(self, code: str, message: str) -> None:
        self.record.update(
            {
                "status": OPS_FAILED,
                "finished_at": _utc_now(),
                "updated_at": _utc_now(),
                "error_code": str(code),
                "error": str(message),
            }
        )
        _write_run(self.path, self.record)


@dataclass(frozen=True)
class AssemblyTransactionDiagnostic:
    transaction_id: str
    source_project_id: str = ""
    source_project_name: str = ""
    target_project_id: str = ""
    target_project_name: str = ""
    stage: str = ""
    final_status: str = ""
    journal_reference: str = ""
    backup_reference: str = ""
    rollback_status: str = ""
    failure_code: str = ""
    failure_summary: str = ""
    integrity: Mapping[str, Any] = field(default_factory=dict)
    started_at: str = ""
    updated_at: str = ""
    journal_mtime_ns: int = 0
    active: bool = False
    unreadable: bool = False

    @property
    def degraded(self) -> bool:
        return self.unreadable or self.active or self.rollback_status == "ROLLBACK_FAILED"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["integrity"] = dict(self.integrity)
        payload["degraded"] = self.degraded
        return payload


@dataclass(frozen=True)
class AssemblyChapterOperationalState:
    order: int
    chapter_project_id: str
    chapter_project_name: str
    chapter_title: str
    relation_status: str
    status: str
    planner_status: str = ""
    merge_status: str = ""
    last_transaction_id: str = ""
    latest_backup_reference: str = ""
    latest_journal_reference: str = ""
    last_transaction_stage: str = ""
    last_transaction_status: str = ""
    rollback_status: str = ""
    blocking_reason: str = ""
    failure_summary: str = ""
    warning_summary: tuple[str, ...] = ()
    source_changed: bool = False
    current_source_fingerprint: str = ""
    historical_merge: bool = False
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warning_summary"] = list(self.warning_summary)
        return payload


@dataclass(frozen=True)
class AssemblyOperationsSnapshot:
    target_book_id: str
    target_book_name: str
    overall_status: str
    total_chapters: int
    merged_count: int
    pending_count: int
    blocked_count: int
    failed_count: int
    integrity_status: str
    integrity_report: Mapping[str, Any] = field(default_factory=dict)
    chapter_states: tuple[AssemblyChapterOperationalState, ...] = ()
    transaction_diagnostics: tuple[AssemblyTransactionDiagnostic, ...] = ()
    active_transactions: tuple[AssemblyTransactionDiagnostic, ...] = ()
    historical_merges: tuple[Mapping[str, Any], ...] = ()
    recent_runs: tuple[Mapping[str, Any], ...] = ()
    latest_run: Mapping[str, Any] = field(default_factory=dict)
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    data_root: str = ""
    generated_at: str = ""
    plan_status: str = ""
    resume_allowed: bool = False
    reanalyze_allowed: bool = True

    @property
    def current_chapters(self) -> tuple[AssemblyChapterOperationalState, ...]:
        return self.chapter_states

    @property
    def degraded(self) -> bool:
        return self.overall_status == OPS_DEGRADED

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATIONS_SCHEMA_VERSION,
            "target_book_id": self.target_book_id,
            "target_book_name": self.target_book_name,
            "overall_status": self.overall_status,
            "total_chapters": self.total_chapters,
            "merged_count": self.merged_count,
            "pending_count": self.pending_count,
            "blocked_count": self.blocked_count,
            "failed_count": self.failed_count,
            "integrity_status": self.integrity_status,
            "integrity_report": dict(self.integrity_report),
            "chapter_states": [item.as_dict() for item in self.chapter_states],
            "transaction_diagnostics": [
                item.as_dict() for item in self.transaction_diagnostics
            ],
            "active_transactions": [
                item.as_dict() for item in self.active_transactions
            ],
            "historical_merges": [dict(item) for item in self.historical_merges],
            "recent_runs": [dict(item) for item in self.recent_runs],
            "latest_run": dict(self.latest_run),
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
            "data_root": self.data_root,
            "generated_at": self.generated_at,
            "plan_status": self.plan_status,
            "resume_allowed": self.resume_allowed,
            "reanalyze_allowed": self.reanalyze_allowed,
        }


@dataclass(frozen=True)
class AssemblyOperationsAnalysis:
    snapshot: AssemblyOperationsSnapshot
    plan: BookAssemblyPlan


@dataclass(frozen=True)
class AssemblyResumeResult:
    snapshot: AssemblyOperationsSnapshot
    execution_result: BookAssemblyResult


def _journal_final_status(record: Mapping[str, Any]) -> str:
    stage = str(record.get("stage") or "")
    rollback = str(record.get("rollback_status") or "")
    if stage == "SUCCEEDED":
        return "SUCCEEDED"
    if stage == "ROLLED_BACK" or rollback == "ROLLED_BACK":
        return MERGE_FAILED_ROLLED_BACK
    if stage == "ROLLBACK_FAILED" or rollback == "ROLLBACK_FAILED":
        return MERGE_FAILED_ROLLBACK_FAILED
    if stage in TRANSACTION_JOURNAL_ACTIVE_STAGES:
        return "RECOVERABLE_INTERRUPTED"
    if stage == "JOURNAL_UNREADABLE":
        return "JOURNAL_UNREADABLE"
    return stage or "UNKNOWN"


def _integrity_status(report: Mapping[str, Any]) -> str:
    if not isinstance(report, Mapping) or "ok" not in report:
        return INTEGRITY_UNKNOWN
    issues = _json_list(report.get("issues"))
    if not bool(report.get("ok")):
        return INTEGRITY_FAIL
    if any(
        isinstance(item, Mapping)
        and str(item.get("severity") or "").lower() == "warning"
        for item in issues
    ):
        return INTEGRITY_WARN
    return INTEGRITY_PASS


class WholeBookAssemblyOperationsService:
    """Derive current Assembly operations state and run safe fresh resumes."""

    @staticmethod
    def _target_map(hierarchy) -> tuple[dict[str, str], dict[str, str]]:
        by_id = {
            str(item.project_id): item.project_name
            for item in hierarchy.projects
            if str(item.project_id or "")
        }
        by_name = {item.project_name: str(item.project_id or "") for item in hierarchy.projects}
        return by_id, by_name

    @classmethod
    def _target_journals(
        cls,
        target_id: str,
        target_name: str,
        hierarchy,
    ) -> tuple[tuple[AssemblyTransactionDiagnostic, ...], tuple[str, ...]]:
        by_id, _by_name = cls._target_map(hierarchy)
        diagnostics: list[AssemblyTransactionDiagnostic] = []
        global_errors: list[str] = []
        for raw in read_transaction_journals():
            journal_error = str(raw.get("journal_error") or "")
            raw_target_id = str(raw.get("target_project_id") or "")
            raw_target_name = str(raw.get("target_project_name") or "")
            matches = raw_target_id == target_id or raw_target_name == target_name
            if journal_error and not raw_target_id and not raw_target_name:
                global_errors.append(
                    f"{raw.get('journal_path', '')}: {journal_error}"
                )
                continue
            if not matches:
                continue
            source_id = str(raw.get("source_project_id") or "")
            source_name = str(raw.get("source_project_name") or "") or by_id.get(
                source_id, ""
            )
            stage = str(raw.get("stage") or "")
            final_status = _journal_final_status(raw)
            diagnostics.append(
                AssemblyTransactionDiagnostic(
                    transaction_id=str(raw.get("transaction_id") or ""),
                    source_project_id=source_id,
                    source_project_name=source_name,
                    target_project_id=raw_target_id or target_id,
                    target_project_name=raw_target_name or target_name,
                    stage=stage,
                    final_status=final_status,
                    journal_reference=str(raw.get("journal_path") or ""),
                    backup_reference=str(raw.get("backup_path") or ""),
                    rollback_status=str(raw.get("rollback_status") or ""),
                    failure_code=str(
                        _json_dict(raw.get("failure")).get("code")
                        or _json_dict(raw.get("rollback_failure")).get("code")
                        or ""
                    ),
                    failure_summary=str(
                        _json_dict(raw.get("failure")).get("message")
                        or _json_dict(raw.get("rollback_failure")).get("message")
                        or raw.get("journal_error")
                        or ""
                    ),
                    integrity=_json_dict(raw.get("integrity")),
                    started_at=str(raw.get("started_at") or ""),
                    updated_at=str(raw.get("updated_at") or ""),
                    journal_mtime_ns=int(raw.get("_journal_mtime_ns") or 0),
                    active=stage in TRANSACTION_JOURNAL_ACTIVE_STAGES,
                    unreadable=bool(journal_error),
                )
            )
        diagnostics.sort(
            key=lambda item: (
                _timestamp_key(item.updated_at or item.started_at),
                item.transaction_id,
            ),
            reverse=True,
        )
        return tuple(diagnostics), tuple(global_errors)

    @staticmethod
    def _history_for_target(
        target_name: str,
    ) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
        if not target_name:
            return (), ()
        try:
            target_dir = ProjectRepository.get_project_dir(target_name)
            history, error = read_merge_history(target_dir)
        except (OSError, TypeError, ValueError) as exc:
            return (), (f"merge history unavailable: {type(exc).__name__}: {exc}",)
        return history, (str(error),) if error else ()

    @staticmethod
    def _run_matches(
        record: Mapping[str, Any], target_id: str, target_name: str
    ) -> bool:
        return (
            str(record.get("target_project_id") or "") == target_id
            or str(record.get("target_project_name") or "") == target_name
        )

    @classmethod
    def _runs_for_target(
        cls, target_id: str, target_name: str
    ) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
        records, errors = _read_run_records()
        matched = tuple(
            record
            for record in records
            if cls._run_matches(record, target_id, target_name)
        )
        return matched, errors

    @staticmethod
    def _latest_transaction(
        chapter: BookAssemblyChapterPlan,
        history: tuple[dict[str, Any], ...],
        diagnostics: tuple[AssemblyTransactionDiagnostic, ...],
    ) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for record in history:
            if not (
                str(record.get("source_project_id") or "")
                == chapter.chapter_project_id
                or str(record.get("source_project_name") or "")
                == chapter.chapter_project_name
            ):
                continue
            candidates.append(
                {
                    "timestamp": str(record.get("created_at") or ""),
                    "mtime_ns": 0,
                    "transaction_id": str(record.get("transaction_id") or ""),
                    "backup_reference": str(record.get("backup_path") or ""),
                    "journal_reference": "",
                    "stage": "SUCCEEDED",
                    "status": str(record.get("result_status") or "SUCCEEDED"),
                    "rollback_status": "NOT_REQUIRED",
                    "failure_summary": "",
                }
            )
        for item in diagnostics:
            if not (
                item.source_project_id == chapter.chapter_project_id
                or item.source_project_name == chapter.chapter_project_name
            ):
                continue
            candidates.append(
                {
                    "timestamp": item.updated_at or item.started_at,
                    "mtime_ns": item.journal_mtime_ns,
                    "transaction_id": item.transaction_id,
                    "backup_reference": item.backup_reference,
                    "journal_reference": item.journal_reference,
                    "stage": item.stage,
                    "status": item.final_status,
                    "rollback_status": item.rollback_status,
                    "failure_summary": item.failure_summary,
                }
            )
        return max(
            candidates,
            key=lambda item: (
                int(item.get("mtime_ns") or 0),
                _timestamp_key(item.get("timestamp")),
                str(item.get("transaction_id") or ""),
            ),
            default={},
        )

    @classmethod
    def _chapter_state(
        cls,
        chapter: BookAssemblyChapterPlan,
        history: tuple[dict[str, Any], ...],
        diagnostics: tuple[AssemblyTransactionDiagnostic, ...],
    ) -> AssemblyChapterOperationalState:
        conflict_codes = {item.code for item in chapter.conflicts}
        source_changed = bool(conflict_codes & _SOURCE_CHANGED_CODES)
        if chapter.relation_status in {RELATION_INVALID, RELATION_ORPHAN}:
            status = CHAPTER_INVALID_RELATIONSHIP
            reason = next(iter(conflict_codes), "INVALID_RELATIONSHIP")
        elif source_changed:
            status = CHAPTER_SOURCE_CHANGED
            reason = next(
                (code for code in conflict_codes if code in _SOURCE_CHANGED_CODES),
                "SOURCE_CHANGED_AFTER_PREVIOUS_MERGE",
            )
        elif chapter.already_merged:
            status = CHAPTER_OPERATIONAL_ALREADY_MERGED
            reason = ""
        elif chapter.initial_plan_status == ASSEMBLY_READY_WITH_WARNINGS:
            status = CHAPTER_READY_WITH_WARNINGS
            reason = ""
        elif chapter.initial_plan_status == ASSEMBLY_READY:
            status = CHAPTER_PENDING
            reason = ""
        else:
            status = CHAPTER_OPERATIONAL_BLOCKED
            reason = next(iter(conflict_codes), "BLOCKED")

        latest = cls._latest_transaction(chapter, history, diagnostics)
        last_status = str(latest.get("status") or "")
        last_stage = str(latest.get("stage") or "")
        rollback_status = str(latest.get("rollback_status") or "")
        failure_summary = str(latest.get("failure_summary") or "")
        if last_status == MERGE_FAILED_ROLLBACK_FAILED or rollback_status == "ROLLBACK_FAILED":
            status = CHAPTER_CRITICAL_FAILURE
            reason = "ROLLBACK_FAILED"
        elif last_status == "RECOVERABLE_INTERRUPTED":
            status = CHAPTER_CRITICAL_FAILURE
            reason = "RECOVERABLE_INTERRUPTED"
        elif (
            last_status == MERGE_FAILED_ROLLED_BACK
            and status in _CURRENT_READY_STATES
        ):
            status = CHAPTER_FAILED_ROLLED_BACK_STATE

        warnings = tuple(chapter.warnings)
        return AssemblyChapterOperationalState(
            order=chapter.order,
            chapter_project_id=chapter.chapter_project_id,
            chapter_project_name=chapter.chapter_project_name,
            chapter_title=chapter.chapter_title,
            relation_status=chapter.relation_status,
            status=status,
            planner_status=chapter.initial_plan_status,
            merge_status=(
                CHAPTER_ALREADY_MERGED
                if chapter.already_merged
                else chapter.initial_plan_status
            ),
            last_transaction_id=str(latest.get("transaction_id") or ""),
            latest_backup_reference=str(latest.get("backup_reference") or ""),
            latest_journal_reference=str(latest.get("journal_reference") or ""),
            last_transaction_stage=last_stage,
            last_transaction_status=last_status,
            rollback_status=rollback_status,
            blocking_reason=reason,
            failure_summary=failure_summary,
            warning_summary=warnings,
            source_changed=source_changed,
            current_source_fingerprint=chapter.source_state_fingerprint,
            retryable=status in _CURRENT_READY_STATES
            or status == CHAPTER_FAILED_ROLLED_BACK_STATE,
        )

    @classmethod
    def _snapshot_from_plan(
        cls,
        plan: BookAssemblyPlan,
        *,
        session: Any = None,
    ) -> AssemblyOperationsSnapshot:
        hierarchy = ProjectCatalogService.scan_hierarchy()
        history, history_errors = cls._history_for_target(plan.target_book_name)
        diagnostics, journal_errors = cls._target_journals(
            plan.target_book_id, plan.target_book_name, hierarchy
        )
        runs, run_history_errors = cls._runs_for_target(
            plan.target_book_id, plan.target_book_name
        )
        integrity_report: dict[str, Any] = {}
        integrity_errors: list[str] = []
        if plan.target_book_name:
            try:
                value = ProjectStorageService.check_integrity(plan.target_book_name)
                if isinstance(value, Mapping):
                    integrity_report = dict(value)
                else:
                    integrity_errors.append("integrity service returned a non-object")
            except Exception as exc:  # noqa: BLE001  # diagnostic boundary
                integrity_errors.append(
                    f"integrity scan failed: {type(exc).__name__}: {exc}"
                )
        integrity = (
            INTEGRITY_UNKNOWN
            if integrity_errors
            else _integrity_status(integrity_report)
        )
        chapter_states = tuple(
            cls._chapter_state(chapter, history, diagnostics)
            for chapter in plan.ordered_chapters
        )
        current_source_ids = {
            item.chapter_project_id
            for item in plan.ordered_chapters
            if item.chapter_project_id
        }
        historical_merges = tuple(
            {
                "source_project_id": str(item.get("source_project_id") or ""),
                "source_project_name": str(item.get("source_project_name") or ""),
                "transaction_id": str(item.get("transaction_id") or ""),
                "backup_reference": str(item.get("backup_path") or ""),
                "created_at": str(item.get("created_at") or ""),
                "status": str(item.get("result_status") or "SUCCEEDED"),
                "status_code": "HISTORICAL_MERGE_NOT_CURRENT_CHILD",
            }
            for item in history
            if str(item.get("source_project_id") or "")
            and str(item.get("source_project_id") or "") not in current_source_ids
        )
        active = tuple(item for item in diagnostics if item.active or item.unreadable)
        rollback_failed = tuple(
            item
            for item in diagnostics
            if item.rollback_status == "ROLLBACK_FAILED"
            or item.final_status == MERGE_FAILED_ROLLBACK_FAILED
        )
        active_runs = tuple(
            item for item in runs if str(item.get("status") or "") == OPS_IN_PROGRESS
        )
        chapter_blocked = sum(
            item.status
            in {
                CHAPTER_OPERATIONAL_BLOCKED,
                CHAPTER_INVALID_RELATIONSHIP,
                CHAPTER_SOURCE_CHANGED,
            }
            for item in chapter_states
        )
        chapter_merged = sum(
            item.status == CHAPTER_OPERATIONAL_ALREADY_MERGED for item in chapter_states
        )
        chapter_failed = sum(
            item.status
            in {
                CHAPTER_FAILED_ROLLED_BACK_STATE,
                CHAPTER_CRITICAL_FAILURE,
            }
            for item in chapter_states
        )
        chapter_pending = sum(
            item.status in _CURRENT_READY_STATES for item in chapter_states
        )
        blocking_reasons = list(
            dict.fromkeys(
                [item.code for item in plan.blocking_conflicts]
                + [item.blocking_reason for item in chapter_states if item.blocking_reason]
            )
        )
        warnings = list(plan.warnings)
        warnings.extend(
            f"{item.get('source_project_name') or item.get('source_project_id')}: historical merge is not a current child"
            for item in historical_merges
        )
        warnings.extend(history_errors)
        warnings.extend(run_history_errors)
        warnings.extend(integrity_errors)
        warnings.extend(journal_errors)
        if integrity == INTEGRITY_WARN:
            warnings.extend(
                str(item.get("message") or item.get("code") or "integrity warning")
                for item in _json_list(integrity_report.get("issues"))
                if isinstance(item, Mapping)
                and str(item.get("severity") or "").lower() == "warning"
            )
        unsafe = bool(
            active
            or rollback_failed
            or active_runs
            or journal_errors
            or integrity in {INTEGRITY_FAIL, INTEGRITY_UNKNOWN}
            or history_errors
        )
        target_valid = bool(plan.target_book_id and plan.target_book_name)
        global_structural_block = any(
            item.code in _STRUCTURAL_BLOCKER_CODES for item in plan.blocking_conflicts
        )
        if not target_valid or global_structural_block:
            overall = OPS_BLOCKED
        elif unsafe:
            overall = OPS_DEGRADED
        elif plan.blocking_conflicts or chapter_blocked:
            overall = OPS_BLOCKED
        elif not chapter_states:
            overall = OPS_NOT_STARTED
        elif (
            chapter_merged == len(chapter_states)
            and integrity == INTEGRITY_PASS
        ):
            overall = OPS_COMPLETE
        elif chapter_merged and integrity == INTEGRITY_WARN:
            overall = OPS_READY_WITH_WARNINGS
        elif chapter_failed and not chapter_merged:
            overall = OPS_FAILED
        elif chapter_merged:
            overall = OPS_PARTIAL
        elif plan.aggregate_status == ASSEMBLY_READY_WITH_WARNINGS:
            overall = OPS_READY_WITH_WARNINGS
        else:
            overall = OPS_NOT_STARTED
        selected, opened, _revision = (
            str(getattr(session, "selected_project", "") or "") if session else "",
            str(getattr(session, "project", "") or "") if session else "",
            int(getattr(session, "selection_revision", 0) or 0) if session else 0,
        )
        _ = selected, opened
        has_retryable = any(item.retryable for item in chapter_states)
        resume_allowed = bool(
            target_valid
            and has_retryable
            and overall not in {OPS_COMPLETE, OPS_BLOCKED, OPS_DEGRADED}
            and plan.aggregate_status in {ASSEMBLY_READY, ASSEMBLY_READY_WITH_WARNINGS}
            and not chapter_blocked
        )
        if overall == OPS_FAILED and has_retryable and not unsafe:
            resume_allowed = True
        latest_run = dict(runs[0]) if runs else {}
        return AssemblyOperationsSnapshot(
            target_book_id=plan.target_book_id,
            target_book_name=plan.target_book_name,
            overall_status=overall,
            total_chapters=len(chapter_states),
            merged_count=chapter_merged,
            pending_count=chapter_pending,
            blocked_count=chapter_blocked,
            failed_count=chapter_failed,
            integrity_status=integrity,
            integrity_report=integrity_report,
            chapter_states=chapter_states,
            transaction_diagnostics=diagnostics,
            active_transactions=active,
            historical_merges=historical_merges,
            recent_runs=runs[:5],
            latest_run=latest_run,
            blocking_reasons=tuple(blocking_reasons),
            warnings=tuple(dict.fromkeys(warnings)),
            data_root=_data_root_path(),
            generated_at=_utc_now(),
            plan_status=plan.aggregate_status,
            resume_allowed=resume_allowed,
            reanalyze_allowed=target_valid,
        )

    @classmethod
    def reconstruct(
        cls,
        target_book: Any,
        *,
        resolutions: Any = None,
        session: Any = None,
        opened_projects: Any = None,
    ) -> AssemblyOperationsSnapshot:
        """Rebuild current state exclusively from durable state and fresh planning."""
        plan = WholeBookAssemblyService.plan_assembly(
            target_book,
            resolutions=resolutions,
            session=session,
            opened_projects=opened_projects,
        )
        return cls._snapshot_from_plan(plan, session=session)

    @classmethod
    def analyze(
        cls,
        target_book: Any,
        *,
        resolutions: Any = None,
        session: Any = None,
        opened_projects: Any = None,
    ) -> AssemblyOperationsAnalysis:
        plan = WholeBookAssemblyService.plan_assembly(
            target_book,
            resolutions=resolutions,
            session=session,
            opened_projects=opened_projects,
        )
        return AssemblyOperationsAnalysis(
            snapshot=cls._snapshot_from_plan(plan, session=session), plan=plan
        )

    @classmethod
    def _execute_with_run(
        cls,
        plan: BookAssemblyPlan,
        resolutions: Any,
        confirmation: Any,
        *,
        session: Any = None,
        fault_injection: Any = None,
    ) -> BookAssemblyExecutionResult:
        run_id = f"assembly-{uuid.uuid4().hex}"
        history = _AssemblyRunHistory(
            run_id,
            plan.target_book_id,
            plan.target_book_name,
            plan.ordered_chapter_ids,
        )
        try:
            result = WholeBookAssemblyService.execute_assembly(
                plan,
                resolutions,
                confirmation,
                session=session,
                fault_injection=fault_injection,
                assembly_id=run_id,
                progress_callback=history.update,
            )
        except WholeBookAssemblyError as exc:
            history.fail(exc.code, str(exc))
            raise
        except Exception as exc:  # pragma: no cover - defensive operational boundary
            history.fail("ASSEMBLY_UNEXPECTED_ERROR", str(exc))
            raise
        history.finish(result)
        return result

    @classmethod
    def execute_confirmed(
        cls,
        plan: BookAssemblyPlan,
        resolutions: Any,
        confirmation: Any,
        *,
        session: Any = None,
        fault_injection: Any = None,
    ) -> AssemblyResumeResult:
        """Execute a confirmed transient plan while persisting only run metadata."""
        snapshot = cls.reconstruct(
            plan.target_book_name, resolutions=resolutions, session=session
        )
        if snapshot.overall_status == OPS_DEGRADED:
            raise WholeBookAssemblyError(
                "ASSEMBLY_DEGRADED",
                "目标 Book 当前处于 DEGRADED，必须先完成诊断/恢复",
                details={"blocking_reasons": snapshot.blocking_reasons},
            )
        if not snapshot.resume_allowed and snapshot.overall_status == OPS_COMPLETE:
            raise WholeBookAssemblyError(
                "ASSEMBLY_COMPLETE",
                "目标 Book 当前已完成整书装配",
            )
        if not snapshot.resume_allowed:
            raise WholeBookAssemblyError(
                "ASSEMBLY_EXECUTION_BLOCKED",
                "当前运营状态不允许执行整书装配",
                details={
                    "overall_status": snapshot.overall_status,
                    "blocking_reasons": snapshot.blocking_reasons,
                },
            )
        result = cls._execute_with_run(
            plan,
            resolutions,
            confirmation,
            session=session,
            fault_injection=fault_injection,
        )
        return AssemblyResumeResult(
            snapshot=cls.reconstruct(
                plan.target_book_name, resolutions=resolutions, session=session
            ),
            execution_result=result,
        )

    @classmethod
    def resume(
        cls,
        target_book: Any,
        resolutions: Any = None,
        *,
        confirmed: bool = False,
        session: Any = None,
        fault_injection: Any = None,
    ) -> AssemblyResumeResult:
        """Freshly analyze and resume pending Chapters; never restore old tokens."""
        if not confirmed:
            raise WholeBookAssemblyError(
                "CONFIRMATION_REQUIRED",
                "继续未完成章节必须先勾选当前整书装配确认",
            )
        analysis = cls.analyze(target_book, resolutions=resolutions, session=session)
        if not analysis.snapshot.resume_allowed:
            raise WholeBookAssemblyError(
                "ASSEMBLY_RESUME_BLOCKED",
                "当前状态不允许继续未完成章节",
                details={
                    "overall_status": analysis.snapshot.overall_status,
                    "blocking_reasons": analysis.snapshot.blocking_reasons,
                },
            )
        confirmation = WholeBookAssemblyService.prepare_confirmation(
            analysis.plan, resolutions, session=session
        )
        return cls.execute_confirmed(
            analysis.plan,
            resolutions,
            confirmation,
            session=session,
            fault_injection=fault_injection,
        )


def reconstruct_assembly(
    target_book: Any,
    **kwargs: Any,
) -> AssemblyOperationsSnapshot:
    return WholeBookAssemblyOperationsService.reconstruct(target_book, **kwargs)


def analyze_assembly_operations(
    target_book: Any,
    **kwargs: Any,
) -> AssemblyOperationsAnalysis:
    return WholeBookAssemblyOperationsService.analyze(target_book, **kwargs)


__all__ = [
    "ASSEMBLY_READY",
    "ASSEMBLY_READY_WITH_WARNINGS",
    "CHAPTER_CRITICAL_FAILURE",
    "CHAPTER_FAILED_ROLLED_BACK_STATE",
    "CHAPTER_INVALID_RELATIONSHIP",
    "CHAPTER_OPERATIONAL_ALREADY_MERGED",
    "CHAPTER_OPERATIONAL_BLOCKED",
    "CHAPTER_PENDING",
    "CHAPTER_READY",
    "CHAPTER_READY_WITH_WARNINGS",
    "CHAPTER_SOURCE_CHANGED",
    "INTEGRITY_FAIL",
    "INTEGRITY_PASS",
    "INTEGRITY_UNKNOWN",
    "INTEGRITY_WARN",
    "OPERATIONS_SCHEMA_VERSION",
    "OPS_BLOCKED",
    "OPS_COMPLETE",
    "OPS_DEGRADED",
    "OPS_FAILED",
    "OPS_IN_PROGRESS",
    "OPS_NOT_STARTED",
    "OPS_PARTIAL",
    "OPS_READY",
    "OPS_READY_WITH_WARNINGS",
    "AssemblyChapterOperationalState",
    "AssemblyOperationsAnalysis",
    "AssemblyOperationsSnapshot",
    "AssemblyResumeResult",
    "AssemblyTransactionDiagnostic",
    "WholeBookAssemblyOperationsService",
    "analyze_assembly_operations",
    "reconstruct_assembly",
]
