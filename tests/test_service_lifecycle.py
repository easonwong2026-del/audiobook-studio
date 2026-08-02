from __future__ import annotations

import json
import time
from pathlib import Path

from services.service_lifecycle import ServiceLifecycle


def _reset_lifecycle():
    ServiceLifecycle._cleanup_hooks = []
    ServiceLifecycle._server_close = None
    ServiceLifecycle._exit_callback = None
    ServiceLifecycle._state = "stopped"
    ServiceLifecycle._stop_event.clear()


def test_shutdown_runs_owned_cleanup_and_is_idempotent(tmp_path: Path):
    _reset_lifecycle()
    calls = []
    ServiceLifecycle.configure(
        pid_path=tmp_path / "runtime/service.json",
        port=0,
        exit_callback=None,
    )
    ServiceLifecycle.register_cleanup("first", lambda: calls.append("first"))
    ServiceLifecycle.register_cleanup("second", lambda: calls.append("second"))
    assert "服务正在关闭" in ServiceLifecycle.request_shutdown(delay=0)
    assert any(
        token in ServiceLifecycle.request_shutdown(delay=0)
        for token in ("正在关闭", "已经停止")
    )
    for _ in range(50):
        if ServiceLifecycle.status()["state"] == "stopped":
            break
        time.sleep(0.01)
    assert ServiceLifecycle.status()["state"] == "stopped"
    assert calls == ["second", "first"]
    record = json.loads(
        (tmp_path / "runtime/service.json").read_text(encoding="utf-8")
    )
    assert record["owner"] == "audiobook-studio"
    assert record["state"] == "stopped"
    assert "已经停止" in ServiceLifecycle.request_shutdown(delay=0)
    _reset_lifecycle()


def test_stop_owned_instance_rejects_wrong_owner(tmp_path: Path):
    path = tmp_path / "service.json"
    path.write_text(json.dumps({"owner": "other-app", "pid": 1}), encoding="utf-8")
    assert "未执行停止" in ServiceLifecycle.stop_owned_instance(path)
