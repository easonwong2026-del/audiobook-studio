"""Transactional v4 synthesis execution with cache and bounded task splitting."""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.v4.production import TtsProfile
from repositories.audio_cache_repository import AudioCacheRepository
from repositories.runtime_repository import RuntimeRepository
from tts.base_adapter import RecoverableTtsError, RuntimeTtsAdapter
from tts.runtime_cleanup import release_inference_memory
from tts.text_measurement import TextMeasurer


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
    ):
        self.runtime = runtime
        self.cache = cache
        self.adapter = adapter
        self.measurer = measurer
        self.project = Path(project_path)

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
        processed = 0
        cancelled = False
        while True:
            if should_cancel and should_cancel():
                cancelled = True
                break
            task = self.runtime.claim_next_task()
            if task is None:
                break
            cache_key = str(task["cache_key"])
            cached = self.cache.lookup(cache_key)
            if cached is not None:
                self.runtime.complete_task(task["task_id"], self._relative(cached))
                completed += 1
                cache_hits += 1
                continue
            output_path = self.project / "audio/chunks" / f"{cache_key}.wav"
            try:
                output = self.adapter.synthesize(task, profile, output_path)
                self.cache.put(cache_key, output.path)
                self.runtime.complete_task(
                    task["task_id"], self._relative(output.path)
                )
                completed += 1
            except RecoverableTtsError as exc:
                release_inference_memory(
                    clear_cuda_cache=profile.clear_cache_after_oom
                )
                if self._can_split(task, profile):
                    children = self._children(task, profile)
                    self.runtime.split_task(
                        task, children, error_type=type(exc).__name__
                    )
                    split_parents += 1
                else:
                    self.runtime.fail_task(
                        task["task_id"],
                        type(exc).__name__,
                        "recoverable inference failure reached split limit",
                        int(task.get("text_length") or 0),
                    )
                    failed += 1
            except Exception as exc:  # noqa: BLE001 - persist isolated task failure
                release_inference_memory(clear_cuda_cache=False)
                self.runtime.fail_task(
                    task["task_id"],
                    type(exc).__name__,
                    str(exc)[:300],
                    int(task.get("text_length") or 0),
                )
                failed += 1
            finally:
                release_inference_memory(clear_cuda_cache=False)
            processed += 1
            restart_after = int(
                profile.runtime_options.get("restart_engine_after_tasks", 100)
            )
            if restart_after > 0 and processed % restart_after == 0:
                self.adapter.close()
        return ExecutionSummary(
            completed=completed,
            cache_hits=cache_hits,
            split_parents=split_parents,
            failed=failed,
            cancelled=cancelled,
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
