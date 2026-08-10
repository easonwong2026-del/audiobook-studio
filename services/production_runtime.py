"""Singleton production runtime and thin client used by Web/MCP services.

Only this runtime owns ``SynthesisService`` state and its TTS executor.  Client
processes communicate by writing transactional commands to ``TaskRepository``.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import wave
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from lib import progress as synthesis_progress
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository

from .runtime_lock import ProcessFileLock
from .runtime_engine import EngineInitError, RuntimeEngineLifecycle
from lib.failures import RecoveryBudget, RecoveryHooks, SynthesisFailure
from .synthesis import SynthesisService, SynthesisState

logger = logging.getLogger(__name__)


def _runtime_event(event: str, **fields: Any) -> None:
    """Emit one structured runtime lifecycle event into the runtime log."""
    parts = [f"{key}={value}" for key, value in fields.items() if value not in (None, "")]
    logger.info("runtime_event=%s %s", event, " ".join(parts))


class ProductionRuntime:
    """The only process allowed to own active synthesis state."""

    def __init__(
        self,
        *,
        owner_id: str | None = None,
        lock_path: str | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        self.owner_id = owner_id or f"runtime_{uuid.uuid4().hex}"
        self.lock = ProcessFileLock(lock_path)
        self.poll_interval = max(float(poll_interval), 0.02)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_state: SynthesisState | None = None
        self._current_record: TaskRecord | None = None
        self._current_segment_to_chapter: dict[str, str] = {}
        self._state_lock = threading.RLock()
        self._last_heartbeat = 0.0
        self._export_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="audiobook-formal-export",
        )
        self._export_future: Future[Any] | None = None
        self._export_record: TaskRecord | None = None
        self._ownership_lock = threading.RLock()
        self._lock_release_deferred = False
        self._engine = RuntimeEngineLifecycle(owner_id=self.owner_id)
        self._engine_failure = False

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def get_runtime_state(self, task_id: str) -> Optional[SynthesisState]:
        with self._state_lock:
            state = self._current_state
            if state is not None and state.task_id == str(task_id or ""):
                return state
            return None

    def engine_snapshot(self) -> dict[str, Any]:
        """Thread-safe engine lifecycle snapshot (tests/diagnostics)."""
        return self._engine.snapshot()

    def ensure_engine_ready(self) -> None:
        """Initialize the process-local TTS engine exactly once per serve cycle."""
        self._engine.ensure_ready()

    def reset_engine(self) -> None:
        """Force a fresh engine lifecycle (used by tests and restart)."""
        self._engine.reset()

    def start_background(self) -> bool:
        if self.is_running:
            return True
        with self._state_lock:
            if self._export_future is not None and not self._export_future.done():
                # A stopped runtime keeps ownership while its export worker is
                # still capable of publishing.  It cannot be restarted or
                # replaced in-process until that worker has settled.
                return False
        if not self.lock.acquire(blocking=False):
            return False
        # This is the only legal interruption-repair point.  Merely reading a
        # task from a client process must never infer worker death.
        orphaned = TaskRepository.mark_orphaned_interrupted(self.owner_id)
        self._engine_failure = False
        self._engine.reset()
        _runtime_event(
            "runtime_start",
            mode="inline",
            pid=os.getpid(),
            owner=self.owner_id,
            orphan_takeover=len(orphaned) or None,
        )
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="audiobook-production-runtime",
            daemon=True,
        )
        self._thread.start()
        return True

    def serve_forever(self) -> bool:
        if not self.lock.acquire(blocking=False):
            return False
        orphaned = TaskRepository.mark_orphaned_interrupted(self.owner_id)
        self._engine_failure = False
        self._engine.reset()
        _runtime_event(
            "runtime_start",
            mode="serve",
            pid=os.getpid(),
            owner=self.owner_id,
            orphan_takeover=len(orphaned) or None,
        )
        self._stop.clear()
        try:
            self._run_loop()
        finally:
            self._release_lock_when_export_safe()
        return not self._engine_failure

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._state_lock:
            state = self._current_state
            export_future = self._export_future
            export_record = self._export_record
        if state is not None:
            # Release a worker that is blocked in a pause gate (normal
            # segment boundary or engine-recovery gate).  Persistence is
            # detached first so the durable task keeps its last active
            # state (paused/recovering/running) and is repaired by the
            # normal orphan-interrupt path on the next runtime takeover,
            # instead of being misclassified as a user cancel.
            state.on_update = None
            state.paused = False
            state.cancel = True
        if export_future is not None and not export_future.done() and export_record:
            try:
                TaskRepository.request_control(export_record.task_id, "cancel")
            except (KeyError, ValueError):
                # The worker may already have reached a terminal state.
                pass
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(timeout, 0.0))
        self._thread = None
        # Inline/test callers may replace the project roots immediately after
        # reset.  Drain an in-flight export while the caller's project context
        # is still valid, but retain the timeout guarantee for a genuinely
        # long-running worker.
        remaining = max(float(timeout), 0.0)
        if export_future is not None and not export_future.done() and remaining:
            try:
                export_future.result(timeout=remaining)
            except Exception:
                # The runtime already persists task failures; stopping should
                # not re-raise a worker exception into a client cleanup path.
                pass
        self._export_executor.shutdown(
            wait=bool(export_future is None or export_future.done()),
            cancel_futures=True,
        )
        # If the timeout expires, the export thread remains alive and the OS
        # singleton lock must remain held. The completion callback below
        # releases it only after the worker can no longer publish.
        self._release_lock_when_export_safe()

    def _release_lock_when_export_safe(self) -> None:
        """Release singleton ownership only after an export worker settles."""
        with self._ownership_lock:
            future = self._export_future
            if future is not None and not future.done():
                if not self._lock_release_deferred:
                    self._lock_release_deferred = True
                    future.add_done_callback(
                        lambda _future: self._release_lock_when_export_safe()
                    )
                return
            self._lock_release_deferred = False
            self.lock.release()

    def poke(self) -> None:
        """Apply a just-written command promptly in inline/test mode."""
        with self._state_lock:
            state = self._current_state
        if state is not None:
            self._apply_control(state)

    def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                with self._state_lock:
                    state = self._current_state
                if state is None:
                    record = TaskRepository.claim_next_pending(
                        self.owner_id, {"synthesis"}
                    )
                    if record is not None:
                        _runtime_event(
                            "task_claim",
                            task_id=record.task_id,
                            task_type=record.task_type,
                            project=record.project,
                            owner=self.owner_id,
                        )
                        self._launch(record)
                else:
                    self._apply_control(state)
                    future = state.future
                    if state.status in {"cancelled", "done", "error", "needs_attention"} and (
                        future is None or future.done()
                    ):
                        with self._state_lock:
                            self._current_state = None
                            self._current_record = None
                            self._current_segment_to_chapter = {}
                if self._export_future is not None and self._export_future.done():
                    self._export_future = None
                    self._export_record = None
                if self._export_future is None:
                    export_record = TaskRepository.claim_next_pending(
                        self.owner_id, {"export"}
                    )
                    if export_record is not None:
                        _runtime_event(
                            "task_claim",
                            task_id=export_record.task_id,
                            task_type=export_record.task_type,
                            project=export_record.project,
                            owner=self.owner_id,
                        )
                        self._launch_export(export_record)
                if self._current_state is None and self._export_future is None:
                    utility_record = TaskRepository.claim_next_pending(
                        self.owner_id, {"supplement", "voice_preview"}
                    )
                    if utility_record is not None:
                        _runtime_event(
                            "task_claim",
                            task_id=utility_record.task_id,
                            task_type=utility_record.task_type,
                            project=utility_record.project,
                            owner=self.owner_id,
                        )
                        self._launch(utility_record)
                now = time.monotonic()
                if now - self._last_heartbeat >= 1.0:
                    TaskRepository.update_runtime_heartbeat(self.owner_id)
                    self._last_heartbeat = now
                self._stop.wait(self.poll_interval)
        except Exception:
            logger.exception("生产运行时主循环异常退出")
            raise
        finally:
            # Do not mark tasks here.  A new process must first prove ownership
            # by acquiring the OS lock before it can repair interrupted tasks.
            if self._thread is threading.current_thread():
                self._thread = None
            self._engine.mark_unknown()
            _runtime_event(
                "runtime_shutdown",
                pid=os.getpid(),
                owner=self.owner_id,
                reason="engine_init_failure" if self._engine_failure else "stop",
            )
            self._release_lock_when_export_safe()

    @staticmethod
    def _bindings(record: TaskRecord) -> dict[str, Any]:
        _meta, _script, document = ProjectRepository.load_project(record.project)
        if not isinstance(document, dict):
            return {}
        bindings = document.get("bindings", {})
        return bindings if isinstance(bindings, dict) else {}

    @staticmethod
    def _segment_to_chapter(project: str) -> dict[str, str]:
        try:
            _meta, script, _bindings = ProjectRepository.load_project(project)
        except Exception:
            return {}
        result: dict[str, str] = {}
        for chapter in script.get("chapters", []):
            if not isinstance(chapter, dict):
                continue
            chapter_id = str(chapter.get("id") or "")
            for segment in chapter.get("segments", []):
                if isinstance(segment, dict) and segment.get("id") is not None:
                    result[str(segment["id"])] = chapter_id
        return result

    def _launch(self, record: TaskRecord) -> None:
        if record.task_type != "synthesis":
            self._run_utility_task(record)
            return
        scope = record.scope if isinstance(record.scope, dict) else {}
        chapters = [
            str(item) for item in scope.get("chapter_ids", [])
        ] if isinstance(scope.get("chapter_ids", []), list) else []
        segments = [
            str(item) for item in scope.get("segment_ids", [])
        ] if isinstance(scope.get("segment_ids", []), list) else []
        progress = record.progress if isinstance(record.progress, dict) else {}
        state = SynthesisState(
            task_id=record.task_id,
            project=record.project,
            status="pending",
            total=max(int(progress.get("total", 0) or 0), 0),
            completed=max(int(progress.get("completed", 0) or 0), 0),
            failed_segment_ids=list(record.failed_segment_ids or []),
        )
        state.selected_chapters = chapters or None
        state.selected_segment_ids = segments or None
        state.on_update = self._on_state_update
        try:
            state.segment_states = synthesis_progress.build_segment_states(
                record.project,
                chapters or None,
            )
        except Exception:
            state.segment_states = []
        with self._state_lock:
            self._current_record = record
            self._current_state = state
            self._current_segment_to_chapter = self._segment_to_chapter(
                record.project
            )
        # P0: engine preflight inside the runtime process.  A failed init is
        # fatal for the whole task: no segment loop, no per-segment errors.
        try:
            self._engine.ensure_ready()
        except EngineInitError as exc:
            self._fail_synthesis_engine_init(record, state, exc)
            return
        state.engine_generation = self._engine.snapshot()["engine_generation"]
        options = record.options if isinstance(record.options, dict) else {}
        _runtime_event(
            "task_start",
            task_id=record.task_id,
            task_type=record.task_type,
            project=record.project,
        )
        try:
            SynthesisService.start(
                state,
                record.project,
                self._bindings(record),
                num_beams=int(options.get("num_beams", 2) or 2),
                emotion=options.get("emotion"),
                emo_alpha=options.get("emo_alpha"),
                speech_rate=options.get("speech_rate"),
                selected_chapters=chapters or None,
                selected_segment_ids=segments or None,
                persist_task=False,
                voice_overrides=(
                    options.get("voice_overrides")
                    if isinstance(options.get("voice_overrides"), dict)
                    else None
                ),
                recovery=self._build_recovery_hooks(state, record),
                budget=RecoveryBudget(),
            )
        except Exception as exc:
            state.status = "error"
            state.error = str(exc)
            state.append_log(f"❌ 启动生产运行时失败: {exc}")
            state.notify()

    def _build_recovery_hooks(
        self,
        state: SynthesisState,
        record: TaskRecord,
    ) -> RecoveryHooks:
        """Build the runtime-owned self-healing callbacks for one task."""

        def _recycle() -> int:
            generation = self._engine.recycle()
            state.engine_generation = generation
            return generation

        def _cancel_requested() -> bool:
            return bool(state.cancel)

        def _pause_gate() -> None:
            while state.paused and not state.cancel:
                if state.status != "paused":
                    state.status = "paused"
                    state.append_log("⏸ 恢复前已暂停，等待人工恢复")
                    state.notify()
                time.sleep(0.1)

        def _on_recovery(event: dict) -> None:
            payload = dict(event)
            event_name = str(payload.get("event") or "")
            previous = dict(state.recovery or {})
            state.engine_generation = int(
                payload.get("engine_generation") or state.engine_generation or 0
            )
            state.recovery = {
                "reason_code": str(
                    payload.get("reason_code") or previous.get("reason_code") or ""
                ),
                "attempt": int(
                    payload.get("attempt") or previous.get("attempt") or 0
                ),
                "max_attempts": int(
                    payload.get("max_attempts") or previous.get("max_attempts") or 0
                ),
                "engine_generation": state.engine_generation,
                "retry_segment": str(
                    payload.get("segment_id") or previous.get("retry_segment") or ""
                ),
                "fingerprint": str(
                    payload.get("fingerprint") or previous.get("fingerprint") or ""
                ),
                "exception_type": str(
                    payload.get("exception_type")
                    or previous.get("exception_type")
                    or ""
                ),
                "errno": (
                    payload.get("errno")
                    if payload.get("errno") is not None
                    else previous.get("errno")
                ),
                "phase": str(
                    payload.get("phase") or previous.get("phase") or ""
                ),
                "recovered": event_name == "recovered",
                "last_recovery_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if event_name == "recovering":
                state.status = "recovering"
                state.append_log(
                    "🔄 检测到 TTS 运行时异常，正在自动恢复 "
                    f"{payload.get('attempt')}/{payload.get('max_attempts')}，"
                    f"将重试 {payload.get('segment_id')}"
                )
            elif event_name == "recovered":
                state.status = "running"
                state.append_log(
                    f"✅ TTS 已恢复（generation {state.engine_generation}），继续生产"
                )
            elif event_name == "recycle_failed":
                state.append_log("❌ 引擎回收失败")
            elif event_name == "exhausted":
                state.status = "needs_attention"
                state.append_log("❌ 自动恢复失败，需要处理")
            state.notify()

        def _on_failure(failure: SynthesisFailure) -> None:
            state.last_failure = failure.as_dict()
            try:
                from lib import tts_engine

                gpu = tts_engine.gpu_snapshot()
            except Exception:  # pylint: disable=broad-except
                gpu = {}
            engine = self._engine.snapshot()
            logger.error(
                "runtime_event=segment_failure task_id=%s project=%s "
                "owner=%s pid=%s engine_state=%s engine_generation=%s "
                "recovery_attempt=%s chapter_id=%s segment_id=%s phase=%s "
                "exception_type=%s errno=%s message=%s origin=%s "
                "fingerprint=%s num_beams=%s gpu=%s",
                record.task_id,
                record.project,
                self.owner_id,
                os.getpid(),
                engine["state"],
                engine["engine_generation"],
                state.recovery.get("attempt") if isinstance(state.recovery, dict) else None,
                failure.chapter_id,
                failure.segment_id,
                failure.phase,
                failure.exception_type,
                failure.errno,
                failure.message[:160],
                failure.traceback_origin,
                failure.fingerprint,
                (record.options or {}).get("num_beams", 2),
                gpu,
            )

        return RecoveryHooks(
            recycle=_recycle,
            cancel_requested=_cancel_requested,
            pause_gate=_pause_gate,
            on_recovery=_on_recovery,
            on_failure=_on_failure,
        )

    def _fail_synthesis_engine_init(
        self,
        record: TaskRecord,
        state: SynthesisState,
        exc: EngineInitError,
    ) -> None:
        """Persist one terminal engine-init failure and shut the runtime down."""
        state.status = "error"
        state.error = str(exc)
        state.completed = 0
        state.failed_segment_ids = []
        progress = {
            "total": max(int(state.total or 0), 0),
            "completed": 0,
            "failed": 0,
            "attempted": 0,
            "percent": 0.0,
            "current_chapter": None,
            "current_segment": None,
        }
        state.append_log("❌ TTS 引擎初始化失败，任务未开始")
        state.append_log(f"错误: {exc.summary}")
        try:
            TaskRepository.persist_runtime_state(
                record.task_id,
                self.owner_id,
                status="error",
                progress=progress,
                failed_segment_ids=[],
                error_summary=str(exc),
                log_lines=list(state.log_lines),
            )
        except Exception:
            logger.exception("持久化引擎初始化失败状态失败: %s", record.task_id)
        logger.error(
            "runtime_event=task_engine_fatal task_id=%s project=%s error=%s",
            record.task_id,
            record.project,
            exc.summary,
        )
        self._engine_failure = True
        self._stop.set()

    def _launch_export(self, record: TaskRecord) -> None:
        """Submit formal export to a managed CPU/IO worker.

        The polling loop never performs the book export itself, so heartbeat,
        ownership takeover, and command processing continue while FFmpeg or a
        long WAV write is running.
        """
        with self._state_lock:
            self._export_record = record
        self._export_future = self._export_executor.submit(
            self._run_export_task,
            record,
        )

    def _run_export_task(self, record: TaskRecord) -> None:
        from .export import ExportCancelled, ExportService

        running = TaskRepository.persist_runtime_state(
            record.task_id,
            self.owner_id,
            status="running",
            progress=record.progress,
            failed_segment_ids=[],
            error_summary="",
            log_lines=["运行时开始执行正式导出"],
        )
        current = running or record
        try:
            result = ExportService.execute_export_job(
                current,
                is_cancelled=lambda: self._export_cancel_requested(record.task_id),
                owner_id=self.owner_id,
            )
            progress = {
                **dict(current.progress or {}),
                "total": 1,
                "completed": 1,
                "failed": 0,
                "percent": 100.0,
                "result": result,
            }
            TaskRepository.persist_runtime_state(
                record.task_id,
                self.owner_id,
                status="done",
                progress=progress,
                failed_segment_ids=[],
                error_summary="",
                log_lines=["运行时完成正式导出"],
            )
            _runtime_event(
                "task_terminal",
                task_id=record.task_id,
                task_type="export",
                status="done",
            )
        except ExportCancelled:
            TaskRepository.persist_runtime_state(
                record.task_id,
                self.owner_id,
                status="cancelled",
                progress=dict(current.progress or {}),
                failed_segment_ids=[],
                # Cancellation is a terminal control outcome, not a task
                # error. Keep the durable task error field empty so clients
                # cannot misclassify a successful cancel as a failure.
                error_summary="",
                log_lines=["正式导出已取消"],
            )
            _runtime_event(
                "task_terminal",
                task_id=record.task_id,
                task_type="export",
                status="cancelled",
            )
        except Exception as exc:
            logger.exception("正式导出任务失败: %s", record.task_id)
            code = str(getattr(exc, "code", "EXPORT_ERROR"))
            TaskRepository.persist_runtime_state(
                record.task_id,
                self.owner_id,
                status="error",
                progress=dict(current.progress or {}),
                failed_segment_ids=[],
                error_summary=f"{code}: {exc}",
                log_lines=[f"正式导出任务失败: {code}"],
            )
            _runtime_event(
                "task_terminal",
                task_id=record.task_id,
                task_type="export",
                status="error",
            )

    @staticmethod
    def _export_cancel_requested(task_id: str) -> bool:
        record = TaskRepository.load_task(task_id)
        return bool(record and record.control_intent == "cancel")

    @staticmethod
    def _safe_component(value: str) -> str:
        result = re.sub(r"[^0-9A-Za-z一-龥_-]+", "_", str(value or "").strip())
        return result.strip("._") or "voice"

    @staticmethod
    def _validate_wav(path: str) -> None:
        if not os.path.isfile(path) or os.path.getsize(path) <= 44:
            raise RuntimeError("TTS 未生成有效 WAV")
        with wave.open(path, "rb") as audio:
            if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                raise RuntimeError("TTS 生成的 WAV 为空")

    def run_voice_preview_direct(
        self,
        speaker_audio: str,
        role: str,
        artifact_dir: str,
    ) -> str:
        """Runtime-owned direct worker; public callers submit through RuntimeTTSService."""
        from lib import config

        self._engine.ensure_ready()

        destination = artifact_dir or os.path.join(
            config.get_preview_dir(),
            "voice_previews",
            uuid.uuid4().hex,
        )
        os.makedirs(destination, exist_ok=True)
        from lib import tts_engine

        parts = tts_engine.test_voice(speaker_audio)
        if not parts:
            raise RuntimeError("声音试听未生成音频")
        for path in parts:
            ProductionRuntime._validate_wav(path)
        output = os.path.join(
            destination,
            f"preview_{ProductionRuntime._safe_component(role)}.wav",
        )
        temporary = os.path.join(
            destination,
            f".{uuid.uuid4().hex}.part.wav",
        )
        tts_engine._concat_wavs(parts, temporary)
        ProductionRuntime._validate_wav(temporary)
        os.replace(temporary, output)
        return output

    def run_supplement_direct(
        self,
        payload: dict[str, Any],
        artifact_dir: str,
        *,
        heartbeat: Any = None,
        initialize: bool = False,
        validate_output: bool = False,
    ) -> list[dict[str, Any]]:
        """Runtime-owned isolated supplement worker."""
        from lib import config

        if initialize:
            self._engine.ensure_ready()

        destination = artifact_dir or os.path.join(
            config.get_preview_dir(),
            "supplement_tasks",
            uuid.uuid4().hex,
        )
        os.makedirs(destination, exist_ok=True)
        overrides = (
            payload.get("overrides")
            if isinstance(payload.get("overrides"), dict)
            else {}
        )
        emotion = overrides.get("emotion") or "neutral"
        emo_alpha = overrides.get("emo_alpha")
        speech_rate = overrides.get("speech_rate")
        try:
            beams = max(int(payload.get("num_beams", 2) or 2), 1)
        except (TypeError, ValueError):
            beams = 2
        speaker_audio = str(payload.get("speaker_audio") or "")
        from lib import tts_engine

        results: list[dict[str, Any]] = []
        for index, raw_text in enumerate(payload.get("lines", [])):
            if callable(heartbeat):
                heartbeat()
            text = str(raw_text or "").strip()
            output = os.path.join(destination, f"{index + 1:03d}.wav")
            if not text:
                results.append({
                    "index": index,
                    "text": "",
                    "wav_path": None,
                    "status": "failed",
                    "error": f"❌ 句{index + 1}: 文本为空",
                })
                continue
            temporary = os.path.join(
                destination,
                f".{index + 1:03d}.{uuid.uuid4().hex}.part.wav",
            )
            try:
                generated = tts_engine.synthesize_segment(
                    text=text,
                    speaker_audio=speaker_audio,
                    emotion=emotion,
                    emo_alpha=float(emo_alpha) if emo_alpha is not None else 1.0,
                    speech_rate=(
                        float(speech_rate) if speech_rate is not None else 1.0
                    ),
                    output_path=temporary,
                    num_beams=beams,
                )
                if validate_output:
                    ProductionRuntime._validate_wav(temporary)
                if os.path.isfile(temporary):
                    os.replace(temporary, output)
                    generated = output
                results.append({
                    "index": index,
                    "text": text,
                    "wav_path": str(generated or output),
                    "status": "ok",
                    "error": "",
                })
            except Exception as exc:
                try:
                    os.remove(temporary)
                except OSError:
                    pass
                results.append({
                    "index": index,
                    "text": text,
                    "wav_path": None,
                    "status": "failed",
                    "error": f"❌ 句{index + 1}: {str(exc)[:120]}",
                })
        tts_engine.empty_cache()
        return results

    def _run_utility_task(self, record: TaskRecord) -> None:
        """Execute one preview/supplement command synchronously under the OS lock."""
        project_dir = ProjectRepository.get_project_dir(record.project)
        artifact_dir = os.path.abspath(record.artifact_dir)
        try:
            if os.path.commonpath([artifact_dir, project_dir]) != os.path.abspath(
                project_dir
            ):
                raise ValueError("runtime artifact_dir 必须位于项目目录内")
        except ValueError as exc:
            TaskRepository.persist_runtime_state(
                record.task_id,
                self.owner_id,
                status="error",
                progress=record.progress,
                failed_segment_ids=[],
                error_summary=str(exc),
                log_lines=[str(exc)],
            )
            return
        running = TaskRepository.persist_runtime_state(
            record.task_id,
            self.owner_id,
            status="running",
            progress=record.progress,
            failed_segment_ids=[],
            error_summary="",
            log_lines=[f"运行时开始执行 {record.task_type}"],
        )
        current = running or record
        try:
            if record.task_type == "voice_preview":
                preview = self.run_voice_preview_direct(
                    str(record.options.get("speaker_audio") or ""),
                    str(record.options.get("role") or "voice"),
                    artifact_dir,
                )
                result: dict[str, Any] = {"preview_path": preview}
                completed, failed = 3, 0
            elif record.task_type == "supplement":
                items = self.run_supplement_direct(
                    record.options,
                    artifact_dir,
                    heartbeat=lambda: TaskRepository.update_runtime_heartbeat(
                        self.owner_id
                    ),
                    initialize=True,
                    validate_output=True,
                )
                result = {"items": items}
                completed = sum(item.get("status") == "ok" for item in items)
                failed = sum(item.get("status") == "failed" for item in items)
                if not completed:
                    raise RuntimeError(
                        "补录合成全部失败：" + "; ".join(
                            str(item.get("error") or "") for item in items[:3]
                        )
                    )
            else:
                raise RuntimeError(f"未知 runtime task_type: {record.task_type}")
            total = max(int(current.progress.get("total", 0) or 0), completed + failed)
            progress = {
                **dict(current.progress or {}),
                "total": total,
                "completed": completed,
                "failed": failed,
                "percent": round((completed / total) * 100, 1) if total else 100.0,
                "result": result,
            }
            TaskRepository.persist_runtime_state(
                record.task_id,
                self.owner_id,
                status="done",
                progress=progress,
                failed_segment_ids=[],
                error_summary="",
                log_lines=[f"运行时完成 {record.task_type}"],
            )
        except Exception as exc:
            logger.exception("运行时任务失败: %s", record.task_id)
            TaskRepository.persist_runtime_state(
                record.task_id,
                self.owner_id,
                status="error",
                progress=dict(current.progress or {}),
                failed_segment_ids=[],
                error_summary=str(exc),
                log_lines=[f"运行时任务失败: {exc}"],
            )
            if isinstance(exc, EngineInitError):
                self._engine_failure = True
                self._stop.set()
                logger.error(
                    "runtime_event=task_engine_fatal task_id=%s task_type=%s error=%s",
                    record.task_id,
                    record.task_type,
                    exc.summary,
                )

    def _progress(self, state: SynthesisState, record: TaskRecord) -> dict[str, Any]:
        total = max(int(state.total or record.progress.get("total", 0) or 0), 0)
        completed = max(int(state.completed or 0), 0)
        failed_ids = sorted({str(item) for item in state.failed_segment_ids if str(item)})
        with self._state_lock:
            chapter = self._current_segment_to_chapter.get(
                str(state.current_segment or "")
            )
        return {
            "total": total,
            "completed": completed,
            "failed": len(failed_ids),
            "percent": round((completed / total) * 100, 1) if total else 0.0,
            "current_chapter": chapter,
            "current_segment": state.current_segment,
            "engine_generation": int(state.engine_generation or 0),
            "recovery": state.recovery,
        }

    def _on_state_update(self, state: SynthesisState) -> None:
        with self._state_lock:
            record = self._current_record
        if record is None or record.task_id != state.task_id:
            return
        # Engine-generation fencing: an update carrying a stale generation
        # must never overwrite the state published after an engine recycle.
        current_generation = self._engine.snapshot()["engine_generation"]
        if (
            int(state.engine_generation or 0) > 0
            and int(state.engine_generation or 0) < current_generation
        ):
            logger.warning(
                "runtime_event=stale_generation_update task_id=%s "
                "state_generation=%s engine_generation=%s status=%s",
                state.task_id,
                state.engine_generation,
                current_generation,
                state.status,
            )
            return
        if state.status == "done" and state.failed_segment_ids:
            state.status = "error"
            state.error = "存在失败段落"
        if state.status in {"cancelled", "done", "error", "needs_attention"}:
            _runtime_event(
                "task_terminal",
                task_id=state.task_id,
                task_type="synthesis",
                status=state.status,
                completed=state.completed,
                failed=len(state.failed_segment_ids),
            )
        try:
            updated = TaskRepository.persist_runtime_state(
                state.task_id,
                self.owner_id,
                status=state.status,
                progress=self._progress(state, record),
                failed_segment_ids=list(state.failed_segment_ids),
                error_summary=state.error or (
                    "存在失败段落" if state.failed_segment_ids else ""
                ),
                log_lines=list(state.log_lines),
            )
            if updated is not None:
                with self._state_lock:
                    self._current_record = updated
        except Exception:
            logger.exception("持久化生产运行时状态失败: %s", state.task_id)

    def _apply_control(self, state: SynthesisState) -> None:
        record = TaskRepository.load_task(state.task_id)
        if record is None or record.owner_id != self.owner_id:
            return
        if record.control_intent == "cancel" and not state.cancel:
            SynthesisService.cancel(state)
        elif record.control_intent == "pause" and not state.paused:
            SynthesisService.pause(state)
        elif record.control_intent == "resume" and state.paused:
            SynthesisService.resume(state)


_INLINE_LOCK = threading.RLock()
_INLINE_RUNTIME: ProductionRuntime | None = None


class ProductionRuntimeClient:
    """Client-side lifecycle helper; task data itself always flows through SQLite."""

    @staticmethod
    def mode() -> str:
        explicit = str(
            os.environ.get("AUDIOBOOK_STUDIO_RUNTIME_MODE") or ""
        ).strip().lower()
        if explicit:
            return explicit
        if "PYTEST_CURRENT_TEST" in os.environ:
            return "inline"
        return "process"

    @classmethod
    def ensure_running(cls) -> None:
        mode = cls.mode()
        if mode in {"off", "disabled"}:
            return
        if mode == "inline":
            global _INLINE_RUNTIME
            with _INLINE_LOCK:
                if _INLINE_RUNTIME is None:
                    _INLINE_RUNTIME = ProductionRuntime()
                _INLINE_RUNTIME.start_background()
            return
        environment = dict(os.environ)
        environment["AUDIOBOOK_STUDIO_RUNTIME_MODE"] = "serve"
        command = [sys.executable, "-m", "services.production_runtime", "--serve"]
        kwargs: dict[str, Any] = {
            "cwd": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)

    @staticmethod
    def get_runtime_state(task_id: str) -> Optional[SynthesisState]:
        with _INLINE_LOCK:
            runtime = _INLINE_RUNTIME
        return runtime.get_runtime_state(task_id) if runtime is not None else None

    @staticmethod
    def poke() -> None:
        with _INLINE_LOCK:
            runtime = _INLINE_RUNTIME
        if runtime is not None:
            runtime.poke()

    @staticmethod
    def reset_inline() -> None:
        global _INLINE_RUNTIME
        with _INLINE_LOCK:
            runtime = _INLINE_RUNTIME
            _INLINE_RUNTIME = None
        if runtime is not None:
            runtime.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.serve:
        parser.error("--serve is required")
    from lib import config

    log_dir = os.path.join(config.get_data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "production_runtime.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        handlers=[handler],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime = ProductionRuntime()
    return 0 if runtime.serve_forever() else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ProductionRuntime", "ProductionRuntimeClient"]
