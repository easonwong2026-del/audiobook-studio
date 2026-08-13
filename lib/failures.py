"""Structured segment-failure model for production self-healing.

The runtime never classifies failures by parsing the legacy ``[X]`` generator
strings.  Every segment failure produced by ``lib.queue.synthesize_project``
is also delivered as a typed :class:`SynthesisFailure` through a callback
channel, so the runtime can distinguish engine-runtime failures (recoverable)
from ordinary segment failures (text/asset/IO problems) and drive a bounded
engine-recycle recovery loop.

Phase taxonomy:

- ``engine_infer``       failure raised inside ``tts_engine`` while calling
                         the IndexTTS2 model (``_tts.infer``).
- ``directed_synthesis`` failure in the v3 director path (pause composition,
                         audio format handling, part file I/O).
- ``wav_validate``       produced WAV failed validation (empty / invalid).
- ``atomic_publish``     ``os.replace`` / file publish failed.
- ``status_persist``     durable segment-status write failed.
- ``unknown``            anything not yet classified.

The same ``OSError(errno=22)`` therefore maps to a *recoverable engine
runtime failure candidate* only when ``phase == engine_infer``; the same
errno from ``atomic_publish`` / ``wav_validate`` is a plain I/O failure and
must never trigger an engine recycle.
"""
from __future__ import annotations

import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Recovery budgets are centralized here (never scattered magic numbers).
@dataclass(frozen=True)
class RecoveryBudget:
    """Bounded recovery budget for one production task.

    - ``segment_retry_limit``: retries of the same segment per engine
      recycle (default 1).
    - ``engine_recycle_limit``: maximum engine recycles for one task.
    - ``systemic_failure_threshold``: distinct segments sharing one failure
      fingerprint before the queue stops pulling new segments.
    """

    segment_retry_limit: int = 1
    engine_recycle_limit: int = 2
    systemic_failure_threshold: int = 3


DEFAULT_RECOVERY_BUDGET = RecoveryBudget()

PHASE_ENGINE_INFER = "engine_infer"
PHASE_DIRECTED_SYNTHESIS = "directed_synthesis"
PHASE_WAV_VALIDATE = "wav_validate"
PHASE_ATOMIC_PUBLISH = "atomic_publish"
PHASE_STATUS_PERSIST = "status_persist"
PHASE_UNKNOWN = "unknown"

PHASES = (
    PHASE_ENGINE_INFER,
    PHASE_DIRECTED_SYNTHESIS,
    PHASE_WAV_VALIDATE,
    PHASE_ATOMIC_PUBLISH,
    PHASE_STATUS_PERSIST,
    PHASE_UNKNOWN,
)

# These are the only engine-runtime failure codes that are currently
# confirmed safe to recover by detaching and reloading the engine.  Keep this
# allow-list deliberately small: a generic ``recoverable`` flag must never be
# enough to recycle a production TTS engine.
RECOVERABLE_ENGINE_CODES = frozenset({"TTS_ENGINE_OOM_EXHAUSTED"})

_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:Users|home|private|tmp|var|opt)/)[^\s,;)]*"
)


def sanitize_message(value: Any) -> str:
    """Strip local absolute paths from a failure message."""
    return _PATH_PATTERN.sub("<local-path>", str(value or "")).strip()


def traceback_origin(exc: BaseException | None) -> str:
    """Return ``file:line:func`` of the raise site (no absolute paths)."""
    if exc is None or exc.__traceback__ is None:
        return ""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return ""
    frame = frames[-1]
    return f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}"


def failure_fingerprint(
    *,
    exception_type: str,
    errno: int | None,
    phase: str,
    message: str,
    origin: str,
) -> str:
    """Stable fingerprint of one failure class.

    Deliberately excludes ``segment_id``, absolute paths and novel text so
    the same systemic failure across different segments shares one key.
    """
    errno_part = str(errno) if errno is not None else "-"
    normalized = re.sub(r"\s+", " ", sanitize_message(message))[:120]
    return f"{exception_type}|{errno_part}|{phase}|{normalized}|{origin}"


@dataclass
class SynthesisFailure:
    """Structured failure event for one segment."""

    segment_id: str = ""
    chapter_id: str = ""
    phase: str = PHASE_UNKNOWN
    exception_type: str = ""
    errno: Optional[int] = None
    message: str = ""
    fingerprint: str = ""
    recoverable: bool = False
    engine_related: bool = False
    traceback_origin: str = ""
    code: str = ""
    original_exception: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_exception(
        cls,
        *,
        segment_id: str,
        chapter_id: str,
        phase: str,
        exc: BaseException,
        recoverable: bool = False,
        engine_related: bool = False,
        code: str = "",
    ) -> "SynthesisFailure":
        # EngineRuntimeFailure is an adapter envelope.  Diagnostics must keep
        # the exception that actually came from the model call, including its
        # type and raise-site traceback origin.  The envelope remains in
        # ``original_exception`` so callers can still inspect the wrapper.
        original = getattr(exc, "original_exception", None)
        source = original if isinstance(original, BaseException) else exc
        message = sanitize_message(source) or sanitize_message(exc)
        errno = getattr(source, "errno", None)
        if errno is None:
            errno = getattr(exc, "errno", None)
        try:
            errno = int(errno) if errno is not None else None
        except (TypeError, ValueError):
            errno = None
        exception_type = type(source).__name__
        origin = traceback_origin(source) or traceback_origin(exc)
        fingerprint = failure_fingerprint(
            exception_type=exception_type,
            errno=errno,
            phase=phase,
            message=message,
            origin=origin,
        )
        return cls(
            segment_id=str(segment_id),
            chapter_id=str(chapter_id),
            phase=phase,
            exception_type=exception_type,
            errno=errno,
            message=message,
            fingerprint=fingerprint,
            recoverable=bool(recoverable),
            engine_related=bool(engine_related),
            traceback_origin=origin,
            code=str(code or ""),
            original_exception=exc,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "chapter_id": self.chapter_id,
            "phase": self.phase,
            "exception_type": self.exception_type,
            "errno": self.errno,
            "message": self.message,
            "fingerprint": self.fingerprint,
            "recoverable": self.recoverable,
            "engine_related": self.engine_related,
            "traceback_origin": self.traceback_origin,
            "code": self.code,
        }


def is_confirmed_engine_recovery(failure: SynthesisFailure) -> bool:
    """Return whether ``failure`` matches the bounded engine-recycle allow-list.

    The queue receives structured failures from several phases.  Only the
    observed ``OSError(errno=22)`` from ``engine_infer`` and the explicit OOM
    exhaustion code are currently confirmed engine-runtime fingerprints.  In
    particular, an arbitrary OSError, a publish error, or a caller-provided
    ``recoverable=True`` flag must not recycle the engine.
    """
    if not failure.recoverable or not failure.engine_related:
        return False
    if failure.phase != PHASE_ENGINE_INFER:
        return False
    if failure.code in RECOVERABLE_ENGINE_CODES:
        return True
    return (
        failure.errno == 22
        and failure.exception_type == "OSError"
    )


@dataclass
class RecoveryHooks:
    """Runtime-injected callbacks that enable queue-level self-healing.

    All fields default to None; legacy callers that do not pass hooks keep
    the previous fail-fast-per-segment behavior.
    """

    recycle: Optional[Callable[[], int]] = None
    cancel_requested: Optional[Callable[[], bool]] = None
    shutdown_requested: Optional[Callable[[], bool]] = None
    pause_gate: Optional[Callable[[], None]] = None
    on_recovery: Optional[Callable[[dict[str, Any]], None]] = None
    on_failure: Optional[Callable[[SynthesisFailure], None]] = None

    @property
    def enabled(self) -> bool:
        return self.recycle is not None


__all__ = [
    "DEFAULT_RECOVERY_BUDGET",
    "PHASE_ATOMIC_PUBLISH",
    "PHASE_DIRECTED_SYNTHESIS",
    "PHASE_ENGINE_INFER",
    "PHASE_STATUS_PERSIST",
    "PHASE_UNKNOWN",
    "PHASE_WAV_VALIDATE",
    "PHASES",
    "RECOVERABLE_ENGINE_CODES",
    "RecoveryBudget",
    "RecoveryHooks",
    "SynthesisFailure",
    "failure_fingerprint",
    "is_confirmed_engine_recovery",
    "sanitize_message",
    "traceback_origin",
]
