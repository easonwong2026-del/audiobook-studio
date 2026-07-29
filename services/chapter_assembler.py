"""Assemble normalized task WAVs into chapter audio with boundary-aware joins."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from domain.v4.production import PlanTask
from lib.audio_format import (
    DEFAULT_TARGET_CHANNELS,
    DEFAULT_TARGET_DTYPE,
    DEFAULT_TARGET_RATE,
    load_and_normalize_wav,
    write_wav,
)
from repositories.runtime_repository import RuntimeRepository


@dataclass(frozen=True)
class ChapterAssembly:
    path: Path
    fingerprint: str
    duration_seconds: float


class ChapterAssembler:
    def __init__(
        self,
        runtime: RuntimeRepository,
        project_path: str | Path,
        *,
        target_rate: int = DEFAULT_TARGET_RATE,
        target_channels: int = DEFAULT_TARGET_CHANNELS,
    ):
        self.runtime = runtime
        self.project = Path(project_path)
        self.target_rate = target_rate
        self.target_channels = target_channels

    def assemble(
        self,
        chapter_id: str,
        tasks: list[PlanTask],
        *,
        plan_revision: int = 1,
    ) -> ChapterAssembly:
        if not tasks:
            raise ValueError("chapter assembly requires tasks")
        parts: list[np.ndarray] = []
        digest = hashlib.sha256()
        previous_task: PlanTask | None = None
        for task in tasks:
            for relative_path in self.runtime.resolved_audio_paths(task.task_id):
                path = self.project / relative_path
                audio = load_and_normalize_wav(
                    str(path),
                    target_rate=self.target_rate,
                    target_channels=self.target_channels,
                    target_dtype=DEFAULT_TARGET_DTYPE,
                ).data
                digest.update(path.read_bytes())
                if parts:
                    pause_ms = self._pause_between(previous_task, task)
                    if pause_ms:
                        parts.append(
                            np.zeros(
                                int(self.target_rate * pause_ms / 1000),
                                dtype=DEFAULT_TARGET_DTYPE,
                            )
                        )
                    crossfade_ms = min(task.crossfade_ms, 50)
                    if crossfade_ms and pause_ms == 0:
                        audio = self._crossfade(parts, audio, crossfade_ms)
                parts.append(audio)
                previous_task = task
        combined = np.concatenate(parts)
        output = self.project / "audio/chapters" / f"{chapter_id}.wav"
        write_wav(str(output), combined, self.target_rate)
        fingerprint = digest.hexdigest()
        duration = len(combined) / float(self.target_rate)
        self.runtime.save_chapter_output(
            chapter_id,
            plan_revision,
            output.relative_to(self.project).as_posix(),
            fingerprint,
            duration,
        )
        return ChapterAssembly(output, fingerprint, duration)

    @staticmethod
    def _pause_between(previous: PlanTask | None, current: PlanTask) -> int:
        if previous is None:
            return current.pause_before_ms
        if current.continuation:
            return min(previous.pause_after_ms, 120)
        if previous.speaker_id != current.speaker_id:
            return max(previous.pause_after_ms, 350)
        return previous.pause_after_ms

    def _crossfade(
        self, parts: list[np.ndarray], audio: np.ndarray, crossfade_ms: int
    ) -> np.ndarray:
        overlap = min(
            int(self.target_rate * crossfade_ms / 1000),
            len(parts[-1]),
            len(audio),
        )
        if overlap <= 0:
            return audio
        fade_out = np.linspace(1.0, 0.0, overlap)
        fade_in = 1.0 - fade_out
        mixed = (
            parts[-1][-overlap:].astype(np.float64) * fade_out
            + audio[:overlap].astype(np.float64) * fade_in
        ).astype(DEFAULT_TARGET_DTYPE)
        parts[-1] = np.concatenate([parts[-1][:-overlap], mixed])
        return audio[overlap:]
