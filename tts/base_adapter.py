"""TTS runtime contracts and classified recoverable failures."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from domain.v4.production import TtsProfile


class RecoverableTtsError(RuntimeError):
    pass


class TtsOutOfMemoryError(RecoverableTtsError):
    pass


class TtsLengthLimitError(RecoverableTtsError):
    pass


class EmptyAudioError(RecoverableTtsError):
    pass


@dataclass(frozen=True)
class SynthesisOutput:
    path: Path


class RuntimeTtsAdapter(Protocol):
    def synthesize(
        self,
        task: dict[str, Any],
        profile: TtsProfile,
        output_path: Path,
    ) -> SynthesisOutput: ...

    def close(self) -> None: ...
