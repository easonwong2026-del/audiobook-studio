import json

from domain.v4.production import (
    PerformanceOverrides,
    PronunciationRules,
    SynthesisPlan,
    TextLimits,
    TtsProfile,
    VoiceBinding,
    VoiceBindings,
)
from repositories.production_repository import ProductionRepository


def _profile():
    return TtsProfile(
        profile_id="test",
        engine="fake",
        limits=TextLimits(8, 10, 12, 3, metric="characters"),
        hardware={"gpu": "fake"},
        options={"fp16": True},
        emotion={"mode": "text_auto"},
        runtime_options={"max_split_depth": 3},
    )


def test_production_documents_round_trip_and_preserve_engine_options(tmp_path):
    repository = ProductionRepository(tmp_path)
    repository.initialize(_profile())
    voices, performance, pronunciation, profile = repository.load_inputs()
    assert voices == VoiceBindings({})
    assert performance == PerformanceOverrides()
    assert pronunciation == PronunciationRules()
    assert profile.hardware == {"gpu": "fake"}
    assert profile.options == {"fp16": True}
    assert profile.runtime_options["max_split_depth"] == 3


def test_production_edit_and_plan_create_revision_snapshots(tmp_path):
    repository = ProductionRepository(tmp_path)
    repository.initialize(_profile())
    voices = VoiceBindings(
        {"narrator": VoiceBinding("voice_1", "fingerprint")},
        revision=2,
    )
    repository.save_document("voices.json", voices.to_dict())
    assert list((tmp_path / "revisions").glob("production-*/voices.json"))
    assert json.loads(
        (tmp_path / "production/voices.json").read_text(encoding="utf-8")
    )["revision"] == 2


def test_synthesis_plan_serialization_round_trip():
    value = {
        "schema_version": "audiobook-synthesis-plan-v1",
        "revision": 1,
        "dependencies": {
            "source_sha256": "0" * 64,
            "script_revision": 1,
            "voices_revision": 1,
            "performance_revision": 1,
            "pronunciation_revision": 1,
            "tts_profile_revision": 1
        },
        "tasks": []
    }
    assert SynthesisPlan.from_dict(value).to_dict() == value
