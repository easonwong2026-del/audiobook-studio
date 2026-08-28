"""PHASE B — ApplicationLifecycleService（应用优雅停机协调器）测试。

覆盖用户验收清单中的：
  9. app lifecycle coordinator single-flight
 10. signal / exit trigger 恰好调用协调器一次

并附一条结构性回归守卫：确认 ``app.py`` 的 ``__main__`` 真的把
「server close / signal / atexit」这三条退出边接到了协调器 —— 本次事故的
根因正是「有停机原语、但没接上 app 退出边」，纯逻辑测试无法防回归。
"""
from __future__ import annotations

import ast
import atexit
import os
import signal
import threading

import pytest

from services.application_lifecycle import (
    ApplicationLifecycleService,
    get_application_lifecycle,
    reset_application_lifecycle,
)
from services.production_runtime import ProductionRuntimeClient

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def shutdown_calls(monkeypatch):
    """记录 Runtime 停机被真正触发的次数。"""
    calls: list[dict] = []

    def fake_request_shutdown(reason="application_shutdown", timeout=30.0, terminate_timeout=10.0):
        calls.append({"reason": reason, "timeout": timeout})
        return True

    monkeypatch.setattr(
        ProductionRuntimeClient,
        "request_shutdown",
        staticmethod(fake_request_shutdown),
    )
    return calls


@pytest.fixture
def lifecycle():
    """独立协调器实例；退出时确保信号处理器 / atexit 钩子被还原。"""
    reset_application_lifecycle()
    service = get_application_lifecycle()
    try:
        yield service
    finally:
        reset_application_lifecycle()


# ── 9. single-flight ───────────────────────────────────────────────────────


def test_coordinator_single_flight(lifecycle, shutdown_calls):
    assert lifecycle.state == "running"
    assert lifecycle.is_shutting_down() is False

    assert lifecycle.request_application_shutdown("first") is True
    assert lifecycle.state == "stopped"
    assert lifecycle.is_shutting_down() is True

    # 重复调用安全：直接返回 False，不再触发第二次 Runtime 停机。
    assert lifecycle.request_application_shutdown("second") is False
    assert lifecycle.request_application_shutdown("third") is False

    assert len(shutdown_calls) == 1
    assert shutdown_calls[0]["reason"] == "first"


def test_coordinator_single_flight_under_concurrency(lifecycle, shutdown_calls):
    winners: list[bool] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        winners.append(lifecycle.request_application_shutdown("concurrent"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert winners.count(True) == 1, "并发下只应有一个调用方拿到停机所有权"
    assert len(shutdown_calls) == 1


def test_shutdown_callbacks_run_once_and_never_raise(lifecycle, shutdown_calls):
    seen: list[str] = []
    lifecycle.register_shutdown_callback(lambda reason: seen.append(reason))
    lifecycle.register_shutdown_callback(lambda reason: (_ for _ in ()).throw(RuntimeError("boom")))
    lifecycle.register_shutdown_callback(lambda reason: seen.append("after-error"))

    assert lifecycle.request_application_shutdown("cb") is True
    # 回调异常不得中断停机序列，后续回调仍执行，状态仍到 stopped。
    assert seen == ["cb", "after-error"]
    assert lifecycle.state == "stopped"


def test_runtime_shutdown_failure_still_completes_app_shutdown(lifecycle, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("runtime unreachable")

    monkeypatch.setattr(ProductionRuntimeClient, "request_shutdown", staticmethod(boom))
    # Runtime 停机失败也必须走完 app 停机（否则进程会卡在 shutting_down）。
    assert lifecycle.request_application_shutdown("failing") is True
    assert lifecycle.state == "stopped"


# ── 10. signal / exit trigger 恰好一次 ─────────────────────────────────────


def test_install_process_exit_hooks_registers_signal_and_atexit(lifecycle, monkeypatch):
    installed: list[int] = []
    registered: list[object] = []

    monkeypatch.setattr(signal, "signal", lambda num, handler: installed.append(num))
    monkeypatch.setattr(atexit, "register", lambda func: registered.append(func) or func)

    lifecycle.install_process_exit_hooks()

    assert signal.SIGINT in installed
    assert getattr(signal, "SIGTERM", None) in installed
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        assert sigbreak in installed
    assert len(registered) == 1

    # 重复安装是 no-op（不会叠加处理器 / 重复注册 atexit）。
    lifecycle.install_process_exit_hooks()
    assert len(registered) == 1


def test_signal_handler_chains_previous_handler(lifecycle, shutdown_calls):
    chained: list[tuple] = []
    lifecycle._prev_signal_handlers[signal.SIGINT] = lambda num, frame: chained.append((num, frame))

    handler = lifecycle._make_signal_handler(signal.SIGINT)
    handler(signal.SIGINT, None)

    assert len(shutdown_calls) == 1, "信号必须触发一次 Runtime 停机"
    assert chained == [(signal.SIGINT, None)], "必须链式转发原处理器（不破坏 Gradio Ctrl+C）"

    # 第二次信号：原处理器照旧转发，但 Runtime 停机不再重复执行。
    handler(signal.SIGINT, None)
    assert len(shutdown_calls) == 1
    assert len(chained) == 2


def test_signal_handler_without_previous_preserves_default_behaviour(lifecycle, shutdown_calls):
    lifecycle._prev_signal_handlers[signal.SIGINT] = signal.SIG_DFL
    handler = lifecycle._make_signal_handler(signal.SIGINT)

    # 无 Python 级原处理器时保留惯例语义，让 finally / atexit 链继续跑。
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGINT, None)
    assert len(shutdown_calls) == 1


def test_all_exit_triggers_cause_exactly_one_runtime_shutdown(lifecycle, shutdown_calls):
    """atexit + signal + gradio server close 同时触发 → 只关一次 Runtime。"""
    lifecycle._prev_signal_handlers[signal.SIGINT] = signal.SIG_IGN
    handler = lifecycle._make_signal_handler(signal.SIGINT)

    handler(signal.SIGINT, None)                                  # Ctrl+C
    lifecycle.request_application_shutdown("gradio_server_stop")   # launch() finally
    lifecycle._atexit_hook()                                       # 进程退出兜底

    assert len(shutdown_calls) == 1
    assert shutdown_calls[0]["reason"] == "signal_2"
    assert lifecycle.state == "stopped"


def test_atexit_hook_is_the_fallback_when_nothing_else_fired(lifecycle, shutdown_calls):
    lifecycle._atexit_hook()
    assert len(shutdown_calls) == 1
    assert shutdown_calls[0]["reason"] == "application_exit"


def test_reset_restores_signal_handlers_and_unregisters_atexit(shutdown_calls):
    original = signal.getsignal(signal.SIGINT)
    reset_application_lifecycle()
    service = get_application_lifecycle()
    service.install_process_exit_hooks()
    assert signal.getsignal(signal.SIGINT) is not original

    reset_application_lifecycle()
    assert signal.getsignal(signal.SIGINT) is original
    # 新的单例是干净的 running 状态。
    assert get_application_lifecycle().state == "running"
    reset_application_lifecycle()


def test_singleton_identity():
    reset_application_lifecycle()
    try:
        first = get_application_lifecycle()
        assert get_application_lifecycle() is first
        reset_application_lifecycle()
        assert get_application_lifecycle() is not first
    finally:
        reset_application_lifecycle()


def test_fresh_service_instances_are_independent(shutdown_calls):
    a = ApplicationLifecycleService()
    b = ApplicationLifecycleService()
    assert a.request_application_shutdown("a") is True
    assert b.state == "running"
    assert b.request_application_shutdown("b") is True
    assert len(shutdown_calls) == 2


# ── 结构性回归守卫：app.py 真的接上了退出边 ────────────────────────────────


def _main_block() -> ast.If:
    with open(os.path.join(BASE, "app.py"), encoding="utf-8") as file:
        tree = ast.parse(file.read())
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            return node
    raise AssertionError("app.py 缺少 __main__ 块")


def _called_attrs(node: ast.AST) -> set[str]:
    return {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }


def test_app_main_installs_exit_hooks():
    assert "install_process_exit_hooks" in _called_attrs(_main_block()), (
        "app.py __main__ 必须安装退出钩子，否则 Runtime 会在应用退出后成为孤儿进程"
    )


def test_app_main_wraps_launch_in_finally_shutdown():
    """launch() 必须在 try/finally 中，且 finally 调用协调器停机。"""
    main_block = _main_block()
    tries = [node for node in ast.walk(main_block) if isinstance(node, ast.Try)]
    matched = [
        node
        for node in tries
        if "launch" in _called_attrs(ast.Module(body=node.body, type_ignores=[]))
        and "request_application_shutdown"
        in _called_attrs(ast.Module(body=node.finalbody, type_ignores=[]))
    ]
    assert matched, (
        "app.py 必须把 launch() 包在 try/finally 中，并在 finally 里调用 "
        "request_application_shutdown —— 这是最可靠的一条退出边"
    )
