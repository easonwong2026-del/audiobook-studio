"""PHASE B — 跨进程 Runtime 优雅停机（客户端侧）测试。

这里验证的是真正修复孤儿进程的那条路径：父进程（App）→ 写停机命令文件 →
detached Runtime 消费并退出 → 释放进程锁。使用 ``tests/_fake_runtime.py``
充当最小 Runtime（只持同一把 ``ProcessFileLock`` + 发布 runtime 状态），
避免拉起 torch / IndexTTS2。

覆盖用户验收清单中的：
  1. idle runtime graceful shutdown（跨进程真实退出）
  5. runtime already dead → no-op
  7. graceful timeout → terminate fallback
  8. unrelated process never killed（PID 归属安全）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from services import production_runtime
from services.production_runtime import ProductionRuntime, ProductionRuntimeClient
from services.runtime_engine import runtime_engine_status_path

FAKE_RUNTIME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fake_runtime.py")


def _wait_until(predicate, timeout: float = 15.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _now_iso(offset_seconds: float = 0.0) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_status(pid: int, *, owner_id: str, age_seconds: float = 0.0) -> None:
    path = runtime_engine_status_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stamp = _now_iso(-age_seconds)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            {
                "state": "ready",
                "engine_state": "ready",
                "runtime_state": "running",
                "pid": int(pid),
                "owner_id": owner_id,
                "updated_at": stamp,
                "runtime_updated_at": stamp,
            },
            file,
        )


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    """把数据目录、进程锁、runtime 模式全部隔离到本测试专属位置。

    关键：``AUDIOBOOK_STUDIO_RUNTIME_LOCK`` 指向私有锁文件，保证测试绝不会
    去操作开发机上真实运行的 Runtime。
    """
    data_dir = tmp_path / "data"
    lock_path = tmp_path / "runtime.lock"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_RUNTIME_LOCK", str(lock_path))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_RUNTIME_MODE", "process")
    monkeypatch.setattr(production_runtime, "_RUNTIME_PROCESS", None, raising=False)
    yield data_dir
    monkeypatch.setattr(production_runtime, "_RUNTIME_PROCESS", None, raising=False)


@pytest.fixture
def spawner():
    """拉起 fake runtime / 无关进程，并保证测试结束一定回收。"""
    children: list[subprocess.Popen] = []

    def spawn_fake(data_dir, mode: str = "respond") -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, FAKE_RUNTIME, str(data_dir), mode])
        children.append(proc)
        assert _wait_until(ProductionRuntimeClient._runtime_is_running, timeout=20.0), (
            "fake runtime 未能取得进程锁"
        )
        return proc

    def spawn_unrelated() -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        children.append(proc)
        return proc

    spawn_fake.unrelated = spawn_unrelated  # type: ignore[attr-defined]
    try:
        yield spawn_fake
    finally:
        for proc in children:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass


# ── 1. idle runtime graceful shutdown（真实跨进程） ────────────────────────


def test_idle_runtime_exits_on_shutdown_command(isolated_runtime, spawner):
    proc = spawner(isolated_runtime, "respond")
    command_path = ProductionRuntime._shutdown_command_path()
    assert not os.path.exists(command_path)

    assert ProductionRuntimeClient.request_shutdown(reason="unit_test", timeout=20.0) is True

    assert proc.wait(timeout=10) is not None, "Runtime 进程必须真正退出（不留孤儿）"
    assert ProductionRuntimeClient._runtime_is_running() is False, "进程锁必须已释放"
    assert not os.path.exists(command_path), "Runtime 必须消费并清理停机命令文件"


def test_repeated_cross_process_shutdown_is_idempotent(isolated_runtime, spawner):
    proc = spawner(isolated_runtime, "respond")
    assert ProductionRuntimeClient.request_shutdown(reason="first", timeout=20.0) is True
    proc.wait(timeout=10)
    # 第二次调用：Runtime 已不在，直接 no-op 返回 True，且不写命令文件。
    assert ProductionRuntimeClient.request_shutdown(reason="second", timeout=1.0) is True
    assert not os.path.exists(ProductionRuntime._shutdown_command_path())


def test_shutdown_command_payload_records_reason(isolated_runtime, spawner):
    """ignore 模式不消费命令文件，正好用来断言写入内容与原子性。"""
    spawner(isolated_runtime, "ignore")
    ProductionRuntimeClient._write_shutdown_command("gradio_server_stop")
    with open(ProductionRuntime._shutdown_command_path(), encoding="utf-8") as file:
        payload = json.load(file)
    assert payload["command"] == "shutdown"
    assert payload["reason"] == "gradio_server_stop"
    assert payload["requested_at"]


# ── 5. runtime already dead → no-op ───────────────────────────────────────


def test_shutdown_when_no_runtime_is_a_fast_noop(isolated_runtime):
    started = time.monotonic()
    assert ProductionRuntimeClient.request_shutdown(reason="noop", timeout=30.0) is True
    assert time.monotonic() - started < 5.0, "无 Runtime 时不得等待 graceful 超时"
    assert not os.path.exists(ProductionRuntime._shutdown_command_path())


def test_shutdown_after_runtime_crashed_is_noop(isolated_runtime, spawner):
    proc = spawner(isolated_runtime, "ignore")
    proc.kill()
    proc.wait(timeout=10)
    # 进程死亡由 OS 释放锁；客户端应识别为「已不在」而非再次终止。
    assert _wait_until(lambda: not ProductionRuntimeClient._runtime_is_running(), timeout=10.0)
    assert ProductionRuntimeClient.request_shutdown(reason="after_crash", timeout=1.0) is True


# ── 7. graceful timeout → terminate fallback ──────────────────────────────


def test_graceful_timeout_falls_back_to_terminate_verified_runtime(isolated_runtime, spawner):
    """无进程句柄（模拟上一次会话遗留的 Runtime）：先 graceful，超时后按已验证身份终止。"""
    proc = spawner(isolated_runtime, "ignore")
    assert production_runtime._RUNTIME_PROCESS is None

    assert ProductionRuntimeClient.request_shutdown(
        reason="timeout_case", timeout=1.5, terminate_timeout=8.0
    ) is True

    assert proc.wait(timeout=10) is not None, "graceful 超时后必须强制回收 Runtime"
    # graceful 确实先被尝试过：ignore 模式不会消费命令文件，故它仍在。
    assert os.path.exists(ProductionRuntime._shutdown_command_path())
    assert ProductionRuntimeClient._runtime_is_running() is False


def test_graceful_timeout_uses_exact_owned_process_handle(isolated_runtime, spawner, monkeypatch):
    """本进程亲手拉起的 Runtime：用精确的 Popen 句柄终止，而不是靠 pid 猜。"""
    proc = spawner(isolated_runtime, "ignore")
    monkeypatch.setattr(production_runtime, "_RUNTIME_PROCESS", proc, raising=False)
    # 故意让状态文件失真（pid=0）：句柄路径必须仍然生效。
    _write_status(0, owner_id="")

    assert ProductionRuntimeClient.request_shutdown(
        reason="handle_case", timeout=1.0, terminate_timeout=8.0
    ) is True
    assert proc.wait(timeout=10) is not None
    assert production_runtime._RUNTIME_PROCESS is None, "终止后必须清理进程句柄"


# ── 8. unrelated process never killed ─────────────────────────────────────


def test_unrelated_process_is_never_killed(isolated_runtime, spawner):
    """PID 存在 ≠ 它是 Studio Runtime：身份校验不通过时一律不终止。"""
    victim = spawner.unrelated()
    assert victim.poll() is None

    # (a) 状态文件缺 owner_id → 不可确认归属 → 不得终止。
    _write_status(victim.pid, owner_id="")
    ProductionRuntimeClient._terminate_runtime(terminate_timeout=1.0)
    assert victim.poll() is None, "缺少 owner_id 时不得终止该 pid"

    # (b) 状态文件过期（stale）→ pid 可能已被复用 → 不得终止。
    _write_status(victim.pid, owner_id="stale-owner", age_seconds=600)
    ProductionRuntimeClient._terminate_runtime(terminate_timeout=1.0)
    assert victim.poll() is None, "状态过期时不得终止该 pid"

    # (c) 没有任何 Runtime 持锁 → request_shutdown 直接 no-op，不进入终止分支。
    _write_status(victim.pid, owner_id="fresh-owner")
    assert ProductionRuntimeClient.request_shutdown(reason="no_lock", timeout=1.0) is True
    assert victim.poll() is None, "无 Runtime 持锁时绝不能因状态文件去杀进程"

    victim.kill()
    victim.wait(timeout=10)


def test_terminate_pid_ignores_invalid_and_dead_pids(isolated_runtime, spawner):
    production_runtime._terminate_pid(0)
    production_runtime._terminate_pid(-1)
    dead = spawner.unrelated()
    dead.kill()
    dead.wait(timeout=10)
    production_runtime._terminate_pid(dead.pid)  # 不得抛异常


def test_shutdown_never_scans_or_kills_by_process_name(isolated_runtime, spawner):
    """回归守卫：同名 python 进程必须毫发无损（禁止 taskkill python.exe 式实现）。"""
    bystanders = [spawner.unrelated() for _ in range(3)]
    proc = spawner(isolated_runtime, "respond")

    assert ProductionRuntimeClient.request_shutdown(reason="scan_guard", timeout=20.0) is True
    proc.wait(timeout=10)

    for bystander in bystanders:
        assert bystander.poll() is None, "无关的 python 进程被误杀"
        bystander.kill()
        bystander.wait(timeout=10)
