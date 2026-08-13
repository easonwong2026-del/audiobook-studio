"""Canonical durable task-state semantics.

Task status strings are part of the Web/MCP contract.  Keep the vocabulary
small and expose predicates for callers that need lifecycle semantics; callers
should not grow their own slightly different status sets.
"""
from __future__ import annotations

from typing import Any


ACTIVE_TASK_STATES = frozenset({
    "pending",
    "running",
    "pausing",
    "paused",
    "recovering",
    "cancelling",
})
ATTENTION_TASK_STATES = frozenset({"needs_attention"})
TERMINAL_TASK_STATES = frozenset({
    "done",
    "error",
    "cancelled",
    "interrupted",
})
# A terminal attempt is not necessarily a permanently disposable record:
# ``interrupted`` can be resumed and ``needs_attention`` requires action.
ENDED_TASK_STATES = TERMINAL_TASK_STATES | ATTENTION_TASK_STATES
FINAL_TASK_STATES = frozenset({"done", "error", "cancelled"})
RESUMABLE_TASK_STATES = frozenset({"interrupted", "needs_attention"})
TASK_STATES = (
    "pending",
    "running",
    "pausing",
    "paused",
    "recovering",
    "cancelling",
    "needs_attention",
    "done",
    "error",
    "cancelled",
    "interrupted",
)


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def is_active_task_status(value: Any) -> bool:
    return _status(value) in ACTIVE_TASK_STATES


def is_attention_status(value: Any) -> bool:
    return _status(value) in ATTENTION_TASK_STATES


def is_terminal_task_status(value: Any) -> bool:
    return _status(value) in TERMINAL_TASK_STATES


def is_ended_task_status(value: Any) -> bool:
    return _status(value) in ENDED_TASK_STATES


def is_final_task_status(value: Any) -> bool:
    return _status(value) in FINAL_TASK_STATES


def is_resumable_task_status(value: Any) -> bool:
    return _status(value) in RESUMABLE_TASK_STATES


__all__ = [
    "ACTIVE_TASK_STATES",
    "ATTENTION_TASK_STATES",
    "ENDED_TASK_STATES",
    "FINAL_TASK_STATES",
    "RESUMABLE_TASK_STATES",
    "TASK_STATES",
    "TERMINAL_TASK_STATES",
    "is_active_task_status",
    "is_attention_status",
    "is_ended_task_status",
    "is_final_task_status",
    "is_resumable_task_status",
    "is_terminal_task_status",
]
