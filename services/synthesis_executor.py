"""Transactional v4 synthesis execution with cache and bounded task splitting."""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.v4.production import TtsProfile
from repositories.audio_cache_repository import AudioCacheRepository
from repositories.runtime_repository import RuntimeRepository
from tts.base_adapter import (
    RecoverableTtsError,
    RuntimeTtsAdapter,
    TtsOutOfMemoryError,
)
from tts.runtime_cleanup import release_inference_memory
from tts.runtime_monitor import MemorySnapshot, RuntimeMonitor
from tts.text_measurement import ConservativeTokenMeasurer, TextMeasurer


@dataclass(frozen=True)
class ExecutionSummary:
    completed: int
    cache_hits: int
    split_parents: int
    failed: int
    cancelled: bool


class SynthesisExecutor:
    def __init__(
        self,
        runtime: RuntimeRepository,
        cache: AudioCacheRepository,
        adapter: RuntimeTtsAdapter,
        measurer: TextMeasurer,
        project_path: str | Path,
        monitor: RuntimeMonitor | None = None,
    ):
        self.runtime = runtime
        self.cache = cache
        self.adapter = adapter
        self.measurer = measurer
        self.project = Path(project_path)
        self.monitor = monitor or RuntimeMonitor()
        self.token_measurer = ConservativeTokenMeasurer()

    def run(
        self,
        profile: TtsProfile,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ExecutionSummary:
        if profile.concurrency != 1:
            raise ValueError("GPU synthesis concurrency must remain one")
        self.runtime.recover_interrupted_tasks()
        completed = cache_hits = split_parents = failed = 0
        worker_tasks = 0
        consecutive_cuda_errors = 0
        baseline = self.monitor.snapshot()
        cancelled = False
        while True:
            if should_cancel and should_cancel():
                cancelled = True
                break
            task = self.runtime.claim_next_task()
            if task is None:
                break
            cache_key = str(task["cache_key"])
            started = time.perf_counter()
            before = self.monitor.begin_task()
            cached = self.cache.lookup(cache_key)
            if cached is not None:
                self.runtime.complete_task(task["task_id"], self._relative(cached))
                after = self.monitor.snapshot()
                self._record_metric(
                    task,
                    profile,
                    before,
                    after,
                    elapsed=time.perf_counter() - started,
                    audio_path=cached,
                    cache_hit=True,
                    error_type=None,
                )
                completed += 1
                cache_hits += 1
                continue
            output_path = self.project / "audio/chunks" / f"{cache_key}.wav"
            error_type: str | None = None
            unrecoverable_cuda = False
            try:
                output = self.adapter.synthesize(task, profile, output_path)
                self.cache.put(cache_key, output.path)
                self.runtime.complete_task(
                    task["task_id"], self._relative(output.path)
                )
                completed += 1
                consecutive_cuda_errors = 0
            except RecoverableTtsError as exc:
                error_type = type(exc).__name__
                if isinstance(exc, TtsOutOfMemoryError):
                    consecutive_cuda_errors += 1
                else:
                    consecutive_cuda_errors = 0
                release_inference_memory(
                    clear_cuda_cache=profile.clear_cache_after_oom
                )
                can_split = self._can_split(task, profile)
                if can_split:
                    children = self._children(task, profile)
                    self.runtime.split_task(
                        task, children, error_type=error_type
                    )
                    split_parents += 1
                else:
                    unrecoverable_cuda = isinstance(
                        exc, TtsOutOfMemoryError
                    )
                    self.runtime.fail_task(
                        task["task_id"],
                        error_type,
                        "recoverable inference failure reached split limit",
                        int(task.get("text_length") or 0),
                    )
                    failed += 1
            except Exception as exc:  # noqa: BLE001 - persist isolated task failure
                error_type = type(exc).__name__
                unrecoverable_cuda = "cuda" in (
                    f"{type(exc).__name__}: {exc}".lower()
                )
                consecutive_cuda_errors = (
                    consecutive_cuda_errors + 1 if unrecoverable_cuda else 0
                )
                release_inference_memory(clear_cuda_cache=False)
                self.runtime.fail_task(
                    task["task_id"],
                    type(exc).__name__,
                    str(exc)[:300],
                    int(task.get("text_length") or 0),
                )
                failed += 1
            finally:
                after = self.monitor.snapshot()
                self._record_metric(
                    task,
                    profile,
                    before,
                    after,
                    elapsed=time.perf_counter() - started,
                    audio_path=output_path if output_path.is_file() else None,
                    cache_hit=False,
                    error_type=error_type,
                )
                release_inference_memory(clear_cuda_cache=False)
            worker_tasks += 1
            if self._should_restart_worker(
                profile,
                baseline,
                after,
                worker_tasks,
                consecutive_cuda_errors,
                unrecoverable_cuda,
            ):
                self.adapter.close()
                release_inference_memory(clear_cuda_cache=True)
                worker_tasks = 0
                consecutive_cuda_errors = 0
                baseline = self.monitor.snapshot()
        return ExecutionSummary(
            completed=completed,
            cache_hits=cache_hits,
            split_parents=split_parents,
            failed=failed,
            cancelled=cancelled,
        )

    def _record_metric(
        self,
        task: dict[str, Any],
        profile: TtsProfile,
        before: MemorySnapshot,
        after: MemorySnapshot,
        *,
        elapsed: float,
        audio_path: Path | None,
        cache_hit: bool,
        error_type: str | None,
    ) -> None:
        text = str(task.get("actual_text") or "")
        self.runtime.record_synthesis_metric(
            {
                "task_id": task["task_id"],
                "attempt": int(task.get("attempts") or 0) + 1,
                "text_chars": len(text),
                "text_tokens": self.token_measurer.measure(text),
                "voice_id": task.get("voice_id"),
                "auto_emotion": int(
                    profile.emotion.get("mode", "text_auto") == "text_auto"
                ),
                "elapsed_seconds": max(float(elapsed), 0.0),
                "audio_duration": (
                    self.monitor.audio_duration(audio_path)
                    if audio_path is not None
                    else None
                ),
                "memory_allocated_before_mb": before.allocated_mb,
                "memory_allocated_after_mb": after.allocated_mb,
                "memory_reserved_before_mb": before.reserved_mb,
                "memory_reserved_after_mb": after.reserved_mb,
                "max_memory_allocated_mb": after.peak_allocated_mb,
                "free_vram_before_mb": before.free_mb,
                "free_vram_after_mb": after.free_mb,
                "cache_hit": int(cache_hit),
                "error_type": error_type,
            }
        )

    @staticmethod
    def _should_restart_worker(
        profile: TtsProfile,
        baseline: MemorySnapshot,
        current: MemorySnapshot,
        worker_tasks: int,
        consecutive_cuda_errors: int,
        unrecoverable_cuda: bool,
    ) -> bool:
        options = profile.runtime_options
        task_limit = int(
            options.get(
                "restart_worker_after_tasks",
                options.get("restart_engine_after_tasks", 100),
            )
        )
        growth_limit = float(
            options.get(
                "restart_on_vram_growth_mb",
                options.get("vram_growth_limit_mb", 1536),
            )
        )
        minimum_free = float(options.get("minimum_free_vram_mb", 1536))
        growth = (
            current.allocated_mb - baseline.allocated_mb
            if current.allocated_mb is not None
            and baseline.allocated_mb is not None
            else None
        )
        restart_after_limit = (
            task_limit > 0
            and worker_tasks >= task_limit
            and growth is not None
            and growth > 0
        )
        return bool(
            restart_after_limit
            or (growth is not None and growth >= growth_limit)
            or (current.free_mb is not None and current.free_mb < minimum_free)
            or unrecoverable_cuda
            or consecutive_cuda_errors >= 2
        )

    def _can_split(self, task: dict[str, Any], profile: TtsProfile) -> bool:
        depth = int(task.get("split_depth") or 0)
        text = str(task.get("actual_text") or "")
        minimum = int(profile.runtime_options.get("min_retry_tokens", 12))
        return (
            depth < profile.max_retry_split_depth
            and self.measurer.measure(text) >= minimum * 2
        )

    def _children(
        self, task: dict[str, Any], profile: TtsProfile
    ) -> list[dict[str, Any]]:
        text = str(task["actual_text"])
        ratio = float(profile.runtime_options.get("oom_split_ratio", 0.6))
        target = min(max(int(len(text) * ratio), 1), len(text) - 1)
        boundary = self._safe_split(text, target)
        pieces = [text[:boundary], text[boundary:]]
        children = []
        for index, piece in enumerate(pieces):
            key = hashlib.sha256(
                f"{task['cache_key']}:{index}:{piece}".encode()
            ).hexdigest()
            children.append(
                {
                    "task_id": f"{task['task_id']}.split{index + 1}",
                    "actual_text": piece,
                    "text_length": self.measurer.measure(piece),
                    "cache_key": key,
                }
            )
        return children

    @staticmethod
    def _safe_split(text: str, target: int) -> int:
        punctuation = "。！？!?；;，,：:\n"
        for distance in range(len(text)):
            for candidate in (target - distance, target + distance):
                if 1 <= candidate < len(text) and text[candidate - 1] in punctuation:
                    return candidate
        boundary = target
        while (
            boundary > 1
            and boundary < len(text)
            and text[boundary - 1].isascii()
            and text[boundary].isascii()
            and text[boundary - 1].isalnum()
            and text[boundary].isalnum()
        ):
            boundary -= 1
        return boundary if boundary > 0 else target

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.project.resolve()).as_posix()
