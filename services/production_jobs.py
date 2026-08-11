"""Unified production-job orchestration for Web and MCP.

The service is deliberately framework-free.  It is a command/query client over
the project-local task database and owns the machine-readable planning
contract.  ``production_runtime`` alone owns ``SynthesisService`` and TTS.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from lib import project_paths, script_loader, segment_cache
from lib.startup import enrich as enrich_startup
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository

from .production_runtime import ProductionRuntimeClient
from .synthesis import SynthesisState
from .voice_cast import VoiceCastResolver

logger = logging.getLogger(__name__)


PRODUCTION_TASK_TYPE = "synthesis"
PRODUCTION_STATES = (
    "pending", "running", "pausing", "paused", "recovering", "cancelling",
    "cancelled", "done", "error", "interrupted", "needs_attention",
)
ACTIVE_PRODUCTION_STATES = frozenset({
    "pending", "running", "pausing", "paused", "recovering", "cancelling",
})
TERMINAL_PRODUCTION_STATES = frozenset({
    "cancelled", "done", "error", "needs_attention",
})
VALID_SOURCES = frozenset({"mcp", "web", "system", "recovery"})


def _now() -> str:
    """Return a stable UTC timestamp suitable for JSON and sorting."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _public_error(value: Any) -> str:
    """Remove common local absolute-path prefixes from task-facing text."""
    text = str(value or "")
    return re.sub(
        r"(?:[A-Za-z]:[\\/]|/(?:Users|home|private|tmp|var|opt)/)[^\s,;)]*",
        "<local-path>",
        text,
    )


class ProductionJobError(ValueError):
    """Domain error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = str(code)
        self.details = details

    def as_error(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **self.details}

    def as_payload(self) -> dict[str, Any]:
        return {"error": self.as_error()}


class ProductionJobService:
    """Transactional command/query façade shared by Web and MCP.

    Runtime objects live exclusively in ``ProductionRuntime``.  This class
    never infers worker death from the absence of process-local memory.
    """

    @classmethod
    def reset_runtime(cls) -> None:
        """Stop the optional inline runtime used by unit tests."""
        ProductionRuntimeClient.reset_inline()

    @staticmethod
    def _project_data(project_name: str) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        try:
            return ProjectRepository.load_project(project_name)
        except Exception as exc:
            raise ProductionJobError(
                "PROJECT_NOT_FOUND", "项目不存在或无法读取", project_name=project_name
            ) from exc

    @staticmethod
    def _script_index(script: dict[str, Any]) -> tuple[
        dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]
    ]:
        """Build chapter/segment indexes with public string identifiers."""
        _voices, chapters = script_loader.resolve_collections(script)
        chapter_map: dict[str, dict[str, Any]] = {}
        segment_map: dict[str, dict[str, Any]] = {}
        segment_chapters: dict[str, str] = {}
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            chapter_id = str(chapter.get("id", "")).strip()
            if not chapter_id:
                continue
            chapter_map[chapter_id] = chapter
            for segment in chapter.get("segments", []):
                if not isinstance(segment, dict):
                    continue
                segment_id = str(segment.get("id", "")).strip()
                if segment_id:
                    segment_map[segment_id] = segment
                    segment_chapters[segment_id] = chapter_id
        return chapter_map, segment_map, segment_chapters

    @classmethod
    def _normalize_scope(
        cls,
        scope: Any,
        chapter_map: dict[str, dict[str, Any]],
        segment_map: dict[str, dict[str, Any]],
        segment_chapters: dict[str, str],
    ) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
        """Normalize scope and return (scope, selected segment ids, blockers)."""
        blockers: list[dict[str, Any]] = []
        if scope is not None and not isinstance(scope, dict):
            blockers.append({
                "code": "INVALID_SCOPE",
                "message": "scope 必须是对象",
                "fix_hint": "使用 {\"all\": true} 或包含 chapter_ids/segment_ids 的对象。",
            })
        raw = scope if isinstance(scope, dict) else {}
        chapter_ids = _unique_strings(raw.get("chapter_ids"))
        segment_ids = _unique_strings(raw.get("segment_ids"))
        has_explicit_scope = bool(chapter_ids or segment_ids)
        raw_all = raw.get("all", not has_explicit_scope)
        all_scope = bool(raw_all)
        if "all" in raw and not isinstance(raw_all, bool):
            blockers.append({
                "code": "INVALID_SCOPE",
                "message": "scope.all 必须是 boolean",
                "fix_hint": "将 all 设置为 true 或 false。",
            })
        if all_scope and has_explicit_scope:
            blockers.append({
                "code": "INVALID_SCOPE",
                "message": "scope.all=true 不能同时指定 chapter_ids 或 segment_ids",
                "fix_hint": "选择 all=true，或只提交明确的章节/段落范围。",
            })
        if not all_scope and not has_explicit_scope:
            blockers.append({
                "code": "INVALID_SCOPE",
                "message": "scope 必须指定 all=true 或非空 chapter_ids/segment_ids",
                "fix_hint": "使用 {\"all\": true} 或提供范围数组。",
            })
        for chapter_id in chapter_ids:
            if chapter_id not in chapter_map:
                blockers.append({
                    "code": "CHAPTER_NOT_FOUND",
                    "message": f"章节不存在: {chapter_id}",
                    "chapter_id": chapter_id,
                    "fix_hint": "先读取项目章节列表，再使用有效的 chapter id。",
                })
        for segment_id in segment_ids:
            if segment_id not in segment_map:
                blockers.append({
                    "code": "SEGMENT_NOT_FOUND",
                    "message": f"段落不存在: {segment_id}",
                    "segment_id": segment_id,
                    "fix_hint": "使用项目 structured script 中存在的 segment id。",
                })
        if chapter_ids and segment_ids:
            outside = [
                segment_id for segment_id in segment_ids
                if segment_id in segment_chapters and segment_chapters[segment_id] not in chapter_ids
            ]
            if outside:
                blockers.append({
                    "code": "INVALID_SCOPE",
                    "message": "segment_ids 必须属于指定 chapter_ids",
                    "segment_ids": outside,
                    "fix_hint": "删除 chapter_ids，或只保留其中章节的段落。",
                })

        normalized = {
            "all": bool(all_scope),
            "chapter_ids": chapter_ids if not all_scope else [],
            "segment_ids": segment_ids if not all_scope else [],
        }
        if all_scope:
            selected_ids = list(segment_map)
        elif segment_ids:
            selected_ids = [segment_id for segment_id in segment_ids if segment_id in segment_map]
        else:
            selected_ids = [
                segment_id for segment_id, chapter_id in segment_chapters.items()
                if chapter_id in chapter_ids
            ]
        return normalized, selected_ids, blockers

    @staticmethod
    def _issue_blocker(issue: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": str(issue.get("code") or "SCRIPT_INVALID"),
            "message": str(issue.get("message") or "structured script 校验失败"),
            "fix_hint": str(issue.get("fix_hint") or "修复 structured script 后重试。"),
            **{
                key: issue[key]
                for key in ("path", "severity")
                if key in issue
            },
        }

    @classmethod
    def _voice_cast_plan(
        cls,
        project_name: str,
        selected_segment_ids: list[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Return readiness for exactly the selected production segments."""
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        try:
            status = VoiceCastResolver.get_voice_binding_status(project_name)
        except Exception as exc:  # project was already loaded
            status = {
                "mode": "unknown",
                "bound": 0,
                "unbound": 0,
                "cast_locked": False,
                "synthesis_ready": False,
            }
            blockers.append({
                "code": "VOICE_CAST_NOT_READY",
                "message": f"无法读取 Voice Cast 状态: {exc}",
                "fix_hint": "检查 Character Roster、Voice Cast 和声音资产。",
            })

        selected_check: dict[str, Any] = {}
        try:
            selected_check = VoiceCastResolver.check_production_scope(
                project_name, selected_segment_ids
            )
        except Exception as exc:
            blockers.append({
                "code": "VOICE_CAST_NOT_READY",
                "message": f"无法解析选中范围角色: {exc}",
                "fix_hint": "检查章节角色与 Character Roster 的映射。",
            })

        for issue in selected_check.get("errors", []) if isinstance(selected_check, dict) else []:
            if isinstance(issue, dict):
                blockers.append(cls._issue_blocker(issue))

        mode = str(status.get("mode") or "legacy_manual")
        cast_locked = bool(status.get("cast_locked", False))
        selected_ready = bool(selected_check.get("ready", False))
        if not selected_ready and not blockers:
            blockers.append({
                "code": "VOICE_CAST_NOT_READY",
                "message": "所选范围尚未达到合成就绪状态。",
                "fix_hint": "完成当前范围所需角色的声音绑定并重新检查。",
            })
        runtime_status = str(status.get("runtime_status") or "unknown")
        engine_state = str(status.get("engine_state") or "unknown")
        engine_ready = bool(status.get("engine_ready", False))
        if runtime_status == "error" or engine_state == "error":
            warnings.append({
                "code": "RUNTIME_ENGINE_ERROR",
                "message": "TTS 引擎初始化失败，生产任务会立即失败。",
                "fix_hint": "修复模型 / CUDA / 显存问题后重新启动运行时再试。",
            })
        warnings.extend(
            item for item in status.get("warnings", [])
            if isinstance(item, dict)
        )
        production_ready = bool(selected_ready) and not blockers
        # engine_state unknown/uninitialized is NOT a blocker: the runtime
        # preflights the engine when it claims the task.  Only a declared
        # engine error makes starting pointless (it would fail fast).
        synthesis_ready = (
            production_ready
            and runtime_status != "error"
            and engine_state != "error"
        )
        return {
            "locked": cast_locked,
            "cast_locked": cast_locked,
            "bound": int(status.get("bound", 0) or 0),
            "unbound": int(status.get("unbound", 0) or 0),
            "mode": mode,
            "full_book_ready": bool(status.get("cast_ready", False)),
            "scope_ready": selected_ready,
            "selected_segment_count": len(selected_segment_ids),
            "required_roles": selected_check.get("required_roles", []),
            "required_role_count": int(selected_check.get("required_role_count", 0) or 0),
            "bound_role_count": int(selected_check.get("bound_role_count", 0) or 0),
            "unbound_roles": list(selected_check.get("unbound_roles", [])),
            "missing_roles": list(selected_check.get("missing_roles", [])),
            "cast_ready": production_ready,
            "runtime_status": runtime_status,
            "engine_state": engine_state,
            "engine_ready": engine_ready,
            "production_ready": production_ready,
            "synthesis_ready": synthesis_ready,
        }, blockers, warnings

    @classmethod
    def plan(cls, project_name: str, scope: Any = None) -> dict[str, Any]:
        """Validate a production scope without creating a task."""
        name = str(project_name or "").strip()
        if not name:
            return {
                "ready": False,
                "project_name": name,
                "scope": {"all": True, "chapter_ids": [], "segment_ids": []},
                "chapters": 0,
                "segments": 0,
                "already_completed": 0,
                "remaining": 0,
                "failed": 0,
                "voice_cast": {"locked": False, "bound": 0, "unbound": 0},
                "blockers": [{
                    "code": "PROJECT_NAME_REQUIRED",
                    "message": "project_name 不能为空",
                    "fix_hint": "提供已有项目名称。",
                }],
                "warnings": [],
            }
        try:
            meta, script, _bindings = cls._project_data(name)
        except ProductionJobError as exc:
            return {
                "ready": False,
                "project_name": name,
                "scope": {"all": True, "chapter_ids": [], "segment_ids": []},
                "chapters": 0,
                "segments": 0,
                "already_completed": 0,
                "remaining": 0,
                "failed": 0,
                "voice_cast": {"locked": False, "bound": 0, "unbound": 0},
                "blockers": [exc.as_error()],
                "warnings": [],
            }

        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        raw_script = script if isinstance(script, dict) else {}
        try:
            script_issues = script_loader.validate_script_issues(raw_script)
        except Exception:
            script_issues = []
        for issue in script_issues:
            if isinstance(issue, dict) and issue.get("severity", "error") == "error":
                blockers.append(cls._issue_blocker(issue))

        chapter_map, segment_map, segment_chapters = cls._script_index(raw_script)
        normalized_scope, selected_ids, scope_blockers = cls._normalize_scope(
            scope, chapter_map, segment_map, segment_chapters
        )
        blockers.extend(scope_blockers)
        selected_chapter_ids = {
            segment_chapters[segment_id]
            for segment_id in selected_ids
            if segment_id in segment_chapters
        }
        if normalized_scope["all"]:
            selected_chapter_ids = set(chapter_map)
        elif normalized_scope["chapter_ids"]:
            selected_chapter_ids = set(normalized_scope["chapter_ids"]) & set(chapter_map)
        project_dir = ProjectRepository.get_project_dir(name)
        segments_dir = project_paths.project_dir(project_dir, "segments")
        completed = 0
        failed_ids: list[str] = []
        remaining = 0
        for segment_id in selected_ids:
            persisted = str(getattr(meta, "segments_status", {}).get(segment_id, "pending"))
            has_audio = segment_cache.has_segment_wav(segments_dir, segment_id)
            if persisted == "done" and has_audio:
                completed += 1
            elif persisted == "failed":
                failed_ids.append(segment_id)
            else:
                remaining += 1

        voice_cast, voice_blockers, voice_warnings = cls._voice_cast_plan(
            name, selected_ids
        )
        blockers.extend(voice_blockers)
        warnings.extend(voice_warnings)
        if failed_ids:
            warnings.append({
                "code": "FAILED_SEGMENTS_PRESENT",
                "message": "选中范围包含失败段落，可在任务结束后重试。",
                "count": len(failed_ids),
            })
        skipped = sum(
            1 for segment_id in selected_ids
            if getattr(meta, "segments_status", {}).get(segment_id) == "skipped"
        )
        if skipped:
            warnings.append({
                "code": "SELECTION_MARKERS_RESTORED",
                "message": "选中范围包含此前跳过的段落，这些段落会重新进入生产。",
                "count": skipped,
            })
        return {
            "ready": not blockers,
            "project_name": name,
            "scope": normalized_scope,
            "chapters": len(selected_chapter_ids),
            "segments": len(selected_ids),
            "selected_segment_count": len(selected_ids),
            "already_completed": completed,
            "already_done": completed,
            "remaining": remaining,
            "pending": remaining,
            "to_synthesize": remaining + len(failed_ids),
            "failed": len(failed_ids),
            "failed_segment_ids": failed_ids,
            "voice_cast": voice_cast,
            "required_roles": voice_cast.get("required_roles", []),
            "unbound_roles": voice_cast.get("unbound_roles", []),
            "missing_roles": voice_cast.get("missing_roles", []),
            "blockers": blockers,
            "warnings": warnings,
        }

    plan_production = plan

    @classmethod
    def _active_or_interrupted(cls, project_name: str) -> Optional[TaskRecord]:
        """Return the current durable active task without stale inference."""
        records = TaskRepository.list_tasks(project=project_name, task_type=PRODUCTION_TASK_TYPE)
        for record in records:
            if record.status in ACTIVE_PRODUCTION_STATES:
                return record
        return None

    @classmethod
    def _existing_idempotent(
        cls, project_name: str, idempotency_key: str
    ) -> Optional[TaskRecord]:
        return TaskRepository.find_by_idempotency(
            project_name, PRODUCTION_TASK_TYPE, idempotency_key
        )

    @classmethod
    def _normalize_options(cls, options: Any) -> dict[str, Any]:
        raw = options if isinstance(options, dict) else {}
        try:
            beams = max(int(raw.get("num_beams", 2) or 2), 1)
        except (TypeError, ValueError):
            beams = 2
        result = {
            "num_beams": beams,
            "emotion": raw.get("emotion"),
            "emo_alpha": raw.get("emo_alpha"),
            "speech_rate": raw.get("speech_rate"),
            "voice_overrides": {
                str(segment_id): str(path)
                for segment_id, path in raw.get("voice_overrides", {}).items()
                if str(segment_id).strip() and str(path).strip()
            } if isinstance(raw.get("voice_overrides"), dict) else {},
        }
        return TaskRepository.canonical_options(result)

    @staticmethod
    def _idempotency_conflict(
        existing: TaskRecord,
        idempotency_key: str,
    ) -> ProductionJobError:
        return ProductionJobError(
            "IDEMPOTENCY_CONFLICT",
            "同一幂等键已用于不同的生产请求",
            project_name=existing.project,
            task_id=existing.task_id,
            status=existing.status,
            idempotency_key=idempotency_key,
        )

    @classmethod
    def start(
        cls,
        project_name: str,
        scope: Any = None,
        options: Any = None,
        *,
        source: str = "system",
        idempotency_key: str = "",
        selected_chapters: Optional[list[str]] = None,
        selected_segment_ids: Optional[list[str]] = None,
        parent_task_id: str = "",
        recovery_of: str = "",
        attempt: int = 1,
    ) -> dict[str, Any]:
        """Plan, create and asynchronously launch one production task."""
        name = str(project_name or "").strip()
        origin = str(source or "system").strip().lower()
        if origin not in VALID_SOURCES:
            raise ProductionJobError(
                "INVALID_SOURCE", f"不支持的任务来源: {origin}",
                allowed_sources=sorted(VALID_SOURCES),
            )
        if scope is None and (selected_chapters or selected_segment_ids):
            scope = {
                "chapter_ids": list(selected_chapters or []),
                "segment_ids": list(selected_segment_ids or []),
            }
        key = str(idempotency_key or "").strip()
        normalized_options = cls._normalize_options(options)
        requested_scope = TaskRepository.canonical_scope(scope)
        # Fast-path existing durable rows before re-running mutable project
        # planning.  The later SQLite transaction remains authoritative.
        replay = cls._existing_idempotent(name, key)
        if replay is not None:
            if (
                (scope is None or isinstance(scope, dict))
                and TaskRepository.same_production_payload(
                    replay, requested_scope, normalized_options
                )
            ):
                return {"created": False, **cls._task_snapshot(replay)}
            raise cls._idempotency_conflict(replay, key)
        active = cls._active_or_interrupted(name)
        if active is not None:
            raise ProductionJobError(
                "PROJECT_HAS_ACTIVE_TASK",
                "同一项目已有生产任务运行中",
                project_name=name,
                task_id=active.task_id,
                status=active.status,
            )
        plan = cls.plan(name, scope)
        if not plan["ready"]:
            raise ProductionJobError(
                "PRODUCTION_BLOCKED", "生产前检查未通过",
                project_name=name, blockers=plan["blockers"], plan=plan,
            )
        now = _now()
        task_id = f"task_{uuid.uuid4().hex[:16]}"
        record = TaskRecord(
            task_id=task_id,
            task_type=PRODUCTION_TASK_TYPE,
            project=name,
            status="pending",
            source=origin,
            scope=plan["scope"],
            options=normalized_options,
            progress={
                "total": plan["segments"],
                "selected_total": plan["segments"],
                "completed": plan["already_completed"],
                "already_completed": plan["already_completed"],
                "failed": plan["failed"],
                "pending": plan.get("pending", plan.get("remaining", 0)),
                "to_synthesize": plan.get(
                    "to_synthesize",
                    plan.get("remaining", 0) + plan.get("failed", 0),
                ),
                "percent": round(
                    (plan["already_completed"] / plan["segments"]) * 100, 1
                ) if plan["segments"] else 0.0,
                "current_chapter": None,
                "current_segment": None,
            },
            failed_segment_ids=list(plan.get("failed_segment_ids", [])),
            attempt=max(int(attempt or 1), 1),
            idempotency_key=key,
            created_at=now,
            updated_at=now,
            parent_task_id=str(parent_task_id or ""),
            recovery_of=str(recovery_of or ""),
            startup={
                "phase": "task_submitted",
                "phase_started_at": now,
                "submitted_at": now,
            },
        )
        outcome, durable = TaskRepository.create_production_task(record)
        if outcome == "idempotent":
            return {"created": False, **cls._task_snapshot(durable)}
        if outcome == "idempotency_conflict":
            raise cls._idempotency_conflict(durable, key)
        if outcome == "active":
            raise ProductionJobError(
                "PROJECT_HAS_ACTIVE_TASK",
                "同一项目已有生产任务运行中",
                project_name=name,
                task_id=durable.task_id,
                status=durable.status,
            )
        # Durable startup phase: mark the client-side spawn step before the
        # runtime claims the task and advances the phase machine itself.
        spawn_ts = _now()
        try:
            TaskRepository.update_startup(
                task_id,
                phase="runtime_starting",
                phase_started_at=spawn_ts,
                runtime_spawn_started_at=spawn_ts,
            )
        except Exception:
            logger.exception("记录 runtime_starting 启动阶段失败: %s", task_id)
        runtime_pid = ProductionRuntimeClient.ensure_running()
        if runtime_pid:
            try:
                TaskRepository.update_startup(task_id, runtime_spawn_pid=runtime_pid)
            except Exception:
                logger.exception("记录 runtime pid 失败: %s", task_id)
        fresh = TaskRepository.load_task(task_id) or durable
        return {"created": True, **cls._task_snapshot(fresh)}

    start_production = start

    @classmethod
    def _get_record(cls, task_id: str) -> TaskRecord:
        identifier = str(task_id or "").strip()
        record = TaskRepository.load_task(identifier)
        if record is None or record.task_type != PRODUCTION_TASK_TYPE:
            raise ProductionJobError("TASK_NOT_FOUND", "生产任务不存在", task_id=identifier)
        return record

    @classmethod
    def _mark_stale(cls, record: TaskRecord) -> TaskRecord:
        """Compatibility no-op: only a lock-owning runtime may repair orphans."""
        return record

    @classmethod
    def _task_snapshot(cls, record: TaskRecord) -> dict[str, Any]:
        progress = dict(record.progress or {})
        status = record.status
        failed_ids = list(record.failed_segment_ids or [])
        log_lines = [_public_error(line) for line in record.log_lines[-50:]]
        base_progress = {
            "total": 0,
            "completed": 0,
            "failed": len(failed_ids),
            "percent": 0.0,
            "current_chapter": None,
            "current_segment": None,
        }
        base_progress.update(progress)
        total = int(base_progress.get("total", 0) or 0)
        completed = int(base_progress.get("completed", 0) or 0)
        base_progress["failed"] = len(failed_ids) if failed_ids else int(base_progress.get("failed", 0) or 0)
        base_progress["percent"] = round((completed / total) * 100, 1) if total else 0.0
        # Do not expose artifact_dir: it is intentionally a private local path.
        recovery = base_progress.get("recovery")
        recovery_payload = recovery if isinstance(recovery, dict) else None
        engine_generation = int(base_progress.get("engine_generation") or 0)
        response = {
            "task_id": record.task_id,
            "task_type": record.task_type,
            "project": record.project,
            "source": record.source,
            "status": status,
            "scope": {
                "all": bool(record.scope.get("all", False)) if isinstance(record.scope, dict) else False,
                "chapter_ids": _unique_strings(record.scope.get("chapter_ids")) if isinstance(record.scope, dict) else [],
                "segment_ids": _unique_strings(record.scope.get("segment_ids")) if isinstance(record.scope, dict) else [],
            },
            "options": dict(record.options) if isinstance(record.options, dict) else {},
            "progress": base_progress,
            "failed_segment_ids": failed_ids,
            "attempt": int(record.attempt or 1),
            "idempotency_key": record.idempotency_key,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "updated_at": record.updated_at,
            "finished_at": record.finished_at,
            "heartbeat_at": record.heartbeat_at,
            "error_summary": _public_error(record.error_summary),
        }
        startup = record.startup if isinstance(record.startup, dict) else {}
        response["startup"] = enrich_startup(startup)
        if recovery_payload:
            response["recovery"] = {
                key: recovery_payload[key]
                for key in (
                    "reason_code", "attempt", "max_attempts",
                    "engine_generation", "retry_segment", "fingerprint",
                    "exception_type", "errno", "phase", "message",
                    "traceback_origin", "code", "recycles_used",
                    "recycle_exception_type", "recycle_errno",
                    "recycle_message", "recycle_traceback_origin",
                    "recovered", "last_recovery_at",
                )
                if key in recovery_payload
            }
        if engine_generation:
            response["engine_generation"] = engine_generation
        if status == "needs_attention":
            error_details = {
                "code": str(recovery_payload.get("reason_code") or record.error_summary.split(":")[0] if record.error_summary else "TTS_ENGINE_RUNTIME_FAILURE"),
            }
            if recovery_payload:
                for key in (
                    "exception_type", "errno", "phase", "fingerprint",
                    "message", "traceback_origin", "code",
                    "recycle_exception_type", "recycle_errno",
                    "recycle_message", "recycle_traceback_origin",
                ):
                    if recovery_payload.get(key) not in (None, ""):
                        error_details[key] = recovery_payload[key]
            response["error"] = error_details
            response["next_actions"] = [
                "retry_task",
                "inspect_runtime_health",
                "cancel_task",
            ]
        if log_lines:
            response["log_lines"] = log_lines
        return response

    @classmethod
    def get_task_snapshot(cls, task_id: str) -> dict[str, Any]:
        return cls._task_snapshot(cls._get_record(task_id))

    get_production_task = get_task_snapshot

    @classmethod
    def get_runtime_state(cls, task_id: str) -> Optional[SynthesisState]:
        """Return the current-process state for Web rendering only.

        The object is never serialized or exposed by MCP; callers may use it
        to render the existing queue rows without making the session the task
        owner.
        """
        return ProductionRuntimeClient.get_runtime_state(str(task_id or "").strip())

    @classmethod
    def list_tasks(
        cls,
        project_name: Optional[str] = None,
        status: Optional[str] = None,
        source: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        records = TaskRepository.list_tasks(
            project=project_name or None,
            task_type=PRODUCTION_TASK_TYPE,
            status=status or None,
            source=source or None,
        )
        return [cls._task_snapshot(record) for record in records]

    list_production_tasks = list_tasks

    @classmethod
    def get_active_task(cls, project_name: str) -> Optional[dict[str, Any]]:
        record = cls._active_or_interrupted(str(project_name or "").strip())
        return cls._task_snapshot(record) if record is not None else None

    @classmethod
    def _scope_for_record(cls, record: TaskRecord) -> dict[str, Any]:
        scope = record.scope if isinstance(record.scope, dict) else {}
        return {
            "all": bool(scope.get("all", False)),
            "chapter_ids": _unique_strings(scope.get("chapter_ids")),
            "segment_ids": _unique_strings(scope.get("segment_ids")),
        }

    @classmethod
    def pause(cls, task_id: str) -> dict[str, Any]:
        record = cls._get_record(task_id)
        try:
            updated = TaskRepository.request_control(record.task_id, "pause")
        except ValueError:
            raise ProductionJobError(
                "INVALID_TASK_STATE", "当前任务不能暂停",
                task_id=record.task_id, status=record.status,
            ) from None
        ProductionRuntimeClient.poke()
        return cls._task_snapshot(TaskRepository.load_task(record.task_id) or updated)

    pause_production = pause

    @classmethod
    def cancel(cls, task_id: str) -> dict[str, Any]:
        record = cls._get_record(task_id)
        try:
            updated = TaskRepository.request_control(record.task_id, "cancel")
        except ValueError:
            raise ProductionJobError(
                "INVALID_TASK_STATE", "当前任务不能取消",
                task_id=record.task_id, status=record.status,
            ) from None
        ProductionRuntimeClient.poke()
        return cls._task_snapshot(TaskRepository.load_task(record.task_id) or updated)

    cancel_production = cancel

    @classmethod
    def _resume_interrupted(cls, record: TaskRecord) -> dict[str, Any]:
        result = cls.start(
            record.project,
            cls._scope_for_record(record),
            record.options,
            source="recovery",
            parent_task_id=record.task_id,
            recovery_of=record.task_id,
            attempt=max(int(record.attempt or 1), 1) + 1,
        )
        result["recovery_of"] = record.task_id
        return result

    @classmethod
    def resume(cls, task_id: str) -> dict[str, Any]:
        record = cls._get_record(task_id)
        if record.status in {"interrupted", "needs_attention"}:
            return cls._resume_interrupted(record)
        try:
            updated = TaskRepository.request_control(record.task_id, "resume")
        except ValueError:
            raise ProductionJobError(
                "INVALID_TASK_STATE", "当前任务不能恢复",
                task_id=record.task_id, status=record.status,
            ) from None
        ProductionRuntimeClient.ensure_running()
        ProductionRuntimeClient.poke()
        return cls._task_snapshot(TaskRepository.load_task(record.task_id) or updated)

    resume_production = resume

    @classmethod
    def _retryable_segments(cls, record: TaskRecord) -> list[str]:
        _meta, script, _bindings = cls._project_data(record.project)
        _chapter_map, segment_map, _segment_chapters = cls._script_index(script)
        scope = cls._scope_for_record(record)
        if scope["all"]:
            selected = list(segment_map)
        elif scope["segment_ids"]:
            selected = [item for item in scope["segment_ids"] if item in segment_map]
        else:
            chapter_set = set(scope["chapter_ids"])
            selected = [
                segment_id for segment_id, chapter_id in _segment_chapters.items()
                if chapter_id in chapter_set
            ]
        project_dir = ProjectRepository.get_project_dir(record.project)
        segments_dir = project_paths.project_dir(project_dir, "segments")
        meta = _meta
        return [
            segment_id for segment_id in selected
            if meta.segments_status.get(segment_id) == "failed"
            or not segment_cache.has_segment_wav(segments_dir, segment_id)
        ]

    @classmethod
    def retry_failed_segments(
        cls,
        task_id: str,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        record = cls._get_record(task_id)
        if record.status in ACTIVE_PRODUCTION_STATES:
            raise ProductionJobError(
                "INVALID_TASK_STATE", "任务仍在运行，不能重试失败段",
                task_id=record.task_id, status=record.status,
            )
        segment_ids = cls._retryable_segments(record)
        if not segment_ids:
            raise ProductionJobError(
                "NO_FAILED_SEGMENTS", "任务没有可重试的失败或缺失段落",
                task_id=record.task_id,
            )
        result = cls.start(
            record.project,
            {"segment_ids": segment_ids},
            record.options,
            source=record.source if record.source in VALID_SOURCES else "recovery",
            idempotency_key=idempotency_key,
            parent_task_id=record.task_id,
            recovery_of=record.task_id,
        )
        result["retry_of"] = record.task_id
        result["retry_segment_ids"] = segment_ids
        return result

    retry_failed = retry_failed_segments

    @classmethod
    def get_runtime_health(cls) -> dict[str, Any]:
        """GPU-free runtime health snapshot for Agent inspection."""
        from services.runtime_engine import read_runtime_engine_status

        status = read_runtime_engine_status()
        active_task: Optional[dict[str, Any]] = None
        for record in TaskRepository.list_tasks(status=None, task_type=None):
            if record.status in ACTIVE_PRODUCTION_STATES and record.task_type == "synthesis":
                active_task = {
                    "task_id": record.task_id,
                    "project": record.project,
                    "status": record.status,
                }
                startup = record.startup if isinstance(record.startup, dict) else {}
                active_task["startup"] = enrich_startup(startup)
                break
        result = {
            "runtime_state": status["runtime_state"],
            "owner_id": status["owner_id"],
            "pid": status["pid"],
            "engine_state": status["engine_state"],
            "engine_generation": status["engine_generation"],
            "recovery_count": status["recovery_count"],
            "last_error_code": status["last_error_code"],
            "last_recovery_at": status["last_recovery_at"],
            "updated_at": status["updated_at"],
            "runtime_updated_at": status["runtime_updated_at"],
            "status_stale": status["status_stale"],
            "active_task_id": active_task["task_id"] if active_task else None,
            "active_task": active_task,
        }
        if active_task:
            startup = active_task.get("startup") or {}
            for key in (
                "startup_phase",
                "startup_phase_started_at",
                "startup_phase_elapsed_seconds",
                "startup_slow",
                "startup_diagnostics",
            ):
                if key in startup:
                    result[key] = startup[key]
            result["task_claimed"] = bool(startup.get("task_claimed"))
            result["first_segment_started"] = bool(startup.get("first_segment_started"))
            result["first_audio_ready"] = bool(startup.get("first_audio_ready"))
        return result


__all__ = [
    "ACTIVE_PRODUCTION_STATES",
    "PRODUCTION_STATES",
    "ProductionJobError",
    "ProductionJobService",
]
