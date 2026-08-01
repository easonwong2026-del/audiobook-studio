"""流程 A / 流程 B 端到端集成测试（mock TTS，不加载真实模型）。

流程 A：新建项目页创建 V4 → 项目管理打开 → 角色指派/音色绑定 →
        生成计划 → 合成（mock）→ 交付导出。
流程 B：V3 项目复制迁移到 V4 → 原项目不变 → 新项目可进入五步流程。
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
from services.source_segmenter import SourceSegmenter
from services.v4_project_service import V4ProjectService
from services.v4_voice_service import V4VoiceService
from services.v4_synthesis_service import V4SynthesisService
from services.v4_quality_service import V4QualityService
from services.v4_export import V4ExportService


@pytest.fixture()
def env_root(tmp_path: Path):
    import os

    from lib import config
    from repositories.project_repo import ProjectRepository

    old = os.environ.get(config.ENV_DATA_DIR)
    old_ws = ProjectRepository.WORKSPACE_ROOT
    os.environ[config.ENV_DATA_DIR] = str(tmp_path)
    # ProjectRepository.WORKSPACE_ROOT 是类级缓存，需同步指向临时根
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    yield tmp_path
    if old is None:
        os.environ.pop(config.ENV_DATA_DIR, None)
    else:
        os.environ[config.ENV_DATA_DIR] = old
    ProjectRepository.WORKSPACE_ROOT = old_ws


def _wav(path: Path, seconds: float = 0.5) -> None:
    rate = 22050
    data = (np.sin(np.linspace(0, 180, int(rate * seconds))) * 8000).astype(
        np.int16
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())


def _profile() -> TtsProfile:
    path = (
        Path(__file__).resolve().parents[2]
        / "config/tts_profiles/indextts2-rtx5070ti-laptop-12gb-v1.json"
    )
    with path.open("r", encoding="utf-8") as handle:
        return TtsProfile.from_dict(json.load(handle))


def _create_v4(root: Path, name: str = "流程测试") -> Path:
    repo = ProjectV4Repository(root / "projects")
    source = (
        "第一章 雨夜\n林晚说：“你好。”顾川急道：“快走！”\n"
        "第二章 手稿\n林晚问：“谁？”顾川答：“我。”"
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
        project_id="project_flow", name=name, title=name, author="",
        created_at=now, updated_at=now,
    )
    return repo.create(
        directory_name=name, manifest=manifest, source_text=source,
        source_metadata=metadata, script=segmented.script,
        speakers=segmented.speakers, tts_profile=_profile(),
    )


# ── 流程 A：原五步流程完整操作 V4 项目 ───────────────────────────────────

def test_flow_a_five_step_workflow(env_root: Path):
    # ① 新建项目（V4ProjectCreationService 等价于新建页按钮）
    project = _create_v4(env_root)
    name = "流程测试"

    # ② 项目管理打开（统一服务）
    context = V4ProjectService.open_project(name)
    assert context is not None and context.is_v4
    assert len(context.script.chapters) == 2

    # ③ 角色与声音：AI 指派（规则已有顾川）→ 音色绑定（mock 音频）
    speakers = context.speakers
    target = next(
        item for item in speakers.speakers if item.display_name == "顾川"
    )
    audio = env_root / "ref.wav"
    _wav(audio)
    ok, message = V4VoiceService.bind_voice(project, target.speaker_id, audio)
    assert ok
    # 旁白默认绑定（迁移/创建时生成）
    narrator = next(
        item for item in speakers.speakers if item.speaker_id == "narrator"
    )
    ok2, _ = V4VoiceService.bind_voice(project, narrator.speaker_id, audio)
    assert ok2

    # ④ 生产与质检：生成计划 + mock 合成
    rows, plan_message = V4SynthesisService.generate_plan(project)
    assert "tasks" in plan_message
    from services.v4_synthesis_service import V4SynthesisService as Svc
    import services.v4_synthesis_service as v4svc

    class QuickAdapter:
        def synthesize(self, task, profile, output_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _wav(output_path, 0.2)
            from tts.base_adapter import SynthesisOutput

            return SynthesisOutput(path=output_path)

        def close(self):
            pass

    adapter = QuickAdapter()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(v4svc, "IndexTTS2Adapter", lambda *a, **k: adapter)
    ok3, _ = Svc.start(name)
    assert ok3
    import time

    deadline = time.time() + 20
    while time.time() < deadline:
        snap = Svc.snapshot(name)
        if snap["run_status"] in ("done", "cancelled", "error"):
            break
        time.sleep(0.05)
    assert Svc.snapshot(name)["run_status"] == "done"

    # 质检：章节音频存在
    chapters = V4QualityService.available_chapters(project)
    assert chapters

    # ⑤ 交付：WAV 导出
    exported = V4ExportService.export(project, output_format="wav")
    assert exported.is_file()
    with wave.open(str(exported), "rb") as handle:
        assert handle.getnframes() > 0
    monkey.undo()


# ── 流程 B：V3 项目复制迁移到 V4 ──────────────────────────────────────────

def _make_v3_project(root: Path, name: str = "旧书") -> Path:
    """构造一个最小的 V3 项目（project.json + structured_script.json +
    voice_bindings.json）。"""
    from lib import config

    projects = Path(config.get_projects_root())
    project_dir = projects / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "name": name,
                "meta": {"title": name, "author": ""},
                "schema": "v3",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = {
        "meta": {"title": name, "author": ""},
        "chapters": [
            {
                "id": 1,
                "title": "第一章",
                "segments": [
                    {"id": "1-1", "role": "旁白", "text": "夜色深了。"},
                    {"id": "1-2", "role": "林晚", "text": "“你来了。”"},
                ],
            }
        ],
    }
    (project_dir / "structured_script.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    (project_dir / "voice_bindings.json").write_text(
        json.dumps({"bindings": {}, "bound_at": ""}), encoding="utf-8"
    )
    return project_dir


def test_flow_b_v3_migration_keeps_original(env_root: Path):
    from lib import config

    _make_v3_project(env_root, "旧书")
    # 迁移前 V3 可识别
    assert V4ProjectService.detect_format("旧书") == "v3"

    # 复制迁移 → 新 V4 项目
    result = V4ProjectService.migrate_to_v4("旧书")
    assert result.project_path.name == "旧书-v4"
    assert result.backup_path.is_dir()
    assert V4ProjectService.detect_format("旧书-v4") == "v4"

    # 原项目不变（仍是 V3）
    assert V4ProjectService.detect_format("旧书") == "v3"

    # 新 V4 项目可进入五步流程（打开 + 章节结构 + 角色 + 计划）
    v4_context = V4ProjectService.open_project("旧书-v4")
    assert v4_context is not None and v4_context.is_v4
    assert len(v4_context.script.chapters) == 1
    # 迁移保留了角色与旁白
    ids = {item.speaker_id for item in v4_context.speakers.speakers}
    assert "narrator" in ids and "林晚" in ids or any(
        item.display_name == "林晚" for item in v4_context.speakers.speakers
    )

    # 幂等：重复迁移复用上次结果
    result2 = V4ProjectService.migrate_to_v4("旧书")
    assert result2.reused_existing is True
    assert result2.project_path == result.project_path

    # 混合列表同时包含 V3 / V4
    names = {item.name for item in V4ProjectService.scan_projects()}
    assert "旧书" in names and "旧书-v4" in names
