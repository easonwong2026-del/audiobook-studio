"""Focused coverage for Batch QA and review repair observer behavior."""
from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import wavfile

from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from services.production_jobs import ProductionJobService
from services.quality import QualityService
from services.repair import (
    ACTIVE_TASK_STATES,
    TERMINAL_TASK_STATES,
    RepairService,
)


SCRIPT = {
    "meta": {"title": "Batch QA", "author": "测试作者"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [
            {"id": "001-001", "role": "旁白", "text": "一段正常的有效音频。"},
            {
                "id": "001-002",
                "role": "旁白",
                "text": "这是一段明显偏短但可读取的音频，用来触发技术警告。",
            },
            {"id": "001-003", "role": "旁白", "text": "第三段有效音频。"},
            {"id": "001-004", "role": "旁白", "text": "这段尚未生产。"},
        ],
    }],
}


@pytest.fixture
def batch_project(tmp_path):
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("batch", SCRIPT)
    project_dir = ProjectRepository.get_project_dir("batch")

    voice = os.path.join(
        project_paths.project_dir(project_dir, "project_voices", create=True),
        "narrator.wav",
    )
    wavfile.write(voice, 22050, np.ones(2205, dtype=np.int16))
    bindings = ProjectRepository.load_bindings(project_dir)
    bindings["bindings"]["旁白"] = voice
    ProjectRepository.save_bindings(project_dir, bindings)

    segments = project_paths.project_dir(project_dir, "segments", create=True)
    axis = np.linspace(0, 1, 22050, endpoint=False)
    valid = (np.sin(2 * np.pi * 220 * axis) * 6000).astype(np.int16)
    short_axis = np.linspace(0, 0.1, 2205, endpoint=False)
    short = (np.sin(2 * np.pi * 220 * short_axis) * 6000).astype(np.int16)
    wavfile.write(os.path.join(segments, "001-001.wav"), 22050, valid)
    wavfile.write(os.path.join(segments, "001-002.wav"), 22050, short)
    wavfile.write(os.path.join(segments, "001-003.wav"), 22050, valid)

    yield "batch"
    (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    ) = original


def _ensure(project, segment_ids):
    for segment_id in segment_ids:
        assert QualityService.ensure_active_revision(project, segment_id)


def test_batch_technical_qa_isolates_errors_and_persists_each_result(
    batch_project, monkeypatch
):
    _ensure(batch_project, ["001-001", "001-002", "001-003"])
    original = QualityService._analyze_technical_qa

    def flaky(project_name, segment_id, revision_id=None, **kwargs):
        if segment_id == "001-003":
            raise RuntimeError("simulated analyzer failure")
        return original(project_name, segment_id, revision_id, **kwargs)

    monkeypatch.setattr(QualityService, "_analyze_technical_qa", flaky)
    results = QualityService.run_technical_qa_batch(
        batch_project,
        ["001-001", "001-002", "001-003"],
    )

    assert [item["segment_id"] for item in results] == [
        "001-001", "001-002", "001-003"
    ]
    assert results[0]["outcome"] == "pass"
    assert results[1]["outcome"] == "warning"
    assert results[2]["outcome"] == "fail"
    assert results[2]["checks"][0]["code"] == "QA_ITEM_ERROR"

    state = QualityRepository.load(batch_project)
    revision_ids = {
        QualityRepository.get_active_revision(batch_project, segment_id)["revision_id"]
        for segment_id in ("001-001", "001-002", "001-003")
    }
    assert revision_ids <= set(state["technical_qa"])
    assert QualityService.get_segment_quality(batch_project, "001-001")["review_status"] == "unreviewed"


def test_batch_human_pass_only_passes_technical_clean_segments(batch_project):
    _ensure(batch_project, ["001-001", "001-002", "001-003"])
    QualityService.run_technical_qa_batch(
        batch_project,
        ["001-001", "001-002", "001-003"],
    )
    result = QualityService.pass_technically_clean(
        batch_project,
        ["001-001", "001-002", "001-004"],
        reviewed_by="batch-test",
    )

    assert result["passed"] == 1
    assert result["segment_ids"] == ["001-001"]
    assert set(result["skipped_segment_ids"]) == {"001-002", "001-004"}
    assert QualityService.get_segment_quality(batch_project, "001-001")["review_status"] == "passed"
    assert QualityService.get_segment_quality(batch_project, "001-002")["review_status"] != "passed"
    assert QualityService.get_segment_quality(batch_project, "001-004")["quality_status"] == "not_started"


def test_batch_review_ui_renders_one_line_per_technical_result(monkeypatch):
    import app

    script = {
        "chapters": [{
            "id": "001",
            "segments": [
                {"id": "001-001"},
                {"id": "001-002"},
                {"id": "001-003"},
            ],
        }],
    }
    session = SimpleNamespace(project="book")
    monkeypatch.setattr(app, "_snap", lambda _ss: SimpleNamespace(script=script))
    monkeypatch.setattr(
        app.QualityService,
        "run_technical_qa_batch",
        staticmethod(lambda *_args, **_kwargs: [
            {"segment_id": "001-001", "outcome": "pass", "checks": []},
            {
                "segment_id": "001-002", "outcome": "warning",
                "checks": [{"code": "LONG_SILENCE", "severity": "warning"}],
            },
            {
                "segment_id": "001-003", "outcome": "fail",
                "checks": [{"code": "AUDIO_MISSING", "severity": "error"}],
            },
        ]),
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )

    result = app.batch_technical_qa(
        ["001-001", "001-002", "001-003"], "all", None, session
    )
    assert "001-001  technical pass" in result[0]
    assert "001-002  technical warning" in result[0]
    assert "001-003  error: audio missing" in result[0]


def test_batch_repair_keeps_one_exact_production_scope_and_prior_revisions(
    batch_project, monkeypatch
):
    _ensure(batch_project, ["001-001", "001-002", "001-003"])
    calls = []

    def fake_start(project, scope, options, **kwargs):
        calls.append((project, scope, options, kwargs))
        return {"task_id": "task_batch_repair", "status": "pending"}

    monkeypatch.setattr(ProductionJobService, "start", staticmethod(fake_start))
    result = RepairService.start(
        batch_project,
        ["001-001", "001-003"],
        source="web",
        requested_by="web",
    )

    assert len(calls) == 1
    assert calls[0][1] == {"segment_ids": ["001-001", "001-003"]}
    assert result["task_id"] == "task_batch_repair"
    assert result["segment_ids"] == ["001-001", "001-003"]
    assert len(QualityRepository.list_revisions(batch_project, "001-001")) == 2
    assert len(QualityRepository.list_revisions(batch_project, "001-003")) == 2
    assert len(QualityRepository.list_revisions(batch_project, "001-002")) == 1


@pytest.mark.parametrize("status", sorted(ACTIVE_TASK_STATES))
def test_review_repair_timer_keeps_all_active_states(monkeypatch, status):
    import app

    session = SimpleNamespace(project="book", invalidate_snapshot=lambda: None)
    monkeypatch.setattr(
        app.ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda _task_id: {
            "task_id": "task-1", "project": "book", "status": status,
            "progress": {"completed": 1, "total": 2},
        }),
    )
    monkeypatch.setattr(
        app.RepairService,
        "refresh",
        classmethod(lambda cls, _project, _repair_id: {
            "repair_id": "repair-1", "task_id": "task-1", "status": status,
            "segment_ids": ["001-001"],
            "result": {"progress": {"completed": 1, "total": 2}},
        }),
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )

    result = app.refresh_review_repair_tick(
        "repair-1", "task-1", "book", None, "all", None, session
    )
    assert len(result) == 11
    assert result[-1].active is True
    assert result[7:10] == ("repair-1", "task-1", "book")


@pytest.mark.parametrize("status", sorted(TERMINAL_TASK_STATES))
def test_review_repair_timer_stops_for_all_terminal_states(monkeypatch, status):
    import app

    session = SimpleNamespace(project="book", invalidate_snapshot=lambda: None)
    monkeypatch.setattr(
        app.ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda _task_id: {
            "task_id": "task-1", "project": "book", "status": status,
            "progress": {"completed": 2, "total": 2},
        }),
    )
    monkeypatch.setattr(
        app.RepairService,
        "refresh",
        classmethod(lambda cls, _project, _repair_id: {
            "repair_id": "repair-1", "task_id": "task-1", "status": status,
            "segment_ids": ["001-001"], "error": "terminal",
        }),
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )
    monkeypatch.setattr(
        app,
        "_review_repair_audio",
        lambda *_args, **_kwargs: ("audio", "audio status"),
    )

    result = app.refresh_review_repair_tick(
        "repair-1", "task-1", "book", None, "all", None, session
    )
    assert len(result) == 11
    assert result[-1].active is False
    assert result[7:10] == ("", "", "")


def test_review_repair_project_switch_clears_stale_tracking(monkeypatch):
    import app

    session = SimpleNamespace(project="project-b", invalidate_snapshot=lambda: None)
    result = app.refresh_review_repair_tick(
        "repair-a", "task-a", "project-a", None, "all", None, session
    )
    assert result == app._review_repair_stale_outputs()


def test_review_repair_recovers_from_durable_history(monkeypatch):
    import app

    monkeypatch.setattr(
        app.RepairService,
        "find_active",
        classmethod(lambda cls, _project: {
            "repair_id": "repair-1", "task_id": "task-1", "status": "running",
        }),
    )
    result = app.recover_review_repair(SimpleNamespace(project="book"))
    assert result[:3] == ("repair-1", "task-1", "book")
    assert result[3].active is True


@pytest.mark.parametrize("history_status", ["preparing", "submitting"])
def test_find_active_ignores_unsubmitted_repair_history(
    batch_project, monkeypatch, history_status
):
    record = QualityRepository.create_history_record(
        batch_project,
        "repair_history",
        "repair",
        {
            "project": batch_project,
            "segment_ids": ["001-001"],
            "task_id": "",
            "status": history_status,
        },
    )
    monkeypatch.setattr(
        ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda *_args: pytest.fail("无 task history 不应查询 observer")),
    )

    assert RepairService.find_active(batch_project) is None
    history = QualityRepository.list_history(batch_project, "repair_history")
    assert history[0]["repair_id"] == record["repair_id"]
    assert history[0]["task_id"] == ""
    assert history[0]["status"] == history_status


def test_find_active_uses_task_snapshot_for_task_backed_repair(
    batch_project, monkeypatch
):
    record = QualityRepository.create_history_record(
        batch_project,
        "repair_history",
        "repair",
        {
            "project": batch_project,
            "segment_ids": ["001-001"],
            "task_id": "task-running",
            "status": "preparing",
        },
    )
    monkeypatch.setattr(
        ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda task_id: {
            "task_id": task_id,
            "project": batch_project,
            "status": "running",
        }),
    )

    active = RepairService.find_active(batch_project)

    assert active["repair_id"] == record["repair_id"]
    assert active["task_id"] == "task-running"


@pytest.mark.parametrize("history_status", ["preparing", "submitting"])
def test_unsubmitted_repair_history_does_not_block_new_submit(
    batch_project, monkeypatch, history_status
):
    import app

    session = _review_test_session(batch_project)
    QualityRepository.create_history_record(
        batch_project,
        "repair_history",
        "repair",
        {
            "project": batch_project,
            "segment_ids": ["001-001"],
            "task_id": "",
            "status": history_status,
        },
    )
    script = _review_test_script()
    monkeypatch.setattr(app, "_snap", lambda _ss: SimpleNamespace(script=script))
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )
    starts = []

    def fake_start(*_args, **_kwargs):
        starts.append(True)
        return {
            "repair_id": "repair-new",
            "task_id": "task-new",
            "project": batch_project,
            "segment_ids": ["001-001"],
            "status": "running",
        }

    monkeypatch.setattr(app.RepairService, "start", staticmethod(fake_start))

    result = app.regenerate_segment(
        ["001-001"], "neutral", 1.0, 1.0, None, session
    )

    assert starts == [True]
    assert result[7:10] == ("repair-new", "task-new", batch_project)
    assert result[-1].active is True


def test_recovery_cas_does_not_overwrite_newer_submit(monkeypatch):
    import app

    session = _review_test_session()
    monkeypatch.setattr(
        app, "_snap", lambda _ss: SimpleNamespace(script=_review_test_script())
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )
    recovery_entered = threading.Event()
    release_recovery = threading.Event()
    recovery_worker = None

    def delayed_find_active(cls, _project):
        if threading.current_thread() is recovery_worker:
            recovery_entered.set()
            if not release_recovery.wait(5):
                raise AssertionError("recovery lookup did not get released")
            return {
                "repair_id": "repair-x",
                "task_id": "task-x",
                "status": "running",
            }
        return None

    monkeypatch.setattr(
        app.RepairService, "find_active", classmethod(delayed_find_active)
    )
    monkeypatch.setattr(
        app.RepairService,
        "start",
        staticmethod(lambda *_args, **_kwargs: {
            "repair_id": "repair-y",
            "task_id": "task-y",
            "project": "book",
            "segment_ids": ["001-001"],
            "status": "running",
        }),
    )
    recovery_result = []

    def run_recovery():
        recovery_result.append(app.recover_review_repair(session))

    recovery_worker = threading.Thread(target=run_recovery)
    recovery_worker.start()
    assert recovery_entered.wait(5)

    y_result = app.regenerate_segment(
        ["001-001"], "happy", 1.0, 1.0, None, session
    )
    y_fence = app._review_repair_fence_snapshot(session)
    assert y_result[7:10] == ("repair-y", "task-y", "book")
    assert y_result[-1].active is True
    assert y_fence[0] == 2

    release_recovery.set()
    recovery_worker.join(5)
    assert not recovery_worker.is_alive()
    assert recovery_result == [app._review_repair_stale_recovery_outputs()]
    assert app._review_repair_fence_snapshot(session) == y_fence


def test_recovery_cas_does_not_restore_old_project_after_switch(monkeypatch):
    import app

    session = _review_test_session("project-a")
    old_fence = app._review_repair_fence_set(
        session, "project-a", "repair-a", "task-a"
    )
    recovery_entered = threading.Event()
    release_recovery = threading.Event()

    def delayed_find_active(cls, _project):
        recovery_entered.set()
        if not release_recovery.wait(5):
            raise AssertionError("recovery lookup did not get released")
        return {
            "repair_id": "repair-a-new",
            "task_id": "task-a-new",
            "status": "running",
        }

    monkeypatch.setattr(
        app.RepairService, "find_active", classmethod(delayed_find_active)
    )
    recovery_result = []

    def run_recovery():
        recovery_result.append(app.recover_review_repair(session))

    worker = threading.Thread(target=run_recovery)
    worker.start()
    assert recovery_entered.wait(5)

    session.project = "project-b"
    switched_fence = app._review_repair_fence_set(
        session, "project-b", "", "", force=True
    )
    assert switched_fence[0] == old_fence[0] + 1

    release_recovery.set()
    worker.join(5)
    assert not worker.is_alive()
    assert recovery_result == [app._review_repair_stale_recovery_outputs()]
    assert app._review_repair_fence_snapshot(session) == switched_fence


def test_repeated_repair_click_reuses_active_task_without_new_start(monkeypatch):
    import app

    script = {
        "chapters": [{"id": "001", "segments": [{"id": "001-001"}]}]
    }
    session = SimpleNamespace(project="book")
    monkeypatch.setattr(app, "_snap", lambda _ss: SimpleNamespace(script=script))
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )
    monkeypatch.setattr(
        app.RepairService,
        "find_active",
        classmethod(lambda cls, _project: {
            "repair_id": "repair-1", "task_id": "task-1", "status": "running",
        }),
    )

    def unexpected_start(*_args, **_kwargs):
        raise AssertionError("重复点击不应创建第二个 repair task")

    monkeypatch.setattr(app.RepairService, "start", unexpected_start)
    result = app.regenerate_segment(
        ["001-001"], "neutral", 1.0, 1.0, None, session
    )
    assert result[7:10] == ("repair-1", "task-1", "book")
    assert result[-1].active is True


def _review_test_script():
    return {
        "chapters": [{"id": "001", "segments": [{"id": "001-001"}]}]
    }


def _review_test_session(project="book"):
    return SimpleNamespace(
        project=project,
        invalidate_snapshot=lambda: None,
    )


def test_late_x_callback_is_noop_after_y_submit(monkeypatch):
    import app

    session = _review_test_session()
    monkeypatch.setattr(
        app, "_snap", lambda _ss: SimpleNamespace(script=_review_test_script())
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )
    monkeypatch.setattr(
        app.RepairService,
        "find_active",
        classmethod(lambda cls, _project: None),
    )
    started = iter([
        {
            "repair_id": "repair-x", "task_id": "task-x", "status": "running",
            "project": "book", "segment_ids": ["001-001"],
        },
        {
            "repair_id": "repair-y", "task_id": "task-y", "status": "running",
            "project": "book", "segment_ids": ["001-001"],
        },
    ])
    monkeypatch.setattr(
        app.RepairService,
        "start",
        staticmethod(lambda *_args, **_kwargs: next(started)),
    )

    x_started = app.regenerate_segment(
        ["001-001"], "neutral", 1.0, 1.0, None, session
    )
    assert x_started[7:10] == ("repair-x", "task-x", "book")

    refresh_entered = threading.Event()
    release_x = threading.Event()

    def delayed_refresh(cls, _project, _repair_id):
        refresh_entered.set()
        assert release_x.wait(5)
        return {
            "repair_id": "repair-x", "task_id": "task-x", "status": "done",
            "project": "book", "segment_ids": ["001-001"],
        }

    monkeypatch.setattr(app.RepairService, "refresh", classmethod(delayed_refresh))
    monkeypatch.setattr(
        app.ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda _task_id: {
            "task_id": "task-x", "project": "book", "status": "done",
        }),
    )
    x_result = []

    def run_x_tick():
        x_result.append(
            app.refresh_review_repair_tick(
                "repair-x", "task-x", "book", None, "all", None, session
            )
        )

    worker = threading.Thread(target=run_x_tick)
    worker.start()
    assert refresh_entered.wait(5)

    y_started = app.regenerate_segment(
        ["001-001"], "happy", 1.0, 1.0, None, session
    )
    assert y_started[7:10] == ("repair-y", "task-y", "book")
    assert y_started[-1].active is True

    release_x.set()
    worker.join(5)
    assert not worker.is_alive()
    assert x_result == [app._review_repair_stale_outputs()]


def test_late_terminal_x_does_not_stop_active_y(monkeypatch):
    import app

    session = _review_test_session()
    app._review_repair_fence_set(session, "book", "repair-y", "task-y")
    monkeypatch.setattr(
        app.ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda *_args: pytest.fail("stale X must not read its task")),
    )
    result = app.refresh_review_repair_tick(
        "repair-x", "task-x", "book", None, "all", None, session
    )
    assert result == app._review_repair_stale_outputs()


def test_late_active_x_does_not_switch_back_from_newer_y(monkeypatch):
    import app

    session = _review_test_session()
    app._review_repair_fence_set(session, "book", "repair-y", "task-y")
    monkeypatch.setattr(
        app.ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda *_args: pytest.fail("stale X must not read its task")),
    )
    result = app.refresh_review_repair_tick(
        "repair-x", "task-x", "book", None, "all", None, session
    )
    assert result == app._review_repair_stale_outputs()


def test_same_task_terminal_reconciles_workspace_and_stops_timer(monkeypatch):
    import app

    invalidated = []
    session = SimpleNamespace(
        project="book",
        invalidate_snapshot=lambda: invalidated.append(True),
    )
    monkeypatch.setattr(
        app.ProductionJobService,
        "get_task_snapshot",
        staticmethod(lambda _task_id: {
            "task_id": "task-x", "project": "book", "status": "done",
        }),
    )
    monkeypatch.setattr(
        app.RepairService,
        "refresh",
        classmethod(lambda cls, _project, _repair_id: {
            "repair_id": "repair-x", "task_id": "task-x", "status": "done",
            "project": "book", "segment_ids": ["001-001"],
        }),
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )
    monkeypatch.setattr(
        app,
        "_review_repair_audio",
        lambda *_args, **_kwargs: ("audio", "audio status"),
    )
    result = app.refresh_review_repair_tick(
        "repair-x", "task-x", "book", "001-001", "all", None, session
    )
    assert result[:4] == ("summary", "preview", "selection", "quality")
    assert result[4:6] == ("audio", "audio status")
    assert "完成" in result[6]
    assert result[7:10] == ("", "", "")
    assert result[-1].active is False
    assert invalidated == [True]


def test_regenerate_immediate_terminal_uses_terminal_reconciliation(monkeypatch):
    import app

    invalidated = []
    session = SimpleNamespace(
        project="book",
        invalidate_snapshot=lambda: invalidated.append(True),
    )
    monkeypatch.setattr(
        app,
        "_snap",
        lambda _ss: SimpleNamespace(script=_review_test_script()),
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "preview", "selection", "quality"),
    )
    monkeypatch.setattr(
        app,
        "_review_repair_audio",
        lambda *_args, **_kwargs: ("audio", "audio status"),
    )
    monkeypatch.setattr(
        app.RepairService,
        "find_active",
        classmethod(lambda cls, _project: None),
    )
    monkeypatch.setattr(
        app.RepairService,
        "start",
        staticmethod(lambda *_args, **_kwargs: {
            "repair_id": "repair-immediate",
            "task_id": "task-immediate",
            "project": "book",
            "segment_ids": ["001-001"],
            "status": "done",
        }),
    )
    result = app.regenerate_segment(
        ["001-001"], "neutral", 1.0, 1.0, None, session
    )
    assert result[:4] == ("summary", "preview", "selection", "quality")
    assert result[4:6] == ("audio", "audio status")
    assert "完成" in result[6]
    assert result[7:10] == ("", "", "")
    assert result[-1].active is False
    assert invalidated == [True]
