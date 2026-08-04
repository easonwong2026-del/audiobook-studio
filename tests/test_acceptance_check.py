import json
from pathlib import Path

from lib import segment_cache
from scripts import acceptance_check


def make_project(root: Path, *, duplicate=False, audio=True):
    root.mkdir(parents=True)
    for folder in ("segments", "output"):
        (root / folder).mkdir()
    segments = [
        {"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "正文"},
        {"id": "1-001" if duplicate else "1-002", "role": "旁白", "emotion": "neutral", "text": "正文二"},
    ]
    script = {"meta": {"title": "测试"}, "voices": {"旁白": {}}, "chapters": [{"id": 1, "title": "第一章", "segments": segments}]}
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
