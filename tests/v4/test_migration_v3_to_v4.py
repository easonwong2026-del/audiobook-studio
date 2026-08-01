from __future__ import annotations

import json

from domain.v4 import ScriptDocument, SourceMetadata
from domain.v4.production import PerformanceOverrides, VoiceBindings
from services.migration_v3_to_v4 import V3ToV4MigrationService


def _v3_project(tmp_path):
    project = tmp_path / "legacy"
    project.mkdir()
    voice = project / "voices/lin.wav"
    voice.parent.mkdir()
    voice.write_bytes(b"voice-fingerprint-source")
    script = {
        "meta": {"title": "旧书", "author": "作者"},
        "voices": {"旁白": {}, "林晚": {}},
        "chapters": [
            {
                "id": 1,
                "title": "第一章",
                "segments": [
                    {"id": "1-1", "role": "旁白", "text": "开场。"},
                    {
                        "id": "1-2",
                        "role": "林晚",
                        "text": "你好。",
                        "emotion": "sad",
                        "speech_rate": 0.9,
                    },
                ],
            },
            {
                "id": 2,
                "title": "第二章",
                "segments": [
                    {"id": "2-1", "role": "旁白", "text": "结束。"}
                ],
            },
        ],
    }
    (project / "structured_script.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    (project / "voice_bindings.json").write_text(
        json.dumps(
            {"bindings": {"旁白": None, "林晚": str(voice)}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project / "project.json").write_text(
        '{"project_name":"legacy"}', encoding="utf-8"
    )
    return project


def test_copy_only_migration_preserves_v3_and_marks_source_fidelity(tmp_path):
    source = _v3_project(tmp_path)
    original = (source / "structured_script.json").read_bytes()
    destination = tmp_path / "projects"
    result = V3ToV4MigrationService().migrate(source, destination)
    assert (source / "structured_script.json").read_bytes() == original
    assert result.backup_path.is_dir()
    assert result.project_path.name == "legacy-v4"
    source_text = (result.project_path / "source/source.txt").read_text(
        encoding="utf-8"
    )
    metadata = SourceMetadata.from_dict(
        json.loads(
            (result.project_path / "source/source.meta.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert metadata.source_origin == "reconstructed-from-v3"
    assert metadata.source_fidelity == "segment-text"
    script = ScriptDocument.from_dict(
        json.loads(
            (result.project_path / "script/script.json").read_text(
                encoding="utf-8"
            )
        ),
        source_text,
    )
    assert sum(len(item.segments) for item in script.chapters) == 3
    assert "开场。\n你好。\n\n结束。" == source_text


def test_migration_moves_voice_and_only_explicit_performance(tmp_path):
    source = _v3_project(tmp_path)
    result = V3ToV4MigrationService().migrate(source, tmp_path / "projects")
    production = result.project_path / "production"
    voices = VoiceBindings.from_dict(
        json.loads((production / "voices.json").read_text(encoding="utf-8"))
    )
    assert len(voices.bindings) == 1
    binding = next(iter(voices.bindings.values()))
    assert (result.project_path / binding.voice_id).is_file()
    performance = PerformanceOverrides.from_dict(
        json.loads((production / "performance.json").read_text(encoding="utf-8"))
    )
    assert list(performance.overrides) == ["segment_000002"]


def test_migration_is_repeatable_without_second_copy(tmp_path):
    source = _v3_project(tmp_path)
    service = V3ToV4MigrationService()
    first = service.migrate(source, tmp_path / "projects")
    second = service.migrate(source, tmp_path / "projects")
    assert second.reused_existing is True
    assert second.project_path == first.project_path
    assert second.backup_path == first.backup_path


def test_migration_does_not_leave_target_on_conversion_failure(tmp_path):
    source = _v3_project(tmp_path)
    (source / "structured_script.json").write_text(
        '{"chapters":[]}', encoding="utf-8"
    )
    destination = tmp_path / "projects"
    try:
        V3ToV4MigrationService().migrate(source, destination)
    except ValueError:
        pass
    assert not (destination / "legacy-v4").exists()
