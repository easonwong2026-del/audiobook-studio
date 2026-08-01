"""流程 A 后半段：V4 导出（章节拼接 → WAV/MP3 生成器路径 → 字幕）。

用 mock 音频直接写 runtime.db 的 chapter_outputs 与合成产物，
验证 V4ExportService 的拼接与字幕不依赖真实 TTS。
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from domain.v4 import ProjectManifest, SourceMetadata
from domain.v4.models import source_sha256
from domain.v4.production import TtsProfile
from repositories.project_v4_repository import ProjectV4Repository
from repositories.runtime_repository import RuntimeRepository
from services.source_segmenter import SourceSegmenter
from services.v4_export import V4ExportService


@pytest.fixture()
def v4_export_project(tmp_path: Path) -> Path:
    import os

    from lib import config

    old = os.environ.get(config.ENV_DATA_DIR)
    os.environ[config.ENV_DATA_DIR] = str(tmp_path)
    root = tmp_path / "projects"
    root.mkdir(parents=True)
    repo = ProjectV4Repository(root)
    source = (
        "第一章 测试\n林晚说：“你好。”\n第二章 测试二\n顾川答：“再见。”"
    )
    segmented = SourceSegmenter().segment(source)
    now = "2026-08-01T00:00:00+00:00"
    metadata = SourceMetadata(
        original_filename="source.txt", source_format="txt", encoding="utf-8",
        normalization="none", char_count=len(source),
        sha256=source_sha256(source), imported_at=now,
        source_origin="test", source_fidelity="full-text",
    )
    manifest = ProjectManifest(
        project_id="project_test", name="导出测试", title="导出测试", author="",
        created_at=now, updated_at=now,
    )
    profile_path = (
        Path(__file__).resolve().parents[2]
        / "config/tts_profiles/indextts2-rtx5070ti-laptop-12gb-v1.json"
    )
    with profile_path.open("r", encoding="utf-8") as handle:
        profile = TtsProfile.from_dict(json.load(handle))
    project = repo.create(
        directory_name="导出测试", manifest=manifest, source_text=source,
        source_metadata=metadata, script=segmented.script,
        speakers=segmented.speakers, tts_profile=profile,
    )
    # 造两章“已拼接”音频
    for chapter_id in ("chapter_0001", "chapter_0002"):
        audio = project / "audio/chapters" / f"{chapter_id}.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        rate = 22050
        data = (np.sin(np.linspace(0, 200, rate // 2)) * 8000).astype(np.int16)
        with wave.open(str(audio), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(data.tobytes())
    yield project
    if old is None:
        os.environ.pop(config.ENV_DATA_DIR, None)
    else:
        os.environ[config.ENV_DATA_DIR] = old


def test_v4_export_wav(v4_export_project: Path):
    path = V4ExportService.export(v4_export_project, output_format="wav")
    assert path.is_file()
    with wave.open(str(path), "rb") as handle:
        assert handle.getnframes() > 0


def test_v4_export_rejects_missing_chapters(tmp_path: Path):
    """未拼接章节时导出应报用户可读错误（而不是成功出空文件）。"""
    import os

    from lib import config

    old = os.environ.get(config.ENV_DATA_DIR)
    os.environ[config.ENV_DATA_DIR] = str(tmp_path)
    try:
        root = tmp_path / "projects"
        root.mkdir(parents=True)
        repo = ProjectV4Repository(root)
        source = "第一章 测试\n林晚说：“你好。”"
        segmented = SourceSegmenter().segment(source)
        now = "2026-08-01T00:00:00+00:00"
        metadata = SourceMetadata(
            original_filename="source.txt", source_format="txt",
            encoding="utf-8", normalization="none", char_count=len(source),
            sha256=source_sha256(source), imported_at=now,
            source_origin="test", source_fidelity="full-text",
        )
        manifest = ProjectManifest(
            project_id="project_test", name="无章节", title="无章节", author="",
            created_at=now, updated_at=now,
        )
        profile_path = (
            Path(__file__).resolve().parents[2]
            / "config/tts_profiles/indextts2-rtx5070ti-laptop-12gb-v1.json"
        )
        with profile_path.open("r", encoding="utf-8") as handle:
            profile = TtsProfile.from_dict(json.load(handle))
        project = repo.create(
            directory_name="无章节", manifest=manifest, source_text=source,
            source_metadata=metadata, script=segmented.script,
            speakers=segmented.speakers, tts_profile=profile,
        )
        with pytest.raises(RuntimeError, match="missing assembled chapters"):
            V4ExportService.export(project, output_format="wav")
    finally:
        if old is None:
            os.environ.pop(config.ENV_DATA_DIR, None)
        else:
            os.environ[config.ENV_DATA_DIR] = old


def test_v4_subtitles_generated(v4_export_project: Path):
    """已合成（runtime 有输出）时生成 srt/lrc。"""
    runtime = RuntimeRepository(v4_export_project / "runtime/runtime.db")
    runtime.initialize()
    # 手动登记合成产物：写 chunks 音频 + cache_entries + tasks completed
    script = json.loads(
        (v4_export_project / "script/script.json").read_text(encoding="utf-8")
    )
    import sqlite3

    with sqlite3.connect(runtime.path) as connection:
        for chapter in script["chapters"]:
            for segment in chapter["segments"]:
                chunk = (
                    v4_export_project
                    / "audio/chunks"
                    / f"{segment['id']}.wav"
                )
                chunk.parent.mkdir(parents=True, exist_ok=True)
                rate = 22050
                data = (
                    np.sin(np.linspace(0, 150, rate // 4)) * 8000
                ).astype(np.int16)
                with wave.open(str(chunk), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(rate)
                    handle.writeframes(data.tobytes())
                connection.execute(
                    """
                    INSERT INTO synthesis_tasks(
                        task_id, plan_revision, chapter_id, speaker_id,
                        cache_key, status, actual_text, output_path,
                        created_at, updated_at
                    ) VALUES (?, '1', ?, 'narrator', ?, 'completed',
                              ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        segment["id"],
                        chapter["chapter_id"],
                        segment["id"],
                        "你好" if segment["id"].endswith("000001") else "再见",
                        f"audio/chunks/{segment['id']}.wav",
                    ),
                )
        connection.commit()
    paths = V4ExportService.generate_subtitles(
        v4_export_project, formats=("srt", "lrc")
    )
    assert len(paths) == 2
    for path in paths:
        assert path.is_file()
    srt = next(item for item in paths if item.suffix == ".srt")
    content = srt.read_text(encoding="utf-8")
    assert "-->" in content
