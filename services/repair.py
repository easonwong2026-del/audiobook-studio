"""Revision-safe segment repair orchestration.

Repair never calls the TTS engine directly.  It archives the currently active
audio, creates pending revisions, marks the requested segments eligible, and
submits one exact segment scope through ``ProductionJobService``.
"""
from __future__ import annotations

import os
import shutil
import hashlib
from typing import Any

from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from services.production_jobs import ProductionJobError, ProductionJobService
from services.quality import QualityService
from services.voice_assets import VoiceAssetService
from lib.tts_profile import resolve_profile


ACTIVE_TASK_STATES = frozenset({
    "pending",
    "running",
    "pausing",
    "paused",
    "cancelling",
})
TERMINAL_TASK_STATES = frozenset({"done", "error", "cancelled", "interrupted"})


def _repair_engine_snapshot(project_name: str) -> dict[str, Any]:
    """Resolve the repair engine: newest production task provenance first.

    Falls back to Settings current default only when the project has no
    production task provenance.  The returned profile is frozen into the
    repair's production task so the regenerated segment uses exactly the
    engine identity recorded in its revision provenance.
    """
    from lib.segment_cache import project_task_engine_snapshot

    provenance = project_task_engine_snapshot(project_name)
    if provenance:
        return provenance
    return resolve_profile({})


class RepairError(ValueError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": str(self), **self.details}}


class RepairService:
    """Public repair service used by Web/MCP adapters."""

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record.get(key)
            for key in (
                "repair_id",
                "project",
                "segment_ids",
                "revision_ids",
                "task_id",
                "status",
                "options",
                "requested_by",
                "note",
                "idempotency_key",
                "created_at",
                "updated_at",
                "finished_at",
                "error",
                "result",
            )
            if key in record
        }

    @staticmethod
    def _normalize_segment_ids(project_name: str, segment_ids: list[str]) -> list[str]:
        _meta, script, _bindings = ProjectRepository.load_project(project_name)
        known = {
            str(segment.get("id"))
            for chapter in script.get("chapters", [])
            if isinstance(chapter, dict)
            for segment in chapter.get("segments", [])
            if isinstance(segment, dict)
        }
        result: list[str] = []
        seen: set[str] = set()
        for value in segment_ids or []:
            segment_id = str(value or "").strip()
            if not segment_id or segment_id in seen:
                continue
            if segment_id not in known:
                raise RepairError(
                    "SEGMENT_NOT_FOUND",
                    f"段落不存在: {segment_id}",
                    segment_id=segment_id,
                )
            result.append(segment_id)
            seen.add(segment_id)
        if not result:
            raise RepairError("SEGMENTS_REQUIRED", "至少选择一个需要修复的段落")
        return result

    @staticmethod
    def _options(
        emotion: str | None,
        emo_alpha: float | None,
        speech_rate: float | None,
        num_beams: int,
        project_name: str = "",
    ) -> dict[str, Any]:
        try:
            beams = max(int(num_beams), 1)
        except (TypeError, ValueError) as exc:
            raise RepairError("INVALID_OPTIONS", "num_beams 必须是正整数") from exc
        options: dict[str, Any] = {
            "num_beams": beams,
            "emotion": str(emotion) if emotion is not None else None,
            "emo_alpha": None,
            "speech_rate": None,
        }
        if emo_alpha is not None:
            try:
                options["emo_alpha"] = float(emo_alpha)
            except (TypeError, ValueError) as exc:
                raise RepairError("INVALID_OPTIONS", "emo_alpha 必须是数字") from exc
        if speech_rate is not None:
            try:
                options["speech_rate"] = float(speech_rate)
            except (TypeError, ValueError) as exc:
                raise RepairError("INVALID_OPTIONS", "speech_rate 必须是数字") from exc
        # Engine selection follows the same single rule as production: use the
        # newest production task's frozen engine when available (provenance),
        # otherwise Settings current default.  This prevents a repair from
        # silently regenerating a v2 book under v2.5 (or reusing another
        # engine's cache key) after Settings changed.
        options["engine_snapshot"] = _repair_engine_snapshot(project_name)
        options["engine_selection_source"] = "explicit"
        return options

    @classmethod
    def start(
        cls,
        project_name: str,
        segment_ids: list[str],
        *,
        emotion: str | None = None,
        emo_alpha: float | None = None,
        speech_rate: float | None = None,
        num_beams: int = 2,
        voice_override: str | None = None,
        requested_by: str = "system",
        source: str = "system",
        note: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        project = str(project_name or "").strip()
        if not project:
            raise RepairError("PROJECT_REQUIRED", "project_name 不能为空")
        ids = cls._normalize_segment_ids(project, segment_ids)
        options = cls._options(emotion, emo_alpha, speech_rate, num_beams, project)
        key = str(idempotency_key or "").strip()
        if key:
            replay = QualityRepository.find_history_by_field(
                project, "repair_history", "idempotency_key", key
            )
            if replay:
                return {"created": False, **cls._public(replay)}

        repair = QualityRepository.create_history_record(
            project,
            "repair_history",
            "repair",
            {
                "project": project,
                "segment_ids": ids,
                "revision_ids": [],
                "task_id": "",
                "status": "preparing",
                "options": options,
                "requested_by": str(requested_by or "system"),
                "source": str(source or "system"),
                "note": str(note or ""),
                "idempotency_key": key,
                "prepared": [],
                "error": "",
                "result": {},
            },
        )
        repair_id = repair["repair_id"]
        prepared: list[dict[str, Any]] = []
        revision_ids: list[str] = []
        meta, _script, _bindings = ProjectRepository.load_project(project)
        original_statuses = dict(getattr(meta, "segments_status", {}) or {})
        params = {
            "emotion": options.get("emotion"),
            "emo_alpha": options.get("emo_alpha"),
            "speech_rate": options.get("speech_rate"),
            "engine_snapshot": options.get("engine_snapshot"),
        }
        override_path = ""
        override_relative = ""
        if voice_override:
            candidate = os.path.abspath(os.path.expanduser(str(voice_override)))
            if os.path.isfile(candidate):
                override_path = candidate
            else:
                try:
                    override_path = VoiceAssetService.resolve_path(str(voice_override))
                except Exception as exc:
                    raise RepairError(
                        "VOICE_OVERRIDE_NOT_FOUND",
                        "临时声音覆盖不存在或不可读取",
                        voice_override=str(voice_override),
                    ) from exc
            quality_root = os.path.dirname(QualityRepository.state_path(project))
            voice_dir = os.path.join(quality_root, "repair_voices")
            os.makedirs(voice_dir, exist_ok=True)
            extension = os.path.splitext(override_path)[1].lower() or ".wav"
            digest = hashlib.sha256()
            with open(override_path, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            copied = os.path.join(voice_dir, f"{digest.hexdigest()}{extension}")
            if not os.path.isfile(copied):
                shutil.copy2(override_path, copied)
            override_path = copied
            override_relative = QualityService._project_relative(project, copied)
            options["voice_overrides"] = {
                segment_id: override_relative for segment_id in ids
            }
        try:
            for segment_id in ids:
                QualityService.archive_active_revision(project, segment_id)
                target, identity, fingerprint, effective = QualityService.expected_audio_path(
                    project,
                    segment_id,
                    params=params,
                    speaker_override=override_path or None,
                )
                preserved_relative = ""
                if os.path.isfile(target):
                    quality_root = os.path.dirname(QualityRepository.state_path(project))
                    preserved_dir = os.path.join(quality_root, "repair_inputs", repair_id)
                    os.makedirs(preserved_dir, exist_ok=True)
                    preserved = os.path.join(preserved_dir, os.path.basename(target))
                    shutil.copy2(target, preserved)
                    preserved_relative = QualityService._project_relative(project, preserved)
                    os.remove(target)
                revision = QualityRepository.create_revision(
                    project,
                    segment_id,
                    cache_identity=identity,
                    voice_fingerprint=fingerprint,
                    params=effective,
                    status="regenerating",
                    activate=False,
                    metadata={
                        "repair_id": repair_id,
                        "voice_override": override_relative or None,
                    },
                )
                revision_ids.append(revision["revision_id"])
                prepared.append({
                    "segment_id": segment_id,
                    "revision_id": revision["revision_id"],
                    "target_relative_path": QualityService._project_relative(project, target),
                    "preserved_relative_path": preserved_relative,
                    "original_status": original_statuses.get(segment_id, "pending"),
                })
                ProjectRepository.update_segment_status(project, segment_id, "pending")

            repair = QualityRepository.update_history_record(
                project,
                "repair_history",
                repair_id,
                status="submitting",
                revision_ids=revision_ids,
                prepared=prepared,
            )
            task_source = str(source or "system").lower()
            if task_source not in {"mcp", "web", "system", "recovery"}:
                task_source = "system"
            started = ProductionJobService.start(
                project,
                {"segment_ids": ids},
                options,
                source=task_source,
                idempotency_key=f"repair:{repair_id}",
            )
            for revision_id in revision_ids:
                QualityRepository.update_revision(
                    project,
                    revision_id,
                    source_task_id=started["task_id"],
                )
            repair = QualityRepository.update_history_record(
                project,
                "repair_history",
                repair_id,
                task_id=started["task_id"],
                status=str(started.get("status") or "pending"),
            )
            result = {"created": True, **cls._public(repair)}
            if result.get("status") in TERMINAL_TASK_STATES:
                return {"created": True, **cls.refresh(project, repair_id)}
            return result
        except Exception as exc:
            for item in prepared:
                target = QualityService._absolute(project, item["target_relative_path"])
                preserved_relative = item.get("preserved_relative_path")
                if preserved_relative and not os.path.isfile(target):
                    preserved = QualityService._absolute(project, preserved_relative)
                    if os.path.isfile(preserved):
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        shutil.copy2(preserved, target)
                ProjectRepository.update_segment_status(
                    project,
                    item["segment_id"],
                    str(item.get("original_status") or "pending"),
                )
                QualityRepository.update_revision(
                    project, item["revision_id"], status="error"
                )
            QualityRepository.update_history_record(
                project,
                "repair_history",
                repair_id,
                status="error",
                error=str(exc)[:500],
            )
            if isinstance(exc, (RepairError, ProductionJobError)):
                raise
            raise RepairError("REPAIR_START_FAILED", "修复任务启动失败") from exc

    @classmethod
    def refresh(cls, project_name: str, repair_id: str) -> dict[str, Any]:
        project = str(project_name or "").strip()
        repair = QualityRepository.get_history_record(
            project, "repair_history", repair_id
        )
        if not repair:
            raise RepairError("REPAIR_NOT_FOUND", "修复记录不存在", repair_id=repair_id)
        task_id = str(repair.get("task_id") or "")
        if not task_id:
            return cls._public(repair)
        snapshot = ProductionJobService.get_task_snapshot(task_id)
        task_status = str(snapshot.get("status") or "")
        if task_status in ACTIVE_TASK_STATES:
            repair = QualityRepository.update_history_record(
                project,
                "repair_history",
                repair_id,
                status=task_status,
                result={"progress": snapshot.get("progress", {})},
            )
            return cls._public(repair)
        if repair.get("status") in {"done", "partial", "cancelled", "error"} and repair.get("result", {}).get("finalized"):
            return cls._public(repair)

        completed: list[str] = []
        failed: list[str] = []
        qa_results: list[dict[str, Any]] = []
        for item in repair.get("prepared", []):
            segment_id = str(item.get("segment_id") or "")
            revision_id = str(item.get("revision_id") or "")
            target = QualityService._absolute(project, item.get("target_relative_path", ""))
            if os.path.isfile(target) and os.path.getsize(target) > 0:
                QualityService.register_completed_revision(
                    project, revision_id, target, source_task_id=task_id
                )
                qa_results.append(
                    QualityService.run_technical_qa(
                        project, segment_id, revision_id=revision_id
                    )
                )
                completed.append(segment_id)
            else:
                QualityRepository.update_revision(
                    project, revision_id, status="error"
                )
                failed.append(segment_id)
                # The previous active revision remains valid and can keep the
                # project deliverable while this repair is retried.
                previous = QualityRepository.get_active_revision(project, segment_id)
                ProjectRepository.update_segment_status(
                    project, segment_id, "done" if previous else "failed"
                )
        if completed and not failed and task_status == "done":
            final_status = "done"
        elif completed:
            final_status = "partial"
        elif task_status in {"cancelled", "interrupted"}:
            final_status = "cancelled"
        else:
            final_status = "error"
        repair = QualityRepository.update_history_record(
            project,
            "repair_history",
            repair_id,
            status=final_status,
            finished_at=snapshot.get("finished_at") or snapshot.get("updated_at") or "",
            error=str(snapshot.get("error_summary") or ""),
            result={
                "finalized": True,
                "completed_segment_ids": completed,
                "failed_segment_ids": failed,
                "technical_qa": [
                    {
                        "segment_id": item.get("segment_id"),
                        "revision_id": item.get("revision_id"),
                        "outcome": item.get("outcome"),
                    }
                    for item in qa_results
                ],
            },
        )
        return cls._public(repair)

    @classmethod
    def get(cls, project_name: str, repair_id: str) -> dict[str, Any]:
        record = QualityRepository.get_history_record(
            project_name, "repair_history", repair_id
        )
        if not record:
            raise RepairError("REPAIR_NOT_FOUND", "修复记录不存在", repair_id=repair_id)
        return cls._public(record)

    @classmethod
    def list(cls, project_name: str) -> list[dict[str, Any]]:
        return [
            cls._public(record)
            for record in QualityRepository.list_history(
                project_name, "repair_history"
            )
        ]


__all__ = ["RepairError", "RepairService"]
