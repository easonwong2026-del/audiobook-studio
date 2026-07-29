from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from domain.v4.production import (
    PerformanceOverrides,
    PronunciationRules,
    TextLimits,
    TtsProfile,
    VoiceBinding,
    VoiceBindings,
)
from repositories.audio_cache_repository import AudioCacheRepository
from repositories.runtime_repository import RuntimeRepository
from services.chapter_assembler import ChapterAssembler
from services.invalidation_service import InvalidationService
from services.source_segmenter import SourceSegmenter
from services.synthesis_executor import SynthesisExecutor
from services.synthesis_planner import SynthesisPlanner
from tts.base_adapter import SynthesisOutput, TtsOutOfMemoryError
from tts.indextts2_adapter import IndexTTS2Adapter
from tts.text_measurement import CharacterMeasurer


def _wav(path: Path, *, rate=22050, channels=1, frames=220):
    data = np.full((frames, channels), 1000, dtype=np.int16)
    if channels == 1:
        data = data[:, 0]
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, rate, data)
    return path


def _profile(**runtime):
    return TtsProfile(
        profile_id="fake",
        engine="fake",
        limits=TextLimits(20, 40, 60, 4, metric="characters"),
        runtime_options={
            "min_retry_tokens": 2,
            "oom_split_ratio": 0.5,
            "restart_engine_after_tasks": 100,
            **runtime,
        },
    )


def _plan(text, profile=None):
    segmented = SourceSegmenter().segment(text)
    voices = VoiceBindings(
        {"narrator": VoiceBinding("voice_narrator", "voice-fingerprint")}
    )
    plan = SynthesisPlanner(CharacterMeasurer()).plan(
        text,
        segmented.script,
        segmented.speakers,
        voices,
        PerformanceOverrides(),
        PronunciationRules(),
        profile or _profile(),
    ).plan
    return plan


class FakeRuntimeAdapter:
    def __init__(self, fail_over=0):
        self.fail_over = fail_over
        self.calls = []
        self.closed = 0

    def synthesize(self, task, profile, output_path):
        self.calls.append(task["task_id"])
        if len(task["actual_text"]) > self.fail_over > 0:
            raise TtsOutOfMemoryError("fake OOM")
        _wav(output_path)
        return SynthesisOutput(output_path)

    def close(self):
        self.closed += 1


def _executor(tmp_path, plan, adapter):
    runtime = RuntimeRepository(tmp_path / "runtime/runtime.db")
    runtime.initialize()
    InvalidationService.sync_runtime(runtime, None, plan)
    cache = AudioCacheRepository(runtime.path, tmp_path)
    return (
        SynthesisExecutor(
            runtime, cache, adapter, CharacterMeasurer(), tmp_path
        ),
        runtime,
        cache,
    )


def test_executor_completes_tasks_and_validates_cache(tmp_path):
    plan = _plan("正文。")
    adapter = FakeRuntimeAdapter()
    executor, runtime, cache = _executor(tmp_path, plan, adapter)
    first = executor.run(_profile())
    assert first.completed == 1
    assert runtime.task_counts()["completed"] == 1
    key = plan.tasks[0].input_fingerprint
    cached = cache.lookup(key)
    assert cached is not None
    cached.write_bytes(b"corrupt")
    assert cache.lookup(key) is None


def test_executor_uses_cache_without_calling_adapter(tmp_path):
    plan = _plan("正文。")
    adapter = FakeRuntimeAdapter()
    executor, runtime, _ = _executor(tmp_path, plan, adapter)
    executor.run(_profile())
    with sqlite3.connect(runtime.path) as connection:
        connection.execute(
            "UPDATE synthesis_tasks SET status = 'pending', output_path = NULL"
        )
        connection.commit()
    second_adapter = FakeRuntimeAdapter()
    cached_executor = SynthesisExecutor(
        runtime,
        AudioCacheRepository(runtime.path, tmp_path),
        second_adapter,
        CharacterMeasurer(),
        tmp_path,
    )
    summary = cached_executor.run(_profile())
    assert summary.cache_hits == 1
    assert second_adapter.calls == []


def test_oom_splits_only_current_task_and_completes_children(tmp_path):
    plan = _plan("很长的一段旁白需要拆分。")
    adapter = FakeRuntimeAdapter(fail_over=5)
    executor, runtime, _ = _executor(tmp_path, plan, adapter)
    summary = executor.run(_profile())
    counts = runtime.task_counts()
    assert summary.split_parents >= 1
    assert counts["skipped"] >= 1
    assert counts["completed"] >= 2
    assert runtime.resolved_audio_paths(plan.tasks[0].task_id)


def test_cancel_stops_before_claiming_new_task(tmp_path):
    plan = _plan("正文。")
    adapter = FakeRuntimeAdapter()
    executor, runtime, _ = _executor(tmp_path, plan, adapter)
    result = executor.run(_profile(), should_cancel=lambda: True)
    assert result.cancelled is True
    assert runtime.task_counts()["pending"] == 1


def test_chapter_assembler_normalizes_rate_channels_and_writes_fingerprint(tmp_path):
    small_profile = replace(
        _profile(),
        limits=TextLimits(3, 4, 6, 2, metric="characters"),
    )
    plan = _plan("第一段。第二段。", small_profile)
    runtime = RuntimeRepository(tmp_path / "runtime/runtime.db")
    runtime.initialize()
    InvalidationService.sync_runtime(runtime, None, plan)
    for index, task in enumerate(plan.tasks):
        path = _wav(
            tmp_path / f"audio/chunks/{index}.wav",
            rate=22050 if index == 0 else 44100,
            channels=1 if index == 0 else 2,
        )
        runtime.complete_task(
            task.task_id, path.relative_to(tmp_path).as_posix()
        )
    result = ChapterAssembler(runtime, tmp_path).assemble(
        plan.tasks[0].chapter_id, plan.tasks
    )
    rate, data = wavfile.read(result.path)
    assert rate == 22050
    assert data.ndim == 1
    assert result.fingerprint
    assert result.duration_seconds > 0
    with sqlite3.connect(runtime.path) as connection:
        stored = connection.execute(
            "SELECT fingerprint FROM chapter_outputs WHERE chapter_id = ?",
            (plan.tasks[0].chapter_id,),
        ).fetchone()
    assert stored == (result.fingerprint,)


def test_indextts_adapter_keeps_one_engine_and_uses_actual_signature(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.yaml").write_text("test", encoding="utf-8")

    class FakeEngine:
        instances = 0

        def __init__(self, cfg_path, model_dir, use_fp16):
            self.__class__.instances += 1

        def infer(self, spk_audio_prompt, text, output_path, use_emo_text):
            _wav(Path(output_path))

    adapter = IndexTTS2Adapter(
        model,
        lambda _voice: tmp_path / "voice.wav",
        engine_class=FakeEngine,
    )
    profile = replace(_profile(), options={"fp16": True})
    task = {"voice_id": "voice", "actual_text": "正文"}
    adapter.synthesize(task, profile, tmp_path / "one.wav")
    adapter.synthesize(task, profile, tmp_path / "two.wav")
    assert FakeEngine.instances == 1


def test_indextts_adapter_classifies_oom_without_leaking_source(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.yaml").write_text("test", encoding="utf-8")

    class OomEngine:
        def __init__(self, cfg_path, model_dir, use_fp16):
            pass

        def infer(self, spk_audio_prompt, text, output_path):
            raise RuntimeError("CUDA out of memory")

    adapter = IndexTTS2Adapter(
        model,
        lambda _voice: tmp_path / "voice.wav",
        engine_class=OomEngine,
    )
    with pytest.raises(TtsOutOfMemoryError, match="out of memory"):
        adapter.synthesize(
            {"voice_id": "voice", "actual_text": "不应出现在错误中的原文"},
            replace(_profile(), options={"fp16": True}),
            tmp_path / "oom.wav",
        )
