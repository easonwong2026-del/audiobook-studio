"""人工导演校正、历史保存与撤销测试。"""
from __future__ import annotations

import json

import pytest

from lib import segment_cache
from services.script_director import ScriptDirectorService


def _script():
    return ScriptDirectorService().analyze_text(
        "第一章\n\n张三说道：“开始吧。”\n\n“继续。”",
        title="测试",
    )


def test_editor_rows_apply_role_delivery_and_pause_changes():
    script = _script()
    rows = ScriptDirectorService.editor_rows(script)
    rows[0][1] = "李四"
    rows[0][3] = "angry"
    rows[0][4] = 1.8
    rows[0][5] = 0.85
    rows[0][6] = "heavy"
    rows[0][7] = 500
    rows[0][8] = 1800

    updated, changed = ScriptDirectorService.apply_segment_edits(script, rows)
    segment = updated["chapters"][0]["segments"][0]
    assert changed == 1
    assert segment["speaker"] == segment["role"] == "李四"
    assert segment["emotion"] == "angry"
    assert segment["delivery"]["speed"] == 1.1
    assert segment["delivery"]["intensity"] == 0.85
    assert segment["delivery"]["breath"] == "heavy"
    assert segment["pause_before"] == 500
    assert segment["pause_after"] == 1800
    assert "李四" in updated["voices"]
    assert "张三" not in updated["voices"]


def test_save_and_undo_restores_previous_script(tmp_path):
    target = tmp_path / "structured_script.json"
    original = _script()
    ScriptDirectorService.save_script(original, str(target))
    rows = ScriptDirectorService.editor_rows(original)
    rows[0][1] = "李四"

    updated, backup, changed = ScriptDirectorService.save_segment_edits(
        str(target),
        rows,
    )
    assert changed == 1
    assert updated["chapters"][0]["segments"][0]["speaker"] == "李四"
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["chapters"][0]["segments"][0]["speaker"] == "李四"

    restored = ScriptDirectorService.undo_segment_edits(str(target), backup)
    assert restored == original
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_editor_rejects_duplicate_ids_and_invalid_breath():
    script = _script()
    rows = ScriptDirectorService.editor_rows(script)
    duplicate = [rows[0], list(rows[0])]
    with pytest.raises(ValueError, match="重复 segment id"):
        ScriptDirectorService.apply_segment_edits(script, duplicate)

    rows[0][6] = "robot"
    with pytest.raises(ValueError, match="breath 无效"):
        ScriptDirectorService.apply_segment_edits(script, rows)


def test_undo_rejects_unrelated_backup(tmp_path):
    target = tmp_path / "structured_script.json"
    backup = tmp_path / "foreign.json"
    ScriptDirectorService.save_script(_script(), str(target))
    ScriptDirectorService.save_script(_script(), str(backup))
    with pytest.raises(ValueError, match="不属于当前剧本"):
        ScriptDirectorService.undo_segment_edits(str(target), str(backup))


@pytest.mark.parametrize(
    ("feedback", "field", "expected"),
    [
        ("slower", "speed", 0.95),
        ("faster", "speed", 1.05),
        ("stronger", "intensity", 0.5),
        ("softer", "intensity", 0.3),
        ("more_breath", "breath", "heavy"),
        ("less_breath", "breath", "light"),
    ],
)
def test_audition_feedback_applies_small_bounded_step(
    tmp_path,
    feedback,
    field,
    expected,
):
    target = tmp_path / f"{feedback}.json"
    script = _script()
    segment = script["chapters"][0]["segments"][0]
    # 为呼吸反馈准备中间等级，确保两个方向都能移动一步。
    segment["delivery"]["breath"] = "normal"
    segment_id = segment["id"]
    ScriptDirectorService.save_script(script, str(target))

    updated, backup, summary = ScriptDirectorService.apply_audition_feedback(
        str(target),
        segment_id,
        feedback,
    )

    changed = updated["chapters"][0]["segments"][0]
    assert changed["delivery"][field] == expected
    assert backup
    assert summary


def test_pause_feedback_changes_internal_and_boundary_pauses_and_is_undoable(tmp_path):
    target = tmp_path / "pauses.json"
    script = _script()
    segment = script["chapters"][0]["segments"][0]
    segment_id = segment["id"]
    old_after = segment["pause_after"]
    old_internal = segment["pauses"][0]["duration"]
    ScriptDirectorService.save_script(script, str(target))

    updated, backup, _ = ScriptDirectorService.apply_audition_feedback(
        str(target),
        segment_id,
        "longer_pauses",
    )

    changed = updated["chapters"][0]["segments"][0]
    assert changed["pause_after"] > old_after
    assert changed["pauses"][0]["duration"] > old_internal
    restored = ScriptDirectorService.undo_segment_edits(str(target), backup)
    assert restored == script


def test_feedback_changes_production_cache_key(tmp_path):
    target = tmp_path / "cache.json"
    script = _script()
    segment = script["chapters"][0]["segments"][0]
    ScriptDirectorService.save_script(script, str(target))
    old_key = segment_cache.segment_cache_key(
        segment["id"],
        segment["emotion"],
        segment["emo_alpha"],
        segment["speech_rate"],
        segment.get("pinyin_hints"),
        segment_cache.director_metadata_for(segment),
    )

    updated, _, _ = ScriptDirectorService.apply_audition_feedback(
        str(target),
        segment["id"],
        "longer_pauses",
    )
    changed = updated["chapters"][0]["segments"][0]
    new_key = segment_cache.segment_cache_key(
        changed["id"],
        changed["emotion"],
        changed["emo_alpha"],
        changed["speech_rate"],
        changed.get("pinyin_hints"),
        segment_cache.director_metadata_for(changed),
    )
    assert new_key != old_key


def test_feedback_respects_parameter_boundaries(tmp_path):
    target = tmp_path / "boundary.json"
    script = _script()
    segment = script["chapters"][0]["segments"][0]
    segment["delivery"]["speed"] = 0.85
    segment["speech_rate"] = 0.85
    ScriptDirectorService.save_script(script, str(target))

    with pytest.raises(ValueError, match="最慢边界"):
        ScriptDirectorService.apply_audition_feedback(
            str(target),
            segment["id"],
            "slower",
        )


def test_unknown_feedback_is_rejected(tmp_path):
    target = tmp_path / "unknown.json"
    script = _script()
    ScriptDirectorService.save_script(script, str(target))
    with pytest.raises(ValueError, match="不支持的试听反馈"):
        ScriptDirectorService.apply_audition_feedback(
            str(target),
            script["chapters"][0]["segments"][0]["id"],
            "rewrite_everything",
        )
