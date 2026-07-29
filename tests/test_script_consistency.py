from services.script_consistency import check_script_consistency


def script(voices=None, segments=None):
    return {
        "voices": voices or {"旁白": {}, "张国强": {}, "老张": {}},
        "chapters": [{"id": 1, "segments": segments or [
            {"id": "1-001", "role": "旁白", "text": "正常文本", "speech_rate": 1.0},
            {"id": "1-002", "role": "张国强", "text": "他说了一句话", "speech_rate": 1.4},
        ]}],
    }


def types(report):
    return {item["type"] for item in report["issues"]}


def test_missing_and_unused_roles():
    report = check_script_consistency(script(
        voices={"旁白": {}, "未使用": {}},
        segments=[{"id": "1-001", "role": "缺失", "text": "文本"}],
    ))
    assert {"missing_voice", "unused_voice"} <= types(report)
    assert report["status"] == "error"


def test_alias_rate_jump_long_segment_and_invalid_pause():
    raw = script(
        voices={"张国强": {}, "张国强老师": {}},
        segments=[
            {"id": "1", "role": "张国强", "text": "正常", "speech_rate": .8},
            {"id": "2", "role": "张国强老师", "text": "长" * 501, "speech_rate": 1.2, "pause_after": 20000},
        ],
    )
    result = check_script_consistency(raw)
    assert {"possible_character_alias", "speech_rate_jump", "segment_too_long", "invalid_pause"} <= types(result)
    assert result["status"] == "warning"


def test_duplicate_segment_is_error():
    raw = script(voices={"旁白": {}}, segments=[
        {"id": "same", "role": "旁白", "text": "一"},
        {"id": "same", "role": "旁白", "text": "二"},
    ])
    assert check_script_consistency(raw)["status"] == "error"


def test_warning_does_not_block_project_creation(monkeypatch, tmp_path):
    from services.project_creation import ProjectCreationService
    import json
    source = tmp_path / "script.json"
    source.write_text(json.dumps(script(voices={"旁白": {}, "未使用": {}}, segments=[
        {"id": "1", "role": "旁白", "text": "正常文本"},
    ]), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("services.project_creation.config.get_projects_root", lambda: str(tmp_path / "projects"))
    monkeypatch.setattr("services.project_creation.ProjectRepository.create_project", lambda *a: None)
    result = ProjectCreationService.create_from_structured_script("ok", str(source))
    assert result.warnings


def test_error_blocks_project_creation(monkeypatch, tmp_path):
    from services.project_creation import ProjectCreationService
    import json
    import pytest
    source = tmp_path / "script.json"
    source.write_text(json.dumps(script(voices={"旁白": {}}, segments=[
        {"id": "same", "role": "旁白", "text": "一"},
        {"id": "same", "role": "旁白", "text": "二"},
    ]), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("services.project_creation.config.get_projects_root", lambda: str(tmp_path / "projects"))
    with pytest.raises(ValueError, match="一致性"):
        ProjectCreationService.create_from_structured_script("bad", str(source))
