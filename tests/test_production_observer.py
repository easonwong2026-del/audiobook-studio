"""Regression tests for the synthesis observer lifecycle and task card."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _task(status="running", *, finished_at="", updated_at="2026-01-01T00:00:00Z"):
    return {
        "task_id": "task-1",
        "project": "book",
        "source": "mcp",
        "status": status,
        "scope": {"all": True, "chapter_ids": [], "segment_ids": []},
        "progress": {
            "total": 65,
            "completed": 65 if status == "done" else 12,
            "failed": 0,
            "percent": 100.0 if status == "done" else 18.5,
            "current_chapter": None,
            "current_segment": None,
        },
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": "2026-01-01T00:00:03Z",
        "finished_at": finished_at,
        "updated_at": updated_at,
    }


@pytest.fixture
def app_module(monkeypatch):
    import app

    monkeypatch.setattr(
        app.ProductionJobService,
        "get_runtime_health",
        staticmethod(lambda: {}),
    )
    monkeypatch.setattr(app, "_global_default_engine", lambda: {})
    return app


@pytest.mark.parametrize("status", ["done", "error", "cancelled", "needs_attention", "interrupted"])
def test_terminal_task_card_shows_total_elapsed(app_module, status):
    task = _task(status, finished_at="2026-01-01T00:03:18Z")

    rendered = app_module._production_task_markdown(task)

    assert "**总耗时**：3 分 18 秒" in rendered


def test_invalid_task_timestamps_are_safe(app_module):
    task = _task("done", finished_at="not-a-timestamp")
    task["created_at"] = "also-not-a-timestamp"

    rendered = app_module._production_task_markdown(task)

    assert "**总耗时**：—" in rendered


def test_latest_task_keeps_terminal_result(app_module, monkeypatch):
    task = _task("done", finished_at="2026-01-01T00:03:18Z")
    monkeypatch.setattr(
        app_module.ProductionJobService,
        "list_tasks",
        staticmethod(lambda **_kwargs: [task]),
    )

    assert app_module._latest_production_task("book") == task


@pytest.mark.parametrize(
    ("task", "active"),
    [([], False), ([_task("running")], True), ([_task("done", finished_at="2026-01-01T00:03:18Z")], False)],
)
def test_production_tick_controls_active_timer(app_module, monkeypatch, task, active):
    monkeypatch.setattr(
        app_module.ProductionJobService,
        "list_tasks",
        staticmethod(lambda **_kwargs: task),
    )
    monkeypatch.setattr(app_module, "refresh_queue_list", lambda _ss: "queue")
    monkeypatch.setattr(app_module, "refresh_production_engine_status", lambda _ss: "engine")
    session = SimpleNamespace(project="book")

    result = app_module.refresh_production_task_tick(session)

    assert result[1:3] == ("queue", "engine")
    assert result[-1].active is active
    if active is False and task:
        assert "**总耗时**" in result[0]


def test_start_action_activates_production_timer(app_module):
    assert app_module.activate_production_timer().active is True


def test_external_mcp_task_is_discovered_once_without_repaint(app_module, monkeypatch):
    task = _task("pending", updated_at="2026-01-01T00:00:01Z")
    tasks = [task]
    monkeypatch.setattr(
        app_module.ProductionJobService,
        "list_tasks",
        staticmethod(lambda **_kwargs: tasks),
    )
    monkeypatch.setattr(app_module, "refresh_queue_list", lambda _ss: "queue")
    monkeypatch.setattr(app_module, "refresh_production_engine_status", lambda _ss: "engine")
    session = SimpleNamespace(project="book")

    first = app_module.watch_external_production_task(session)
    second = app_module.watch_external_production_task(session)

    assert "Agent / MCP" in first[0]
    assert first[1:3] == ("queue", "engine")
    assert first[4].active is True
    assert second == (app_module.gr.skip(),) * 5

    tasks[0] = _task("done", finished_at="2026-01-01T00:03:18Z", updated_at="2026-01-01T00:03:18Z")
    terminal = app_module.watch_external_production_task(session)
    assert "已完成" in terminal[0]
    assert "**总耗时**：3 分 18 秒" in terminal[0]
    assert terminal[4].active is False


def test_production_observer_has_one_active_timer_and_low_frequency_watcher():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

    assert "s_task_timer" not in source
    assert "time.sleep(0.5)" not in source
    assert "s_start_timer = gr.Timer(4.0, active=False)" in source
    assert "s_external_task_watcher = gr.Timer(20.0)" in source
    assert "watch_external_production_task" in source
