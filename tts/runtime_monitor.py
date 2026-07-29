"""Best-effort, text-free runtime metrics for local GPU synthesis."""
from __future__ import annotations

import sys
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemorySnapshot:
    allocated_mb: float | None = None
    reserved_mb: float | None = None
    peak_allocated_mb: float | None = None
    free_mb: float | None = None


class RuntimeMonitor:
    """Read CUDA counters when torch is loaded; stay portable in CPU CI."""

    def begin_task(self) -> MemorySnapshot:
        cuda = self._cuda()
        if cuda is None:
            return MemorySnapshot()
        try:
            cuda.reset_peak_memory_stats()
        except Exception:  # noqa: BLE001 - telemetry must not break synthesis
            return self.snapshot()
        return self.snapshot()

    def snapshot(self) -> MemorySnapshot:
        cuda = self._cuda()
        if cuda is None:
            return MemorySnapshot()
        try:
            free_bytes, _total_bytes = cuda.mem_get_info()
            return MemorySnapshot(
                allocated_mb=self._mb(cuda.memory_allocated()),
                reserved_mb=self._mb(cuda.memory_reserved()),
                peak_allocated_mb=self._mb(cuda.max_memory_allocated()),
                free_mb=self._mb(free_bytes),
            )
        except Exception:  # noqa: BLE001 - unsupported CUDA APIs are optional
            return MemorySnapshot()

    @staticmethod
    def audio_duration(path: Path) -> float | None:
        try:
            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate()
                return handle.getnframes() / rate if rate > 0 else None
        except (OSError, wave.Error):
            return None

    @staticmethod
    def _cuda():
        torch = sys.modules.get("torch")
        cuda = getattr(torch, "cuda", None) if torch is not None else None
        try:
            return cuda if cuda is not None and cuda.is_available() else None
        except Exception:  # noqa: BLE001 - telemetry must remain best effort
            return None

    @staticmethod
    def _mb(value: float) -> float:
        return round(float(value) / (1024 * 1024), 3)
