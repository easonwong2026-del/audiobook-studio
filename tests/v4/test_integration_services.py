"""统一服务层测试：音频校验 / 项目服务 / 音色服务 / 质检服务 / 合成服务（mock）。"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from services.audio_validation import validate_audio_file
from services.source_segmenter import SourceSegmenter
from services.v4_project_service import V4ProjectService
from services.v4_quality_service import V4QualityService
from services.v4_synthesis_service import V4SynthesisService
from services.v4_voice_service import V4VoiceService


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    """构造一个 V4 项目并落到 tmp_path（充当数据根目录，经环境变量重定向）。"""
    import os

    from lib import config

    old = os.environ.get(config.ENV_DATA_DIR)
    os.environ[config.ENV_DATA_DIR] = str(tmp_path)
    root = tmp_path / "projects"
    root.mkdir(parents=True)
    yield root
    if old is None:
        os.environ.pop(config.ENV_DATA_DIR, None)
    else:
        os.environ[config.ENV_DATA_DIR] = old


def _make_v4_project(root: Path, name: str = "测试书") -> Path:
    import json as _json
    from datetime import datetime, timezone

    from domain.v4 import ProjectManifest, SourceMetadata
    from domain.v4.models import source_sha256
    from domain.v4.production import TtsProfile
    from repositories.project_v4_repository import ProjectV4Repository

    repo = ProjectV4Repository(root)
    source = "雨夜书店\n林晚著\n\n第一章 雨夜的书店\n林晚说：“你好。”顾川急道：“快走！”"
    segmented = SourceSegmenter().segment(source)
    now = datetime.now(timezone.utc).isoformat()
    metadata = SourceMetadata(
        original_filename="source.txt",
        source_format="txt",
        encoding="utf-8",
        normalization="none",
        char_count=len(source),
        sha256=source_sha256(source),
        imported_at=now,
        source_origin="test",
        source_fidelity="full-text",
    )
    manifest = ProjectManifest(
        project_id="project_test", name=name, title=name, author="",
        created_at=now, updated_at=now,
    )
    profile_path = (
        Path(__file__).resolve().parents[2]
        / "config/tts_profiles/indextts2-rtx5070ti-laptop-12gb-v1.json"
    )
    with profile_path.open("r", encoding="utf-8") as handle:
        profile = TtsProfile.from_dict(_json.load(handle))
    return repo.create(
        directory_name=name,
        manifest=manifest,
        source_text=source,
        source_metadata=metadata,
        script=segmented.script,
        speakers=segmented.speakers,
        tts_profile=profile,
    )


# ── 音频校验 ────────────────────────────────────────────────────────────────

def test_audio_validation_missing_file():
    ok, message = validate_audio_file("Z:/does/not/exist.wav")
    assert not ok
    assert "不存在" in message


def test_audio_validation_bad_extension(tmp_path):
    bad = tmp_path / "voice.txt"
    bad.write_text("hello", encoding="utf-8")
    ok, message = validate_audio_file(bad)
    assert not ok
    assert "不支持" in message


def test_audio_validation_short_wav(tmp_path):
    path = tmp_path / "short.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 2205)  # 0.1s
    ok, message = validate_audio_file(path)
    assert not ok
    assert "时长过短" in message


def test_audio_validation_valid_wav(tmp_path):
    path = tmp_path / "ok.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 44100)  # 2s
    ok, message = validate_audio_file(path)
    assert ok and message == ""


# ── 项目服务 ────────────────────────────────────────────────────────────────

def test_project_service_scan_detect(data_root, tmp_path):
    project = _make_v4_project(data_root)
    assert project.is_dir()
    assert V4ProjectService.detect_format("测试书") == "v4"
    infos = V4ProjectService.scan_projects()
    assert any(item.name == "测试书" and item.project_format == "v4" for item in infos)


def test_project_service_open_v4(data_root):
    _make_v4_project(data_root)
    context = V4ProjectService.open_project("测试书")
    assert context is not None and context.is_v4
    assert context.script is not None and context.speakers is not None
    assert sum(len(c.segments) for c in context.script.chapters) > 0


def test_project_service_open_missing(data_root):
    assert V4ProjectService.open_project("不存在的项目") is None


# ── 音色服务 ────────────────────────────────────────────────────────────────

def test_voice_service_bind_validates_and_binds(data_root, tmp_path):
    project = _make_v4_project(data_root)
    audio = tmp_path / "voice.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 44100)
    speakers = json.loads((project / "script/speakers.json").read_text(encoding="utf-8"))
    speaker_id = next(
        item["id"]
        for item in speakers["speakers"]
        if item["name"] == "林晚"
    )
    ok, _message = V4VoiceService.bind_voice(project, speaker_id, audio)
    assert ok
    voices = json.loads((project / "production/voices.json").read_text(encoding="utf-8"))
    assert speaker_id in voices["bindings"]


def test_voice_service_bind_rejects_missing_audio(data_root, tmp_path):
    project = _make_v4_project(data_root)
    ok, message = V4VoiceService.bind_voice(
        project, "any_speaker", "Z:/no.wav"
    )
    assert not ok
    assert "不存在" in message


def test_voice_service_unbind_restores_unbound_card_state(data_root, tmp_path):
    project = _make_v4_project(data_root)
    audio = tmp_path / "voice.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 44100)
    speakers = json.loads((project / "script/speakers.json").read_text(encoding="utf-8"))
    speaker_id = next(
        item["id"] for item in speakers["speakers"] if item["name"] == "林晚"
    )
    ok, _message = V4VoiceService.bind_voice(project, speaker_id, audio)
    assert ok
    ok, _message = V4VoiceService.unbind_voice(project, speaker_id)
    assert ok
    voices = json.loads((project / "production/voices.json").read_text(encoding="utf-8"))
    assert speaker_id not in voices["bindings"]


def test_merge_moves_source_voice_binding_to_target(data_root, tmp_path):
    from ui.v4_workspace_handlers import merge_v4_speakers

    project = _make_v4_project(data_root)
    audio = tmp_path / "voice.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 44100)
    speakers = json.loads((project / "script/speakers.json").read_text(encoding="utf-8"))
    source_id = next(
        item["id"] for item in speakers["speakers"] if item["name"] == "林晚"
    )
    target_id = next(
        item["id"] for item in speakers["speakers"] if item["name"] == "顾川"
    )
    ok, _message = V4VoiceService.bind_voice(project, source_id, audio)
    assert ok
    message = merge_v4_speakers(project.name, source_id, target_id)
    assert "迁移到目标角色" in message
    voices = json.loads((project / "production/voices.json").read_text(encoding="utf-8"))
    assert source_id not in voices["bindings"]
    assert target_id in voices["bindings"]
    updated = json.loads((project / "script/speakers.json").read_text(encoding="utf-8"))
    assert source_id not in {item["id"] for item in updated["speakers"]}


# ── 质检服务 ────────────────────────────────────────────────────────────────

def test_quality_service_empty_states_do_not_raise(data_root):
    project = _make_v4_project(data_root)
    assert V4QualityService.chapter_audio(project, "") is None
    assert V4QualityService.segment_audio(project, "") is None
    assert V4QualityService.segment_audio(project, "segment_999999") is None


# ── 合成服务 ────────────────────────────────────────────────────────────────

def test_synthesis_service_generate_plan(data_root):
    project = _make_v4_project(data_root)
    _rows, message = V4SynthesisService.generate_plan(project)
    assert "tasks" in message
    runtime = project / "runtime/runtime.db"
    assert runtime.is_file()


def test_synthesis_service_start_without_plan(data_root):
    project = _make_v4_project(data_root)
    ok, message = V4SynthesisService.start(project.name)
    assert not ok
    assert "计划" in message


def test_synthesis_service_snapshot_idle(data_root):
    project = _make_v4_project(data_root)
    snapshot = V4SynthesisService.snapshot(project.name)
    assert snapshot["run_status"] in ("idle", "error")
