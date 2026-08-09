"""Thin client for non-production TTS work owned by ``production_runtime``.

Voice previews and supplement synthesis use the same project-local SQLite queue
and OS-singleton runtime as formal production.  The Web process never imports
or initializes the TTS engine.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository

from .production_runtime import ProductionRuntime, ProductionRuntimeClient


_TERMINAL = frozenset({"done", "error", "cancelled", "interrupted"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class RuntimeTTSBusyError(RuntimeError):
    """Raised when a project already has an active runtime task."""

    code = "RUNTIME_BUSY"

    def __init__(self, active: TaskRecord) -> None:
        super().__init__("项目已有活动的 TTS 任务，请等待完成后重试")
        self.task_id = active.task_id
        self.status = active.status
        self.task_type = active.task_type


class RuntimeTTSError(RuntimeError):
    """Stable client-side error for a failed runtime utility task."""

    code = "RUNTIME_TTS_FAILED"

    def __init__(self, record: TaskRecord) -> None:
        message = record.error_summary or "TTS 运行时任务失败"
        super().__init__(message)
        self.task_id = record.task_id
        self.status = record.status
        self.task_type = record.task_type


class RuntimeTTSService:
    """Submit preview/supplement commands and wait on durable task state."""

    @staticmethod
    def _artifact_dir(project_name: str, group: str, task_id: str) -> str:
        project_dir = ProjectRepository.get_project_dir(project_name)
        cache_dir = project_paths.project_dir(project_dir, "cache", create=True)
        path = os.path.join(cache_dir, group, task_id)
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def _wait(task_id: str, timeout: float) -> TaskRecord:
        deadline = time.monotonic() + max(float(timeout), 1.0)
        while time.monotonic() < deadline:
            record = TaskRepository.load_task(task_id)
            if record is not None and record.status in _TERMINAL:
                if record.status == "done":
                    return record
                raise RuntimeTTSError(record)
            time.sleep(0.05)
        record = TaskRepository.load_task(task_id)
        if record is None:
            record = TaskRecord(task_id, "runtime_tts", "", "error")
        record.error_summary = "等待 TTS 运行时任务超时"
        raise RuntimeTTSError(record)

    @classmethod
    def _submit(
        cls,
        *,
        project_name: str,
        task_type: str,
        artifact_dir: str,
        options: dict[str, Any],
        total: int,
        timeout: float,
    ) -> TaskRecord:
        task_id = f"task_{uuid.uuid4().hex[:20]}"
        now = _now()
        record = TaskRecord(
            task_id=task_id,
            task_type=task_type,
            project=project_name,
            status="pending",
            artifact_dir=artifact_dir,
            source="web",
            scope={"all": False, "chapter_ids": [], "segment_ids": []},
            options=options,
            progress={
                "total": max(int(total), 0),
                "completed": 0,
                "failed": 0,
                "percent": 0.0,
                "current_chapter": None,
                "current_segment": None,
            },
            idempotency_key=task_id,
            created_at=now,
            updated_at=now,
        )
        outcome, durable = TaskRepository.create_runtime_task(record)
        if outcome == "active":
            raise RuntimeTTSBusyError(durable)
        ProductionRuntimeClient.ensure_running()
        return cls._wait(durable.task_id, timeout)

    @classmethod
    def test_voice_and_concat_wavs(
        cls,
        project_name: str,
        role: str,
        speaker_audio: str,
        *,
        timeout: float = 600.0,
    ) -> str:
        """Create a complete three-sentence voice preview in the singleton runtime."""
        project = str(project_name or "").strip()
        if not project or not TaskRepository.get_database_path(project, create=True):
            # Compatibility for isolated service tests without a real project.
            return ProductionRuntime.run_voice_preview_direct(
                str(speaker_audio), str(role or "voice"), ""
            )
        task_id = f"preview_{uuid.uuid4().hex[:20]}"
        artifact_dir = cls._artifact_dir(project, "voice_previews", task_id)
        record = cls._submit(
            project_name=project,
            task_type="voice_preview",
            artifact_dir=artifact_dir,
            options={
                "speaker_audio": os.path.abspath(str(speaker_audio)),
                "role": str(role or "voice"),
            },
            total=3,
            timeout=timeout,
        )
        result = record.progress.get("result", {}) if isinstance(record.progress, dict) else {}
        preview = str(result.get("preview_path") or "")
        if not preview or not os.path.isfile(preview):
            raise RuntimeTTSError(record)
        return preview

    @classmethod
    def synthesize_supplement(
        cls,
        *,
        project_name: str,
        role: str,
        lines: list[str],
        speaker_audio: str,
        overrides: dict[str, Any] | None,
        num_beams: int,
        artifact_dir: str,
        timeout: float = 3600.0,
    ) -> list[dict[str, Any]]:
        """Synthesize isolated supplement lines through the singleton runtime."""
        project = str(project_name or "").strip()
        payload = {
            "role": str(role or ""),
            "lines": [str(item) for item in lines],
            "speaker_audio": str(speaker_audio),
            "overrides": dict(overrides or {}),
            "num_beams": max(int(num_beams or 2), 1),
        }
        if not project or not TaskRepository.get_database_path(project, create=True):
            # Legacy/unit-test fixtures have no durable project queue.
            return ProductionRuntime.run_supplement_direct(payload, artifact_dir)
        record = cls._submit(
            project_name=project,
            task_type="supplement",
            artifact_dir=artifact_dir,
            options=payload,
            total=len(lines),
            timeout=timeout,
        )
        result = record.progress.get("result", {}) if isinstance(record.progress, dict) else {}
        items = result.get("items", [])
        if not isinstance(items, list):
            raise RuntimeTTSError(record)
        return [dict(item) for item in items if isinstance(item, dict)]


__all__ = [
    "RuntimeTTSBusyError",
    "RuntimeTTSError",
    "RuntimeTTSService",
]
