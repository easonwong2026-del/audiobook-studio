"""Canonical task-state matrix tests."""
from __future__ import annotations

from lib.task_state import (
    ACTIVE_TASK_STATES,
    ATTENTION_TASK_STATES,
    RESUMABLE_TASK_STATES,
    TERMINAL_TASK_STATES,
    is_active_task_status,
    is_attention_status,
    is_resumable_task_status,
    is_terminal_task_status,
)


def test_task_state_matrix_matches_lifecycle_contract():
    assert ACTIVE_TASK_STATES == {
        "pending", "running", "pausing", "paused", "recovering", "cancelling",
    }
    assert ATTENTION_TASK_STATES == {"needs_attention"}
    assert TERMINAL_TASK_STATES == {"done", "error", "cancelled", "interrupted"}
    assert RESUMABLE_TASK_STATES == {"interrupted", "needs_attention"}

    for status in ACTIVE_TASK_STATES:
        assert is_active_task_status(status)
        assert not is_terminal_task_status(status)
        assert not is_attention_status(status)

    assert is_attention_status("needs_attention")
    assert not is_terminal_task_status("needs_attention")

    for status in TERMINAL_TASK_STATES:
        assert is_terminal_task_status(status)
        assert not is_active_task_status(status)

    assert is_resumable_task_status("interrupted")
    assert is_resumable_task_status("needs_attention")
    assert not is_resumable_task_status("done")
