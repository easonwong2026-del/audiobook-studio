"""O12 状态机纯单元（无需线程 / 引擎）。

直接构造 SynthesisState，调 SynthesisService.pause / resume / cancel，断言 status 转换
与 paused 标志。设计 §2.2 状态机：
  - pause()  -> pausing（worker 段边界确认后才 paused）
  - resume() -> running（且 paused 标志清除）
  - cancel() -> cancelled（terminal，cancel 优先）

注意：pause/resume 直接改写 state.status；按设计 §2.2「cancel() -> cancelled (terminal)」，
cancel() 也应直接把 status 置为 cancelled（cancel 标志与终态一致、与 pause/resume 对称）。
若实现仅在 worker 内改写 status 而 cancel() 不置位，则本文件 cancel 相关用例失败 -> 源码 Bug。
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.synthesis import SynthesisState, SynthesisService  # noqa: E402


def _state(status="running"):
    st = SynthesisState(task_id="t", project="p")
    st.status = status
    return st


def test_pause_sets_paused_and_status():
    st = _state("running")
    SynthesisService.pause(st)
    assert st.paused is True
    assert st.status == "pausing"


def test_pause_pending_enters_pausing():
    st = _state("pending")
    SynthesisService.pause(st)
    assert st.paused is True
    assert st.status == "pausing"


def test_resume_clears_paused_and_restores_running():
    st = _state("paused")
    SynthesisService.resume(st)
    assert st.paused is False
    assert st.status == "running"


def test_resume_when_not_paused_keeps_status():
    st = _state("running")
    SynthesisService.resume(st)
    assert st.paused is False
    assert st.status == "running"


def test_cancel_sets_cancelling_status_and_flag():
    # 方案 §5.4：cancel() -> cancelling（非 terminal，等待 worker 检查边界后变 cancelled）
    st = _state("running")
    SynthesisService.cancel(st)
    assert st.cancel is True
    assert st.cancel_requested is True
    assert st.status == "cancelling", \
        f"cancel() 应把 status 置为 'cancelling'（方案 §5.4），实际 {st.status!r}"


def test_cancel_during_pause_still_cancelling_and_paused_preserved():
    # 暂停态可取消，cancel 优先；paused 标志保留（设计未要求取消时复位）
    st = _state("paused")
    st.paused = True
    SynthesisService.cancel(st)
    assert st.cancel is True
    assert st.cancel_requested is True
    assert st.status == "cancelling", \
        f"暂停态 cancel() 应置 'cancelling'（方案 §5.4），实际 {st.status!r}"
    assert st.paused is True
