"""Voice preview and supplement work must share the singleton TTS runtime."""
from __future__ import annotations

import os
import wave

import pytest

from lib import project_paths, tts_engine
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services.production_runtime import ProductionRuntimeClient
from services.runtime_tts import RuntimeTTSBusyError, RuntimeTTSService


SCRIPT = {
    "meta": {"title": "Runtime utility", "author": "Test"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


def _write_wav(path: str, frames: int = 800) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * frames)
    return path


@pytest.fixture
def runtime_utility_project(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_RUNTIME_MODE", "inline")
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("book", SCRIPT)
    monkeypatch.setattr(tts_engine, "init_engine", lambda: None)
    monkeypatch.setattr(tts_engine, "empty_cache", lambda reason="manual": None)
    yield ProjectRepository.get_project_dir("book")
    ProductionRuntimeClient.reset_inline()


def test_supplement_runs_as_project_local_runtime_task(
    runtime_utility_project,
    monkeypatch,
):
    calls: list[str] = []

    def fake_synthesis(*, output_path: str, speaker_audio: str, **_kwargs):
        calls.append(speaker_audio)
        return _write_wav(output_path)

    monkeypatch.setattr(tts_engine, "synthesize_segment", fake_synthesis)
    artifact_dir = os.path.join(
        project_paths.project_dir(runtime_utility_project, "cache", create=True),
        "supplement_tasks",
        "request",
    )
    items = RuntimeTTSService.synthesize_supplement(
        project_name="book",
        role="旁白",
        lines=["第一句", "第二句"],
        speaker_audio="/tmp/speaker.wav",
        overrides={"emotion": "happy"},
        num_beams=2,
        artifact_dir=artifact_dir,
        timeout=10,
    )

    assert [item["status"] for item in items] == ["ok", "ok"]
    assert calls == ["/tmp/speaker.wav", "/tmp/speaker.wav"]
    records = TaskRepository.list_tasks(project="book", task_type="supplement")
    assert len(records) == 1
    assert records[0].status == "done"
    assert all(os.path.commonpath([item["wav_path"], runtime_utility_project])
               == runtime_utility_project for item in items)


def test_voice_preview_runs_and_concatenates_inside_runtime(
    runtime_utility_project,
    tmp_path,
    monkeypatch,
):
    parts = [_write_wav(str(tmp_path / f"part-{index}.wav")) for index in range(3)]
    monkeypatch.setattr(tts_engine, "test_voice", lambda _speaker: parts)

    preview = RuntimeTTSService.test_voice_and_concat_wavs(
        "book",
        "旁白",
        "/tmp/speaker.wav",
        timeout=10,
    )

    assert os.path.isfile(preview)
    assert os.path.commonpath([preview, runtime_utility_project]) == runtime_utility_project
    records = TaskRepository.list_tasks(project="book", task_type="voice_preview")
    assert len(records) == 1
    assert records[0].status == "done"


def test_runtime_utility_rejects_second_active_task(runtime_utility_project):
    now = "2026-08-09T00:00:00Z"
    outcome, _record = TaskRepository.create_production_task(TaskRecord(
        task_id="task_production",
        task_type="synthesis",
        project="book",
        status="pending",
        idempotency_key="production",
        created_at=now,
        updated_at=now,
    ))
    assert outcome == "created"
    artifact_dir = os.path.join(runtime_utility_project, "cache", "supplement")

    with pytest.raises(RuntimeTTSBusyError) as captured:
        RuntimeTTSService.synthesize_supplement(
            project_name="book",
            role="旁白",
            lines=["不能并发"],
            speaker_audio="/tmp/speaker.wav",
            overrides={},
            num_beams=2,
            artifact_dir=artifact_dir,
            timeout=2,
        )
    assert captured.value.task_id == "task_production"
