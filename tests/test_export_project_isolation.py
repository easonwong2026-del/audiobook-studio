"""Regression coverage for Formal Export state across project switches."""
from __future__ import annotations

import threading
from types import SimpleNamespace

from ui import export_handlers as export_ui


def _session(project: str, task_id: str, output_dir: str):
    return SimpleNamespace(
        project=project,
        _export_ui_task_id=task_id,
        _export_ui_output_dir=output_dir,
        _export_ui_project=project,
    )


def _update_value(result, index: int):
    value = result[index]
    assert isinstance(value, dict) and value.get("__type__") == "update"
    return value


def _assert_cleared_export_outputs(result):
    assert len(result) == 7
    assert result[0] is None
    assert result[2] == ""
    assert result[3] == ""
    assert result[5].active is False
    assert _update_value(result, 4)["interactive"] is False
    assert _update_value(result, 6)["interactive"] is True
    assert "A" not in result[1]
    assert "A.wav" not in result[1]
    assert "A_export" not in result[1]


def test_open_project_reconciliation_clears_all_formal_export_outputs(monkeypatch, tmp_path):
    """Opening B clears every UI pointer left by a completed A export."""
    durable = {
        "task_id": "export-a",
        "project": "A",
        "status": "done",
        "manifest_id": "manifest-a",
    }
    calls = []

    class Backend:
        @staticmethod
        def get_export_task(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("project reconciliation must not read B history")

    monkeypatch.setattr(export_ui, "ExportService", Backend)
    session = _session("A", "export-a", str(tmp_path / "A_external"))
    hidden_components = {
        "task_id": "export-a",
        "output_dir": str(tmp_path / "A_external"),
        "artifact": str(tmp_path / "A" / "A.wav"),
        "status": "✅ 导出成功\n文件：A.wav\n位置：A_export",
        "open": True,
        "timer_active": False,
    }

    session.project = "B"
    result = export_ui.reconcile_export_state(
        hidden_components["task_id"],
        hidden_components["output_dir"],
        session,
    )

    _assert_cleared_export_outputs(result)
    assert calls == []
    assert session._export_ui_project == "B"
    assert session._export_ui_task_id == ""
    assert session._export_ui_output_dir == ""
    assert durable == {
        "task_id": "export-a",
        "project": "A",
        "status": "done",
        "manifest_id": "manifest-a",
    }

    planned_projects = []
    monkeypatch.setattr(
        export_ui.ProjectService,
        "get_project_dir",
        lambda project: planned_projects.append(project) or str(tmp_path / project),
    )
    monkeypatch.setattr(
        export_ui.ExportService,
        "plan_export",
        lambda project, *_args, **_kwargs: planned_projects.append(project) or {
            "summary": {
                "active_revisions": 0,
                "segments": 0,
                "failed_segments": 0,
                "chapters": 0,
                "ffmpeg_ready": True,
                "metadata": {"title": "B"},
            },
            "ready": True,
            "blockers": [],
        },
        raising=False,
    )
    monkeypatch.setattr(
        export_ui.ExportService,
        "list_exports",
        lambda project: [],
        raising=False,
    )
    monkeypatch.setattr(
        export_ui.WorkflowService,
        "get_state",
        lambda project: {"summary": {"delivered": False}},
    )
    default_dir = export_ui.refresh_export_default_dir(session)
    readiness = export_ui.refresh_export_readiness("wav", "require_passed", session)
    assert str(tmp_path / "B") in default_dir
    assert str(tmp_path / "A") not in default_dir
    assert planned_projects == ["B", "B"]
    assert "export-a" not in readiness
    assert "A_export" not in readiness


def test_nav_export_reconciliation_is_a_second_project_isolation_guard(tmp_path):
    """Entering Export after switching projects cannot revive A's hidden task."""
    session = _session("A", "export-a", str(tmp_path / "A_external"))
    session.project = "B"

    result = export_ui.reconcile_export_state(
        "export-a",
        str(tmp_path / "A_external"),
        session,
    )

    _assert_cleared_export_outputs(result)


def test_inflight_project_a_timer_is_noop_after_project_b_opens(monkeypatch):
    """A timer result that returns after the switch must not overwrite B."""
    started = threading.Event()
    release = threading.Event()
    calls = []
    durable = {"task_id": "export-a", "project": "A", "status": "running"}

    class Backend:
        @staticmethod
        def get_export_task(project, task_id):
            calls.append((project, task_id))
            if len(calls) == 1:
                started.set()
                assert release.wait(timeout=3)
            return dict(durable)

    monkeypatch.setattr(export_ui, "ExportService", Backend)
    session = _session("A", "export-a", "A_external")
    result_holder = []
    errors = []

    def run_timer():
        try:
            result_holder.append(export_ui.refresh_export_status("export-a", "A_external", session))
        except Exception as exc:  # pragma: no cover - makes thread failures visible
            errors.append(exc)

    thread = threading.Thread(target=run_timer)
    thread.start()
    assert started.wait(timeout=3)

    session.project = "B"
    reset = export_ui.reconcile_export_state("export-a", "A_external", session)
    _assert_cleared_export_outputs(reset)

    release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors == []
    assert len(result_holder) == 1
    stale_result = result_holder[0]
    assert len(stale_result) == 7
    assert all(
        isinstance(value, dict) and value.get("__type__") == "update"
        for value in stale_result
    )
    assert session._export_ui_project == "B"
    assert session._export_ui_task_id == ""
    assert durable == {"task_id": "export-a", "project": "A", "status": "running"}
    assert calls == [("A", "export-a")]


def test_validated_export_active_race_adopts_new_task_after_project_reconcile(monkeypatch):
    """A validated EXPORT_ACTIVE task becomes B's new UI tracking authority."""
    calls = []
    durable = {"task_id": "export-race", "project": "B", "status": "running"}

    class ActiveExportError(RuntimeError):
        def __init__(self):
            super().__init__("export already active")
            self.plan = {
                "blockers": [{
                    "code": "EXPORT_ACTIVE",
                    "task_id": "export-race",
                    "status": "running",
                }]
            }

    class Backend:
        @staticmethod
        def start_export(*_args, **_kwargs):
            raise ActiveExportError()

        @staticmethod
        def get_export_task(project, task_id):
            calls.append((project, task_id))
            return dict(durable)

    monkeypatch.setattr(export_ui, "ExportService", Backend)
    session = SimpleNamespace(
        project="B",
        _export_ui_project="B",
        _export_ui_task_id="",
        _export_ui_output_dir="",
    )

    result = export_ui.do_export("mp3", "192k", "", "require_passed", session)

    assert result[2] == "export-race"
    assert "正在导出" in result[1]
    assert result[5].active is True
    assert result[4]["interactive"] is False
    assert result[6]["interactive"] is False
    assert session._export_ui_project == "B"
    assert session._export_ui_task_id == "export-race"
    assert calls == [("B", "export-race"), ("B", "export-race")]
    assert not all(
        isinstance(value, dict) and value.get("__type__") == "update"
        for value in result
    )
