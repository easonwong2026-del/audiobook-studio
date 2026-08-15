"""Singleton production runtime and thin client used by Web/MCP services.

Only this runtime owns ``SynthesisService`` state and its TTS executor.  Client
processes communicate by writing transactional commands to ``TaskRepository``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from lib import project_paths
from lib import progress as synthesis_progress
from lib.failures import RecoveryBudget, RecoveryHooks, SynthesisFailure
from repositories.project_repo import ProjectRepository
from repositories._atomic import atomic_write
from repositories.task_repo import RuntimePendingSignal, TaskRecord, TaskRepository

from .runtime_engine import (
    EngineInitError,
    RuntimeEngineLifecycle,
    _pid_is_alive,
    read_runtime_engine_status,
)
from lib.tts_profile import public_profile
from .performance_trace import PerformanceTrace
from .runtime_lock import ProcessFileLock
from .synthesis import SynthesisService, SynthesisState

logger = logging.getLogger(__name__)


def _runtime_event(event: str, **fields: Any) -> None:
    """Emit one structured runtime lifecycle event into the runtime log."""
    parts = [f"{key}={value}" for key, value in fields.items() if value not in (None, "")]
    logger.info("runtime_event=%s %s", event, " ".join(parts))


def _iso_delta_ms(start: str, end: str) -> int | None:
    """Milliseconds between two UTC ISO timestamps (best effort)."""
    try:
        begin = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return max(int((finish - begin).total_seconds() * 1000), 0)
    except (TypeError, ValueError):
        return None


def _is_windows() -> bool:
    """Windows platform probe.

    独立函数而非直接读 ``os.name``：测试可通过 monkeypatch 本函数切换
    平台分支，避免污染全局 ``os.name``（全局改动会波及 pytest 自身的
    pathlib 行为，例如 Linux 上 ``Path()`` 被错误地实例化为
    ``WindowsPath`` 而崩溃）。
    """
    return os.name == "nt"


def _open_bootstrap_log():
    """Open the runtime bootstrap log for stderr redirection (Windows).

    Rationale: the runtime subprocess runs with ``stdout/stderr`` detached.
    Errors raised **before** ``main()`` configures the rotating file handler
    (import-time failures, missing module, ``--serve`` misuse) would otherwise
    vanish into ``DEVNULL``.  Redirecting stderr into a dedicated bootstrap log
    preserves them: 无黑框 ≠ 无日志.

    Returns an open text file handle, or ``None`` (→ ``DEVNULL``) when the log
    directory is unavailable (e.g. POSIX, where no console exists anyway).
    """
    if not _is_windows():
        return None
    try:
        from lib import config

        log_dir = os.path.join(config.get_data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        return open(
            os.path.join(log_dir, "production_runtime_bootstrap.log"),
            "a",
            encoding="utf-8",
            buffering=1,
        )
    except Exception:  # pragma: no cover - logging must never block startup
        logger.exception("无法打开 runtime bootstrap 日志，回退 DEVNULL")
        return None


class ProductionRuntime:
    """The only process allowed to own active synthesis state."""

    def __init__(
        self,
        *,
        owner_id: str | None = None,
        lock_path: str | None = None,
        status_path: str | None = None,
        poll_interval: float = 0.1,
        shutdown_grace: float = 20.0,
    ) -> None:
        self.owner_id = owner_id or f"runtime_{uuid.uuid4().hex}"
        self.lock = ProcessFileLock(lock_path)
        self.poll_interval = max(float(poll_interval), 0.02)
        self._idle_poll_interval = 1.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_state: SynthesisState | None = None
        self._current_record: TaskRecord | None = None
        self._current_segment_to_chapter: dict[str, str] = {}
        self._state_lock = threading.RLock()
        self._last_heartbeat = 0.0
        self._last_full_claim = 0.0
        self._last_full_heartbeat = 0.0
        self._pending_signal = RuntimePendingSignal()
        # P1 修复：每 claim 类型最后一次全扫的信号 stamp（key: synthesis/export/utility）。
        # 合成活跃期间他项目 notify → 该类型扫一次记 stamp → 后续 tick 同 stamp 跳过，
        # 避免 export claim 每 0.1s tick 全库扫描；任务 retire 后类型 stamp 仍是旧值 →
        # 新 stamp ≠ 旧值 → 立即全扫，无 30s 兜底延迟。
        self._claim_scan_stamps: dict[str, int] = {}
        # task_ids 对哪些任务已写入 first_audio_ready（避免重复落库）
        self._first_audio_ready_tasks: set[str] = set()
        self._export_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="audiobook-formal-export",
        )
        self._export_future: Future[Any] | None = None
        self._export_record: TaskRecord | None = None
        self._ownership_lock = threading.RLock()
        self._lock_release_deferred = False
        self._engine = RuntimeEngineLifecycle(
            owner_id=self.owner_id,
            status_path=status_path,
        )
        self._engine_failure = False
        self._shutdown_after_task = False
        self._shutdown_requested = False
        # Upper bound for waiting on the active segment's safe boundary before
        # forcing ``interrupted`` persistence.  Kept below the client's graceful
        # timeout so the runtime normally exits on its own, never force-killed.
        self._shutdown_grace = max(float(shutdown_grace), 0.0)
        self._shutdown_deadline: float | None = None
        self._shutdown_complete = threading.Event()
        self._shutdown_complete.set()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def requires_fresh_runtime(self) -> bool:
        """Whether the next task must be claimed by a new runtime owner."""
        return bool(self._shutdown_after_task)

    def wait_until_stopped(self, timeout: float = 5.0) -> bool:
        """Wait for a requested runtime handoff to release its ownership lock."""
        return self._shutdown_complete.wait(timeout=max(float(timeout), 0.0))

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

    def request_engine_recycle(self, engine_id: str | None = None) -> bool:
        """Recycle an idle runtime to the selected profile.

        This is called only after the client-side active-task guard.  The
        runtime repeats the guard at its ownership boundary so a stale UI
        request can never switch an engine while synthesis/export is active.

        Idempotent switch (P0-1): when the runtime is already ``ready`` and
        its current engine profile matches the requested target, the engine
        switch is treated as already satisfied — no ``recycle()`` (no
        ``reset_engine`` / ``init_engine``), no generation or recovery bump.
        This is safe because it only guards *controlled* Settings switches /
        command consumption; the self-healing recovery path calls
        ``RuntimeEngineLifecycle.recycle`` directly and still force-reloads
        the same profile when a segment failed.
        """
        with self._state_lock:
            if self._current_state is not None:
                raise RuntimeError("生产任务仍在运行，不能切换 TTS 引擎")
            if self._export_future is not None and not self._export_future.done():
                raise RuntimeError("导出任务仍在运行，不能切换 TTS 引擎")
        if self._durable_tts_task_active():
            raise RuntimeError("当前有生产任务正在运行，不能切换 TTS 引擎")
        from lib.tts_profile import profile_matches, resolve_profile

        raw = str(engine_id or "").lower()
        version = "2.5" if ("25" in raw or "2.5" in raw) else "2"
        target = resolve_profile({"engine_version": version})
        current = self._engine.snapshot()
        if str(current.get("state") or "") == "ready" and profile_matches(current, target):
            return True
        self._engine.recycle(target)
        return True

    @staticmethod
    def _engine_command_path() -> str:
        from lib import config

        return os.path.join(config.get_data_dir(), "logs", "runtime_engine_command.json")

    def _consume_engine_command(self) -> None:
        """Consume one idle-only engine switch command in the owner process."""
        path = self._engine_command_path()
        try:
            with open(path, encoding="utf-8") as file:
                command = json.load(file)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not isinstance(command, dict):
            return
        with self._state_lock:
            if self._current_state is not None or (
                self._export_future is not None and not self._export_future.done()
            ):
                return
        if self._durable_tts_task_active():
            return
        try:
            self.request_engine_recycle(str(command.get("engine_id") or ""))
        except Exception as exc:
            logger.error("runtime_event=engine_switch_failure error=%s", exc)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _shutdown_command_path() -> str:
        from lib import config

        return os.path.join(config.get_data_dir(), "logs", "runtime_shutdown_command.json")

    def _consume_shutdown_command(self) -> None:
        """Honor a parent/App shutdown request written by ProductionRuntimeClient.

        Idempotent: once the request flag is set it stays set for this serve
        cycle, so repeated command files or repeated ticks are harmless.
        """
        if self._shutdown_requested:
            return
        path = self._shutdown_command_path()
        try:
            with open(path, encoding="utf-8") as file:
                command = json.load(file)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not isinstance(command, dict) or command.get("command") != "shutdown":
            return
        self._shutdown_requested = True
        self._shutdown_after_task = True
        try:
            os.remove(path)
        except OSError:
            pass
        _runtime_event(
            "runtime_shutdown_requested",
            pid=os.getpid(),
            owner=self.owner_id,
            reason=str(command.get("reason") or ""),
        )

    def request_shutdown(
        self,
        reason: str = "",
        wait: bool = True,
        timeout: float = 30.0,
    ) -> bool:
        """In-process graceful shutdown request (inline mode & tests).

        The detached process-mode path writes a command file via
        ``ProductionRuntimeClient.request_shutdown``; this method is the
        same-process equivalent.  Returns ``True`` once the shutdown intent is
        registered — the loop stops at the next safe segment/claim boundary.
        """
        self._shutdown_requested = True
        self._shutdown_after_task = True
        self._wake.set()
        if wait:
            self.wait_until_stopped(timeout=max(float(timeout), 0.0))
        return True

    def _finalize_interrupted(self, state: SynthesisState) -> None:
        """Persist an active task as ``interrupted`` and stop the serve loop.

        Called only on a controlled Application Shutdown — never on User Cancel,
        which flows through ``control_intent=cancel`` → ``cancelled``.  Progress
        is preserved so the task stays recoverable on the next runtime takeover.
        """
        record = self._current_record
        if record is None:
            return
        try:
            TaskRepository.persist_runtime_state(
                state.task_id,
                self.owner_id,
                status="interrupted",
                progress=self._progress(state, record),
                failed_segment_ids=list(state.failed_segment_ids),
                error_summary="",
                log_lines=list(state.log_lines),
                project=record.project,
            )
        except Exception:  # pragma: no cover - defensive persistence boundary
            logger.exception("应用关闭时持久化 interrupted 状态失败: %s", state.task_id)
        _runtime_event(
            "task_interrupted_by_app_shutdown",
            task_id=state.task_id,
            project=record.project,
            owner=self.owner_id,
        )
        with self._state_lock:
            self._current_state = None
            self._current_record = None
            self._current_segment_to_chapter = {}
        self._shutdown_after_task = True
        self._stop.set()

    @staticmethod
    def _durable_tts_task_active() -> bool:
        active_states = {
            "active", "pending", "queued", "starting", "preparing", "submitting",
            "running", "pausing", "paused", "recovering", "cancelling",
        }
        task_types = {"synthesis", "voice_preview", "preview", "supplement", "export"}
        try:
            return any(
                str(getattr(record, "task_type", "")) in task_types
                and str(getattr(record, "status", "")) in active_states
                for record in TaskRepository.list_tasks()
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return True

    def start_background(self) -> bool:
        if self.requires_fresh_runtime:
            return False
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
        self._engine.set_runtime_state("starting")
        self._engine.reset()
        _runtime_event(
            "runtime_start",
            mode="inline",
            pid=os.getpid(),
            owner=self.owner_id,
            orphan_takeover=len(orphaned) or None,
        )
        self._stop.clear()
        self._shutdown_complete.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="audiobook-production-runtime",
            daemon=True,
        )
        self._thread.start()
        return True

    def serve_forever(self) -> bool:
        # A recycle failure asks the current owner to persist needs_attention
        # and retire.  A resume may launch the replacement process in the
        # small handoff window before the old worker has released its lock, so
        # the serve child waits briefly for ownership instead of exiting and
        # leaving the resumed task pending.
        deadline = time.monotonic() + 5.0
        while not self.lock.acquire(blocking=False):
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(self.poll_interval, max(deadline - time.monotonic(), 0.0)))
        orphaned = TaskRepository.mark_orphaned_interrupted(self.owner_id)
        self._engine_failure = False
        self._engine.set_runtime_state("starting")
        self._engine.reset()
        _runtime_event(
            "runtime_start",
            mode="serve",
            pid=os.getpid(),
            owner=self.owner_id,
            orphan_takeover=len(orphaned) or None,
        )
        self._stop.clear()
        self._shutdown_complete.clear()
        try:
            self._run_loop()
        finally:
            self._release_lock_when_export_safe()
        return not self._engine_failure

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
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
        self._wake.set()
        with self._state_lock:
            state = self._current_state
        if state is not None:
            self._apply_control(state)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _note_task_claim(self, record: TaskRecord) -> None:
        """Persist the durable startup transition for a claimed task."""
        now = self._utc_now()
        try:
            TaskRepository.update_startup_phase(
                record.task_id,
                "task_claimed",
                owner_id=self.owner_id,
                project=record.project,
                runtime_available_at=now,
                claimed_at=now,
            )
        except Exception:  # pragma: no cover - defensive persistence boundary
            logger.exception("记录任务 claim 启动阶段失败: %s", record.task_id)
        _runtime_event(
            "task_claim",
            task_id=record.task_id,
            task_type=record.task_type,
            project=record.project,
            owner=self.owner_id,
            pid=os.getpid(),
            claimed_at=now,
        )

    def _note_startup(
        self,
        record: TaskRecord,
        phase: str,
        *,
        engine_generation: Any = None,
        engine_was_ready: Any = None,
        extra: dict | None = None,
    ) -> None:
        """Persist a startup phase transition with optional diagnostics."""
        fields: dict = {"phase": phase}
        if engine_generation is not None:
            fields["engine_generation"] = engine_generation
        if engine_was_ready is not None:
            fields["engine_was_ready"] = engine_was_ready
        if extra:
            fields.update(extra)
        try:
            TaskRepository.update_startup_phase(
                record.task_id,
                phase,
                owner_id=self.owner_id,
                project=record.project,
                **{key: value for key, value in fields.items() if key != "phase"},
            )
        except Exception:  # pragma: no cover - defensive persistence boundary
            logger.exception("记录启动阶段 %s 失败: %s", phase, record.task_id)

    def _claim_pending(
        self,
        key: str,
        task_types: set[str] | frozenset[str],
        force: bool,
    ) -> Optional[TaskRecord]:
        """Per-claim-type signal stamp 去重的权威全扫。

        每次只读一次信号戳：若该类型在本戳上已全扫过（且非 force），直接跳过，
        避免“合成活跃期间他项目 notify → export claim 每 tick 全扫”退化回基线。
        force=True（30s 兜底 / 首轮）忽略戳去重，无条件执行权威全扫。
        """
        stamp = self._pending_signal.stamp_ns()
        if not force and (stamp == -1 or stamp == self._claim_scan_stamps.get(key, -1)):
            return None
        record = TaskRepository.claim_next_pending(self.owner_id, task_types, force=True)
        self._claim_scan_stamps[key] = stamp
        return record

    def _run_loop(self) -> None:
        try:
            self._engine.set_runtime_state("running")
            while not self._stop.is_set():
                now0 = time.monotonic()
                # 周期兜底：每 30s 强制一次权威全扫，防止信号文件被清理/漏发。
                force_claim = (now0 - self._last_full_claim >= 30.0)
                if force_claim:
                    self._last_full_claim = now0
                with self._state_lock:
                    state = self._current_state
                # Honor a parent/App shutdown request at the top of every tick.
                self._consume_shutdown_command()
                if self._shutdown_requested:
                    if state is None:
                        # Idle runtime: nothing active, exit immediately.
                        self._stop.set()
                        break
                    with self._state_lock:
                        cur = self._current_state
                    if cur is not None and not cur.shutdown_requested:
                        # Active task: ask the synthesis loop to stop at the
                        # next safe segment boundary (do not cancel).
                        cur.shutdown_requested = True
                        cur.append_log("⏹ 应用关闭请求，将在当前段完成后中断")
                        cur.notify()
                        self._shutdown_deadline = time.monotonic() + self._shutdown_grace
                if state is not None:
                    self._apply_control(state)
                    future = state.future
                    if state.status in {"cancelled", "done", "error", "needs_attention"} and (
                        future is None or future.done()
                    ):
                        retire_after_task = self._shutdown_after_task
                        with self._state_lock:
                            self._current_state = None
                            self._current_record = None
                            self._current_segment_to_chapter = {}
                        if retire_after_task:
                            # The worker has already persisted its terminal
                            # state.  Only now request loop exit so ownership
                            # cannot be handed to a new runtime mid-task.
                            self._stop.set()
                        state = None
                # Controlled Application Shutdown: the synthesis worker broke at
                # a safe segment boundary (``state.shutdown_requested``) without
                # reaching a terminal status.  Only now — once the worker has
                # actually stopped, so no segment is mid-write — persist
                # ``interrupted`` (NOT cancelled) and stop the loop.  A bounded
                # grace period guarantees exit even if a segment hangs.
                if self._shutdown_requested and self._current_state is not None:
                    cur = self._current_state
                    if cur.status not in {"cancelled", "done", "error", "needs_attention"}:
                        future = cur.future
                        at_boundary = future is None or future.done()
                        expired = (
                            self._shutdown_deadline is not None
                            and time.monotonic() >= self._shutdown_deadline
                        )
                        if at_boundary or expired:
                            if expired and not at_boundary:
                                _runtime_event(
                                    "runtime_shutdown_boundary_timeout",
                                    task_id=cur.task_id,
                                    owner=self.owner_id,
                                )
                            self._finalize_interrupted(cur)
                            state = None
                # 合成 claim 放在 retire 之后：若本 tick 刚 retire 上一个任务，
                # synthesis 类型的 stamp 仍是旧值 → 新 notify 戳 ≠ 旧值 → 立即
                # 全扫 claim 他项目 pending 任务（无 30s 兜底延迟）。
                if state is None and not self._shutdown_requested:
                    self._consume_engine_command()
                    record = self._claim_pending("synthesis", {"synthesis"}, force_claim)
                    if record is not None:
                        self._note_task_claim(record)
                        self._launch(record)
                if self._export_future is not None and self._export_future.done():
                    self._export_future = None
                    self._export_record = None
                    # 排队导出由 per-type stamp 机制覆盖：export claim 在 worker
                    # 运行期间被跳过 → 其 stamp 未更新 → worker 结束后立即全扫。
                if self._export_future is None and not self._shutdown_requested:
                    export_record = self._claim_pending("export", {"export"}, force_claim)
                    if export_record is not None:
                        self._note_task_claim(export_record)
                        self._launch_export(export_record)
                if (
                    self._current_state is None
                    and self._export_future is None
                    and not self._shutdown_requested
                ):
                    utility_record = self._claim_pending(
                        "utility", {"supplement", "voice_preview"}, force_claim
                    )
                    if utility_record is not None:
                        self._note_task_claim(utility_record)
                        self._launch(utility_record)
                now = time.monotonic()
                if now - self._last_heartbeat >= 1.0:
                    # heartbeat 局部化：只更新 Runtime 当前持有的项目 DB，
                    # 30s 全扫兜底保持 orphan 可观测语义（频率 30× 降低）。
                    with self._state_lock:
                        owned_projects = []
                        if self._current_record is not None:
                            owned_projects.append(self._current_record.project)
                        if self._export_record is not None:
                            owned_projects.append(self._export_record.project)
                    if owned_projects:
                        TaskRepository.update_runtime_heartbeat(
                            self.owner_id, projects=owned_projects
                        )
                    if now - self._last_full_heartbeat >= 30.0:
                        TaskRepository.update_runtime_heartbeat(self.owner_id)
                        self._last_full_heartbeat = now
                    self._engine.heartbeat()
                    self._last_heartbeat = now
                # active 保持 poll_interval（0.1s，pause/cancel 响应 ≤100ms），
                # idle 降频到 _idle_poll_interval（1s）；poke()/stop() 立即唤醒。
                active = (
                    self._current_state is not None
                    or self._export_future is not None
                )
                delay = self.poll_interval if active else self._idle_poll_interval
                self._wake.wait(delay)
                self._wake.clear()
        except Exception:
            logger.exception("生产运行时主循环异常退出")
            raise
        finally:
            # Do not mark tasks here.  A new process must first prove ownership
            # by acquiring the OS lock before it can repair interrupted tasks.
            if self._thread is threading.current_thread():
                self._thread = None
            self._engine.mark_unknown()
            self._shutdown_complete.set()
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
    def _on_segment_audio(record: TaskRecord):
        """Persist the task-frozen engine identity with each active revision."""
        options = record.options if isinstance(record.options, dict) else {}
        profile = options.get("engine_snapshot") if isinstance(options.get("engine_snapshot"), dict) else None

        def _segment_inputs(segment_id: str) -> tuple[dict[str, Any] | None, str | None]:
            try:
                from lib import segment_cache

                _meta, script, _bindings = ProjectRepository.load_project(record.project)
                segment = next(
                    (
                        item
                        for chapter in script.get("chapters", [])
                        if isinstance(chapter, dict)
                        for item in chapter.get("segments", [])
                        if isinstance(item, dict) and str(item.get("id") or "") == str(segment_id)
                    ),
                    None,
                )
                if segment is None:
                    return None, None
                overrides = {
                    "emotion": options.get("emotion"),
                    "override": options.get("emo_alpha") is not None
                    or options.get("speech_rate") is not None,
                    "emo_alpha": options.get("emo_alpha") if options.get("emo_alpha") is not None else 1.0,
                    "speech_rate": options.get("speech_rate") if options.get("speech_rate") is not None else 1.0,
                }
                emotion, emo_alpha, speech_rate = segment_cache.effective_params(segment, overrides)
                params = {
                    "emotion": emotion,
                    "emo_alpha": emo_alpha,
                    "speech_rate": speech_rate,
                    "engine_snapshot": profile,
                }
                override = options.get("voice_overrides")
                speaker_override = override.get(str(segment_id)) if isinstance(override, dict) else None
                if speaker_override and not os.path.isabs(str(speaker_override)):
                    speaker_override = os.path.join(
                        ProjectRepository.get_project_dir(record.project), str(speaker_override)
                    )
                return params, speaker_override
            except Exception:
                return None, None

        def _callback(segment_id, path):
            try:
                from services.quality import QualityService

                params, speaker_override = _segment_inputs(str(segment_id))

                QualityService.ensure_active_revision(
                    record.project,
                    str(segment_id),
                    engine_snapshot=profile,
                    source_path=path,
                    params=params,
                    speaker_override=speaker_override,
                )
            except Exception:
                logger.debug("记录 segment engine revision 失败: %s", segment_id, exc_info=True)

        return _callback

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
        segment_to_chapter = self._segment_to_chapter(record.project)
        scope_segment_ids = (
            list(segments)
            if segments
            else [
                segment_id
                for segment_id, chapter_id in segment_to_chapter.items()
                if not chapters or chapter_id in set(chapters)
            ]
        )
        try:
            state.segment_states = synthesis_progress.build_segment_states(
                record.project,
                chapters or None,
                scope_segment_ids if segments else None,
            )
        except Exception:
            state.segment_states = []
        with self._state_lock:
            self._current_record = record
            self._current_state = state
            self._current_segment_to_chapter = segment_to_chapter
        # P0: engine preflight inside the runtime process.  A failed init is
        # fatal for the whole task: no segment loop, no per-segment errors.
        engine_before = self._engine.snapshot()
        load_started = self._utc_now()
        self._note_startup(
            record,
            "engine_loading",
            engine_was_ready=engine_before["state"] == "ready",
            extra={"engine_load_started_at": load_started},
        )
        _runtime_event(
            "engine_init_begin",
            task_id=record.task_id,
            project=record.project,
            pid=os.getpid(),
            engine_state=engine_before["state"],
            engine_generation=engine_before["engine_generation"],
            timestamp=load_started,
        )
        try:
            options = record.options if isinstance(record.options, dict) else {}
            engine_profile = options.get("engine_snapshot") if isinstance(options.get("engine_snapshot"), dict) else None
            self._engine.ensure_ready(engine_profile)
        except EngineInitError as exc:
            self._fail_synthesis_engine_init(record, state, exc)
            return
        if self._shutdown_requested:
            # Application shutdown arrived during engine loading: do not start
            # synthesis; leave the freshly-claimed task as interrupted.
            self._finalize_interrupted(state)
            return
        engine_snapshot = self._engine.snapshot()
        state.engine_generation = engine_snapshot["engine_generation"]
        ready_at = self._utc_now()
        self._note_startup(
            record,
            "engine_ready",
            engine_generation=state.engine_generation,
            extra={"engine_ready_at": ready_at},
        )
        _runtime_event(
            "engine_init_success",
            task_id=record.task_id,
            project=record.project,
            pid=os.getpid(),
            engine_generation=state.engine_generation,
            duration_ms=_iso_delta_ms(load_started, ready_at),
        )
        # The task is now claimed by the singleton runtime.  Re-check the
        # exact task scope and lock only roles that this task will actually
        # use.  A draft whole-book cast is valid here; the lock is per role.
        # ``selected_total`` is written by the current ProductionJobService;
        # its absence identifies legacy/direct TaskRecord callers that predate
        # scope readiness and must retain their old runtime behavior.
        if isinstance(record.progress, dict) and "selected_total" in record.progress:
            try:
                from .voice_cast import VoiceCastError, VoiceCastResolver

                VoiceCastResolver.lock_production_scope(
                    record.project,
                    scope_segment_ids,
                )
            except VoiceCastError as exc:
                state.status = "error"
                state.error = str(exc)
                state.append_log(f"❌ 生产范围校验失败: {exc}")
                state.notify()
                return
            except Exception as exc:  # pragma: no cover - defensive runtime boundary
                state.status = "error"
                state.error = str(exc)
                state.append_log(f"❌ 生产范围校验异常: {exc}")
                state.notify()
                return
        options = record.options if isinstance(record.options, dict) else {}
        from lib import config

        performance_trace = PerformanceTrace(
            task_id=record.task_id,
            project=record.project,
            persist_path=os.path.join(
                project_paths.project_dir(config.get_data_dir(), "logs", create=True),
                "performance",
                f"{record.task_id}.json",
            ),
            engine=public_profile(engine_snapshot),
        )
        self._note_startup(record, "preparing_first_segment")
        _runtime_event(
            "task_start",
            task_id=record.task_id,
            task_type=record.task_type,
            project=record.project,
        )
        first_segment_started = self._utc_now()
        self._note_startup(
            record,
            "synthesizing_first_segment",
            extra={"first_segment_started_at": first_segment_started},
        )
        _runtime_event(
            "first_segment_begin",
            task_id=record.task_id,
            project=record.project,
            pid=os.getpid(),
            engine_generation=state.engine_generation,
            timestamp=first_segment_started,
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
                performance_trace=performance_trace,
                engine_identity=engine_snapshot.get("cache_identity"),
                cb_audio=self._on_segment_audio(record),
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
            options = record.options if isinstance(record.options, dict) else {}
            profile = options.get("engine_snapshot") if isinstance(options.get("engine_snapshot"), dict) else None
            generation = self._engine.recycle(profile)
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
            if state.performance_trace is not None:
                try:
                    state.performance_trace.record_event(
                        f"recovery:{event_name}",
                        data=payload,
                    )
                    state.performance_trace.record_boundary(
                        f"recovery:{event_name}"
                    )
                except Exception:  # noqa: BLE001  # diagnostics must not alter recovery
                    logger.debug("记录 recovery trace 失败", exc_info=True)
            previous = dict(state.recovery or {})
            if event_name == "recovering":
                self._engine.set_runtime_state("recovering")
            elif event_name == "recovered":
                self._engine.set_runtime_state("running")
            elif event_name == "recycle_failed":
                self._engine.set_runtime_state("error")
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
                "message": str(
                    payload.get("message") or previous.get("message") or ""
                ),
                "traceback_origin": str(
                    payload.get("traceback_origin")
                    or previous.get("traceback_origin")
                    or ""
                ),
                "code": str(
                    payload.get("code") or previous.get("code") or ""
                ),
                "recycle_exception_type": str(
                    payload.get("recycle_exception_type")
                    or previous.get("recycle_exception_type")
                    or ""
                ),
                "recycle_errno": (
                    payload.get("recycle_errno")
                    if payload.get("recycle_errno") is not None
                    else previous.get("recycle_errno")
                ),
                "recycle_message": str(
                    payload.get("recycle_message")
                    or previous.get("recycle_message")
                    or ""
                ),
                "recycle_traceback_origin": str(
                    payload.get("recycle_traceback_origin")
                    or previous.get("recycle_traceback_origin")
                    or ""
                ),
                "recycles_used": int(
                    payload.get("recycles_used")
                    or previous.get("recycles_used")
                    or payload.get("attempt")
                    or previous.get("attempt")
                    or 0
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
                state.status = "needs_attention"
                state.error = (
                    "TTS_ENGINE_RECYCLE_FAILED: "
                    f"{payload.get('recycle_message') or '引擎回收失败'}"
                )
                state.append_log("❌ 引擎回收失败，当前运行时将退出并交给新 Runtime 接管")
                state.notify()
                # Do not stop the loop until the synthesis future returns; the
                # callback above must durably persist needs_attention first.
                self._shutdown_after_task = True
                self._engine_failure = True
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
        progress = dict(record.progress) if isinstance(record.progress, dict) else {}
        progress.update({
            "total": max(int(state.total or progress.get("total", 0) or 0), 0),
            "completed": max(int(state.completed or 0), 0),
            "failed": len(state.failed_segment_ids),
            "attempted": 0,
            "percent": round(
                (state.completed / state.total) * 100, 1
            ) if state.total else 0.0,
            "current_chapter": None,
            "current_segment": None,
        })
        progress["selected_total"] = progress["total"]
        progress["pending"] = max(
            progress["total"] - progress["completed"] - progress["failed"],
            0,
        )
        progress["to_synthesize"] = progress["pending"] + progress["failed"]
        state.append_log("❌ TTS 引擎初始化失败，任务未开始")
        state.append_log(f"错误: {exc.summary}")
        try:
            TaskRepository.update_startup_phase(
                record.task_id,
                "engine_failed",
                owner_id=self.owner_id,
                project=record.project,
                engine_error_code="TTS_ENGINE_INIT_FAILED",
                engine_error_summary=str(exc.summary),
            )
        except Exception:
            logger.exception("记录引擎初始化失败启动阶段失败: %s", record.task_id)
        try:
            TaskRepository.persist_runtime_state(
                record.task_id,
                self.owner_id,
                status="error",
                progress=progress,
                failed_segment_ids=[],
                error_summary=str(exc),
                log_lines=list(state.log_lines),
                project=record.project,
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
            project=record.project,
        )
        current = running or record
        try:
            result = ExportService.execute_export_job(
                current,
                is_cancelled=lambda: self._export_cancel_requested(
                    record.task_id, record.project
                ),
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
                project=record.project,
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
                project=record.project,
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
                project=record.project,
            )
            _runtime_event(
                "task_terminal",
                task_id=record.task_id,
                task_type="export",
                status="error",
            )

    @staticmethod
    def _export_cancel_requested(task_id: str, project: str) -> bool:
        record = TaskRepository.load_project_task(project, task_id)
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
        engine_profile: dict[str, Any] | None = None,
        progress_cb: Any = None,
    ) -> str:
        """Runtime-owned direct worker; public callers submit through RuntimeTTSService."""
        from lib import config

        self._engine.ensure_ready(engine_profile, progress_cb=progress_cb)

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
        engine_profile: dict[str, Any] | None = None,
        progress_cb: Any = None,
    ) -> list[dict[str, Any]]:
        """Runtime-owned isolated supplement worker."""
        from lib import config

        if initialize:
            self._engine.ensure_ready(engine_profile, progress_cb=progress_cb)

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
        lines = list(payload.get("lines", []))
        total = max(len(lines), 1)
        for index, raw_text in enumerate(lines):
            line_started = time.monotonic()
            if callable(heartbeat):
                heartbeat()
            text = str(raw_text or "").strip()
            output = os.path.join(destination, f"{index + 1:03d}.wav")
            if progress_cb is not None:
                progress_cb(
                    "supplement_infer_start",
                    line_index=index,
                    line_total=total,
                )
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
                if progress_cb is not None:
                    progress_cb(
                        "supplement_infer_done",
                        line_index=index,
                        line_total=total,
                        status="ok",
                        elapsed_ms=int((time.monotonic() - line_started) * 1000),
                    )
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
                if progress_cb is not None:
                    progress_cb(
                        "supplement_infer_done",
                        line_index=index,
                        line_total=total,
                        status="failed",
                        elapsed_ms=int((time.monotonic() - line_started) * 1000),
                    )
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
                project=record.project,
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
            project=record.project,
        )
        current = running or record
        options = record.options if isinstance(record.options, dict) else {}
        snapshot = options.get("engine_snapshot") if isinstance(options.get("engine_snapshot"), dict) else None

        def _task_progress(event: str, **fields: Any) -> None:
            """Persist one structured phase into the task log + runtime log.

            Fields are path-free identity/elapsed data; the same event also
            goes through ``_runtime_event`` so the runtime log carries the
            full chain (``engine_load_start`` / ``engine_init_done`` /
            ``supplement_infer_start`` / …).
            """
            nonlocal current
            fields = dict(fields)
            fields.setdefault("task_id", record.task_id)
            fields.setdefault("owner", self.owner_id)
            detail = " ".join(
                f"{key}={value}"
                for key, value in fields.items()
                if value not in (None, "")
            )
            try:
                progress = dict(current.progress or {})
                log_lines = list(current.log_lines or [])
                log_lines.append(f"[{event}] {detail}".strip())
                updated = TaskRepository.persist_runtime_state(
                    record.task_id,
                    self.owner_id,
                    status="running",
                    progress=progress,
                    failed_segment_ids=list(record.failed_segment_ids or []),
                    error_summary="",
                    log_lines=log_lines,
                    project=record.project,
                )
                if updated is not None:
                    current = updated
            except Exception:
                logger.debug("记录 utility 任务阶段失败: %s", event, exc_info=True)
            _runtime_event(event, **fields)

        # Engine intent diagnostics: if the claimed task needs a cold load or a
        # profile switch, say so *before* the blocking reload so the web wait
        # loop can render "正在加载 IndexTTS 2.5…" instead of dead silence.
        try:
            from lib.tts_profile import profile_matches, resolve_profile

            engine_before = self._engine.snapshot()
            desired = resolve_profile(snapshot or {})
            loaded_id = str(engine_before.get("engine_identity") or "")
            target_id = str(desired.get("engine_identity") or "")
            engine_state = str(engine_before.get("state") or "unknown")
            match = engine_state == "ready" and profile_matches(engine_before, desired)
            _task_progress(
                "engine_intent",
                current_engine_identity=loaded_id or "none",
                target_engine_identity=target_id or "none",
                current_engine_version=str(engine_before.get("engine_version") or ""),
                target_engine_version=str(desired.get("engine_version") or ""),
                profile_match="true" if match else "false",
                engine_state=engine_state,
            )
        except Exception:  # pragma: no cover - diagnostics must never break the task
            logger.debug("引擎意图诊断失败: %s", record.task_id, exc_info=True)
        try:
            if record.task_type == "voice_preview":
                preview = self.run_voice_preview_direct(
                    str(record.options.get("speaker_audio") or ""),
                    str(record.options.get("role") or "voice"),
                    artifact_dir,
                    record.options.get("engine_snapshot") if isinstance(record.options, dict) else None,
                    progress_cb=_task_progress,
                )
                result: dict[str, Any] = {"preview_path": preview}
                completed, failed = 3, 0
            elif record.task_type == "supplement":
                items = self.run_supplement_direct(
                    record.options,
                    artifact_dir,
                    heartbeat=lambda: TaskRepository.update_runtime_heartbeat(
                        self.owner_id, projects=[record.project]
                    ),
                    initialize=True,
                    validate_output=True,
                    engine_profile=record.options.get("engine_snapshot") if isinstance(record.options, dict) else None,
                    progress_cb=_task_progress,
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
                project=record.project,
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
                project=record.project,
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
        progress = dict(record.progress) if isinstance(record.progress, dict) else {}
        progress.update({
            "total": total,
            "completed": completed,
            "failed": len(failed_ids),
            "percent": round((completed / total) * 100, 1) if total else 0.0,
            "current_chapter": chapter,
            "current_segment": state.current_segment,
            "engine_generation": int(state.engine_generation or 0),
            "recovery": state.recovery,
            "last_failure": state.last_failure,
        })
        progress["selected_total"] = total
        progress["pending"] = max(total - completed - len(failed_ids), 0)
        progress["to_synthesize"] = progress["pending"] + len(failed_ids)
        if (
            state.performance_trace is not None
            and state.status in {"cancelled", "done", "error", "needs_attention"}
        ):
            try:
                progress["performance"] = state.performance_trace.summary()
            except Exception:  # noqa: BLE001  # diagnostics must not alter persistence
                logger.debug("读取 performance summary 失败", exc_info=True)
        return progress

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
        if (
            state.task_id not in self._first_audio_ready_tasks
            and int(state.completed or 0) >= 1
            and state.status in {"running", "done"}
        ):
            self._first_audio_ready_tasks.add(state.task_id)
            first_audio_at = self._utc_now()
            try:
                TaskRepository.update_startup_phase(
                    state.task_id,
                    "running",
                    owner_id=self.owner_id,
                    project=record.project,
                    first_audio_ready_at=first_audio_at,
                )
            except Exception:
                logger.exception("记录首段完成启动阶段失败: %s", state.task_id)
            _runtime_event(
                "first_audio_ready",
                task_id=state.task_id,
                project=record.project,
                pid=os.getpid(),
                engine_generation=current_generation,
                timestamp=first_audio_at,
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
                project=record.project,
            )
            if updated is not None:
                with self._state_lock:
                    self._current_record = updated
        except Exception:
            logger.exception("持久化生产运行时状态失败: %s", state.task_id)

    def _apply_control(self, state: SynthesisState) -> None:
        # P0-1: Runtime 已知 state.project，project-local O(1) 读取，避免全库扫描。
        record = TaskRepository.load_project_task(state.project, state.task_id)
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

# Exact, process-owned handle to the runtime subprocess spawned by THIS process
# (via ``ensure_running``).  Used only as a precise termination fallback after a
# graceful shutdown timeout.  Pre-existing runtimes (spawned by another process,
# e.g. a previous app session) are NOT referenced here and are terminated only
# after runtime-identity verification — never by blind pid or process scan.
_RUNTIME_PROCESS: "subprocess.Popen | None" = None
_PROCESS_LOCK = threading.Lock()


def _wait_for_pid_exit(pid: int, timeout: float) -> bool:
    """Event-driven bounded wait for a pid to disappear (no fixed sleep)."""
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while True:
        if not _pid_is_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _terminate_pid(pid: int, timeout: float = 10.0) -> None:
    """Terminate a *confirmed* Audiobook Studio runtime by pid (last resort).

    Only ever called after ``read_runtime_engine_status`` has verified the pid
    belongs to a live, owned runtime.  Never used against arbitrary pids.
    """
    if pid <= 0 or not _pid_is_alive(pid):
        return
    if _is_windows():
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
        except Exception:  # pragma: no cover - platform specific
            logger.exception("Windows 终止 Runtime 进程失败 pid=%s", pid)
        # TerminateProcess is asynchronous: wait for the kernel to actually
        # reap it, otherwise the caller may probe the lock too early.
        _wait_for_pid_exit(pid, timeout)
        return
    try:
        os.kill(pid, signal.SIGTERM)
        if _wait_for_pid_exit(pid, timeout):
            return
        os.kill(pid, signal.SIGKILL)
        _wait_for_pid_exit(pid, timeout)
    except Exception:  # pragma: no cover - platform specific
        logger.exception("POSIX 终止 Runtime 进程失败 pid=%s", pid)


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
    def request_engine_recycle(cls, engine_id: str) -> bool:
        """Ask the singleton owner to reload an idle selected engine."""
        mode = cls.mode()
        if mode in {"off", "disabled"}:
            raise RuntimeError("生产 runtime 已禁用")
        if mode == "inline":
            with _INLINE_LOCK:
                runtime = _INLINE_RUNTIME
            if runtime is None:
                cls.ensure_running()
                with _INLINE_LOCK:
                    runtime = _INLINE_RUNTIME
            if runtime is None:
                raise RuntimeError("无法取得 inline runtime")
            return runtime.request_engine_recycle(engine_id)
        path = ProductionRuntime._engine_command_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write(path, {
            "engine_id": str(engine_id or ""),
            "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        })
        cls.ensure_running()
        return True

    @classmethod
    def _resolve_runtime_launch(cls) -> tuple[list[str], dict[str, str]]:
        """Resolve a console-less launch for the runtime subprocess (Windows).

        Root cause: on uv-managed venvs, ``<venv>/Scripts/python.exe`` is a
        small launcher stub.  When spawned with ``DETACHED_PROCESS`` it
        re-spawns the real interpreter as a NEW child **without propagating
        the creation flags**, so the actual runtime process ends up with its
        own visible console (the black box) — verified by process-tree tracing
        (a conhost.exe appears under the runtime during engine bootstrap).

        Fix: resolve the real interpreter from ``pyvenv.cfg`` ``home`` and
        bootstrap the venv site-packages via ``site.addsitedir`` (processes
        ``.pth`` files, including PEP-660 editable installs) before running
        the runtime module.  Spawned with ``DETACHED_PROCESS`` this yields a
        genuinely console-less runtime (no conhost in the tree).

        Applies only on Windows for a stub-sized venv python (<=100 KB).
        Any resolution failure falls back to the default ``python -m ...``.
        """
        if not _is_windows():
            return [sys.executable, "-m", "services.production_runtime", "--serve"], {}
        try:
            venv_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
            cfg_path = os.path.join(venv_dir, "pyvenv.cfg")
            if not os.path.isfile(cfg_path):
                return [sys.executable, "-m", "services.production_runtime", "--serve"], {}
            try:
                stub_size = os.path.getsize(sys.executable)
            except OSError:
                stub_size = 0
            if stub_size and stub_size > 100 * 1024:
                # 真实解释器（非 uv stub）：DETACHED 直接生效，无需绕行。
                return [sys.executable, "-m", "services.production_runtime", "--serve"], {}
            home = ""
            with open(cfg_path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    key, _, value = line.partition("=")
                    if key.strip() == "home":
                        home = value.strip().strip('"')
                        break
            base_python = os.path.join(home, "python.exe")
            venv_site = os.path.join(venv_dir, "Lib", "site-packages")
            if not home or not os.path.isfile(base_python) or not os.path.isdir(venv_site):
                return [sys.executable, "-m", "services.production_runtime", "--serve"], {}
            bootstrap = (
                "import site, runpy, sys; "
                "site.addsitedir(%r); "
                "sys.argv=['services.production_runtime','--serve']; "
                "runpy.run_module('services.production_runtime', run_name='__main__')"
            ) % venv_site
            logger.info(
                "runtime_event=runtime_launch_resolved interpreter=%s venv_site=%s",
                base_python,
                venv_site,
            )
            return [base_python, "-c", bootstrap], {}
        except Exception:  # pragma: no cover - fallback must never break spawn
            logger.exception("解析 runtime 真实解释器失败，回退默认启动")
            return [sys.executable, "-m", "services.production_runtime", "--serve"], {}

    @classmethod
    def ensure_running(cls) -> Optional[int]:
        """Ensure the singleton runtime subprocess is running.

        Returns the spawned child pid, or ``None`` when the runtime is already
        running / disabled / inline (nothing new was spawned).
        """
        mode = cls.mode()
        if mode in {"off", "disabled"}:
            return None
        if mode == "inline":
            global _INLINE_RUNTIME
            retired: ProductionRuntime | None = None
            with _INLINE_LOCK:
                runtime = _INLINE_RUNTIME
            if runtime is not None and runtime.requires_fresh_runtime:
                runtime.wait_until_stopped(timeout=5.0)
                if runtime.is_running:
                    # The old owner has not completed its durable handoff
                    # yet.  Leave the pending task for the next poke rather
                    # than allowing two inline runtimes to race for the GPU.
                    return None
                with _INLINE_LOCK:
                    if _INLINE_RUNTIME is runtime:
                        _INLINE_RUNTIME = None
                        retired = runtime
            if retired is not None:
                retired.stop(timeout=0.0)
            with _INLINE_LOCK:
                if _INLINE_RUNTIME is None:
                    _INLINE_RUNTIME = ProductionRuntime()
                _INLINE_RUNTIME.start_background()
            return None
        # Process mode single-flight: a lock-holding runtime is already alive,
        # spawning a duplicate would only churn a 5s lock-wait process.
        from .runtime_lock import ProcessFileLock

        probe = ProcessFileLock()
        if not probe.acquire(blocking=False):
            logger.info("runtime_event=runtime_already_running skip_spawn=true")
            return None
        probe.release()
        environment = dict(os.environ)
        environment["AUDIOBOOK_STUDIO_RUNTIME_MODE"] = "serve"
        command, extra_env = cls._resolve_runtime_launch()
        environment.update(extra_env)
        bootstrap = _open_bootstrap_log()
        kwargs: dict[str, Any] = {
            "cwd": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": bootstrap if bootstrap is not None else subprocess.DEVNULL,
            "close_fds": True,
        }
        if _is_windows():
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            kwargs["start_new_session"] = True
        try:
            child = subprocess.Popen(command, **kwargs)
        finally:
            if bootstrap is not None:
                bootstrap.close()
        global _RUNTIME_PROCESS
        with _PROCESS_LOCK:
            _RUNTIME_PROCESS = child
        logger.info(
            "runtime_event=runtime_spawn pid=%s parent_pid=%s command=%s",
            child.pid,
            os.getpid(),
            " ".join(command),
        )
        return child.pid

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

    # ── Graceful shutdown (orphan Runtime fix) ──────────────────────────────

    @staticmethod
    def _shutdown_command_path() -> str:
        return ProductionRuntime._shutdown_command_path()

    @classmethod
    def _runtime_is_running(cls) -> bool:
        """True when a runtime owns the singleton lock (i.e. is alive)."""
        lock = ProcessFileLock()
        if lock.acquire(blocking=False):
            lock.release()
            return False
        return True

    @classmethod
    def _runtime_has_exited(cls) -> bool:
        """True once the runtime process is really gone.

        The singleton lock is the authoritative signal: it is an OS advisory
        lock, so it is released the moment the owning process dies — including
        a hard kill.  A *stale status file* must NOT count as "exited": a hung
        runtime that stopped publishing status while still holding the lock is
        precisely the orphan we are trying to reap, and treating it as gone
        would make ``request_shutdown`` report a false success.

        The only extra signal we trust is the exact process handle for a
        runtime this process spawned: if it has exited, our runtime is gone
        regardless of who else may hold the lock.
        """
        lock = ProcessFileLock()
        if lock.acquire(blocking=False):
            lock.release()
            return True
        with _PROCESS_LOCK:
            proc = _RUNTIME_PROCESS
        return proc is not None and proc.poll() is not None

    @classmethod
    def _write_shutdown_command(cls, reason: str) -> None:
        path = cls._shutdown_command_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write(path, {
            "command": "shutdown",
            "reason": str(reason or ""),
            "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        })

    @classmethod
    def _clear_runtime_process(cls) -> None:
        global _RUNTIME_PROCESS
        with _PROCESS_LOCK:
            _RUNTIME_PROCESS = None

    @classmethod
    def _terminate_runtime(cls, terminate_timeout: float = 10.0) -> None:
        """Forced termination fallback after a graceful timeout.

        Priority: exact-owned process handle → hard kill.  For a pre-existing
        runtime (no handle) terminate by pid ONLY after confirming it is
        genuinely our runtime (fresh status + owner_id + alive pid).  This
        prevents killing an unrelated process that reuses a stale pid.
        """
        with _PROCESS_LOCK:
            proc = _RUNTIME_PROCESS
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=max(float(terminate_timeout), 0.0))
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return
        status = read_runtime_engine_status()
        pid = int(status.get("pid") or 0)
        if status.get("status_stale") or not status.get("owner_id") or pid <= 0:
            return
        if not _pid_is_alive(pid):
            return
        _terminate_pid(pid, timeout=max(float(terminate_timeout), 0.0))

    @classmethod
    def request_shutdown(
        cls,
        reason: str = "application_shutdown",
        timeout: float = 30.0,
        terminate_timeout: float = 10.0,
    ) -> bool:
        """Gracefully stop the detached production runtime (cross-process).

        Protocol:
          1. if no runtime is running → return (idempotent no-op).
          2. atomically write ``runtime_shutdown_command.json`` (idempotent).
          3. wait (event-driven on lock release / status staleness) up to
             ``timeout`` for the runtime to exit on its own safe boundary.
          4. on timeout → terminate the *exact-owned* process handle, then
             hard-kill as final fail-safe; for a pre-existing runtime verify
             runtime identity (fresh status + owner_id + pid alive) before
             terminating by pid.  Never kills unrelated Python processes.

        Returns ``True`` if the runtime is gone (or was never running).
        """
        mode = cls.mode()
        if mode == "inline":
            with _INLINE_LOCK:
                runtime = _INLINE_RUNTIME
            if runtime is None:
                return True
            runtime.request_shutdown(reason=reason, wait=False)
            runtime.wait_until_stopped(timeout=max(float(timeout), 0.0))
            return True
        if not cls._runtime_is_running():
            return True
        cls._write_shutdown_command(reason)
        if cls._wait_for_exit(timeout):
            cls._clear_runtime_process()
            _runtime_event("runtime_shutdown_graceful", reason=reason)
            return True
        # Graceful timeout → forced termination (precise ownership only).
        _runtime_event("runtime_shutdown_graceful_timeout", reason=reason)
        cls._terminate_runtime(terminate_timeout=max(float(terminate_timeout), 0.0))
        # The OS releases the lock as the process dies, but not necessarily
        # before ``_terminate_*`` returns; wait (bounded) instead of racing.
        exited = cls._wait_for_exit(min(max(float(terminate_timeout), 0.0), 10.0))
        cls._clear_runtime_process()
        _runtime_event("runtime_shutdown_forced", reason=reason, exited=exited)
        return exited

    @classmethod
    def _wait_for_exit(cls, timeout: float) -> bool:
        """Bounded, event-driven wait on runtime disappearance (no fixed sleep)."""
        deadline = time.monotonic() + max(float(timeout), 0.0)
        while True:
            if cls._runtime_has_exited():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


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
