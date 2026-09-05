"""GPU-free coverage for independent TTS performance settings and cache."""
from __future__ import annotations

import json
import sys
import types

from lib import config, tts_engine
from lib.tts_profile import profile_matches, resolve_profile
from ui import settings_handlers


def test_performance_defaults_match_the_two_production_lanes():
    assert config.get_tts_performance(data={}) == {
        "tts2": {
            "cuda_kernel": True,
            "gpt_accel": False,
            "s2mel_compile": False,
            "conditioning_cache": False,
        },
        "tts25": {"gpt_accel": False},
    }


def test_settings_normalize_cuda_kernel_but_preserve_explicit_frozen_snapshot():
    raw = {
        "tts_performance": {
            "tts2": {"cuda_kernel": False},
            "tts25": {"gpt_accel": True},
        },
        config.INDEXTTS25_GPT_ACCEL_CONFIG_KEY: False,
    }

    settings = config.get_tts_performance(data=raw)
    assert settings["tts2"]["cuda_kernel"] is True
    assert settings["tts25"]["gpt_accel"] is True

    frozen = resolve_profile({
        "engine_version": "2",
        "performance": {
            "cuda_kernel": False,
            "gpt_accel": False,
            "s2mel_compile": False,
            "conditioning_cache": False,
        },
    })
    assert frozen["performance"]["cuda_kernel"] is False


def test_merging_settings_rewrites_legacy_cuda_kernel_false_to_true():
    merged = config.merge_tts_performance(
        {"tts_performance": {"tts2": {"cuda_kernel": False}}},
        {config.TTS25_PERFORMANCE_KEY: {"gpt_accel": False}},
    )

    assert merged[config.TTS_PERFORMANCE_CONFIG_KEY]["tts2"]["cuda_kernel"] is True


def test_legacy_flat_v25_true_is_still_a_migration_fallback():
    settings = config.get_tts_performance(data={
        config.INDEXTTS25_GPT_ACCEL_CONFIG_KEY: True,
    })

    assert settings[config.TTS25_PERFORMANCE_KEY]["gpt_accel"] is True


def test_settings_callback_always_requests_automatic_cuda_kernel():
    performance = settings_handlers._performance_from_values(
        False, False, False, False, False
    )

    assert performance[config.TTS2_PERFORMANCE_KEY]["cuda_kernel"] is True
    assert performance[config.TTS25_PERFORMANCE_KEY]["gpt_accel"] is False


def test_profile_performance_isolated_and_part_of_runtime_identity(monkeypatch):
    monkeypatch.setattr(
        "lib.tts_profile._raw_config",
        lambda: {
            "tts_performance": {
                "tts2": {
                    "cuda_kernel": True,
                    "gpt_accel": True,
                    "s2mel_compile": False,
                    "conditioning_cache": True,
                },
                "tts25": {"gpt_accel": False},
            }
        },
    )
    v2 = resolve_profile({"engine_version": "2"})
    v2_changed = resolve_profile({
        "engine_version": "2",
        "performance": {"gpt_accel": False},
    })
    v25 = resolve_profile({"engine_version": "2.5"})

    assert v2["performance"] == {
        "cuda_kernel": True,
        "gpt_accel": True,
        "s2mel_compile": False,
        "conditioning_cache": True,
    }
    assert v25["performance"] == {"gpt_accel": False}
    assert not profile_matches(v2, v2_changed)
    assert v25["performance"] != v2["performance"]


def test_old_flat_v25_setting_migrates_without_cross_lane_changes():
    raw = {
        "ai_provider": {"default_provider": "local"},
        config.INDEXTTS25_GPT_ACCEL_CONFIG_KEY: False,
    }
    merged = config.merge_tts_performance(
        raw,
        {config.TTS2_PERFORMANCE_KEY: {"gpt_accel": True}},
    )

    assert merged["ai_provider"] == {"default_provider": "local"}
    assert merged[config.TTS_PERFORMANCE_CONFIG_KEY]["tts2"]["gpt_accel"] is True
    assert merged[config.TTS_PERFORMANCE_CONFIG_KEY]["tts25"]["gpt_accel"] is False


def test_persisting_one_lane_preserves_the_other_lane(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "ai_provider": {"default_provider": "local"},
        "tts_performance": {
            "tts2": {
                "cuda_kernel": True,
                "gpt_accel": False,
                "s2mel_compile": False,
                "conditioning_cache": False,
            },
            "tts25": {"gpt_accel": False},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(settings_handlers.ConfigRepository, "CONFIG_PATH", str(path))

    settings_handlers._persist_tts_engine_settings(
        settings_handlers.TTS_ENGINE_LEGACY,
        str(tmp_path / "v2"),
        str(tmp_path / "v25"),
        performance={"tts2": {"gpt_accel": True}},
    )
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["ai_provider"]["default_provider"] == "local"
    assert saved["tts_performance"]["tts2"]["gpt_accel"] is True
    assert saved["tts_performance"]["tts25"]["gpt_accel"] is False


def test_active_tts2_toggle_is_saved_and_deferred(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "tts_engine": "legacy",
        "engine_version": "2",
        "model_dir_v2": str(tmp_path / "v2"),
        "model_dir_v25": str(tmp_path / "v25"),
    }), encoding="utf-8")
    monkeypatch.setattr(settings_handlers.ConfigRepository, "CONFIG_PATH", str(config_path))
    record = types.SimpleNamespace(
        task_id="active",
        task_type="synthesis",
        status="running",
        options={"engine_snapshot": {"engine_version": "2"}},
        startup={},
    )
    monkeypatch.setattr(settings_handlers, "_active_tts_tasks", lambda: [record])
    recycle = []
    monkeypatch.setattr(
        settings_handlers,
        "_request_runtime_recycle",
        lambda engine: recycle.append(engine) or "已排队",
    )

    result = settings_handlers.apply_tts2_performance_settings(
        False, True, True, True,
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert "当前任务结束后" in result[0]
    assert recycle == [settings_handlers.TTS_ENGINE_LEGACY]
    assert saved["tts_performance"]["tts2"] == {
        "cuda_kernel": True,
        "gpt_accel": True,
        "s2mel_compile": True,
        "conditioning_cache": True,
    }
    assert saved["tts_performance"]["tts25"]["gpt_accel"] is False


def test_tts2_constructor_routes_all_requested_performance_arguments():
    kwargs = tts_engine.IndexTTS2Backend.constructor_kwargs(
        cfg_path="v2/config.yaml",
        model_dir="v2",
        precision="FP16",
        use_cuda_kernel=True,
        use_deepspeed=False,
        use_accel=True,
        use_torch_compile=True,
    )

    assert kwargs["use_cuda_kernel"] is True
    assert kwargs["use_accel"] is True
    assert kwargs["use_torch_compile"] is True


def test_profile_flags_reach_the_native_tts2_constructor(monkeypatch, tmp_path):
    captured = {}

    class FakeNative:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.device = "cuda:0"
            self.use_cuda_kernel = kwargs["use_cuda_kernel"]
            self.use_torch_compile = kwargs["use_torch_compile"]
            self.gpt = types.SimpleNamespace(accel_engine=object())
            self.s2mel = types.SimpleNamespace(enable_torch_compile=lambda: None)
            for field in tts_engine._CONDITIONING_FIELDS:
                setattr(self, field, None)

        def infer(self, **_kwargs):
            return None

    model_dir = tmp_path / "v2"
    model_dir.mkdir()
    (model_dir / "config.yaml").write_text("version: 2\n", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                empty_cache=lambda: None,
            ),
            compile=lambda *_args, **_kwargs: None,
        ),
    )
    monkeypatch.setattr(
        tts_engine,
        "_prepare_v2_performance",
        lambda requested, _torch: (
            {field: True for field in requested},
            {
                field: tts_engine._pending_performance_status(True)
                for field in requested
            },
        ),
    )
    monkeypatch.setattr(
        tts_engine.IndexTTS2Backend,
        "load_class",
        staticmethod(lambda: FakeNative),
    )

    tts_engine.init_engine(profile=resolve_profile({
        "engine_version": "2",
        "model_dir": str(model_dir),
        "performance": {
            "cuda_kernel": True,
            "gpt_accel": True,
            "s2mel_compile": True,
            "conditioning_cache": True,
        },
    }))

    assert captured["use_cuda_kernel"] is True
    assert captured["use_accel"] is True
    assert captured["use_torch_compile"] is True
    assert tts_engine.get_engine_profile()["effective_performance"] == {
        "cuda_kernel": True,
        "gpt_accel": True,
        "s2mel_compile": True,
        "conditioning_cache": True,
    }
    tts_engine.reset_engine()


def test_tts2_optional_constructor_fallback_cleans_before_one_baseline_retry(
    monkeypatch, tmp_path
):
    events = []

    class FakeNative:
        def __init__(self, **kwargs):
            use_compile = bool(kwargs["use_torch_compile"])
            events.append(("construct", use_compile))
            if use_compile:
                raise RuntimeError("torch.compile triton initialization failed")
            self.device = "cpu"
            self.use_cuda_kernel = False
            self.use_torch_compile = False
            self.gpt = types.SimpleNamespace()
            self.s2mel = types.SimpleNamespace()

        def infer(self, **_kwargs):
            return None

    model_dir = tmp_path / "v2"
    model_dir.mkdir()
    (model_dir / "config.yaml").write_text("version: 2\n", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                empty_cache=lambda: None,
            ),
            compile=lambda *_args, **_kwargs: None,
        ),
    )
    monkeypatch.setattr(
        tts_engine,
        "_prepare_v2_performance",
        lambda requested, _torch: (
            {field: bool(value) for field, value in requested.items()},
            {
                field: tts_engine._pending_performance_status(bool(value))
                for field, value in requested.items()
            },
        ),
    )
    monkeypatch.setattr(
        tts_engine.IndexTTS2Backend,
        "load_class",
        staticmethod(lambda: FakeNative),
    )
    real_reset_engine = tts_engine.reset_engine
    monkeypatch.setattr(tts_engine, "reset_engine", lambda: events.append("cleanup"))
    monkeypatch.setattr(tts_engine.gc, "collect", lambda: events.append("gc"))

    try:
        tts_engine.init_engine(profile=resolve_profile({
            "engine_version": "2",
            "model_dir": str(model_dir),
            "performance": {
                "cuda_kernel": False,
                "gpt_accel": False,
                "s2mel_compile": True,
                "conditioning_cache": False,
            },
        }))

        assert events == [
            ("construct", True),
            "gc",
            "cleanup",
            ("construct", False),
        ]
        profile = tts_engine.get_engine_profile()
        assert profile["effective_performance"]["s2mel_compile"] is False
        assert profile["performance_status"]["s2mel_compile"] == {
            "requested": True,
            "effective": False,
            "state": "unavailable",
            "reason": "init_failed",
        }
    finally:
        real_reset_engine()


def test_legacy_positional_num_beams_slot_is_preserved(monkeypatch):
    engine = _FakeConditioningEngine()
    _install_conditioning_engine(monkeypatch, engine)
    monkeypatch.setattr(tts_engine, "_CONDITIONING_CACHE_ENABLED", False)

    tts_engine.synthesize_segment(
        "text", "speaker.wav", "neutral", 1.0, 1.0, "out.wav", 120, None, 5
    )

    assert engine.last_generation_kwargs["num_beams"] == 5


class _FakeConditioningEngine:
    def __init__(self):
        self.cache_spk_cond = None
        self.cache_s2mel_style = None
        self.cache_s2mel_prompt = None
        self.cache_spk_audio_prompt = None
        self.cache_emo_cond = None
        self.cache_emo_audio_prompt = None
        self.cache_mel = None
        self.speaker_computes = 0
        self.emotion_computes = 0
        self.cache_sizes_at_infer = []
        self.last_generation_kwargs = {}

    def infer(
        self,
        spk_audio_prompt,
        text,
        output_path,
        use_emo_text,
        emo_text,
        emo_alpha,
        max_text_tokens_per_segment,
        emo_audio_prompt=None,
        **_kwargs,
    ):
        del text, output_path, use_emo_text, emo_text, emo_alpha
        del max_text_tokens_per_segment
        self.cache_sizes_at_infer.append(len(tts_engine._CONDITIONING_CACHE))
        self.last_generation_kwargs = dict(_kwargs)
        emotion_ref = emo_audio_prompt or spk_audio_prompt
        if self.cache_spk_cond is None or self.cache_spk_audio_prompt != spk_audio_prompt:
            self.speaker_computes += 1
            self.cache_spk_cond = f"spk:{self.speaker_computes}"
            self.cache_s2mel_style = f"style:{self.speaker_computes}"
            self.cache_s2mel_prompt = f"prompt:{self.speaker_computes}"
            self.cache_spk_audio_prompt = spk_audio_prompt
            self.cache_mel = f"mel:{self.speaker_computes}"
        if self.cache_emo_cond is None or self.cache_emo_audio_prompt != emotion_ref:
            self.emotion_computes += 1
            self.cache_emo_cond = f"emo:{self.emotion_computes}"
            self.cache_emo_audio_prompt = emotion_ref


def _install_conditioning_engine(monkeypatch, engine):
    class FakeOOM(Exception):
        pass

    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                OutOfMemoryError=FakeOOM,
                is_available=lambda: False,
            )
        ),
    )
    monkeypatch.setattr(tts_engine, "_tts", engine)
    monkeypatch.setattr(tts_engine, "_ENGINE_PROFILE", {
        "engine_version": "2",
        "engine_identity": "indextts:2",
        "model_identity": "fake",
        "precision": "FP16",
        "performance": {
            "cuda_kernel": True,
            "gpt_accel": False,
            "s2mel_compile": False,
            "conditioning_cache": True,
        },
    })
    monkeypatch.setattr(tts_engine, "_CONDITIONING_CACHE_ENABLED", True)
    tts_engine._CONDITIONING_CACHE.clear()
    tts_engine._CAPABILITY_ENGINE_REF = None
    tts_engine._INFER_PARAM_NAMES = frozenset()
    tts_engine._INFER_HAS_VAR_KEYWORD = False


def test_conditioning_cache_hits_lru_and_file_identity(monkeypatch, tmp_path):
    engine = _FakeConditioningEngine()
    _install_conditioning_engine(monkeypatch, engine)
    speaker_a = tmp_path / "speaker-a.wav"
    speaker_b = tmp_path / "speaker-b.wav"
    speaker_a.write_bytes(b"a")
    speaker_b.write_bytes(b"b")

    for speaker in (speaker_a, speaker_b, speaker_a):
        tts_engine.synthesize_segment("text", str(speaker), output_path="out.wav")
    assert engine.speaker_computes == 2
    assert len(tts_engine._CONDITIONING_CACHE) == 2

    speaker_a.write_bytes(b"changed")
    tts_engine.synthesize_segment("text", str(speaker_a), output_path="out.wav")
    assert engine.speaker_computes == 3

    for index in range(tts_engine.CONDITIONING_CACHE_MAXSIZE + 1):
        path = tmp_path / f"speaker-{index}.wav"
        path.write_bytes(str(index).encode())
        tts_engine.synthesize_segment("text", str(path), output_path="out.wav")
    assert len(tts_engine._CONDITIONING_CACHE) == tts_engine.CONDITIONING_CACHE_MAXSIZE
    computes = engine.speaker_computes
    tts_engine.synthesize_segment("text", str(speaker_a), output_path="out.wav")
    assert engine.speaker_computes == computes + 1


def test_conditioning_cache_evicts_before_fifth_infer(monkeypatch, tmp_path):
    engine = _FakeConditioningEngine()
    _install_conditioning_engine(monkeypatch, engine)

    for index in range(tts_engine.CONDITIONING_CACHE_MAXSIZE):
        speaker = tmp_path / f"speaker-{index}.wav"
        speaker.write_bytes(str(index).encode())
        tts_engine.synthesize_segment("text", str(speaker), output_path="out.wav")

    assert len(tts_engine._CONDITIONING_CACHE) == tts_engine.CONDITIONING_CACHE_MAXSIZE
    fifth = tmp_path / "speaker-4.wav"
    fifth.write_bytes(b"4")
    tts_engine.synthesize_segment("text", str(fifth), output_path="out.wav")

    assert engine.cache_sizes_at_infer[-1] == tts_engine.CONDITIONING_CACHE_MAXSIZE - 1
    assert len(tts_engine._CONDITIONING_CACHE) == tts_engine.CONDITIONING_CACHE_MAXSIZE


def test_conditioning_cache_separates_emotion_reference_and_clears(monkeypatch, tmp_path):
    engine = _FakeConditioningEngine()
    _install_conditioning_engine(monkeypatch, engine)
    speaker = tmp_path / "speaker.wav"
    emotion_a = tmp_path / "emotion-a.wav"
    emotion_b = tmp_path / "emotion-b.wav"
    speaker.write_bytes(b"speaker")
    emotion_a.write_bytes(b"a")
    emotion_b.write_bytes(b"b")

    tts_engine.synthesize_segment(
        "text", str(speaker), emo_audio_prompt=str(emotion_a), output_path="out.wav"
    )
    tts_engine.synthesize_segment(
        "text", str(speaker), emo_audio_prompt=str(emotion_b), output_path="out.wav"
    )
    tts_engine.synthesize_segment(
        "text", str(speaker), emo_audio_prompt=str(emotion_a), output_path="out.wav"
    )
    assert engine.emotion_computes == 2
    assert len(tts_engine._CONDITIONING_CACHE) == 2

    tts_engine.reset_engine()
    assert not tts_engine._CONDITIONING_CACHE
    assert tts_engine._CONDITIONING_CACHE_ENABLED is False


def test_conditioning_cache_missing_upstream_fields_falls_back(monkeypatch):
    class BaselineEngine:
        def infer(self, **_kwargs):
            return None

    _install_conditioning_engine(monkeypatch, BaselineEngine())
    tts_engine.synthesize_segment("text", "speaker.wav", output_path="out.wav")
    assert not tts_engine._CONDITIONING_CACHE


def test_conditioning_cache_off_keeps_upstream_one_slot_behavior(monkeypatch, tmp_path):
    engine = _FakeConditioningEngine()
    _install_conditioning_engine(monkeypatch, engine)
    monkeypatch.setattr(tts_engine, "_CONDITIONING_CACHE_ENABLED", False)
    speaker_a = tmp_path / "speaker-a.wav"
    speaker_b = tmp_path / "speaker-b.wav"
    speaker_a.write_bytes(b"a")
    speaker_b.write_bytes(b"b")

    for speaker in (speaker_a, speaker_b, speaker_a):
        tts_engine.synthesize_segment("text", str(speaker), output_path="out.wav")
    assert engine.speaker_computes == 3
    assert not tts_engine._CONDITIONING_CACHE
