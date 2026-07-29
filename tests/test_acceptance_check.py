import json
from pathlib import Path

from scripts import acceptance_check


def make_project(root: Path, *, duplicate=False, audio=True):
    root.mkdir(parents=True)
    for folder in ("segments", "output"):
        (root / folder).mkdir()
    segments = [
        {"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "正文"},
        {"id": "1-001" if duplicate else "1-002", "role": "旁白", "emotion": "neutral", "text": "正文二"},
    ]
    script = {"meta": {"title": "测试"}, "voices": {"旁白": {}}, "chapters": [{"id": 1, "segments": segments}]}
    (root / "structured_script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (root / "project.json").write_text("{}", encoding="utf-8")
    (root / "voice_bindings.json").write_text(json.dumps({"bindings": {"旁白": "voice.wav"}}), encoding="utf-8")
    if audio:
        for sid in {s["id"] for s in segments}:
            (root / "segments" / f"{sid}.wav").write_bytes(b"RIFF")


def test_normal_project(monkeypatch, tmp_path):
    project = tmp_path / "normal"
    make_project(project)
    monkeypatch.setattr(acceptance_check, "_project_dir", lambda _: project)
    code, report = acceptance_check.check_project("normal")
    assert code == 0
    assert report["export_ready"] is True


def test_missing_required_file(monkeypatch, tmp_path):
    project = tmp_path / "missing"
    project.mkdir()
    monkeypatch.setattr(acceptance_check, "_project_dir", lambda _: project)
    code, report = acceptance_check.check_project("missing")
    assert code != 0
    assert "structured_script.json" in report["missing"]


def test_duplicate_and_missing_audio(monkeypatch, tmp_path):
    project = tmp_path / "bad"
    make_project(project, duplicate=True, audio=False)
    monkeypatch.setattr(acceptance_check, "_project_dir", lambda _: project)
    code, report = acceptance_check.check_project("bad")
    assert code != 0
    assert report["missing_audio_segments"]


def test_provider_not_configured(monkeypatch):
    monkeypatch.setattr(acceptance_check.AiSettingsService, "has_api_key", lambda _: False)
    code, report = acceptance_check.check_provider("openai", False, 1)
    assert code != 0
    assert report["key_configured"] is False


def test_no_real_request_without_flag(monkeypatch):
    monkeypatch.setattr(acceptance_check.AiSettingsService, "has_api_key", lambda _: True)
    monkeypatch.setattr(acceptance_check.AiSettingsService, "get_api_key_source", lambda _: "keyring")
    monkeypatch.setattr(acceptance_check.AiSettingsService, "get_effective_provider_config", lambda _: {"model": "test"})
    monkeypatch.setattr(
        acceptance_check.AiSettingsService,
        "check_connection",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network request sent")),
    )
    code, report = acceptance_check.check_provider("openai", False, 1)
    assert code == 0
    assert report["real_request_sent"] is False
