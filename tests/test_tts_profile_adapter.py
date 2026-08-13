"""GPU-free acceptance tests for the model-independent engine adapter boundary."""
from __future__ import annotations

import sys
import types

from lib import directed_synthesis, segment_cache, tts_engine
from lib.tts_profile import public_profile, resolve_profile


def test_profile_freezes_version_model_fingerprint_and_precision(tmp_path):
    legacy = tmp_path / "legacy"
    recommended = tmp_path / "recommended"
    legacy.mkdir()
    recommended.mkdir()
    (legacy / "config.yaml").write_text("version: 2\n", encoding="utf-8")
    (recommended / "config.yaml").write_text("version: 2.5\n", encoding="utf-8")

    v2 = resolve_profile({"engine_version": "2", "model_dir": str(legacy)})
    v25 = resolve_profile({"engine_version": "2.5", "model_dir": str(recommended)})
    assert v2["engine_identity"] == "indextts:2"
    assert v25["engine_identity"] == "indextts:2.5"
    assert v2["cache_identity"] != v25["cache_identity"]
    assert str(legacy) not in public_profile(v2).values()


def test_model_identity_is_path_independent_but_changes_with_config_or_checkpoint_size(tmp_path):
    first = tmp_path / "first" / "checkpoints_25"
    second = tmp_path / "second" / "checkpoints_25"
    for root in (first, second):
        root.mkdir(parents=True)
        (root / "config_v2_5.yaml").write_text(
            "gpt_checkpoint: gpt.pth\nversion: 2.5\n", encoding="utf-8"
        )
        (root / "gpt.pth").write_bytes(b"same-checkpoint")
    assert resolve_profile({"engine_version": "2.5", "model_dir": str(first)})["model_identity"] == resolve_profile(
        {"engine_version": "2.5", "model_dir": str(second)}
    )["model_identity"]

    (second / "gpt.pth").write_bytes(b"different-size")
    assert resolve_profile({"engine_version": "2.5", "model_dir": str(first)})["model_identity"] != resolve_profile(
        {"engine_version": "2.5", "model_dir": str(second)}
    )["model_identity"]

    (second / "gpt.pth").write_bytes(b"same-checkpoint")
    (second / "config_v2_5.yaml").write_text(
        "gpt_checkpoint: gpt.pth\nversion: 2.5\nchanged: true\n", encoding="utf-8"
    )
    assert resolve_profile({"engine_version": "2.5", "model_dir": str(first)})["model_identity"] != resolve_profile(
        {"engine_version": "2.5", "model_dir": str(second)}
    )["model_identity"]


def test_model_fingerprint_without_explicit_version_reads_v25_generic_config(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        root.mkdir()
        (root / "config.yaml").write_text(
            "gpt_checkpoint: gpt.pth\nversion: 2.5\n", encoding="utf-8"
        )
        (root / "gpt.pth").write_bytes(b"same")
    from lib.tts_profile import model_fingerprint

    assert model_fingerprint(first) == model_fingerprint(second)

def test_clean_profile_defaults_to_recommended_v25_without_legacy_config(monkeypatch):
    monkeypatch.delenv("AUDIOBOOK_STUDIO_ENGINE_VERSION", raising=False)
    monkeypatch.delenv("AUDIOBOOK_STUDIO_VERSION", raising=False)
    monkeypatch.delenv("AUDIOBOOK_STUDIO_MODEL_DIR", raising=False)
    monkeypatch.delenv("AUDIOBOOK_STUDIO_MODEL_DIR_V2", raising=False)
    monkeypatch.delenv("AUDIOBOOK_STUDIO_MODEL_DIR_V25", raising=False)
    monkeypatch.setattr("lib.tts_profile._raw_config", lambda: {"ai_provider": {}})
    assert resolve_profile({})["engine_identity"] == "indextts:2.5"


def test_engine_identity_changes_segment_cache_key_without_changing_legacy_formula():
    legacy = segment_cache.segment_cache_key("1-001", "neutral")
    v2 = segment_cache.segment_cache_key("1-001", "neutral", engine_identity="indextts:2|model-a|FP16")
    v25 = segment_cache.segment_cache_key("1-001", "neutral", engine_identity="indextts:2.5|model-b|BF16")
    assert legacy != v2
    assert v2 != v25


def test_engine_aware_cache_never_falls_back_to_legacy_wav(tmp_path):
    legacy = tmp_path / "1-001.wav"
    legacy.write_bytes(b"legacy")

    assert segment_cache.find_segment_wav(
        str(tmp_path), "1-001", "text", "speaker", "neutral",
        engine_identity="indextts:2.5|model-b|BF16",
    ) is None
    assert not segment_cache.has_segment_wav(
        str(tmp_path), "1-001", engine_identity="indextts:2.5|model-b|BF16"
    )


def test_v25_adapter_maps_canonical_fields_and_records_explicit_report(monkeypatch, tmp_path):
    captured = {}

    class FakeV25:
        def infer(self, spk_audio_prompt, text, output_path, lang, **kwargs):
            captured.update(text=text, lang=lang, kwargs=kwargs)

    monkeypatch.setattr(tts_engine, "_tts", FakeV25())
    monkeypatch.setattr(tts_engine, "_ENGINE_PROFILE", {
        "engine_identity": "indextts:2.5",
        "engine_version": "2.5",
        "model_identity": "model-b",
        "precision": "BF16",
    })
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)
    output = str(tmp_path / "out.wav")
    tts_engine.synthesize_segment(
        "银行里行走",
        "speaker.wav",
        speech_rate=1.25,
        pinyin_hints=[
            {"text": "行", "pinyin": "xing2", "start": 1},
            {"text": "行", "pinyin": "hang2", "start": 3},
        ],
        output_path=output,
    )
    assert "<行|XING2>" in captured["text"]
    assert "<行|HANG2>" in captured["text"]
    assert captured["lang"] == "ZH"
    assert captured["kwargs"]["duration_factor"] == 0.8
    report = tts_engine.last_adapter_report()
    assert report["contract"] == "Structured Script JSON"
    assert "speech_rate" in [item["field"] for item in report["approximated"]]
    assert report["unsupported"] == []


def test_native_backend_constructor_baselines_are_version_specific():
    v2 = tts_engine.IndexTTS2Backend.constructor_kwargs(
        cfg_path="v2/config.yaml",
        model_dir="v2",
        precision="FP16",
        use_cuda_kernel=True,
        use_deepspeed=True,
        use_accel=True,
    )
    v25 = tts_engine.IndexTTS25Backend.constructor_kwargs(
        cfg_path="v25/config.yaml",
        model_dir="v25",
        precision="BF16",
    )

    assert v2["use_fp16"] is True
    assert v2["use_cuda_kernel"] is True
    assert v2["use_deepspeed"] is True
    assert v25 == {
        "cfg_path": "v25/config.yaml",
        "model_dir": "v25",
        "use_bf16": True,
        "use_cuda_kernel": False,
        "use_deepspeed": False,
        "use_accel": False,
        "use_torch_compile": False,
        "use_qwen_emo": True,
    }


def test_init_engine_selects_native_version_and_precision(monkeypatch, tmp_path):
    captured: list[dict] = []

    class FakeNative:
        def __init__(self, **kwargs):
            captured.append(dict(kwargs))

        def infer(self, spk_audio_prompt, text, output_path, **kwargs):
            return output_path

    monkeypatch.setattr(
        tts_engine.IndexTTS2Backend,
        "load_class",
        staticmethod(lambda: FakeNative),
    )
    monkeypatch.setattr(
        tts_engine.IndexTTS25Backend,
        "load_class",
        staticmethod(lambda: FakeNative),
    )
    # Constructor selection is GPU-free; the real production path still
    # requires torch, so provide only the import sentinel in this test.
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace())
    for version, expected in (
        ("2", "use_fp16"),
        ("2.5", "use_bf16"),
    ):
        model_dir = tmp_path / version
        model_dir.mkdir()
        config_name = "config_v2_5.yaml" if version == "2.5" else "config.yaml"
        (model_dir / config_name).write_text(f"version: {version}\n", encoding="utf-8")
        tts_engine.reset_engine()
        tts_engine.init_engine(
            profile=resolve_profile({
                "engine_version": version,
                "model_dir": str(model_dir),
            })
        )
        kwargs = captured[-1]
        assert kwargs[expected] is True
        assert kwargs["cfg_path"].endswith(config_name)
        if version == "2.5":
            assert kwargs["use_cuda_kernel"] is False
            assert kwargs["use_deepspeed"] is False
            assert kwargs["use_accel"] is False
            assert kwargs["use_torch_compile"] is False
        else:
            assert "use_bf16" not in kwargs
    tts_engine.reset_engine()


def test_v25_local_bundle_guard_uses_config_driven_layout(tmp_path):
    bundle = tmp_path / "checkpoints_25"
    bundle.mkdir()
    (bundle / "config_v2_5.yaml").write_text(
        "gpt_checkpoint: gpt.pth\n"
        "s2mel_checkpoint: s2mel.pth\n"
        "spk_matrix: feat1.pt\n"
        "emo_matrix: feat2.pt\n"
        "w2v_stat: wav2vec2bert_stats.pt\n"
        "bpe_model: bpe.model\n"
        "qwen_emo_path: qwen/\n",
        encoding="utf-8",
    )
    for name in ("gpt.pth", "s2mel.pth", "codec.pth", "feat1.pt", "feat2.pt", "wav2vec2bert_stats.pt", "bpe.model"):
        (bundle / name).write_bytes(b"x")
    (bundle / "qwen").mkdir()
    (bundle / "qwen" / "config.json").write_text("{}", encoding="utf-8")
    (bundle / "hf_cache" / "w2v-bert-2.0").mkdir(parents=True)
    (bundle / "hf_cache" / "w2v-bert-2.0" / "config.json").write_text("{}", encoding="utf-8")
    (bundle / "hf_cache" / "campplus_cn_common.bin").write_bytes(b"x")
    (bundle / "hf_cache" / "bigvgan").mkdir(parents=True)
    (bundle / "hf_cache" / "bigvgan" / "config.json").write_text("{}", encoding="utf-8")
    assert tts_engine._looks_like_local_v25_bundle(str(bundle)) is True
    (bundle / "codec.pth").unlink()
    assert tts_engine._looks_like_local_v25_bundle(str(bundle)) is False


def test_v25_emotion_mapping_reports_approximation_for_non_native_labels():
    _use_emo, emo_text, direct = tts_engine.IndexTTS25Backend.emotion_control("happy")
    _use_emo_approx, approx_text, approximate = tts_engine.IndexTTS25Backend.emotion_control("excited")

    assert emo_text == "happy"
    assert "emotion" in direct["mapped"]
    assert approx_text == "happy"
    assert approximate["approximated"][0]["field"] == "emotion"


def test_v2_adapter_does_not_leak_v25_only_arguments(monkeypatch, tmp_path):
    captured = {}

    class FakeV2:
        def infer(self, spk_audio_prompt, text, output_path, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(tts_engine, "_tts", FakeV2())
    monkeypatch.setattr(tts_engine, "_ENGINE_PROFILE", {
        "engine_identity": "indextts:2",
        "engine_version": "2",
        "model_identity": "model-a",
        "precision": "FP16",
    })
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)
    tts_engine.synthesize_segment(
        "短句", "speaker.wav", speech_rate=1.25,
        pinyin_hints={"句": "ju4"}, output_path=str(tmp_path / "out.wav"),
    )
    assert "duration_factor" not in captured
    assert "lang" not in captured
    report = tts_engine.last_adapter_report()
    assert report["unsupported"]


def test_director_capability_gaps_are_reported_without_mutating_canonical_input(monkeypatch, tmp_path):
    captured = {}

    class FakeV2:
        def infer(self, spk_audio_prompt, text, output_path, **kwargs):
            captured.update(text=text, kwargs=kwargs)

    monkeypatch.setattr(tts_engine, "_tts", FakeV2())
    monkeypatch.setattr(tts_engine, "_ENGINE_PROFILE", resolve_profile({"engine_version": "2"}))
    monkeypatch.setattr(tts_engine, "empty_cache", lambda: None)
    segment = {
        "id": "1-001",
        "text": "保留原始字段",
        "pitch": 3,
        "breath": "normal",
    }
    original = dict(segment)
    directed_synthesis.synthesize(
        segment,
        "speaker.wav",
        str(tmp_path / "out.wav"),
        emotion="neutral",
        emo_alpha=1.0,
        speech_rate=1.0,
        engine=tts_engine,
    )
    assert segment == original
    unsupported = {item["field"] for item in tts_engine.last_adapter_report()["unsupported"]}
    assert {"pitch", "breath"}.issubset(unsupported)
