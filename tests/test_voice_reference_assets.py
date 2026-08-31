"""Synthetic Voice Reference Asset Pipeline P0 checks."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from scipy.io import wavfile

from lib import config, tts_engine
from lib import queue as synthesis_queue
from repositories.project_repo import ProjectRepository
from services import voice_assets as va
from services.project import ProjectService
from services.voice_assets import VoiceAssetError, VoiceAssetService

RATE = 22050


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_voice_library", lambda: str(tmp_path))
    return tmp_path


def _tone(seconds: float, amplitude: float = 0.2, frequency: float = 220.0) -> np.ndarray:
    count = int(RATE * seconds)
    time_axis = np.arange(count) / RATE
    return (amplitude * np.sin(2 * np.pi * frequency * time_axis)).astype(np.float32)


def _write(path, *parts) -> bytes:
    data = np.concatenate(parts)
    wavfile.write(path, RATE, data)
    return path.read_bytes()


def _metadata(reference: str) -> dict:
    with open(reference[:-4] + ".json", encoding="utf-8") as file:
        return json.load(file)


def test_short_compliant_audio_is_standardized_and_original_is_preserved(library):
    path = library / "乔欣.mp3.wav"
    original = _write(path, _tone(8.0))
    asset_id = VoiceAssetService.list_assets()[0]["voice_asset_id"]

    reference = VoiceAssetService.ensure_reference(voice_asset_id=asset_id)
    rate, data = wavfile.read(reference)
    assert os.path.basename(reference) == "乔欣.mp3.reference.wav"
    assert rate == RATE and data.ndim == 1 and data.dtype == np.int16
    assert path.read_bytes() == original
    assert 6.0 <= len(data) / RATE <= 10.0


def test_long_audio_selects_voice_after_front_silence(library):
    path = library / "front.wav"
    _write(path, np.zeros(RATE * 10, dtype=np.float32), _tone(12.0), np.zeros(RATE * 8, dtype=np.float32))

    reference = VoiceAssetService.ensure_reference(source_path=str(path))
    metadata = _metadata(reference)
    assert metadata["reference_method"] == "auto_vad"
    assert metadata["selection"]["start_seconds"] >= 9.0


def test_clean_middle_window_beats_clipping_window(library):
    path = library / "clipping.wav"
    _write(path, np.full(RATE * 9, 0.999, dtype=np.float32), _tone(15.0, 0.2))

    reference = VoiceAssetService.ensure_reference(source_path=str(path))
    metadata = _metadata(reference)
    assert metadata["selection"]["start_seconds"] >= 9.0
    assert metadata["selection"]["clipping_ratio"] == 0.0


def test_stable_middle_window_beats_unstable_opening(library):
    path = library / "middle.wav"
    rng = np.random.default_rng(7)
    noisy = (rng.normal(0.0, 0.18, RATE * 8)).astype(np.float32)
    _write(path, noisy, _tone(16.0, 0.18), noisy[: RATE * 6])

    reference = VoiceAssetService.ensure_reference(source_path=str(path))
    assert _metadata(reference)["selection"]["start_seconds"] >= 8.0


def test_mostly_silent_audio_requires_manual_reference(library):
    path = library / "silent.wav"
    _write(path, np.zeros(RATE * 30, dtype=np.float32))

    with pytest.raises(VoiceAssetError) as error:
        VoiceAssetService.ensure_reference(source_path=str(path))
    assert error.value.code == "REFERENCE_AUDIO_MANUAL_REQUIRED"
    assert VoiceAssetService.status_for_path(str(path))["reference_status"] == "manual_required"


def test_ensure_is_cache_hit_and_missing_reference_regenerates(library, monkeypatch):
    path = library / "cached.wav"
    _write(path, _tone(8.0))
    reference = VoiceAssetService.ensure_reference(source_path=str(path))
    os.remove(reference)
    regenerated = VoiceAssetService.ensure_reference(source_path=str(path))
    assert regenerated == reference and os.path.isfile(reference)

    monkeypatch.setattr(va, "_decode_audio", lambda _path: (_ for _ in ()).throw(AssertionError("cache miss")))
    assert VoiceAssetService.ensure_reference(source_path=str(path)) == reference


def test_source_change_rebuilds_reference_without_changing_bound_asset_id(library):
    path = library / "mutable.wav"
    _write(path, _tone(8.0, 0.2))
    asset_id = VoiceAssetService.list_assets()[0]["voice_asset_id"]
    reference = VoiceAssetService.ensure_reference(voice_asset_id=asset_id)
    with open(reference, "rb") as file:
        reference_before = file.read()
    _write(path, _tone(8.0, 0.1))
    source_after = path.read_bytes()

    rebuilt = VoiceAssetService.ensure_reference(voice_asset_id=asset_id)
    assert rebuilt == reference
    assert path.read_bytes() == source_after
    assert VoiceAssetService.list_assets()[0]["voice_asset_id"] == asset_id
    with open(reference, "rb") as file:
        assert file.read() != reference_before


def test_concurrent_ensure_publishes_one_valid_reference(library):
    path = library / "concurrent.wav"
    _write(path, np.zeros(RATE * 5, dtype=np.float32), _tone(20.0), np.zeros(RATE * 5, dtype=np.float32))

    with ThreadPoolExecutor(max_workers=4) as pool:
        references = list(pool.map(lambda _: VoiceAssetService.ensure_reference(source_path=str(path)), range(4)))
    assert len(set(references)) == 1
    status = VoiceAssetService.status_for_path(str(path))
    assert status["reference_status"] == "ready"
    assert 6.0 <= status["reference_duration"] <= 10.0


def test_tts_resolver_never_returns_long_original(library):
    path = library / "long.wav"
    original = _write(path, np.zeros(RATE * 10, dtype=np.float32), _tone(20.0))

    resolved = VoiceAssetService.resolve_tts_reference(source_path=str(path))
    assert resolved != str(path)
    assert os.path.basename(resolved) == "long.reference.wav"
    assert path.read_bytes() == original


def test_generation_error_is_fail_closed(library, monkeypatch):
    path = library / "broken.wav"
    _write(path, _tone(8.0))
    monkeypatch.setattr(va, "_decode_audio", lambda _path: (_ for _ in ()).throw(RuntimeError("decode boom")))

    with pytest.raises(VoiceAssetError) as error:
        VoiceAssetService.resolve_tts_reference(source_path=str(path))
    assert error.value.code == "REFERENCE_AUDIO_GENERATION_FAILED"
    assert not (library / "broken.reference.wav").exists()


def test_library_check_reports_reference_states(library):
    _write(library / "ready.wav", _tone(8.0))
    _write(library / "needs.wav", np.zeros(RATE * 2, dtype=np.float32), _tone(20.0))
    _write(library / "silent.wav", np.zeros(RATE * 30, dtype=np.float32))
    VoiceAssetService.ensure_reference(source_path=str(library / "ready.wav"))

    result = VoiceAssetService.check_library()
    assert result["total"] == 3
    assert result["ready"] == 1
    assert result["needs_reference"] == 1
    assert result["manual_required"] == 1


def test_formal_queue_passes_derived_reference_to_tts(library, monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(data_dir / "projects"))
    monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", str(data_dir / "legacy"))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    ProjectService.create_project_from_data("book", {
        "meta": {"title": "reference queue"},
        "voices": {"旁白": {}},
        "chapters": [{"id": "1", "title": "第一章", "segments": [{"id": "1-001", "role": "旁白", "text": "测试"}]}],
    })
    source = library / "queue.wav"
    original = _write(source, np.zeros(RATE * 4, dtype=np.float32), _tone(20.0))
    captured: list[str] = []

    def fake_synthesis(*, speaker_audio, output_path, **_kwargs):
        captured.append(speaker_audio)
        wavfile.write(output_path, RATE, _tone(0.2))
        return output_path

    monkeypatch.setattr(tts_engine, "synthesize_segment", fake_synthesis)
    list(synthesis_queue.synthesize_project("book", {"旁白": str(source)}))

    assert captured and captured[0] != str(source)
    assert captured[0].endswith("queue.reference.wav")
    assert source.read_bytes() == original
