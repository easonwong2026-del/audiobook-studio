"""Formal delivery duration metadata regressions."""
from __future__ import annotations
from lib import project_paths

import json
import os
import wave

from services.export import ExportService


def _write_wav(path: str, duration: float, rate: int = 10_000) -> None:
    frames = round(duration * rate)
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x00" * frames)


def test_artifact_duration_reads_final_wav_metadata(tmp_path):
    path = tmp_path / "book.wav"
    _write_wav(str(path), 1.234)

    assert ExportService._artifact_duration(str(path), "wav") == 1.234


def test_timed_duration_fallback_includes_segment_and_chapter_silence(tmp_path):
    project_dir = str(tmp_path / "project")
    segments = project_paths.project_dir(project_dir, "segments", create=True)
    script = {
        "version": "2.0",
        "chapters": [
            {
                "id": "001",
                "segments": [
                    {"id": "001-001"},
                    {"id": "001-002"},
                ],
            },
            {"id": "002", "segments": [{"id": "002-001"}]},
        ],
    }
    with open(project_paths.project_file(project_dir, "structured_script"), "w", encoding="utf-8") as file:
        json.dump(script, file)
    paths = {}
    for segment_id, duration in (
        ("001-001", 0.4),
        ("001-002", 0.6),
        ("002-001", 0.8),
    ):
        path = os.path.join(segments, f"{segment_id}.wav")
        _write_wav(path, duration)
        paths[segment_id] = path

    # 0.4 + 0.3 + 0.6 + 0.8 + 0.8 chapter transition silence.
    assert ExportService._timed_export_duration(project_dir, paths) == 2.9


def test_compressed_artifact_uses_explicit_timing_fallback(tmp_path, monkeypatch):
    path = tmp_path / "book.mp3"
    path.write_bytes(b"not an encoded file")
    monkeypatch.setattr("services.export.shutil.which", lambda _name: None)

    assert ExportService._artifact_duration(
        str(path),
        "mp3",
        fallback=12.3456,
    ) == 12.346
