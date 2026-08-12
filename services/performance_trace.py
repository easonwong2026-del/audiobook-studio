"""Low-intrusion production performance trace primitives.

The module is intentionally independent of workflow and MCP code. Queue,
directed synthesis, and the TTS adapter only call its optional recording hooks.
The default implementation is in-memory, uses ``perf_counter`` only, and
writes nothing unless a caller explicitly supplies a persistence path.

Trace collection is diagnostic: every public operation is failure-isolated so
an instrumentation or checkpoint failure cannot change synthesis semantics.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from .engine_capabilities import gpu_snapshot as default_gpu_snapshot

logger = logging.getLogger(__name__)

TIMING_PHASES = (
    "task_total",
    "chapter_total",
    "segment_total",
    "cache_lookup",
    "speaker_resolution",
    "speaker_fingerprint",
    "effective_params",
    "directed_synthesis_total",
    "engine_infer",
    "wav_compose",
    "wav_validate",
    "atomic_publish",
    "status_persist",
    "segment_gap",
)
SCOPES = frozenset({"task", "chapter", "segment"})
_LOCAL_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:Users|home|private|tmp|var|opt)/)[^\s,;)]*"
)


def _public_value(value: Any) -> Any:
    if isinstance(value, str):
        return _LOCAL_PATH_RE.sub("<local-path>", value)
    if isinstance(value, dict):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return value


def _number(value: Any) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value >= 0 else 0.0


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(_number(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


@dataclass
class _Timing:
    total: float = 0.0
    count: int = 0
    samples: list[float] = field(default_factory=list)

    def add(self, elapsed: Any) -> None:
        value = _number(elapsed)
        self.total += value
        self.count += 1
        self.samples.append(value)

    def as_dict(self, *, statistics: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "seconds": self.total,
            "count": self.count,
        }
        if statistics:
            result.update(
                {
                    "median_seconds": _percentile(self.samples, 0.5),
                    "p95_seconds": _percentile(self.samples, 0.95),
                    "max_seconds": max(self.samples, default=0.0),
                }
            )
        return result


@dataclass
class _Segment:
    segment_id: str
    chapter_id: str | None = None
    started_at: float | None = None
    active: bool = False
    timing: dict[str, _Timing] = field(default_factory=dict)
    infer_calls: int = 0
    infer_elapsed_total: float = 0.0
    directed_parts: set[str] = field(default_factory=set)
    cache_hits: int = 0
    cache_misses: int = 0
    failures: int = 0
    last_status: str | None = None

    @property
    def directed_part_count(self) -> int:
        return len(self.directed_parts) or (1 if self.infer_calls else 0)


class PerformanceTrace:
    """In-memory task/chapter/segment performance accumulator.

    Typical future integration::

        trace = PerformanceTrace(task_id=task_id, project=project_name)
        trace.start_task()
        segment = trace.start_segment(segment_id, chapter_id=chapter_id)
        segment.record_cache(hit=cache_hit, lookup_elapsed=lookup_seconds)
        segment.record_infer(elapsed, part_index=part_index)
        segment.record_publish(elapsed=publish_seconds)
        segment.close()
        trace.end_task()

    ``record_infer`` counts actual calls, so retrying one directed part counts
    twice while ``directed_part_count`` remains one.
    """

    def __init__(
        self,
        task_id: str | None = None,
        project: str | None = None,
        *,
        enabled: bool = True,
        clock: Callable[[], float] | None = None,
        gpu_sampler: Callable[[], Mapping[str, Any]] | None = default_gpu_snapshot,
        persist_path: str | os.PathLike[str] | None = None,
        max_failures: int = 100,
    ) -> None:
        self.task_id = str(task_id) if task_id is not None else None
        self.project = str(project) if project is not None else None
        self.enabled = bool(enabled)
        self._clock = clock or time.perf_counter
        self._gpu_sampler = gpu_sampler
        self._persist_path = str(persist_path) if persist_path is not None else None
        self._max_failures = max(0, int(max_failures))
        self._task_started_at: float | None = None
        self._task_active = False
        self._chapters: dict[str, dict[str, Any]] = {}
        self._segments: dict[str, _Segment] = {}
        self._timings: dict[str, _Timing] = {
            phase: _Timing() for phase in TIMING_PHASES
        }
        self._scope_timings: dict[str, dict[str, _Timing]] = {
            scope: {} for scope in SCOPES
        }
        self._cache_hits = 0
        self._cache_misses = 0
        self._infer_calls = 0
        self._infer_samples: list[float] = []
        self._failures: list[dict[str, Any]] = []
        self._gpu_snapshots: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._trace_errors = 0
        self._checkpoint_count = 0
        self._last_segment_finished_at: float | None = None

    # --- isolation and clock helpers ---------------------------------

    def _now(self) -> float:
        try:
            return _number(self._clock())
        except (TypeError, ValueError, RuntimeError) as exc:  # pragma: no cover
            self._trace_errors += 1
            logger.debug("performance trace clock failed: %s", exc)
            return 0.0

    def _capture_gpu(self, boundary: str) -> None:
        if not self.enabled or self._gpu_sampler is None:
            return
        try:
            snapshot = dict(self._gpu_sampler() or {"available": False})
            snapshot["boundary"] = boundary
            self._gpu_snapshots.append(snapshot)
        except Exception as exc:  # noqa: BLE001  # diagnostics must not escape
            self._trace_errors += 1
            logger.debug("performance trace GPU snapshot failed: %s", exc)

    def record_boundary(self, boundary: str) -> None:
        """Best-effort GPU snapshot at an explicit lifecycle boundary."""

        self._capture_gpu(str(boundary))

    def _timing(self, phase: str, scope: str) -> _Timing:
        phase = str(phase or "unknown")
        scope = scope if scope in SCOPES else "segment"
        timing = self._timings.setdefault(phase, _Timing())
        scoped = self._scope_timings[scope].setdefault(phase, _Timing())
        return timing, scoped

    def _segment(self, segment_id: str | None, chapter_id: str | None = None) -> _Segment | None:
        if segment_id is None:
            return None
        key = str(segment_id)
        record = self._segments.get(key)
        if record is None:
            record = _Segment(key, str(chapter_id) if chapter_id is not None else None)
            self._segments[key] = record
        elif chapter_id is not None and record.chapter_id is None:
            record.chapter_id = str(chapter_id)
        return record

    def _chapter(self, chapter_id: str | None) -> dict[str, Any] | None:
        if chapter_id is None:
            return None
        key = str(chapter_id)
        return self._chapters.setdefault(
            key,
            {
                "chapter_id": key,
                "started_at": None,
                "active": False,
                "timings": {},
                "failures": 0,
            },
        )

    # --- lifecycle ----------------------------------------------------

    def start_task(self, task_id: str | None = None, project: str | None = None):
        if not self.enabled:
            return TraceSession(self, "task", task_id=task_id, active=False)
        try:
            if task_id is not None:
                self.task_id = str(task_id)
            if project is not None:
                self.project = str(project)
            if not self._task_active:
                self._task_started_at = self._now()
                self._task_active = True
                self._capture_gpu("task_start")
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace start_task failed: %s", exc)
        return TraceSession(self, "task", task_id=self.task_id, active=self._task_active)

    def end_task(self) -> None:
        if not self.enabled or not self._task_active:
            return
        try:
            for segment_id, record in list(self._segments.items()):
                if record.active:
                    self.end_segment(segment_id, chapter_id=record.chapter_id)
            for chapter_id, chapter in list(self._chapters.items()):
                if chapter["active"]:
                    self.end_chapter(chapter_id)
            elapsed = max(0.0, self._now() - (self._task_started_at or self._now()))
            self.add_timing("task_total", elapsed, scope="task")
            self._task_active = False
            self._capture_gpu("task_end")
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace end_task failed: %s", exc)

    def start_chapter(self, chapter_id: str) -> TraceSession:
        key = str(chapter_id)
        if self.enabled:
            try:
                chapter = self._chapter(key)
                if chapter is not None and not chapter["active"]:
                    chapter["started_at"] = self._now()
                    chapter["active"] = True
                    self._capture_gpu(f"chapter:{key}:start")
            except Exception as exc:  # noqa: BLE001  # trace must not alter production
                self._trace_errors += 1
                logger.debug("performance trace start_chapter failed: %s", exc)
        return TraceSession(self, "chapter", chapter_id=key, active=self.enabled)

    def end_chapter(self, chapter_id: str) -> None:
        if not self.enabled:
            return
        try:
            chapter = self._chapter(chapter_id)
            if chapter is None or not chapter["active"]:
                return
            elapsed = max(0.0, self._now() - (chapter["started_at"] or self._now()))
            self.add_timing("chapter_total", elapsed, scope="chapter", chapter_id=chapter_id)
            chapter["active"] = False
            self._capture_gpu(f"chapter:{chapter_id}:end")
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace end_chapter failed: %s", exc)

    def start_segment(
        self,
        segment_id: str,
        *,
        chapter_id: str | None = None,
    ) -> TraceSession:
        key = str(segment_id)
        if self.enabled:
            try:
                record = self._segment(key, chapter_id)
                if record is not None and not record.active:
                    record.started_at = self._now()
                    record.active = True
                    if self._last_segment_finished_at is not None:
                        self.add_timing(
                            "segment_gap",
                            max(0.0, record.started_at - self._last_segment_finished_at),
                            scope="segment",
                            chapter_id=record.chapter_id,
                            segment_id=record.segment_id,
                        )
            except Exception as exc:  # noqa: BLE001  # trace must not alter production
                self._trace_errors += 1
                logger.debug("performance trace start_segment failed: %s", exc)
        return TraceSession(
            self,
            "segment",
            chapter_id=str(chapter_id) if chapter_id is not None else None,
            segment_id=key,
            active=self.enabled,
        )

    def end_segment(self, segment_id: str, *, chapter_id: str | None = None) -> None:
        if not self.enabled:
            return
        try:
            record = self._segment(segment_id, chapter_id)
            if record is None or not record.active:
                return
            elapsed = max(0.0, self._now() - (record.started_at or self._now()))
            self.add_timing(
                "segment_total",
                elapsed,
                scope="segment",
                chapter_id=record.chapter_id,
                segment_id=record.segment_id,
            )
            record.active = False
            self._last_segment_finished_at = self._now()
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace end_segment failed: %s", exc)

    def session(
        self,
        *,
        task_id: str | None = None,
        chapter_id: str | None = None,
        segment_id: str | None = None,
    ) -> TraceSession:
        """Create an unstarted bound context for a future thin integration."""

        scope = "segment" if segment_id is not None else "chapter" if chapter_id is not None else "task"
        return TraceSession(
            self,
            scope,
            task_id=task_id,
            chapter_id=chapter_id,
            segment_id=segment_id,
            active=False,
        )

    # --- recording ----------------------------------------------------

    def add_timing(
        self,
        phase: str,
        elapsed: float,
        *,
        scope: str = "segment",
        chapter_id: str | None = None,
        segment_id: str | None = None,
    ) -> None:
        """Accumulate one phase without doing any I/O or project scanning."""

        if not self.enabled:
            return
        try:
            value = _number(elapsed)
            timing, scoped = self._timing(phase, scope)
            timing.add(value)
            scoped.add(value)
            if segment_id is not None:
                record = self._segment(segment_id, chapter_id)
                if record is not None:
                    segment_timing = record.timing.setdefault(str(phase), _Timing())
                    segment_timing.add(value)
            if chapter_id is not None:
                chapter = self._chapter(chapter_id)
                if chapter is not None:
                    chapter_timing = chapter["timings"].setdefault(str(phase), _Timing())
                    chapter_timing.add(value)
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace add_timing failed: %s", exc)

    def record_directed_part(
        self,
        segment_id: str,
        *,
        part_index: int | str | None = None,
        chapter_id: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            record = self._segment(segment_id, chapter_id)
            if record is None:
                return
            key = str(part_index) if part_index is not None else str(record.directed_part_count)
            record.directed_parts.add(key)
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace record_directed_part failed: %s", exc)

    def record_infer(
        self,
        segment_id: str,
        elapsed: float,
        *,
        part_index: int | str | None = None,
        chapter_id: str | None = None,
        success: bool = True,
        error: BaseException | str | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            record = self._segment(segment_id, chapter_id)
            if record is None:
                return
            if part_index is not None:
                self.record_directed_part(
                    segment_id,
                    part_index=part_index,
                    chapter_id=chapter_id,
                )
            elif not record.directed_parts:
                record.directed_parts.add("0")
            value = _number(elapsed)
            record.infer_calls += 1
            record.infer_elapsed_total += value
            self._infer_calls += 1
            self._infer_samples.append(value)
            self.add_timing(
                "engine_infer",
                value,
                scope="segment",
                chapter_id=record.chapter_id,
                segment_id=record.segment_id,
            )
            if not success:
                self.record_failure(
                    "engine_infer",
                    segment_id=segment_id,
                    chapter_id=chapter_id,
                    error=error,
                    recoverable=True,
                )
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace record_infer failed: %s", exc)

    def record_cache(
        self,
        segment_id: str,
        *,
        hit: bool,
        lookup_elapsed: float | None = None,
        chapter_id: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            record = self._segment(segment_id, chapter_id)
            if hit:
                self._cache_hits += 1
                if record is not None:
                    record.cache_hits += 1
            else:
                self._cache_misses += 1
                if record is not None:
                    record.cache_misses += 1
            if lookup_elapsed is not None:
                self.add_timing(
                    "cache_lookup",
                    lookup_elapsed,
                    scope="segment",
                    chapter_id=chapter_id,
                    segment_id=segment_id,
                )
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace record_cache failed: %s", exc)

    def record_publish(
        self,
        elapsed: float,
        *,
        segment_id: str | None = None,
        chapter_id: str | None = None,
        success: bool = True,
        error: BaseException | str | None = None,
    ) -> None:
        self.add_timing(
            "atomic_publish",
            elapsed,
            scope="segment",
            chapter_id=chapter_id,
            segment_id=segment_id,
        )
        if not success:
            self.record_failure(
                "atomic_publish",
                segment_id=segment_id,
                chapter_id=chapter_id,
                error=error,
            )

    def record_status(
        self,
        elapsed: float,
        *,
        status: str | None = None,
        segment_id: str | None = None,
        chapter_id: str | None = None,
        success: bool = True,
        error: BaseException | str | None = None,
    ) -> None:
        self.add_timing(
            "status_persist",
            elapsed,
            scope="segment",
            chapter_id=chapter_id,
            segment_id=segment_id,
        )
        try:
            if segment_id is not None:
                record = self._segment(segment_id, chapter_id)
                if record is not None:
                    record.last_status = str(status) if status is not None else None
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace record_status failed: %s", exc)
        if not success:
            self.record_failure(
                "status_persist",
                segment_id=segment_id,
                chapter_id=chapter_id,
                error=error,
            )

    def record_failure(
        self,
        phase: str,
        *,
        segment_id: str | None = None,
        chapter_id: str | None = None,
        error: BaseException | str | None = None,
        recoverable: bool = False,
    ) -> None:
        if not self.enabled:
            return
        try:
            if isinstance(error, BaseException):
                exception_type = type(error).__name__
                message = _LOCAL_PATH_RE.sub("<local-path>", str(error))
            elif error is None:
                exception_type = None
                message = None
            else:
                exception_type = "RuntimeError"
                message = _LOCAL_PATH_RE.sub("<local-path>", str(error))
            item = {
                "phase": str(phase),
                "chapter_id": str(chapter_id) if chapter_id is not None else None,
                "segment_id": str(segment_id) if segment_id is not None else None,
                "exception_type": exception_type,
                "message": message[:500] if message else None,
                "recoverable": bool(recoverable),
            }
            if len(self._failures) < self._max_failures:
                self._failures.append(item)
            record = self._segment(segment_id, chapter_id)
            if record is not None:
                record.failures += 1
            chapter = self._chapter(chapter_id)
            if chapter is not None:
                chapter["failures"] += 1
        except Exception as exc:  # noqa: BLE001  # trace must not alter production
            self._trace_errors += 1
            logger.debug("performance trace record_failure failed: %s", exc)

    def record_event(self, name: str, *, data: Mapping[str, Any] | None = None) -> None:
        """Record a small lifecycle marker such as pause/resume/recovery."""

        if not self.enabled:
            return
        try:
            self._events.append(
                {"name": str(name), "data": _public_value(dict(data or {}))}
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            self._trace_errors += 1
            logger.debug("performance trace record_event failed: %s", exc)

    # --- snapshots and persistence -----------------------------------

    def _timings_summary(self, source: Mapping[str, _Timing]) -> dict[str, Any]:
        return {name: timing.as_dict() for name, timing in sorted(source.items())}

    def segment_details(self) -> list[dict[str, Any]]:
        details = []
        for segment_id, record in sorted(self._segments.items()):
            details.append(
                {
                    "segment_id": segment_id,
                    "chapter_id": record.chapter_id,
                    "directed_part_count": record.directed_part_count,
                    "infer_call_count": record.infer_calls,
                    "infer_elapsed_total": record.infer_elapsed_total,
                    "cache_hits": record.cache_hits,
                    "cache_misses": record.cache_misses,
                    "failures": record.failures,
                    "last_status": record.last_status,
                    "timings": self._timings_summary(record.timing),
                }
            )
        return details

    def summary(self, *, include_segments: bool = False) -> dict[str, Any]:
        """Return a compact, JSON-safe aggregate; no file I/O is performed."""

        if not self.enabled:
            return {
                "trace_available": False,
                "task_id": self.task_id,
                "project": self.project,
                "segments": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "infer_calls": 0,
                "timings": {},
                "segment_stats": {
                    "count": 0,
                    "median_seconds": 0.0,
                    "p95_seconds": 0.0,
                    "max_seconds": 0.0,
                },
                "inference": {
                    "median_seconds": 0.0,
                    "p95_seconds": 0.0,
                    "calls_per_segment": 0.0,
                },
            }
        try:
            segment_totals = []
            for record in self._segments.values():
                timing = record.timing.get("segment_total")
                if timing is not None:
                    segment_totals.extend(timing.samples)
            phase_seconds = {
                name: timing.total for name, timing in sorted(self._timings.items())
            }
            phase_seconds.update({
                "task_total_seconds": phase_seconds.get("task_total", 0.0),
                "engine_infer_seconds": phase_seconds.get("engine_infer", 0.0),
                "directed_synthesis_seconds": phase_seconds.get(
                    "directed_synthesis_total", 0.0
                ),
                "wav_compose_seconds": phase_seconds.get("wav_compose", 0.0),
                "publish_seconds": phase_seconds.get("atomic_publish", 0.0),
                "status_persist_seconds": phase_seconds.get("status_persist", 0.0),
            })
            result: dict[str, Any] = {
                "trace_available": True,
                "task_id": self.task_id,
                "project": self.project,
                "segments": len(self._segments),
                "segments_profiled": len(self._segments),
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "infer_calls": self._infer_calls,
                "timings": phase_seconds,
                "elapsed_seconds": phase_seconds.get("task_total", 0.0),
                "scope_timings": {
                    scope: self._timings_summary(self._scope_timings[scope])
                    for scope in sorted(SCOPES)
                },
                "segment_stats": {
                    "count": len(segment_totals),
                    "median_seconds": _percentile(segment_totals, 0.5),
                    "p95_seconds": _percentile(segment_totals, 0.95),
                    "max_seconds": max(segment_totals, default=0.0),
                },
                "inference": {
                    "median_seconds": _percentile(self._infer_samples, 0.5),
                    "p95_seconds": _percentile(self._infer_samples, 0.95),
                    "max_seconds": max(self._infer_samples, default=0.0),
                    "calls_per_segment": self._infer_calls / len(self._segments)
                    if self._segments
                    else 0.0,
                },
                "failures": len(self._failures),
                "trace_errors": self._trace_errors,
                "gpu_snapshots": list(self._gpu_snapshots),
                "events": list(self._events),
            }
            if include_segments:
                result["segment_details"] = self.segment_details()
            return result
        except Exception as exc:  # noqa: BLE001  # serialization must not escape
            self._trace_errors += 1
            logger.debug("performance trace summary failed: %s", exc)
            return {
                "trace_available": True,
                "task_id": self.task_id,
                "project": self.project,
                "segments": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "infer_calls": 0,
                "timings": {},
                "trace_errors": self._trace_errors,
            }

    def checkpoint(self, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        """Return a compact snapshot and optionally batch-persist full details."""

        snapshot = self.summary()
        self._checkpoint_count += 1
        target = path if path is not None else self._persist_path
        if target is not None:
            self.persist(target)
        return snapshot

    def persist(self, path: str | os.PathLike[str] | None = None) -> bool:
        """Atomically persist one batch snapshot; never raises to production."""

        target = path if path is not None else self._persist_path
        if target is None or not self.enabled:
            return False
        try:
            target_path = Path(target)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self.summary(include_segments=True)
            fd, temporary = tempfile.mkstemp(
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                dir=str(target_path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                    stream.write("\n")
                os.replace(temporary, target_path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            self._persist_path = str(target_path)
            return True
        except Exception as exc:  # noqa: BLE001  # persistence must not escape
            self._trace_errors += 1
            logger.debug("performance trace persist failed: %s", exc)
            return False


class TraceSession:
    """A bound, thin recorder context for one task/chapter/segment."""

    def __init__(
        self,
        trace: PerformanceTrace,
        scope: str,
        *,
        task_id: str | None = None,
        chapter_id: str | None = None,
        segment_id: str | None = None,
        active: bool = False,
    ) -> None:
        self.trace = trace
        self.scope = scope if scope in SCOPES else "segment"
        self.task_id = task_id
        self.chapter_id = chapter_id
        self.segment_id = segment_id
        self.active = bool(active)

    def __enter__(self) -> Self:
        if not self.active:
            if self.scope == "task":
                started = self.trace.start_task(self.task_id)
            elif self.scope == "chapter":
                started = self.trace.start_chapter(str(self.chapter_id))
            else:
                started = self.trace.start_segment(
                    str(self.segment_id), chapter_id=self.chapter_id
                )
            self.active = started.active
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_value is not None:
            self.trace.record_failure(
                self.scope,
                chapter_id=self.chapter_id,
                segment_id=self.segment_id,
                error=exc_value,
            )
        self.close()
        return False

    def close(self) -> None:
        if not self.active:
            return
        if self.scope == "task":
            self.trace.end_task()
        elif self.scope == "chapter":
            self.trace.end_chapter(str(self.chapter_id))
        else:
            self.trace.end_segment(
                str(self.segment_id), chapter_id=self.chapter_id
            )
        self.active = False

    def add_timing(
        self,
        phase: str,
        elapsed: float,
        *,
        scope: str | None = None,
    ) -> None:
        self.trace.add_timing(
            phase,
            elapsed,
            scope=scope or self.scope,
            chapter_id=self.chapter_id,
            segment_id=self.segment_id,
        )

    def record_infer(
        self,
        elapsed: float,
        *,
        part_index: int | str | None = None,
        success: bool = True,
        error: BaseException | str | None = None,
    ) -> None:
        if self.segment_id is None:
            return
        self.trace.record_infer(
            self.segment_id,
            elapsed,
            part_index=part_index,
            chapter_id=self.chapter_id,
            success=success,
            error=error,
        )

    def record_cache(self, *, hit: bool, lookup_elapsed: float | None = None) -> None:
        if self.segment_id is None:
            return
        self.trace.record_cache(
            self.segment_id,
            hit=hit,
            lookup_elapsed=lookup_elapsed,
            chapter_id=self.chapter_id,
        )

    def record_publish(self, elapsed: float, *, success: bool = True, error=None) -> None:
        self.trace.record_publish(
            elapsed,
            segment_id=self.segment_id,
            chapter_id=self.chapter_id,
            success=success,
            error=error,
        )

    def record_status(
        self,
        elapsed: float,
        *,
        status: str | None = None,
        success: bool = True,
        error=None,
    ) -> None:
        self.trace.record_status(
            elapsed,
            status=status,
            segment_id=self.segment_id,
            chapter_id=self.chapter_id,
            success=success,
            error=error,
        )

    def record_failure(self, phase: str, *, error=None, recoverable: bool = False) -> None:
        self.trace.record_failure(
            phase,
            chapter_id=self.chapter_id,
            segment_id=self.segment_id,
            error=error,
            recoverable=recoverable,
        )

    def record_event(self, name: str, *, data: Mapping[str, Any] | None = None) -> None:
        self.trace.record_event(name, data=data)


__all__ = [
    "SCOPES",
    "TIMING_PHASES",
    "PerformanceTrace",
    "TraceSession",
]
