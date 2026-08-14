"""Thin client for non-production TTS work owned by ``production_runtime``.

Voice previews and supplement synthesis use the same project-local SQLite queue
and OS-singleton runtime as formal production.  The Web process never imports
or initializes the TTS engine.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from lib.tts_profile import resolve_profile

from .production_runtime import ProductionRuntime, ProductionRuntimeClient

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"done", "error", "cancelled", "interrupted"})


def _supplement_event(event: str, **fields: Any) -> None:
    """Emit one structured web-side supplement event (path-free fields only)."""
    parts = [f"{key}={value}" for key, value in fields.items() if value not in (None, "")]
    logger.info("supplement_event=%s %s", event, " ".join(parts))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _latest_progress_phase(record: TaskRecord) -> tuple[str, str] | None:
    """Map the newest durable task log line to a UI progress (phase, message).

    The runtime writes structured ``[engine_intent]`` / ``[engine_init_start]`` /
    ``[engine_init_done]`` / ``[supplement_infer_start]`` lines while loading
    or running; the web wait loop turns them into readable progress text.
    """
    lines = list(getattr(record, "log_lines", None) or [])
    if not lines:
        return None
    line = str(lines[-1])
    if "[engine_intent]" in line and "profile_match=false" in line:
        identity = _value_of(line, "target_engine_identity") or "引擎"
        version = _value_of(line, "target_engine_version") or ""
        return "engine_loading", f"正在加载 {_engine_display(identity, version)}（引擎切换/首次加载）…"
    if "[engine_init_start]" in line or "[engine_recycle_start]" in line:
        identity = _value_of(line, "engine_identity") or "引擎"
        version = _value_of(line, "engine_version") or ""
        return "engine_loading", f"正在加载 {_engine_display(identity, version)}…"
    if "[engine_reset_done]" in line:
        return "engine_loading", "已释放旧引擎，正在加载新引擎…"
    if "[engine_init_done]" in line:
        elapsed = _value_of(line, "elapsed_ms")
        identity = _value_of(line, "engine_identity") or "引擎"
        suffix = f"（耗时 {int(elapsed) / 1000:.0f} 秒）" if elapsed else ""
        return "engine_ready", f"引擎 {identity} 就绪{suffix}"
    if "[supplement_infer_start]" in line:
        index = _value_of(line, "line_index")
        total = _value_of(line, "line_total")
        if index is not None and total:
            return "infer", f"正在生成第 {int(index) + 1}/{total} 句…"
    return None


def _value_of(line: str, key: str) -> str | None:
    prefix = f"{key}="
    for token in str(line).split():
        if token.startswith(prefix):
            value = token[len(prefix):].strip()
            if value and value != "none":
                return value
    return None


def _engine_display(identity: str, version: str) -> str:
    label = {
        "indextts:2": "IndexTTS 2",
        "indextts:2.5": "IndexTTS 2.5",
    }.get(str(identity or ""), str(identity or "") or str(version or "") or "引擎")
    return f"{label}（{version}）" if version and label != version else label


def _direct_progress_adapter(progress_cb: Any) -> Any:
    """Bridge lifecycle ``(event, **fields)`` callbacks to ``(phase, message)``.

    Used by the legacy/direct runtime path (no durable task) so the UI gets
    the same progress text as the durable queue path.
    """
    if progress_cb is None:
        return None

    def _adapt(event: str, **fields: Any) -> None:
        if event == "engine_init_start":
            progress_cb(
                "engine_loading",
                f"正在加载 {_engine_display(fields.get('engine_identity'), fields.get('engine_version'))}…",
            )
        elif event == "engine_reset_done":
            progress_cb("engine_loading", "已释放旧引擎，正在加载新引擎…")
        elif event == "engine_init_done":
            elapsed = fields.get("elapsed_ms")
            identity = str(fields.get("engine_identity") or "")
            suffix = f"（耗时 {int(elapsed) / 1000:.0f} 秒）" if elapsed else ""
            progress_cb("engine_ready", f"引擎 {_engine_display(identity, '')} 就绪{suffix}")
        elif event == "supplement_infer_start":
            index = fields.get("line_index")
            total = fields.get("line_total")
            if index is not None and total:
                progress_cb("infer", f"正在生成第 {int(index) + 1}/{total} 句…")

    return _adapt


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
    def _wait(
        task_id: str,
        timeout: float,
        progress_cb: Any = None,
    ) -> TaskRecord:
        """Block until the durable task reaches a terminal state.

        ``progress_cb(phase, message)`` is invoked while polling with the
        latest task log line / runtime engine state, so the UI can render
        "正在加载 IndexTTS 2.5…" / "正在生成第 X/N 句" during a multi-minute
        engine (re)load instead of appearing hung.
        """
        deadline = time.monotonic() + max(float(timeout), 1.0)
        last_phase = ""
        while time.monotonic() < deadline:
            record = TaskRepository.load_task(task_id)
            if record is not None:
                if record.status in _TERMINAL:
                    if record.status == "done":
                        return record
                    raise RuntimeTTSError(record)
                if progress_cb is not None:
                    phase = _latest_progress_phase(record)
                    if phase and phase != last_phase:
                        last_phase = phase
                        progress_cb(*phase)
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
        progress_cb: Any = None,
    ) -> TaskRecord:
        task_id = f"task_{uuid.uuid4().hex[:20]}"
        now = _now()
        options = dict(options or {})
        options.setdefault("engine_snapshot", resolve_profile(options))
        engine = options["engine_snapshot"]
        _supplement_event(
            "task_created",
            task_id=task_id,
            task_type=task_type,
            engine_version=str(engine.get("engine_version") or ""),
            engine_identity=str(engine.get("engine_identity") or ""),
            total=total,
        )
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
        if progress_cb is not None:
            progress_cb("runtime_start_requested", "正在等待运行时…")
        _supplement_event(
            "runtime_start_requested",
            task_id=task_id,
            task_type=task_type,
        )
        ProductionRuntimeClient.ensure_running()
        started = time.monotonic()
        try:
            result = cls._wait(durable.task_id, timeout, progress_cb=progress_cb)
        except Exception:
            _supplement_event(
                "task_error",
                task_id=task_id,
                task_type=task_type,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        _supplement_event(
            "task_done",
            task_id=task_id,
            task_type=task_type,
            status=str(result.status),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    @classmethod
    def test_voice_and_concat_wavs(
        cls,
        project_name: str,
        role: str,
        speaker_audio: str,
        *,
        timeout: float = 600.0,
        engine_profile: dict[str, Any] | None = None,
    ) -> str:
        """Create a complete three-sentence voice preview in the singleton runtime."""
        project = str(project_name or "").strip()
        if not project or not TaskRepository.get_database_path(project, create=True):
            # Compatibility for isolated service tests without a real project.
            return ProductionRuntime().run_voice_preview_direct(
                str(speaker_audio), str(role or "voice"), "", engine_profile
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
                "engine_snapshot": resolve_profile(engine_profile or {}),
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
        engine_profile: dict[str, Any] | None = None,
        progress_cb: Any = None,
    ) -> list[dict[str, Any]]:
        """Synthesize isolated supplement lines through the singleton runtime."""
        project = str(project_name or "").strip()
        payload = {
            "role": str(role or ""),
            "lines": [str(item) for item in lines],
            "speaker_audio": str(speaker_audio),
            "overrides": dict(overrides or {}),
            "num_beams": max(int(num_beams or 2), 1),
            "engine_snapshot": resolve_profile(engine_profile or {}),
        }
        engine = payload["engine_snapshot"]
        _supplement_event(
            "supplement_request_received",
            task_type="supplement",
            role=str(role or ""),
            lines=len(lines),
            engine_version=str(engine.get("engine_version") or ""),
            engine_identity=str(engine.get("engine_identity") or ""),
        )
        if progress_cb is not None:
            progress_cb("submitted", "已提交补录任务，正在等待运行时…")
        if not project or not TaskRepository.get_database_path(project, create=True):
            # Legacy/unit-test fixtures have no durable project queue.
            return ProductionRuntime().run_supplement_direct(
                payload,
                artifact_dir,
                engine_profile=payload["engine_snapshot"],
                progress_cb=_direct_progress_adapter(progress_cb),
            )
        record = cls._submit(
            project_name=project,
            task_type="supplement",
            artifact_dir=artifact_dir,
            options=payload,
            total=len(lines),
            timeout=timeout,
            progress_cb=progress_cb,
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
