"""V2 model readiness regression tests (PHASE A).

The official ``indextts.infer_v2`` adapter (2026-08 main) loads gpt / s2mel /
wav2vec2bert / spk / emo / BPE from the native config and unconditionally
loads campplus + semantic codec + w2v-bert + BigVGAN from ``hf_cache/``.
Legacy 1.x filenames (``dvae.pth``, ``campplus.onnx``) are not part of the
real V2 runtime, so the readiness checker must not report a valid bundle as
missing.  These tests pin the corrected contract.
"""
from __future__ import annotations

import pytest

from lib import environment
from lib.tts_model_layout import resolve_model_config_path


def _write_v2_bundle(model_dir, *, include_assets=True, drop_gpt=False):
    """Write the real, Windows-validated Legacy V2 bundle layout."""
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config.yaml").write_text(
        "gpt_checkpoint: gpt.pth\n"
        "w2v_stat: wav2vec2bert_stats.pt\n"
        "s2mel_checkpoint: s2mel.pth\n"
        "emo_matrix: feat2.pt\n"
        "spk_matrix: feat1.pt\n"
        "bpe_model: bpe.model\n"
        "version: 2.0\n",
        encoding="utf-8",
    )
    for name in (
        "gpt.pth",
        "s2mel.pth",
        "wav2vec2bert_stats.pt",
        "feat1.pt",
        "feat2.pt",
        "bpe.model",
    ):
        if name == "gpt.pth" and drop_gpt:
            continue
        (model_dir / name).write_bytes(b"stub")
    if include_assets:
        hf = model_dir / "hf_cache"
        (hf / "w2v-bert-2.0").mkdir(parents=True)
        (hf / "w2v-bert-2.0" / "config.json").write_text("{}", encoding="utf-8")
        (hf / "campplus_cn_common.bin").write_bytes(b"stub")
        (hf / "semantic_codec_model.safetensors").write_bytes(b"stub")
        (hf / "bigvgan").mkdir()
        (hf / "bigvgan" / "config.json").write_text("{}", encoding="utf-8")


def _write_v25_bundle(model_dir):
    """Minimal real v2.5 bundle for cross-version confusion checks."""
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config_v2_5.yaml").write_text(
        "gpt_checkpoint: gpt.pth\n"
        "w2v_stat: wav2vec2bert_stats.pt\n"
        "s2mel_checkpoint: s2mel.pth\n"
        "emo_matrix: feat2.pt\n"
        "spk_matrix: feat1.pt\n"
        "bpe_model: bpe.model\n"
        "qwen_emo_path: qwen0.6bemo4-merge/\n"
        "version: 2.5\n",
        encoding="utf-8",
    )
    for name in (
        "gpt.pth",
        "s2mel.pth",
        "wav2vec2bert_stats.pt",
        "feat1.pt",
        "feat2.pt",
        "multilingual_zh_ja_yue_char_del.tiktoken",
        "codec.pth",
    ):
        (model_dir / name).write_bytes(b"stub")
    (model_dir / "qwen0.6bemo4-merge").mkdir()
    (model_dir / "qwen0.6bemo4-merge" / "config.json").write_text("{}", encoding="utf-8")
    hf = model_dir / "hf_cache"
    (hf / "w2v-bert-2.0").mkdir(parents=True)
    (hf / "w2v-bert-2.0" / "config.json").write_text("{}", encoding="utf-8")
    (hf / "campplus_cn_common.bin").write_bytes(b"stub")
    (hf / "bigvgan").mkdir()
    (hf / "bigvgan" / "config.json").write_text("{}", encoding="utf-8")


class TestV2ReadinessContract:
    def test_real_v2_layout_is_ready(self, tmp_path):
        """A validated Legacy V2 bundle must report missing == []."""
        model_dir = tmp_path / "checkpoints"
        _write_v2_bundle(model_dir)
        state = environment.model_checkpoint_state("v2", model_dir)
        assert state["directory"] is True
        assert state["missing_required"] == []

    def test_missing_core_checkpoint_is_not_ready(self, tmp_path):
        """Dropping a genuinely required checkpoint must report Not Ready."""
        model_dir = tmp_path / "checkpoints"
        _write_v2_bundle(model_dir, drop_gpt=True)
        state = environment.model_checkpoint_state("v2", model_dir)
        assert "gpt checkpoint (config)" in state["missing_required"]

    def test_missing_aux_asset_is_not_ready(self, tmp_path):
        """The adapter always loads campplus; missing it must be Not Ready."""
        model_dir = tmp_path / "checkpoints"
        _write_v2_bundle(model_dir, include_assets=False)
        state = environment.model_checkpoint_state("v2", model_dir)
        assert "hf_cache/campplus_cn_common.bin" in state["missing_required"]
        assert "hf_cache/w2v-bert-2.0" in state["missing_required"]

    def test_v25_layout_is_not_affected(self, tmp_path):
        """The v2.5 contract keeps its config-driven checks."""
        model_dir = tmp_path / "checkpoints-v2.5"
        _write_v25_bundle(model_dir)
        state = environment.model_checkpoint_state("v2.5", model_dir)
        assert state["missing_required"] == []

    def test_v2_and_v25_do_not_cross_match(self, tmp_path):
        """A v2 bundle must not satisfy v2.5 and vice versa."""
        v2 = tmp_path / "v2"
        _write_v2_bundle(v2)
        assert environment.model_checkpoint_state("v2", v2)["missing_required"] == []
        v25_as_v2 = environment.model_checkpoint_state("v2.5", v2)
        assert v25_as_v2["missing_required"] != []

        v25 = tmp_path / "v25"
        _write_v25_bundle(v25)
        assert environment.model_checkpoint_state("v2.5", v25)["missing_required"] == []
        v2_as_v25 = environment.model_checkpoint_state("v2", v25)
        assert v2_as_v25["missing_required"] != []

    def test_legacy_config_model_dir_compatibility_is_preserved(self, monkeypatch, tmp_path):
        """Legacy config ``model_dir`` still selects v2 without a version field."""
        v2_dir = tmp_path / "checkpoints"
        _write_v2_bundle(v2_dir)
        monkeypatch.delenv("AUDIOBOOK_STUDIO_ENGINE_VERSION", raising=False)
        monkeypatch.delenv("AUDIOBOOK_STUDIO_VERSION", raising=False)
        monkeypatch.delenv("AUDIOBOOK_STUDIO_ENGINE", raising=False)
        monkeypatch.setattr(
            environment,
            "_read_environment_config",
            lambda: {"model_dir": str(v2_dir)},
        )
        monkeypatch.setattr(environment, "_auto_model_dir", lambda _version: None)
        selection = environment.resolve_engine_selection()
        assert selection.version == "v2"
        assert selection.version_source == "legacy_model_dir"

    def test_config_yml_alias_still_resolves_for_v2(self, tmp_path):
        """config.yml (not config.yaml) remains a valid v2 config name."""
        model_dir = tmp_path / "checkpoints"
        _write_v2_bundle(model_dir)
        (model_dir / "config.yaml").unlink()
        (model_dir / "config.yml").write_text(
            (model_dir / "config.yml").read_text(encoding="utf-8")
            if (model_dir / "config.yml").exists()
            else "gpt_checkpoint: gpt.pth\nversion: 2\n",
            encoding="utf-8",
        )
        assert resolve_model_config_path("2", model_dir).name == "config.yml"


def test_diagnostics_v2_bundle_is_ok(monkeypatch, tmp_path):
    """Environment diagnostics must report a validated V2 bundle as ok."""
    from services import environment_diagnostics as diagnostics

    model_dir = tmp_path / "checkpoints"
    _write_v2_bundle(model_dir)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ENGINE_VERSION", "v2")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V2", str(model_dir))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V25", str(tmp_path / "v25-missing"))
    report = diagnostics.run_environment_diagnostics()
    item = next(c for c in report["checks"] if c["name"] == "模型目录 v2")
    assert item["status"] == "ok"
    assert item["details"]["missing_files"] == []


def test_diagnostics_missing_core_v2_is_warning(monkeypatch, tmp_path):
    """A V2 bundle missing its core GPT checkpoint stays a warning, not ok."""
    from services import environment_diagnostics as diagnostics

    model_dir = tmp_path / "checkpoints"
    _write_v2_bundle(model_dir, drop_gpt=True)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ENGINE_VERSION", "v2")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V2", str(model_dir))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V25", str(tmp_path / "v25-missing"))
    report = diagnostics.run_environment_diagnostics()
    item = next(c for c in report["checks"] if c["name"] == "模型目录 v2")
    assert item["status"] == "warning"
    assert "gpt checkpoint (config)" in item["details"]["missing_files"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
