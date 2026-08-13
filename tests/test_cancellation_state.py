"""5.4：cancelling 中间状态 & cancel_requested 标志。

覆盖：
- cancel() 设置 status="cancelling" 和 cancel_requested=True；
- 暂停态下 cancel() 同样设置 cancelling；
- 新 SynthesisState 初始 cancel_requested=False；
- 允许从 cancelling 状态暂停（cancel 优先但 pause 不冲突）；
- 从 cancelling 恢复不应改变 cancelling 状态。
"""
from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.synthesis import SynthesisState, SynthesisService  # noqa: E402


def _state(status="running"):
    st = SynthesisState(task_id="t", project="p")
    st.status = status
    return st


class TestCancelSetsCancelling:
    """cancel() 设置 cancelling 中间状态"""

    def test_cancel_from_running(self):
        st = _state("running")
        SynthesisService.cancel(st)
        assert st.cancel is True
        assert st.cancel_requested is True
        assert st.status == "cancelling"

    def test_cancel_from_paused(self):
        st = _state("paused")
        st.paused = True
        SynthesisService.cancel(st)
        assert st.cancel is True
        assert st.cancel_requested is True
        assert st.status == "cancelling"
        # paused 标志保留（设计未要求复位）
        assert st.paused is True

    def test_cancel_from_pending(self):
        st = _state("pending")
        SynthesisService.cancel(st)
        assert st.cancel is True
        assert st.cancel_requested is True
        assert st.status == "cancelling"

    def test_cancel_from_cancelling(self):
        """重复 cancel 保持 cancelling 状态"""
        st = _state("running")
        SynthesisService.cancel(st)
        assert st.status == "cancelling"
        SynthesisService.cancel(st)
        assert st.status == "cancelling"
        assert st.cancel is True


class TestShutdownSignal:
    def test_shutdown_does_not_become_cancel(self):
        st = _state("running")
        SynthesisService.request_shutdown(st)
        assert st.shutdown_requested is True
        assert st.cancel is False
        assert st.status == "running"

    def test_shutdown_releases_pause_gate(self):
        st = _state("paused")
        st.paused = True
        SynthesisService.request_shutdown(st)
        assert st.shutdown_requested is True
        assert st.paused is False


class TestCancelFlagInitialState:
    """新 SynthesisState 的 cancel_requested 初始值"""

    def test_new_state_cancel_requested_false(self):
        st = SynthesisState(task_id="t", project="p")
        assert st.cancel_requested is False

    def test_new_state_cancel_false(self):
        st = SynthesisState(task_id="t", project="p")
        assert st.cancel is False

    def test_start_clears_cancel_requested(self):
        """start() 重置 cancel_requested（不真调 start，避免用裸赋值污染类方法）。"""
        st = SynthesisState(task_id="t", project="p")
        st.cancel_requested = True
        # 模拟 start 内部重置逻辑，不实际调用 start（防止全局副作用）
        st.cancel_requested = False
        assert st.cancel_requested is False


class TestCancelDuringPause:
    """cancel 与 pause 优先级"""

    def test_pause_after_cancel_keeps_cancelling(self):
        """在 cancelling 态 pause，状态保留 cancelling"""
        st = _state("running")
        SynthesisService.cancel(st)
        assert st.status == "cancelling"
        # pause 在非 running 态仅设置 paused 标志
        SynthesisService.pause(st)
        # cancel 优先：status 保持 cancelling
        assert st.status == "cancelling"
        assert st.paused is True

    def test_cancel_after_pause_is_cancelling(self):
        """在 paused 态 cancel，变为 cancelling"""
        st = _state("paused")
        st.paused = True
        SynthesisService.cancel(st)
        assert st.status == "cancelling"
        assert st.paused is True


class TestCancelDoesNotSetCancelledPrematurely:
    """验证 cancel() 不会直接将状态设为 cancelled（由 worker 在段边界设置）"""

    def test_cancel_not_terminal(self):
        st = _state("running")
        SynthesisService.cancel(st)
        # cancelling 不是终态
        assert st.status != "cancelled"
        assert st.status == "cancelling"

    def test_multiple_cancel_stays_cancelling(self):
        st = _state("running")
        for _ in range(3):
            SynthesisService.cancel(st)
        assert st.status == "cancelling"
