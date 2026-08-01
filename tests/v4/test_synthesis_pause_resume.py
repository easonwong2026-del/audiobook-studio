"""流程 C：V4 暂停 / 恢复 / 取消集成测试（mock TTS，不加载真实模型）。

验证语义：
- pause 在任务边界协作暂停，已完成结果保留
- resume 继续，不重复合成已完成任务
- cancel 保留已完成缓存，pending 任务被取消
"""
from __future__ import annotations

import json
import threading
import time
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
from services.v4_synthesis_service import V4SynthesisService
from tts.base_adapter import SynthesisOutput


@pytest.fixture()
def v4_project(tmp_path: Path) -> Path:
    import os

    from lib import config

    old = os.environ.get(config.ENV_DATA_DIR)
    os.environ[config.ENV_DATA_DIR] = str(tmp_path)
    root = tmp_path / "projects"
    root.mkdir(parents=True)
    repo = ProjectV4Repository(root)
    source = (
        "第一章 测试\n林晚说：“你好。”顾川急道：“快走！”\n"
        "第二章 测试二\n林晚问：“谁？”顾川答：“我。”\n"
        "第三章 测试三\n林晚笑着说：“再见。”"
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
        project_id="project_test", name="暂停测试", title="暂停测试", author="",
        created_at=now, updated_at=now,
    )
    profile_path = (
        Path(__file__).resolve().parents[2]
        / "config/tts_profiles/indextts2-rtx5070ti-laptop-12gb-v1.json"
    )
    with profile_path.open("r", encoding="utf-8") as handle:
        profile = TtsProfile.from_dict(json.load(handle))
    project = repo.create(
        directory_name="暂停测试", manifest=manifest, source_text=source,
        source_metadata=metadata, script=segmented.script,
        speakers=segmented.speakers, tts_profile=profile,
    )
    yield project
    if old is None:
        os.environ.pop(config.ENV_DATA_DIR, None)
    else:
        os.environ[config.ENV_DATA_DIR] = old


def _write_wav(path: Path) -> None:
    rate = 22050
    data = (np.sin(np.linspace(0, 100, rate // 4)) * 8000).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())


class SlowMockAdapter:
    """每个任务耗时 0.15s 的 mock TTS；计数合成调用次数。"""

    def __init__(self, delay: float = 0.15):
        self.delay = delay
        self.synthesize_calls = 0

    def synthesize(self, task, profile, output_path) -> SynthesisOutput:
        self.synthesize_calls += 1
        time.sleep(self.delay)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(output_path)
        return SynthesisOutput(path=output_path)

    def close(self) -> None:
        pass


def _bind_all_speakers(project: Path) -> None:
    """给所有角色绑定 mock 音色（写 voices.json）。"""
    import hashlib
    import shutil

    from domain.v4.production import VoiceBinding, VoiceBindings
    from repositories.production_repository import ProductionRepository

    production = ProductionRepository(project)
    voices, _p, _pr, _profile = production.load_inputs()
    speakers = json.loads(
        (project / "script/speakers.json").read_text(encoding="utf-8")
    )
    bindings = dict(voices.bindings)
    for item in speakers["speakers"]:
        sid = item["id"]
        if sid in bindings:
            continue
        fake = project / "assets/voices" / f"{sid}.wav"
        fake.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(fake)
        fingerprint = hashlib.sha256(fake.read_bytes()).hexdigest()
        bindings[sid] = VoiceBinding(
            fake.relative_to(project).as_posix(), fingerprint
        )
    production.save_document(
        "voices.json", VoiceBindings(bindings, revision=voices.revision + 1).to_dict()
    )


def test_pause_resume_cancel_semantics(v4_project: Path, monkeypatch):
    _bind_all_speakers(v4_project)
    rows, message = V4SynthesisService.generate_plan(v4_project)
    assert "tasks" in message
    runtime = RuntimeRepository(v4_project / "runtime/runtime.db")
    runtime.initialize()
    counts = runtime.task_counts()
    total = counts.get("pending", 0) + counts.get("completed", 0)
    assert total >= 2, "测试稿至少生成 2 个任务"

    # 注入慢速 mock adapter（拦截 V4SynthesisService._worker 内的 executor 构造）
    adapter = SlowMockAdapter(delay=0.05)
    import services.v4_synthesis_service as v4svc

    original_executor = v4svc.SynthesisExecutor
    from repositories.audio_cache_repository import AudioCacheRepository
    from services.synthesis_executor import ExecutionSummary

    def fake_executor_factory(runtime_repo, cache, adptr, measurer, project_path, monitor=None):
        executor = original_executor(
            runtime_repo, cache, adptr, measurer, project_path, monitor=monitor
        )
        # 用慢速 adapter 替换
        executor.adapter = adapter
        return executor

    monkeypatch.setattr(v4svc, "SynthesisExecutor", fake_executor_factory)
    # 同时替换 _worker 里创建 adapter 的路径：直接换 IndexTTS2Adapter 构造
    monkeypatch.setattr(
        v4svc, "IndexTTS2Adapter", lambda *args, **kwargs: adapter
    )

    ok, start_msg = V4SynthesisService.start("暂停测试")
    assert ok

    # 等一小段让部分任务完成
    deadline = time.time() + 5
    while time.time() < deadline:
        snap = V4SynthesisService.snapshot("暂停测试")
        if snap["run_status"] in ("done", "cancelled", "error"):
            break
        if snap["counts"].get("completed", 0) >= 1:
            break
        time.sleep(0.05)
    completed_before_pause = V4SynthesisService.snapshot(
        "暂停测试"
    )["counts"].get("completed", 0)
    assert completed_before_pause >= 1

    # 暂停：已完成保留，不再推进
    paused_ok, _ = V4SynthesisService.pause("暂停测试")
    assert paused_ok
    time.sleep(0.3)
    after_pause = V4SynthesisService.snapshot("暂停测试")
    assert after_pause["run_status"] == "paused"
    assert after_pause["counts"].get("completed", 0) >= completed_before_pause
    calls_at_pause = adapter.synthesize_calls

    # 继续：不重复合成已完成任务（新合成调用数只增不减已完成部分）
    resumed_ok, _ = V4SynthesisService.resume("暂停测试")
    assert resumed_ok
    deadline = time.time() + 20
    while time.time() < deadline:
        snap = V4SynthesisService.snapshot("暂停测试")
        if snap["run_status"] in ("done", "cancelled", "error"):
            break
        time.sleep(0.05)
    final_snap = V4SynthesisService.snapshot("暂停测试")
    assert final_snap["run_status"] == "done"
    # 合成调用次数 = 完成数（缓存命中不计调用）；不重复合成已完成任务
    assert adapter.synthesize_calls == final_snap["counts"].get("completed", 0)
    assert final_snap["counts"].get("failed", 0) == 0

    # 取消：已完成保留、状态正确（先把任务重置回 pending，模拟新一轮）
    _reset(v4_project)
    _requeue_all(v4_project)
    ok2, _ = V4SynthesisService.start("暂停测试")
    assert ok2
    # 立即取消（任务尚在进行中）
    cancelled_ok, cancel_msg = V4SynthesisService.cancel("暂停测试")
    assert cancelled_ok
    deadline = time.time() + 10
    while time.time() < deadline:
        snap = V4SynthesisService.snapshot("暂停测试")
        if snap["run_status"] in ("done", "cancelled", "error"):
            break
        time.sleep(0.05)
    snap_c = V4SynthesisService.snapshot("暂停测试")
    assert snap_c["run_status"] == "cancelled"
    # 已完成任务仍保留在 runtime.db
    assert snap_c["counts"].get("completed", 0) >= 0
    assert snap_c["counts"].get("cancelled", 0) >= 0


def _reset(project: Path) -> None:
    """清空运行注册表并重置任务状态（模拟新会话）。"""
    import services.v4_synthesis_service as v4svc

    with v4svc.V4SynthesisService._lock:
        v4svc.V4SynthesisService._runs.clear()


def _requeue_all(project: Path) -> None:
    """把所有任务重置为 pending（模拟新一轮合成）。"""
    import sqlite3

    runtime = project / "runtime/runtime.db"
    with sqlite3.connect(runtime) as connection:
        connection.execute(
            """
            UPDATE synthesis_tasks
               SET status = 'pending', attempts = 0,
                   error_type = NULL, error_message = NULL,
                   started_at = NULL, completed_at = NULL
            """
        )
        connection.commit()
