"""Derived book workflow state for Web and Agent control planes."""
from __future__ import annotations

from typing import Any, ClassVar

from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from repositories.task_repo import TaskRepository
from services.delivery import compute_delivery_input_snapshot
from services.production_jobs import ProductionJobService
from services.quality import QualityService
from services.voice_cast import VoiceCastResolver


class WorkflowService:
    """Derive workflow stage from durable facts instead of persisting a second stage."""

    _ACTION_CONTRACTS: ClassVar[dict[str, dict[str, Any]]] = {
        "complete_voice_cast": {
            "action_type": "human",
            "requires_confirmation": True,
            "retryable": False,
            "recommended_poll_seconds": 0,
            "terminal": False,
        },
        "wait_for_recovery": {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 10,
            "terminal": False,
        },
        "check_production": {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 10,
            "terminal": False,
        },
        "retry_task": {
            "action_type": "auto",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 10,
            "terminal": False,
        },
        "inspect_runtime_health": {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 0,
            "terminal": False,
        },
        "cancel_task": {
            "action_type": "human",
            "requires_confirmation": True,
            "retryable": False,
            "recommended_poll_seconds": 0,
            "terminal": False,
        },
        "check_repair": {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 10,
            "terminal": False,
        },
        "retry_failed_segments": {
            "action_type": "auto",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 10,
            "terminal": False,
        },
        "resolve_failed_task": {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 0,
            "terminal": False,
        },
        "repair_segments": {
            "action_type": "human",
            "requires_confirmation": True,
            "retryable": False,
            "recommended_poll_seconds": 0,
            "terminal": False,
        },
        "produce_remaining": {
            "action_type": "auto",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 10,
            "terminal": False,
        },
        "check_export": {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 10,
            "terminal": False,
        },
        "inspect_delivery": {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": False,
            "recommended_poll_seconds": 0,
            "terminal": True,
        },
        "plan_export": {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 0,
            "terminal": False,
        },
    }

    @classmethod
    def _action(
        cls,
        action_name: str,
        tool: str,
        project_name: str,
        reason: str,
        *,
        count: int = 0,
        include_project_name: bool = True,
        **arguments: Any,
    ) -> dict[str, Any]:
        contract = dict(cls._ACTION_CONTRACTS.get(action_name, {
            "action_type": "observe",
            "requires_confirmation": False,
            "retryable": True,
            "recommended_poll_seconds": 0,
            "terminal": False,
        }))
        tool_arguments = dict(arguments)
        if include_project_name:
            tool_arguments = {"project_name": project_name, **tool_arguments}
        return {
            "action": action_name,
            "tool": tool,
            "arguments": tool_arguments,
            "reason": reason,
            "count": int(count or 0),
            **contract,
        }

    @staticmethod
    def _unique_failed_task_id(
        project_name: str,
        failed_segment_ids: set[str],
    ) -> str | None:
        """Resolve a retry source only from explicit durable failed IDs.

        A project can have several historical synthesis attempts.  Scope or
        recency alone is not enough to infer which task owns a failure, so an
        action is directly executable only when exactly one non-active task
        explicitly records an overlapping ``failed_segment_ids`` set.
        """
        if not failed_segment_ids:
            return None
        candidates: list[str] = []
        for record in TaskRepository.list_tasks(
            project=project_name,
            task_type="synthesis",
        ):
            if record.status in {"pending", "running", "pausing", "paused", "recovering", "cancelling"}:
                continue
            recorded = {
                str(item).strip()
                for item in (record.failed_segment_ids or [])
                if str(item).strip()
            }
            if recorded & failed_segment_ids:
                candidates.append(str(record.task_id))
        unique = sorted({item for item in candidates if item})
        return unique[0] if len(unique) == 1 else None

    @classmethod
    def get_state(cls, project_name: str) -> dict[str, Any]:
        project = str(project_name or "").strip()
        if not project:
            raise ValueError("project_name 不能为空")
        meta, script, bindings = ProjectRepository.load_project(project)
        chapters = [
            chapter for chapter in script.get("chapters", [])
            if isinstance(chapter, dict)
        ]
        segments = [
            segment
            for chapter in chapters
            for segment in chapter.get("segments", [])
            if isinstance(segment, dict)
        ]
        total = len(segments)
        statuses = dict(getattr(meta, "segments_status", {}) or {})
        done = sum(statuses.get(str(segment.get("id"))) == "done" for segment in segments)
        failed = sum(statuses.get(str(segment.get("id"))) == "failed" for segment in segments)
        remaining = max(total - done, 0)

        try:
            cast = VoiceCastResolver.get_voice_binding_status(project)
        except Exception:  # noqa: BLE001  # legacy projects must remain queryable
            legacy_bindings = (
                bindings.get("bindings", {}) if isinstance(bindings, dict) else {}
            )
            roles = list((script.get("voices") or {}).keys())
            bound = sum(bool(legacy_bindings.get(role)) for role in roles)
            cast = {
                "bound": bound,
                "unbound": max(len(roles) - bound, 0),
                "cast_locked": False,
                "cast_ready": bool(roles) and bound == len(roles),
                "synthesis_ready": bool(roles) and bound == len(roles),
                "runtime_status": "unknown",
                "engine_state": "unknown",
                "engine_ready": False,
                "mode": "legacy_manual",
            }
        unbound = int(cast.get("unbound", 0) or 0)
        cast_ready = bool(
            cast.get("cast_ready", cast.get("synthesis_ready", False))
        )
        engine_ready = bool(cast.get("engine_ready", False))
        runtime_status = str(cast.get("runtime_status") or "unknown")
        engine_state = str(cast.get("engine_state") or "unknown")

        active_task = ProductionJobService.get_active_task(project)
        attention_tasks = TaskRepository.list_tasks(
            project=project,
            task_type="synthesis",
            status="needs_attention",
        )
        attention_task = attention_tasks[0] if attention_tasks else None
        repairs = QualityRepository.list_history(project, "repair_history")
        active_repairs = [
            item for item in repairs
            if item.get("status") in {
                "preparing", "submitting", "pending", "running",
                "pausing", "paused", "cancelling",
            }
        ]
        inventory = QualityService.get_active_revision_inventory(project)
        audio_by_segment = {
            str(item.get("segment_id") or ""): item
            for item in inventory.get("segments", [])
            if isinstance(item, dict)
        }
        missing_audio_ids = [
            str(segment.get("id") or "")
            for segment in segments
            if statuses.get(str(segment.get("id") or "")) == "done"
            and not audio_by_segment.get(str(segment.get("id") or ""), {}).get("audio_revision")
        ]
        invalid_audio_ids = [
            str(segment.get("id") or "")
            for segment in segments
            if statuses.get(str(segment.get("id") or "")) == "done"
            and audio_by_segment.get(str(segment.get("id") or ""), {}).get("audio_revision")
            and not audio_by_segment.get(str(segment.get("id") or ""), {}).get("audio_valid")
        ]

        exports = QualityRepository.list_history(project, "export_jobs")
        active_exports = []
        for item in exports:
            if item.get("status") not in {"pending", "running"}:
                continue
            task_id = str(item.get("task_id") or "")
            # Since #31, SQLite TaskRepository is the only source of truth for
            # live exports.  #30 history rows without a durable task_id are
            # retained as audit history but can never keep the workflow in an
            # exporting state after a crash or upgrade.
            if not task_id:
                continue
            task = TaskRepository.load_task(task_id)
            if task is None or task.status not in {
                "pending", "running", "cancelling",
            }:
                continue
            active_exports.append(item)
        history_task_ids = {
            str(item.get("task_id") or "") for item in active_exports
        }
        for task in TaskRepository.list_tasks(project=project, task_type="export"):
            if task.status in {"pending", "running", "cancelling"} and task.task_id not in history_task_ids:
                active_exports.append({
                    "export_id": task.task_id,
                    "task_id": task.task_id,
                    "status": task.status,
                })
        manifests = QualityRepository.list_history(project, "delivery_manifests")
        try:
            delivery_snapshot = compute_delivery_input_snapshot(project)
            current_delivery_hash = str(
                delivery_snapshot.get("delivery_input_hash") or ""
            )
        except Exception:  # noqa: BLE001  # delivery is best-effort derived state
            # Workflow state should remain queryable for a partially-created or
            # legacy project.  An unavailable snapshot can never make an old
            # manifest current, so it is safe to treat delivery as stale.
            delivery_snapshot = {}
            current_delivery_hash = ""
        delivered = next(
            (
                item
                for item in manifests
                if item.get("ready") is True
                and current_delivery_hash
                and (
                    not str(item.get("export_id") or "")
                    or (
                        (manifest_task := TaskRepository.load_task(
                            str(item.get("export_id") or "")
                        )) is not None
                        and manifest_task.status == "done"
                    )
                )
                and str(
                    item.get("delivery_input_hash")
                    or item.get("freshness_hash")
                    or item.get("delivery_input_snapshot_hash")
                    or item.get("input_snapshot_hash")
                    or ""
                )
                == current_delivery_hash
            ),
            None,
        )
        latest_manifest = manifests[0] if manifests else None

        blockers: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        if not cast_ready:
            stage = "prepared" if not (script.get("voices") or {}) else "cast_pending"
            blockers.append({
                "code": "VOICE_CAST_NOT_READY",
                "message": f"还有 {unbound} 个角色未完成声音绑定。",
                "count": unbound,
            })
            actions.append(cls._action(
                "complete_voice_cast",
                "configure_voice_cast",
                project,
                "配置角色与声音绑定；完成后必须由用户显式确认 Voice Cast。",
                count=unbound,
            ))
        elif active_task:
            if active_task.get("status") == "recovering":
                stage = "recovering"
                actions.append(cls._action(
                    "wait_for_recovery",
                    "get_production_task",
                    project,
                    "TTS 引擎正在自动恢复，等待 recovery 完成，不要重复提交任务。",
                    task_id=active_task.get("task_id"),
                    include_project_name=False,
                ))
            else:
                stage = "producing"
                actions.append(cls._action(
                    "check_production",
                    "get_production_task",
                    project,
                    "生产任务正在运行。",
                    task_id=active_task.get("task_id"),
                    include_project_name=False,
                ))
        elif attention_task:
            stage = "needs_attention"
            actions.append(cls._action(
                "retry_task",
                "control_production",
                project,
                "自动恢复已耗尽，重试剩余段落。",
                task_id=attention_task.task_id,
                action="resume",
                include_project_name=False,
            ))
            actions.append(cls._action(
                "inspect_runtime_health",
                "get_runtime_health",
                project,
                "检查 TTS 运行时与引擎健康状态。",
                include_project_name=False,
            ))
            actions.append(cls._action(
                "cancel_task",
                "control_production",
                project,
                "放弃当前任务。",
                task_id=attention_task.task_id,
                action="cancel",
                include_project_name=False,
            ))
        elif active_repairs:
            stage = "needs_repair"
            actions.append(cls._action(
                "check_repair",
                "get_repair_task",
                project,
                "段落修复任务正在运行。",
                repair_id=active_repairs[0].get("repair_id"),
            ))
        elif failed or missing_audio_ids or invalid_audio_ids:
            stage = "needs_repair"
            if failed:
                failed_segment_ids = {
                    str(segment.get("id") or "")
                    for segment in segments
                    if statuses.get(str(segment.get("id"))) == "failed"
                }
                retry_task_id = cls._unique_failed_task_id(
                    project, failed_segment_ids
                )
                blockers.append({
                    "code": "SYNTHESIS_FAILED",
                    "message": f"有 {failed} 个段落生产失败。",
                    "count": failed,
                })
                if retry_task_id:
                    actions.append(cls._action(
                        "retry_failed_segments",
                        "retry_failed_segments",
                        project,
                        "直接重试唯一记录该失败段的生产任务。",
                        count=failed,
                        task_id=retry_task_id,
                        include_project_name=False,
                    ))
                else:
                    actions.append(cls._action(
                        "resolve_failed_task",
                        "list_production_tasks",
                        project,
                        "无法唯一关联失败段落所属的 synthesis task；先观察错误任务列表，再将明确 task_id 传给 retry_failed_segments。",
                        count=failed,
                        status="error",
                    ))
            if missing_audio_ids:
                blockers.append({
                    "code": "AUDIO_MISSING",
                    "message": f"有 {len(missing_audio_ids)} 个已完成段落缺少 active revision。",
                    "count": len(missing_audio_ids),
                    "segment_ids": missing_audio_ids,
                })
            if invalid_audio_ids:
                blockers.append({
                    "code": "AUDIO_INVALID",
                    "message": f"有 {len(invalid_audio_ids)} 个 active revision WAV 无法读取。",
                    "count": len(invalid_audio_ids),
                    "segment_ids": invalid_audio_ids,
                })
            audio_problem_ids = missing_audio_ids + [
                item for item in invalid_audio_ids if item not in missing_audio_ids
            ]
            if audio_problem_ids:
                actions.append(cls._action(
                    "repair_segments",
                    "regenerate_segments",
                    project,
                    "重新生成缺少或无效音频的段落。",
                    count=len(audio_problem_ids),
                    segment_ids=audio_problem_ids,
                ))
        elif remaining:
            stage = "ready_for_production"
            actions.append(cls._action(
                "produce_remaining",
                "start_production",
                project,
                f"生产剩余 {remaining} 个段落。",
                count=remaining,
                scope={"all": True},
            ))
        elif total > 0:
            if active_exports:
                stage = "exporting"
                actions.append(cls._action(
                    "check_export",
                    "get_export_task",
                    project,
                    "导出任务正在运行。",
                    export_id=active_exports[0].get("export_id"),
                ))
            elif delivered:
                stage = "delivered"
                actions.append(cls._action(
                    "inspect_delivery",
                    "get_delivery_manifest",
                    project,
                    "当前项目输入与最近一次可交付成品一致。",
                ))
            else:
                stage = "ready_for_export"
                reason = (
                    "历史 Delivery Manifest 缺少 freshness hash 或输入已变化，"
                    "需要重新导出。"
                    if latest_manifest
                    else "所有必需段落已完成且 active revision 有效，可以规划交付。"
                )
                actions.append(cls._action(
                    "plan_export",
                    "plan_export",
                    project,
                    reason,
                ))
        else:
            stage = "ready_for_production"
            actions.append(cls._action(
                "produce_remaining",
                "start_production",
                project,
                "项目还没有可生产的段落。",
                count=0,
                scope={"all": True},
            ))

        return {
            "project": project,
            "stage": stage,
            "summary": {
                "chapters": len(chapters),
                "segments": total,
                "completed": done,
                "remaining": remaining,
                "failed": failed,
                "roles_bound": int(cast.get("bound", 0) or 0),
                "roles_unbound": unbound,
                "cast_ready": cast_ready,
                "engine_ready": engine_ready,
                "engine_state": engine_state,
                "runtime_status": runtime_status,
                "active_revisions": int(inventory.get("summary", {}).get("active_revisions", 0) or 0),
                "valid_audio": int(inventory.get("summary", {}).get("valid_audio", 0) or 0),
                "missing_audio": len(missing_audio_ids),
                "invalid_audio": len(invalid_audio_ids),
                "active_production_task": (
                    active_task.get("task_id") if active_task else None
                ),
                "active_repairs": len(active_repairs),
                "active_exports": len(active_exports),
                "delivered": bool(delivered),
                "delivery_input_hash": current_delivery_hash,
                "delivery_manifest_id": (
                    delivered.get("manifest_id")
                    if delivered else (
                        latest_manifest.get("manifest_id")
                        if latest_manifest else None
                    )
                ),
                "delivery_manifest_stale": bool(
                    latest_manifest and not delivered
                ),
            },
            "blockers": blockers,
            "next_actions": actions,
        }

    get_workflow_state = get_state


__all__ = ["WorkflowService"]
