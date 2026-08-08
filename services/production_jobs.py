"""Unified production-job orchestration for Web and MCP.

The service is deliberately framework-free.  It owns the durable task
record, the in-process runtime registry, the production state machine and the
machine-readable planning contract.  ``SynthesisService`` remains the only
worker/TTS entry point; this module never reimplements synthesis.
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from lib import progress as synthesis_progress
from lib import project_paths, script_loader, segment_cache
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository

from .synthesis import SynthesisService, SynthesisState
from .voice_cast import VoiceCastResolver

logger = logging.getLogger(__name__)


PRODUCTION_TASK_TYPE = "synthesis"
PRODUCTION_STATES = (
    "pending", "running", "pausing", "paused", "cancelling",
    "cancelled", "done", "error", "interrupted",
)
ACTIVE_PRODUCTION_STATES = frozenset({
    "pending", "running", "pausing", "paused", "cancelling",
})
TERMINAL_PRODUCTION_STATES = frozenset({"cancelled", "done", "error"})
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
    """The single production-job kernel shared by Web and MCP."""

    _lock: ClassVar[threading.RLock] = threading.RLock()
    _running_tasks: ClassVar[dict[str, SynthesisState]] = {}
    _task_metadata: ClassVar[dict[str, dict[str, Any]]] = {}

    @classmethod
    def reset_runtime(cls) -> None:
        """Clear the registry for isolated tests; workers are owned by SynthesisService."""
        with cls._lock:
            cls._running_tasks.clear()
            cls._task_metadata.clear()

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
        selected_chapters: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Return public voice summary, blockers and warnings."""
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
            selected_check = VoiceCastResolver.check_chapter_roles(
                project_name, selected_chapters
            )
        except Exception as exc:
            blockers.append({
                "code": "VOICE_CAST_NOT_READY",
                "message": f"无法解析选中章节角色: {exc}",
                "fix_hint": "检查章节角色与 Character Roster 的映射。",
            })

        for issue in selected_check.get("errors", []) if isinstance(selected_check, dict) else []:
            if isinstance(issue, dict):
                blockers.append(cls._issue_blocker(issue))
        for role in selected_check.get("new_roles", []) if isinstance(selected_check, dict) else []:
            role_name = role.get("name") if isinstance(role, dict) else str(role)
            blockers.append({
                "code": "ROLE_NOT_IN_ROSTER",
                "message": f"角色未加入 Character Roster: {role_name}",
                "role": role_name,
                "fix_hint": "先新增角色并完成 Voice Cast 绑定。",
            })
        for role_id in selected_check.get("unbound_roles", []) if isinstance(selected_check, dict) else []:
            blockers.append({
                "code": "ROLE_UNBOUND",
                "message": f"角色尚未绑定声音: {role_id}",
                "role_id": role_id,
                "fix_hint": "为该角色绑定 voice_asset_id 并重新锁定 Voice Cast。",
            })

        mode = str(status.get("mode") or "legacy_manual")
        cast_locked = bool(status.get("cast_locked", False))
        selected_ready = bool(
            selected_check.get("synthesis_ready", status.get("synthesis_ready", False))
        )
        if mode == "legacy_manual":
            # Legacy projects may contain roles outside the selected chapter
            # scope.  Production readiness is about required roles in this
            # job, not unrelated future chapters.
            required_names = {
                str(segment.get("role") or segment.get("speaker") or "").strip()
                for chapter in selected_chapters
                if isinstance(chapter, dict)
                for segment in chapter.get("segments", [])
                if isinstance(segment, dict)
                and str(segment.get("role") or segment.get("speaker") or "").strip()
            }
            bound_names = {
                str(item.get("name") or "").strip()
                for item in status.get("roles", [])
                if isinstance(item, dict) and item.get("bound")
            }
            missing_names = sorted(required_names - bound_names)
            for name in missing_names:
                if not any(
                    item.get("code") == "ROLE_UNBOUND" and item.get("role") == name
                    for item in blockers
                ):
                    blockers.append({
                        "code": "ROLE_UNBOUND",
                        "message": f"角色尚未绑定声音: {name}",
                        "role": name,
                        "fix_hint": "在角色与声音页面完成绑定。",
                    })
            selected_ready = not missing_names
        if mode == "voice_cast" and not cast_locked:
            blockers.append({
                "code": "VOICE_CAST_NOT_READY",
                "message": "Voice Cast 尚未锁定，不能开始正式生产。",
                "fix_hint": "完成所有角色绑定后调用 finalize_voice_cast。",
            })
        if not selected_ready:
            if not any(item.get("code") in {"ROLE_UNBOUND", "ROLE_NOT_IN_ROSTER", "VOICE_CAST_NOT_READY"} for item in blockers):
                blockers.append({
                    "code": "VOICE_CAST_NOT_READY",
                    "message": "所选范围尚未达到合成就绪状态。",
                    "fix_hint": "完成所需角色的声音绑定并重新检查。",
                })
        warnings.extend(
            item for item in status.get("warnings", [])
            if isinstance(item, dict)
        )
        return {
            "locked": cast_locked,
            "bound": int(status.get("bound", 0) or 0),
            "unbound": int(status.get("unbound", 0) or 0),
            "mode": mode,
            "synthesis_ready": bool(
                selected_ready
            ) and not blockers,
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
        selected_chapters = [
            chapter_map[chapter_id]
            for chapter_id in chapter_map
            if chapter_id in selected_chapter_ids
        ]
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
            name, selected_chapters
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
            "already_completed": completed,
            "remaining": remaining,
            "failed": len(failed_ids),
            "failed_segment_ids": failed_ids,
            "voice_cast": voice_cast,
            "blockers": blockers,
            "warnings": warnings,
        }

    plan_production = plan

    @classmethod
    def _record_progress(
        cls,
        state: SynthesisState,
        record: TaskRecord,
    ) -> dict[str, Any]:
        metadata = cls._task_metadata.get(state.task_id, {})
        chapter = metadata.get("segment_to_chapter", {}).get(state.current_segment)
        total = max(int(state.total or record.progress.get("total", 0) or 0), 0)
        completed = max(int(state.completed or 0), 0)
        failed_ids = sorted({str(item) for item in state.failed_segment_ids if str(item)})
        failed = len(failed_ids)
        percent = round((completed / total) * 100, 1) if total else 0.0
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "percent": percent,
            "current_chapter": chapter,
            "current_segment": state.current_segment,
        }

    @classmethod
    def _on_state_update(cls, state: SynthesisState) -> None:
        """Persist a state callback from the SynthesisService worker."""
        with cls._lock:
            record = TaskRepository.load_task(state.task_id)
            if record is None:
                return
            if state.status == "done" and state.failed_segment_ids:
                # A queue can finish its generator while individual segments
                # failed.  Production exposes that outcome as error so retry
                # is discoverable through both interfaces.
                state.status = "error"
                state.error = "存在失败段落"
            record.status = state.status
            record.progress = cls._record_progress(state, record)
            record.failed_segment_ids = sorted({str(item) for item in state.failed_segment_ids})
            record.error_summary = _public_error(
                state.error or ("存在失败段落" if record.failed_segment_ids else "")
            )[:500]
            record.updated_at = _now()
            if state.status == "running" and not record.started_at:
                record.started_at = record.updated_at
            if state.status in TERMINAL_PRODUCTION_STATES:
                record.finished_at = record.updated_at
            TaskRepository.save_task(record)
            if state.status in TERMINAL_PRODUCTION_STATES:
                cls._running_tasks.pop(state.task_id, None)
                cls._task_metadata.pop(state.task_id, None)

    @classmethod
    def _runtime_state(
        cls,
        record: TaskRecord,
        plan: dict[str, Any],
    ) -> SynthesisState:
        _meta, _script, _bindings_document = cls._project_data(record.project)
        selected_chapters = record.scope.get("chapter_ids", []) if isinstance(record.scope, dict) else []
        selected_segments = record.scope.get("segment_ids", []) if isinstance(record.scope, dict) else []
        state = SynthesisState(
            task_id=record.task_id,
            project=record.project,
            status="pending",
            total=int(plan.get("segments", 0) or 0),
            completed=int(plan.get("already_completed", 0) or 0),
            failed_segment_ids=list(plan.get("failed_segment_ids", []) or []),
        )
        state.on_update = cls._on_state_update
        state.selected_chapters = list(selected_chapters) or None
        state.selected_segment_ids = list(selected_segments) or None
        try:
            state.segment_states = synthesis_progress.build_segment_states(
                record.project,
                list(selected_chapters) if selected_chapters else None,
            )
        except Exception:
            state.segment_states = []
        cls._task_metadata[record.task_id] = {
            "segment_to_chapter": cls._script_index(_script)[2],
        }
        cls._running_tasks[record.task_id] = state
        record.status = "pending"
        record.progress = {
            "total": state.total,
            "completed": state.completed,
            "failed": len(state.failed_segment_ids),
            "percent": round((state.completed / state.total) * 100, 1) if state.total else 0.0,
            "current_chapter": None,
            "current_segment": None,
        }
        record.updated_at = _now()
        TaskRepository.save_task(record)
        return state

    @classmethod
    def _launch(cls, record: TaskRecord, plan: dict[str, Any]) -> None:
        """Register and submit a task to the existing synthesis worker."""
        state = cls._runtime_state(record, plan)
        try:
            _meta, _script, bindings_document = cls._project_data(record.project)
            bindings = (
                bindings_document.get("bindings", {})
                if isinstance(bindings_document, dict) else {}
            )
            options = record.options if isinstance(record.options, dict) else {}
            selected_chapters = record.scope.get("chapter_ids", []) if isinstance(record.scope, dict) else []
            selected_segments = record.scope.get("segment_ids", []) if isinstance(record.scope, dict) else []
            synthesis_kwargs = {
                "num_beams": int(options.get("num_beams", 2) or 2),
                "emotion": options.get("emotion"),
                "emo_alpha": options.get("emo_alpha"),
                "speech_rate": options.get("speech_rate"),
                "selected_chapters": selected_chapters or None,
                "selected_segment_ids": selected_segments or None,
                "persist_task": False,
            }
            try:
                SynthesisService.start(
                    state, record.project, bindings, **synthesis_kwargs
                )
            except TypeError as exc:
                # Keep adapters friendly to small test/integration doubles
                # implementing the pre-Phase-3 SynthesisService signature.
                if "unexpected keyword" not in str(exc) and "keyword argument" not in str(exc):
                    raise
                fallback = dict(synthesis_kwargs)
                fallback.pop("selected_segment_ids", None)
                fallback.pop("persist_task", None)
                SynthesisService.start(state, record.project, bindings, **fallback)
        except Exception as exc:
            cls._running_tasks.pop(record.task_id, None)
            cls._task_metadata.pop(record.task_id, None)
            record.status = "error"
            record.error_summary = _public_error(exc)[:500]
            record.updated_at = _now()
            record.finished_at = record.updated_at
            TaskRepository.save_task(record)
            raise

    @classmethod
    def _active_or_interrupted(cls, project_name: str) -> Optional[TaskRecord]:
        """Return the current active task and repair stale runtime claims."""
        records = TaskRepository.list_tasks(project=project_name, task_type=PRODUCTION_TASK_TYPE)
        for record in records:
            if record.status not in ACTIVE_PRODUCTION_STATES:
                continue
            if record.task_id not in cls._running_tasks:
                record.status = "interrupted"
                record.error_summary = record.error_summary or "应用重启后未找到运行中的任务实例"
                record.updated_at = _now()
                TaskRepository.save_task(record)
                continue
            return record
        return None

    @classmethod
    def _existing_idempotent(
        cls, project_name: str, idempotency_key: str
    ) -> Optional[TaskRecord]:
        record = TaskRepository.find_by_idempotency(
            project_name, PRODUCTION_TASK_TYPE, idempotency_key
        )
        if record is None:
            return None
        # A cancelled task is explicitly terminal and may be intentionally
        # started again with the same human key; all other rows are replayable.
        if record.status == "cancelled":
            return None
        return record

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
        }
        return result

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
        plan = cls.plan(name, scope)
        if not plan["ready"]:
            raise ProductionJobError(
                "PRODUCTION_BLOCKED", "生产前检查未通过",
                project_name=name, blockers=plan["blockers"], plan=plan,
            )
        key = str(idempotency_key or "").strip()
        with cls._lock:
            replay = cls._existing_idempotent(name, key)
            if replay is not None:
                return {
                    "created": False,
                    **cls.get_task_snapshot(replay.task_id),
                }
            active = cls._active_or_interrupted(name)
            if active is not None:
                raise ProductionJobError(
                    "PROJECT_HAS_ACTIVE_TASK",
                    "同一项目已有生产任务运行中",
                    project_name=name,
                    task_id=active.task_id,
                    status=active.status,
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
                options=cls._normalize_options(options),
                progress={
                    "total": plan["segments"],
                    "completed": plan["already_completed"],
                    "failed": plan["failed"],
                    "percent": round(
                        (plan["already_completed"] / plan["segments"]) * 100, 1
                    ) if plan["segments"] else 0.0,
                    "current_chapter": None,
                    "current_segment": None,
                },
                failed_segment_ids=list(plan.get("failed_segment_ids", [])),
                attempt=1,
                idempotency_key=key,
                created_at=now,
                updated_at=now,
                parent_task_id=str(parent_task_id or ""),
                recovery_of=str(recovery_of or ""),
            )
            TaskRepository.save_task(record)
            cls._launch(record, plan)
            snapshot = cls.get_task_snapshot(task_id)
            return {"created": True, **snapshot}

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
        if record.status in ACTIVE_PRODUCTION_STATES and record.task_id not in cls._running_tasks:
            record.status = "interrupted"
            record.error_summary = record.error_summary or "应用重启后未找到运行中的任务实例"
            record.updated_at = _now()
            TaskRepository.save_task(record)
        return record

    @classmethod
    def _task_snapshot(cls, record: TaskRecord) -> dict[str, Any]:
        state = cls._running_tasks.get(record.task_id)
        if state is not None:
            progress = cls._record_progress(state, record)
            status = state.status
            failed_ids = sorted({str(item) for item in state.failed_segment_ids})
            log_lines = [_public_error(line) for line in state.log_lines[-50:]]
        else:
            progress = dict(record.progress or {})
            status = record.status
            failed_ids = list(record.failed_segment_ids or [])
            log_lines = []
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
            "error_summary": _public_error(record.error_summary),
        }
        if log_lines:
            response["log_lines"] = log_lines
        return response

    @classmethod
    def get_task_snapshot(cls, task_id: str) -> dict[str, Any]:
        with cls._lock:
            record = cls._mark_stale(cls._get_record(task_id))
            return cls._task_snapshot(record)

    get_production_task = get_task_snapshot

    @classmethod
    def get_runtime_state(cls, task_id: str) -> Optional[SynthesisState]:
        """Return the current-process state for Web rendering only.

        The object is never serialized or exposed by MCP; callers may use it
        to render the existing queue rows without making the session the task
        owner.
        """
        with cls._lock:
            return cls._running_tasks.get(str(task_id or "").strip())

    @classmethod
    def list_tasks(
        cls,
        project_name: Optional[str] = None,
        status: Optional[str] = None,
        source: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        with cls._lock:
            records = TaskRepository.list_tasks(
                project=project_name or None,
                task_type=PRODUCTION_TASK_TYPE,
                source=source or None,
            )
            snapshots = [cls._task_snapshot(cls._mark_stale(record)) for record in records]
            if status:
                snapshots = [
                    snapshot for snapshot in snapshots
                    if snapshot.get("status") == status
                ]
            return snapshots

    list_production_tasks = list_tasks

    @classmethod
    def get_active_task(cls, project_name: str) -> Optional[dict[str, Any]]:
        with cls._lock:
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
        with cls._lock:
            record = cls._mark_stale(cls._get_record(task_id))
            if record.status == "paused":
                return cls._task_snapshot(record)
            if record.status not in {"pending", "running", "pausing"}:
                raise ProductionJobError(
                    "INVALID_TASK_STATE", "当前任务不能暂停",
                    task_id=record.task_id, status=record.status,
                )
            state = cls._running_tasks.get(record.task_id)
            if state is None:
                record = cls._mark_stale(record)
                return cls._task_snapshot(record)
            SynthesisService.pause(state)
            return cls._task_snapshot(TaskRepository.load_task(record.task_id) or record)

    pause_production = pause

    @classmethod
    def cancel(cls, task_id: str) -> dict[str, Any]:
        with cls._lock:
            record = cls._mark_stale(cls._get_record(task_id))
            if record.status in TERMINAL_PRODUCTION_STATES:
                return cls._task_snapshot(record)
            if record.status == "interrupted":
                # There is no worker left to observe a cooperative cancel.
                record.status = "cancelled"
                record.updated_at = _now()
                record.finished_at = record.updated_at
                TaskRepository.save_task(record)
                return cls._task_snapshot(record)
            state = cls._running_tasks.get(record.task_id)
            if state is None:
                raise ProductionJobError(
                    "RUNTIME_TASK_NOT_FOUND", "当前进程找不到任务运行实例",
                    task_id=record.task_id, status=record.status,
                )
            SynthesisService.cancel(state)
            return cls._task_snapshot(TaskRepository.load_task(record.task_id) or record)

    cancel_production = cancel

    @classmethod
    def _resume_interrupted(cls, record: TaskRecord) -> dict[str, Any]:
        plan = cls.plan(record.project, cls._scope_for_record(record))
        if not plan["ready"]:
            raise ProductionJobError(
                "PRODUCTION_BLOCKED", "恢复前检查未通过",
                project_name=record.project, blockers=plan["blockers"], plan=plan,
            )
        record.attempt = max(int(record.attempt or 1), 1) + 1
        record.source = "recovery"
        record.recovery_of = record.task_id
        record.status = "pending"
        record.finished_at = ""
        record.updated_at = _now()
        TaskRepository.save_task(record)
        cls._launch(record, plan)
        return cls._task_snapshot(TaskRepository.load_task(record.task_id) or record)

    @classmethod
    def resume(cls, task_id: str) -> dict[str, Any]:
        with cls._lock:
            record = cls._mark_stale(cls._get_record(task_id))
            if record.status == "interrupted":
                return cls._resume_interrupted(record)
            if record.status != "paused":
                raise ProductionJobError(
                    "INVALID_TASK_STATE", "当前任务不能恢复",
                    task_id=record.task_id, status=record.status,
                )
            state = cls._running_tasks.get(record.task_id)
            if state is None:
                record.status = "interrupted"
                record.updated_at = _now()
                TaskRepository.save_task(record)
                return cls._resume_interrupted(record)
            SynthesisService.resume(state)
            return cls._task_snapshot(TaskRepository.load_task(record.task_id) or record)

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
        with cls._lock:
            record = cls._mark_stale(cls._get_record(task_id))
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


__all__ = [
    "ACTIVE_PRODUCTION_STATES",
    "PRODUCTION_STATES",
    "ProductionJobError",
    "ProductionJobService",
]
