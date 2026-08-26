"""Regression tests for the synthesis observer lifecycle and task card."""
from __future__ import annotations

import ast
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


def test_task_card_distinguishes_active_and_terminal_tasks(app_module):
    assert "### 当前生产任务" in app_module._production_task_markdown(_task("running"))
    assert "### 最近生产任务" in app_module._production_task_markdown(
        _task("done", finished_at="2026-01-01T00:03:18Z")
    )


def test_terminal_task_card_does_not_render_stale_active_details(app_module):
    task = _task("done", finished_at="2026-01-01T00:03:18Z")
    task["startup"] = {
        "startup_phase": "runtime_starting",
        "startup_phase_elapsed_seconds": 291,
    }
    task["progress"].update({
        "current_chapter": "11",
        "current_segment": "11-007",
    })

    rendered = app_module._production_task_markdown(task)

    assert "### 最近生产任务" in rendered
    assert "**总耗时**" in rendered
    assert "生产中" not in rendered
    assert "- **当前**" not in rendered
    assert "已持续" not in rendered


def test_active_task_card_keeps_phase_and_current_segment(app_module):
    task = _task("running")
    task["startup"] = {
        "startup_phase": "runtime_starting",
        "startup_phase_elapsed_seconds": 12,
    }
    task["progress"].update({
        "current_chapter": "11",
        "current_segment": "11-007",
    })

    rendered = app_module._production_task_markdown(task)

    assert "### 当前生产任务" in rendered
    assert "已持续 12 秒" in rendered
    assert "- **当前**：11 · `11-007`" in rendered


def test_terminal_engine_failure_remains_visible_without_active_elapsed(app_module):
    task = _task("error", finished_at="2026-01-01T00:03:18Z")
    task["startup"] = {
        "startup_phase": "engine_failed",
        "startup_phase_elapsed_seconds": 291,
        "engine_error_code": "TTS_ENGINE_INIT_FAILED",
        "engine_error_summary": "引擎不可用",
    }
    task["error_summary"] = "TTS_ENGINE_INIT_FAILED: 引擎不可用"

    rendered = app_module._production_task_markdown(task)

    assert "TTS_ENGINE_INIT_FAILED" in rendered
    assert "引擎不可用" in rendered
    assert "已持续" not in rendered


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        ({"ready": False, "to_synthesize": 3}, False),
        ({"ready": True, "to_synthesize": 3}, True),
        ({"ready": True, "to_synthesize": 0}, False),
    ],
)
def test_scope_start_requires_ready_plan_and_remaining_work(app_module, plan, expected):
    assert app_module._scope_can_start(plan) is expected
    assert app_module._scope_start_update(plan)["interactive"] is expected


def test_scope_plan_separates_selected_scope_from_whole_book(app_module):
    selected = {
        "ready": True,
        "segments": 3,
        "chapters": 1,
        "already_completed": 3,
        "remaining": 0,
        "to_synthesize": 0,
        "failed": 0,
        "voice_cast": {"required_role_count": 1, "bound_role_count": 1},
    }
    whole_book = {
        "ready": True,
        "segments": 32,
        "chapters": 11,
        "already_completed": 3,
        "remaining": 29,
        "to_synthesize": 29,
        "failed": 0,
    }

    rendered = app_module._format_scope_plan(selected, "segments", whole_book)

    assert "✅ 本次选择已全部完成" in rendered
    assert "本次选择：3 段" in rendered
    assert "项目整体：32 段" in rendered
    assert "项目仍有 29 段待生产" in rendered
    assert "整本”或选择“仅未完成" in rendered
    assert "当前选择无需重复生产" not in rendered


def test_scope_plan_reports_current_scope_complete_without_fallback(app_module):
    selected = {
        "ready": True,
        "segments": 3,
        "already_completed": 3,
        "remaining": 0,
        "to_synthesize": 0,
        "failed": 0,
        "voice_cast": {},
    }
    whole_book = {
        "ready": True,
        "segments": 32,
        "already_completed": 32,
        "remaining": 0,
        "to_synthesize": 0,
        "failed": 0,
    }

    rendered = app_module._format_scope_plan(selected, "segments", whole_book)

    assert "✅ 本次选择已全部完成" in rendered
    assert "✅ 项目已全部生产完成" not in rendered


def test_scope_plans_only_replan_whole_book_for_all_scope(app_module, monkeypatch):
    calls = []
    selected_plan = {"scope": {"all": False}}
    whole_plan = {"scope": {"all": True}}

    def fake_plan(project_name, scope):
        calls.append((project_name, scope))
        return whole_plan if scope.get("all") else selected_plan

    monkeypatch.setattr(app_module.ProductionJobService, "plan", staticmethod(fake_plan))

    current, project_plan = app_module._production_scope_plans(
        "book", {"all": False, "segment_ids": ["11-007"]},
    )

    assert current is selected_plan
    assert project_plan is None
    assert calls == [("book", {"all": False, "segment_ids": ["11-007"]})]

    current, project_plan = app_module._production_scope_plans(
        "book", {"all": True},
    )

    assert current is whole_plan
    assert project_plan is whole_plan
    assert calls == [
        ("book", {"all": False, "segment_ids": ["11-007"]}),
        ("book", {"all": True}),
    ]

    selected_plan.update({
        "ready": True,
        "segments": 1,
        "already_completed": 0,
        "remaining": 1,
        "to_synthesize": 1,
        "failed": 0,
    })
    rendered = app_module._format_scope_plan(selected_plan, "segments", None)
    assert "本次选择：1 段" in rendered
    assert "项目整体：" not in rendered
    assert app_module._scope_start_update(selected_plan)["interactive"] is True

    selected_plan.update({"already_completed": 1, "remaining": 0, "to_synthesize": 0})
    assert "✅ 项目已全部生产完成" in app_module._format_scope_plan(
        selected_plan, "all", selected_plan,
    )


def test_scope_preview_is_only_visible_for_chapter_mode(app_module):
    assert app_module.update_scope_visibility("all")[2]["visible"] is False
    assert app_module.update_scope_visibility("segments")[2]["visible"] is False
    assert app_module.update_scope_visibility("chapters")[2]["visible"] is True


def test_scope_controls_output_contract_matches_wiring(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "_chapter_options",
        lambda _ss: ([("第 1 章", "c1")], ["c1"]),
    )
    monkeypatch.setattr(
        app_module.ProjectService,
        "get_synthesis_selections",
        staticmethod(lambda _project: {"mode": "chapters", "chapters": ["c1"]}),
    )
    monkeypatch.setattr(app_module, "_segment_choices", lambda *_args: ([], []))
    monkeypatch.setattr(app_module, "_segment_records", lambda *_args: [])
    monkeypatch.setattr(app_module, "_scope_preview_rows", lambda *_args: [])
    monkeypatch.setattr(
        app_module,
        "_production_scope_plans",
        lambda *_args: (
            {
                "project_name": "book",
                "ready": True,
                "segments": 1,
                "chapters": 1,
                "already_completed": 0,
                "remaining": 1,
                "to_synthesize": 1,
                "failed": 0,
                "voice_cast": {},
                "blockers": [],
                "warnings": [],
            },
        ) * 2,
    )
    monkeypatch.setattr(
        app_module.df_style,
        "style_dataframe",
        lambda *_args, **_kwargs: "styled-preview",
    )

    result = app_module.render_scope_controls(SimpleNamespace(project="book"))

    assert len(result) == 10
    assert result[-3]["value"] == "styled-preview"
    assert result[-3]["visible"] is True
    assert "### 按章节" in result[-2]
    assert result[-1]["interactive"] is True

    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    def function(name):
        return next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def return_counts(name):
        returns = [node for node in ast.walk(function(name)) if isinstance(node, ast.Return)]
        return [len(node.value.elts) for node in returns]

    def wired_output_counts(name):
        counts = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 3:
                continue
            callback = node.args[0]
            if isinstance(callback, ast.Name) and callback.id == name:
                outputs = node.args[2]
                if isinstance(outputs, ast.List):
                    counts.append(len(outputs.elts))
        return counts

    assert return_counts("render_scope_controls") == [10]
    assert return_counts("refresh_scope_preview") == [3, 3]
    assert return_counts("update_scope_visibility") == [3]
    assert wired_output_counts("render_scope_controls") == [10, 10]
    assert wired_output_counts("refresh_scope_preview") == [3, 3, 3, 3, 3]
    assert wired_output_counts("update_scope_visibility") == [3]


def test_custom_segment_selection_accumulates_and_reaches_scope_plan(app_module, monkeypatch):
    visible_by_chapter = {
        "11": ["11-001", "11-002", "11-003"],
        "12": ["12-001", "12-004"],
    }
    monkeypatch.setattr(
        app_module,
        "_segment_choices",
        lambda _ss, chapter: ([], visible_by_chapter[str(chapter)]),
    )
    session = SimpleNamespace(project="book")

    visible_update, state = app_module.merge_segment_selection(
        ["11-002", "11-003"], [], "11", session,
    )
    assert visible_update["value"] == ["11-002", "11-003"]
    assert state == ["11-002", "11-003"]

    _, state = app_module.merge_segment_selection(
        ["12-004"], state, "12", session,
    )
    assert state == ["11-002", "11-003", "12-004"]
    assert app_module.refresh_segment_filter(session, "11", state)["value"] == [
        "11-002", "11-003",
    ]

    plan_calls = []

    def fake_plan(_project, scope):
        plan_calls.append(scope)
        selected_count = len(scope.get("segment_ids", []))
        return {
            "project_name": "book",
            "ready": True,
            "segments": selected_count or 3,
            "chapters": 1,
            "already_completed": 0,
            "remaining": selected_count or 3,
            "to_synthesize": selected_count or 3,
            "failed": 0,
            "voice_cast": {},
            "blockers": [],
            "warnings": [],
        }

    monkeypatch.setattr(
        app_module.ProductionJobService,
        "plan",
        staticmethod(fake_plan),
    )
    monkeypatch.setattr(app_module, "_scope_preview_rows", lambda *_args: [])
    monkeypatch.setattr(
        app_module.df_style,
        "style_dataframe",
        lambda *_args, **_kwargs: [],
    )

    _, readiness, start = app_module.refresh_scope_preview(
        session, "segments", [], "11", state,
    )

    assert plan_calls[0]["segment_ids"] == ["11-002", "11-003", "12-004"]
    assert "本次选择：3 段" in readiness
    assert start["interactive"] is True


def test_segment_action_preserves_other_chapter_selection(app_module, monkeypatch):
    records = {
        "11": [
            {"id": "11-001", "status": "pending"},
            {"id": "11-002", "status": "done"},
            {"id": "11-003", "status": "failed"},
        ],
    }
    monkeypatch.setattr(
        app_module,
        "_segment_choices",
        lambda _ss, chapter: ([], [item["id"] for item in records[str(chapter)]]),
    )
    monkeypatch.setattr(
        app_module,
        "_segment_records",
        lambda _ss, chapter: records[str(chapter)],
    )
    session = SimpleNamespace(project="book")

    _, cleared = app_module.clear_scope_segments(
        "11", ["12-004", "11-002"], session,
    )
    assert cleared == ["12-004"]
    _, pending = app_module.select_pending_scope_segments("11", cleared, session)
    assert pending == ["12-004", "11-001", "11-003"]
    _, failed = app_module.select_failed_scope_segments("11", pending, session)
    assert failed == ["12-004", "11-003"]


def test_custom_segment_checkbox_listens_only_to_user_input(app_module):
    merge_event = next(
        item
        for item in app_module.app.get_config_file()["dependencies"]
        if item.get("api_name") == "merge_segment_selection"
    )

    assert merge_event["targets"] == [(app_module.s_segments_sel._id, "input")]


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
    monkeypatch.setattr(app_module, "_format_queue_list", lambda _ss, _task: "queue")
    monkeypatch.setattr(
        app_module,
        "_format_production_engine_status",
        lambda _ss, _health: "engine",
    )
    session = SimpleNamespace(project="book")

    result = app_module.refresh_production_task_tick(session)

    assert result[1:3] == ("queue", "engine")
    assert result[-1].active is active
    if active is False and task:
        assert "**总耗时**" in result[0]


def test_production_tick_reads_task_and_health_once(app_module, monkeypatch):
    task = _task("running")
    calls = {"tasks": 0, "health": 0}

    def list_tasks(**_kwargs):
        calls["tasks"] += 1
        return [task]

    def get_health():
        calls["health"] += 1
        return {}

    monkeypatch.setattr(
        app_module.ProductionJobService,
        "list_tasks",
        staticmethod(list_tasks),
    )
    monkeypatch.setattr(
        app_module.ProductionJobService,
        "get_runtime_health",
        staticmethod(get_health),
    )
    monkeypatch.setattr(
        app_module.ProductionJobService,
        "get_runtime_state",
        staticmethod(lambda *_args: None),
    )
    monkeypatch.setattr(
        app_module,
        "_production_task_markdown",
        lambda _task, **_kwargs: "markdown",
    )
    monkeypatch.setattr(app_module, "_format_queue_list", lambda _ss, _task: "queue")
    monkeypatch.setattr(
        app_module,
        "_format_production_engine_status",
        lambda _ss, _health: "engine",
    )

    result = app_module.refresh_production_task_tick(
        SimpleNamespace(project="book", synthesis=None),
    )

    assert calls == {"tasks": 1, "health": 1}
    assert result[:3] == ("markdown", "queue", "engine")
    assert result[-1].active is True


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
    monkeypatch.setattr(app_module, "_format_queue_list", lambda _ss, _task: "queue")
    monkeypatch.setattr(
        app_module,
        "_format_production_engine_status",
        lambda _ss, _health: "engine",
    )
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
