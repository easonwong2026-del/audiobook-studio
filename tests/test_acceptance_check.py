import json
from pathlib import Path

from scripts import acceptance_check
from lib import segment_cache


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
        for segment in segments:
            Path(segment_cache.segment_wav_path(
                str(root / "segments"),
                segment["id"],
                segment["emotion"],
                segment.get("emo_alpha", 1.0),
                segment.get("speech_rate", 1.0),
                segment.get("pinyin_hints"),
                segment_cache.director_metadata_for(segment),
            )).write_bytes(b"RIFF")


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


def _segment(segment_id="1-001", **updates):
    value = {
        "id": segment_id,
        "role": "旁白",
        "text": "测试正文",
        "emotion": "neutral",
        "emo_alpha": 0.6,
        "speech_rate": 1.0,
    }
    value.update(updates)
    return value


def test_find_audio_supports_legacy_bare_file(tmp_path):
    segment = _segment()
    legacy = tmp_path / "1-001.wav"
    legacy.write_bytes(b"RIFF")
    assert acceptance_check.find_existing_segment_audio(tmp_path, segment) == legacy


def test_find_audio_uses_real_parameter_hash(tmp_path):
    segment = _segment()
    hashed = Path(segment_cache.segment_wav_path(
        str(tmp_path), segment["id"], segment["emotion"],
        segment["emo_alpha"], segment["speech_rate"],
        segment.get("pinyin_hints"), segment_cache.director_metadata_for(segment),
    ))
    hashed.write_bytes(b"RIFF")
    assert acceptance_check.find_existing_segment_audio(tmp_path, segment) == hashed


def test_changed_parameters_do_not_match_old_hash(tmp_path):
    previous = _segment(speech_rate=1.0, emotion="neutral")
    current = _segment(speech_rate=1.1, emotion="happy")
    Path(segment_cache.segment_wav_path(
        str(tmp_path), previous["id"], previous["emotion"],
        previous["emo_alpha"], previous["speech_rate"],
        None, segment_cache.director_metadata_for(previous),
    )).write_bytes(b"RIFF")
    assert acceptance_check.find_existing_segment_audio(tmp_path, current) is None


def test_similar_segment_ids_never_cross_match(tmp_path):
    shorter = _segment("1-01")
    longer = _segment("1-010")
    Path(segment_cache.segment_wav_path(
        str(tmp_path), longer["id"], longer["emotion"],
        longer["emo_alpha"], longer["speech_rate"],
        None, segment_cache.director_metadata_for(longer),
    )).write_bytes(b"RIFF")
    assert acceptance_check.find_existing_segment_audio(tmp_path, shorter) is None
    assert acceptance_check.find_existing_segment_audio(tmp_path, longer) is not None


def test_v3_director_metadata_participates_in_hash(tmp_path):
    segment = _segment(
        delivery={"speed": 1.0, "pitch": 2, "intensity": 0.6, "breath": "light"},
        pause_before=250,
        pause_after=800,
        pauses=[{"position": 2, "duration": 400, "type": "pause_think"}],
    )
    hashed = Path(segment_cache.segment_wav_path(
        str(tmp_path), segment["id"], segment["emotion"],
        segment["emo_alpha"], segment["speech_rate"],
        None, segment_cache.director_metadata_for(segment),
    ))
    hashed.write_bytes(b"RIFF")
    assert acceptance_check.find_existing_segment_audio(tmp_path, segment) == hashed


def test_real_provider_check_passes_effective_config_and_sanitizes(monkeypatch, capsys):
    secret = "sk-super-secret-test-value"
    calls = []
    monkeypatch.setattr(acceptance_check.AiSettingsService, "has_api_key", lambda _: True)
    monkeypatch.setattr(acceptance_check.AiSettingsService, "get_api_key_source", lambda _: "keyring")
    monkeypatch.setattr(
        acceptance_check.AiSettingsService,
        "get_effective_provider_config",
        lambda provider: {
            "model": f"{provider}-model",
            "base_url": f"https://{provider}.example.test/v1",
            "api_key": secret,
        },
    )
    def connection(provider, **kwargs):
        calls.append((provider, kwargs))
        return f"❌ 连接失败：credential={secret}"
    monkeypatch.setattr(acceptance_check.AiSettingsService, "check_connection", connection)

    for provider in ("openai", "deepseek"):
        code, report = acceptance_check.check_provider(provider, True, 17)
        assert code == 2
        assert secret not in json.dumps(report, ensure_ascii=False)
        assert report["base_url"] == f"https://{provider}.example.test/v1"

    assert len(calls) == 2
    for provider, kwargs in calls:
        assert kwargs == {
            "api_key": secret,
            "base_url": f"https://{provider}.example.test/v1",
            "timeout": 17,
        }
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_real_provider_check_calls_connection_once(monkeypatch):
    monkeypatch.setattr(acceptance_check.AiSettingsService, "has_api_key", lambda _: True)
    monkeypatch.setattr(acceptance_check.AiSettingsService, "get_api_key_source", lambda _: "environment")
    monkeypatch.setattr(
        acceptance_check.AiSettingsService,
        "get_effective_provider_config",
        lambda _: {"model": "model", "base_url": "https://custom.example/v1", "api_key": "fake"},
    )
    calls = []
    monkeypatch.setattr(
        acceptance_check.AiSettingsService,
        "check_connection",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "✅ 连接成功",
    )
    code, report = acceptance_check.check_provider("openai", True, 9)
    assert code == 0
    assert report["real_request_sent"] is True
    assert len(calls) == 1


def test_provider_exception_containing_key_is_sanitized(monkeypatch, capsys):
    secret = "sk-super-secret-test-value"
    monkeypatch.setattr(acceptance_check.AiSettingsService, "has_api_key", lambda _: True)
    monkeypatch.setattr(acceptance_check.AiSettingsService, "get_api_key_source", lambda _: "keyring")
    monkeypatch.setattr(
        acceptance_check.AiSettingsService,
        "get_effective_provider_config",
        lambda _: {"model": "model", "base_url": "https://custom.example/v1", "api_key": secret},
    )
    monkeypatch.setattr(
        acceptance_check.AiSettingsService,
        "check_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"bad credential {secret}")),
    )
    code, report = acceptance_check.check_provider("deepseek", True, 10)
    captured = capsys.readouterr()
    serialized = json.dumps(report, ensure_ascii=False) + captured.out + captured.err
    assert code == 2
    assert secret not in serialized
    assert "***" in serialized
