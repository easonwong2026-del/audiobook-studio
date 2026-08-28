"""Review/Repair workspace regressions for the single-segment UX contract."""
from __future__ import annotations

import ast
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app():
    import app as app_module

    return app_module


def _session(project="book"):
    return SimpleNamespace(project=project, invalidate_snapshot=lambda: None)


def _script():
    return {
        "chapters": [
            {
                "id": "12",
                "title": "第二章",
                "segments": [
                    {
                        "id": "12-006",
                        "role": "旁白",
                        "text": "一日，飞廉急急从山下回来。",
                        "emotion": "happy",
                        "emo_alpha": 0.8,
                        "speech_rate": 1.0,
                    },
                    {
                        "id": "12-007",
                        "role": "旁白",
                        "text": "第二段尚未生成。",
                        "emotion": "sad",
                        "emo_alpha": 0.6,
                        "speech_rate": 0.9,
                    },
                ],
            },
            {
                "id": "13",
                "title": "第三章",
                "segments": [
                    {
                        "id": "13-001",
                        "role": "旁白",
                        "text": "第三章第一段。",
                    },
                ],
            },
        ]
    }


def test_review_page_has_one_audio_and_no_batch_surface():
    source = (ROOT / "ui/pages/review_page.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    builder = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_review_page"
    )
    audio_calls = [
        node
        for node in ast.walk(builder)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "gr"
        and node.func.attr == "Audio"
    ]
    assert len(audio_calls) == 1
    assert "e_current_segment" in source
    assert "multiselect=True" not in source
    for old_name in (
        "e_" + "seg_preview_sel",
        "e_" + "seg_regen_sel",
        "e_" + "batch_repair",
        "e_" + "select_chapter_segments",
        "e_" + "select_filtered_segments",
        "e_" + "clear_segment_selection",
        "e_" + "audio_filter",
    ):
        assert old_name not in source
        assert old_name not in (ROOT / "app.py").read_text(encoding="utf-8")


def test_review_segment_choices_keep_missing_items_visible(app, monkeypatch):
    session = _session()
    snapshot = SimpleNamespace(script=_script())
    monkeypatch.setattr(app, "_snap", lambda _session: snapshot)
    monkeypatch.setattr(
        app,
        "_review_inventory",
        lambda *_args: {
            "segments": [
                {
                    "segment_id": "12-006",
                    "audio_valid": True,
                    "audio_status": "valid",
                },
                {
                    "segment_id": "12-007",
                    "audio_valid": False,
                    "audio_status": "missing",
                },
            ],
            "summary": {
                "segments": 2,
                "valid_audio": 1,
                "active_revisions": 1,
                "missing_revisions": 1,
                "invalid_audio": 0,
            },
        },
    )

    choices, _audio, _inventory = app._review_segment_choices(
        session, chapter_id="12"
    )

    assert [value for _label, value in choices] == ["12-006", "12-007"]
    assert "✅" in choices[0][0]
    assert "⚪" in choices[1][0]
    assert "未生成" in choices[1][0]


def test_submit_review_repair_maps_unchecked_overrides_to_none(app, monkeypatch):
    captured = {}

    def fake_regenerate(*args):
        captured["args"] = args
        return "submitted"

    monkeypatch.setattr(app, "regenerate_segment", fake_regenerate)

    result = app.submit_review_repair(
        "12-006",
        None,
        False,
        0.7,
        False,
        0.9,
        None,
        _session(),
    )

    assert result == "submitted"
    assert captured["args"][:5] == ("12-006", None, None, None, None)


def test_regenerate_segment_submits_exactly_one_current_target(app, monkeypatch):
    session = _session()
    monkeypatch.setattr(
        app,
        "_snap",
        lambda _session: SimpleNamespace(script=_script()),
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "current", "detail", "target"),
    )
    monkeypatch.setattr(
        app.RepairService,
        "find_active",
        classmethod(lambda cls, _project: None),
    )
    captured = {}

    def fake_start(project, segment_ids, **kwargs):
        captured["project"] = project
        captured["segment_ids"] = segment_ids
        captured["kwargs"] = kwargs
        return {
            "repair_id": "repair-1",
            "task_id": "task-1",
            "project": project,
            "segment_ids": segment_ids,
            "status": "running",
        }

    monkeypatch.setattr(app.RepairService, "start", staticmethod(fake_start))

    result = app.regenerate_segment(
        "12-006", None, None, None, None, session, chapter_id="12"
    )

    assert captured["segment_ids"] == ["12-006"]
    assert captured["kwargs"]["emotion"] is None
    assert captured["kwargs"]["emo_alpha"] is None
    assert captured["kwargs"]["speech_rate"] is None
    assert result[7:10] == ("repair-1", "task-1", "book")


def test_regenerate_segment_rejects_multiple_targets(app, monkeypatch):
    session = _session()
    monkeypatch.setattr(
        app,
        "_snap",
        lambda _session: SimpleNamespace(script=_script()),
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "current", "detail", "target"),
    )
    monkeypatch.setattr(
        app,
        "_review_repair_audio",
        lambda *_args, **_kwargs: ("old-audio", "old-status"),
    )
    result = app.regenerate_segment(
        ["12-006", "12-007"], None, None, None, None, session, chapter_id="12"
    )

    assert "只能重新合成当前段落" in result[6]


def test_regenerate_segment_rejects_stale_segment_from_other_chapter(app, monkeypatch):
    session = _session()
    monkeypatch.setattr(
        app,
        "_snap",
        lambda _session: SimpleNamespace(script=_script()),
    )
    monkeypatch.setattr(
        app,
        "_review_workspace_values",
        lambda *_args, **_kwargs: ("summary", "current", "detail", "target"),
    )
    monkeypatch.setattr(
        app,
        "_review_repair_audio",
        lambda *_args, **_kwargs: ("old-audio", "old-status"),
    )

    result = app.regenerate_segment(
        "13-001", None, None, None, None, session, chapter_id="12"
    )

    assert "与所选章节不一致" in result[6]


def test_navigation_stays_inside_chapter_and_moves_current_target(app, monkeypatch):
    session = _session()
    snapshot = SimpleNamespace(script=_script())
    monkeypatch.setattr(app, "_snap", lambda _session: snapshot)
    monkeypatch.setattr(
        app,
        "_review_segment_choices",
        lambda *_args, **_kwargs: (
            [("12-006", "12-006"), ("12-007", "12-007")],
            {
                "12-006": {"audio_valid": True, "audio_status": "valid"},
                "12-007": {"audio_valid": False, "audio_status": "missing"},
            },
            {"summary": {"segments": 2, "valid_audio": 1}},
        ),
    )
    monkeypatch.setattr(
        app,
        "_review_segment_audio",
        lambda *_args, **_kwargs: ("audio-007", "status-007"),
    )

    result = app.navigate_review_segment(
        "next", "12-006", None, "12", session
    )

    assert result[0]["value"] == "12-007"
    assert "12-007" in result[4]
    assert "12-006" not in result[4]


def test_chapter_switch_rebuilds_current_segment_and_repair_target(app, monkeypatch):
    session = _session()
    snapshot = SimpleNamespace(script=_script())
    monkeypatch.setattr(app, "_snap", lambda _session: snapshot)
    monkeypatch.setattr(
        app,
        "_review_segment_choices",
        lambda _session, _filter, chapter_id, **_kwargs: (
            [("13-001", "13-001")],
            {"13-001": {"audio_valid": True, "audio_status": "valid"}},
            {"summary": {"segments": 1, "valid_audio": 1}},
        ) if str(chapter_id) == "13" else ([], {}, {"summary": {}}),
    )
    monkeypatch.setattr(
        app,
        "_review_segment_audio",
        lambda *_args, **_kwargs: ("audio-13", "status-13"),
    )

    result = app.refresh_review_workspace_for_chapter("13", session)

    assert result[1]["value"] == "13-001"
    assert "13-001" in result[2]
    assert "13-001" in result[3]
    assert result[4] == "audio-13"


def test_segment_change_resets_all_temporary_overrides(app):
    updates = app.reset_review_overrides()

    assert len(updates) == 6
    assert updates[0]["value"] is None
    assert updates[1]["value"] is None
    assert updates[2]["value"] is False
    assert updates[3]["interactive"] is False
    assert updates[4]["value"] is False
    assert updates[5]["interactive"] is False


def test_failed_repair_can_still_play_active_revision(monkeypatch, tmp_path):
    from repositories.quality_repo import QualityRepository
    from services import review_audio

    project_dir = tmp_path / "book"
    project_dir.mkdir()
    active_path = project_dir / "archived-old.wav"
    with wave.open(str(active_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 16000)
    state_path = project_dir / "quality_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        QualityRepository,
        "state_path",
        staticmethod(lambda _project, create=False: str(state_path)),
    )
    monkeypatch.setattr(
        QualityRepository,
        "get_active_revision",
        classmethod(lambda cls, _project, _segment: {
            "relative_path": "archived-old.wav",
            "status": "ready",
        }),
    )

    resolved = review_audio._segment_audio(
        "book",
        str(project_dir),
        {"id": "12-006", "role": "旁白", "emotion": "happy"},
    )

    assert resolved == str(active_path)


def test_combined_override_values_reach_repair(app, monkeypatch):
    captured = {}

    def fake_regenerate(*args):
        captured["args"] = args
        return "submitted"

    monkeypatch.setattr(app, "regenerate_segment", fake_regenerate)
    app.submit_review_repair(
        "12-006",
        "sad",
        True,
        0.7,
        True,
        0.9,
        "Voice B",
        _session(),
    )

    assert captured["args"][:5] == ("12-006", "sad", 0.7, 0.9, "Voice B")
