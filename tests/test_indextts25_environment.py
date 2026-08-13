import json
import sys
from types import SimpleNamespace

from lib import environment
from lib.tts_model_layout import resolve_model_config_path
from services import environment_diagnostics as diagnostics


def _item(report, name):
    return next(item for item in report["checks"] if item["name"] == name)


def _write_v25_bundle(model_dir, *, config_name="config_v2_5.yaml"):
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / config_name).write_text(
        """gpt_checkpoint: gpt.pth
w2v_stat: wav2vec2bert_stats.pt
s2mel_checkpoint: s2mel.pth
emo_matrix: feat2.pt
spk_matrix: feat1.pt
qwen_emo_path: qwen0.6bemo4-merge/
version: 2.5
""",
        encoding="utf-8",
    )
    for name in (
        "gpt.pth",
        "s2mel.pth",
        "codec.pth",
        "feat1.pt",
        "feat2.pt",
        "wav2vec2bert_stats.pt",
        "multilingual_zh_ja_yue_char_del.tiktoken",
    ):
        (model_dir / name).write_bytes(b"stub")
    (model_dir / "qwen0.6bemo4-merge").mkdir()
    (model_dir / "qwen0.6bemo4-merge" / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "hf_cache" / "w2v-bert-2.0").mkdir(parents=True)
    (model_dir / "hf_cache" / "w2v-bert-2.0" / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "hf_cache" / "campplus_cn_common.bin").write_bytes(b"stub")
    (model_dir / "hf_cache" / "bigvgan").mkdir(parents=True)
    (model_dir / "hf_cache" / "bigvgan" / "config.json").write_text("{}", encoding="utf-8")


def test_engine_and_version_environment_precede_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        environment._cfg,
        "_read_config",
        lambda: {"engine": "indextts2", "engine_version": "v2"},
    )
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ENGINE", "IndexTTS")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ENGINE_VERSION", "2.5")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V2", str(tmp_path / "v2"))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V25", str(tmp_path / "v25"))

    selected = environment.resolve_engine_selection()
    dirs = environment.resolve_model_directories()
    assert selected.engine == "indextts"
    assert selected.version == "v2.5"
    assert selected.engine_source == "environment"
    assert selected.version_source == "environment"
    assert dirs["v2"]["path"] == str(tmp_path / "v2")
    assert dirs["v2.5"]["path"] == str(tmp_path / "v25")


def test_environment_diagnostics_defaults_to_recommended_when_no_legacy_config(monkeypatch):
    monkeypatch.setattr(environment, "_read_environment_config", lambda: {})
    monkeypatch.setattr(environment, "_auto_model_dir", lambda _version: None)
    selected = environment.resolve_engine_selection()
    assert selected.version == "v2.5"
    assert selected.version_source == "default"


def test_checkpoint_check_is_local_and_reports_missing_files(tmp_path):
    model_dir = tmp_path / "checkpoints-v2.5"
    model_dir.mkdir()
    (model_dir / "config_v2_5.yaml").write_text("version: 2.5\n", encoding="utf-8")
    (model_dir / "gpt.pth").write_bytes(b"stub")

    state = environment.model_checkpoint_state("v2.5", model_dir)
    assert state["directory"] is True
    assert state["config_name"] == "config_v2_5.yaml"
    assert "s2mel checkpoint (config)" in state["missing_required"]
    assert "gpt checkpoint (config)" in state["missing_required"]
    assert environment.detect_model_version(model_dir) == "v2.5"


def test_checkpoint_check_accepts_the_v25_core_layout(tmp_path):
    model_dir = tmp_path / "checkpoints-v2.5"
    _write_v25_bundle(model_dir)

    state = environment.model_checkpoint_state("v2.5", model_dir)
    assert state["missing_required"] == []
    assert "codec.pth" in state["present_required"]
    assert "dvae.pth" not in state["missing_required"]
    assert "campplus.onnx" not in state["missing_required"]


def test_v25_config_resolution_prefers_versioned_then_compatible_names(tmp_path):
    model_dir = tmp_path / "checkpoints_25"
    model_dir.mkdir()
    assert resolve_model_config_path("2.5", model_dir) is None
    (model_dir / "config.yml").write_text("version: 2.5\n", encoding="utf-8")
    assert resolve_model_config_path("2.5", model_dir).name == "config.yml"
    (model_dir / "config.yaml").write_text("version: 2.5\n", encoding="utf-8")
    assert resolve_model_config_path("2.5", model_dir).name == "config.yaml"
    (model_dir / "config_v2_5.yaml").write_text("version: 2.5\n", encoding="utf-8")
    assert resolve_model_config_path("2.5", model_dir).name == "config_v2_5.yaml"


def test_v2_config_resolution_and_checkpoint_behavior_are_unchanged(tmp_path):
    model_dir = tmp_path / "checkpoints"
    model_dir.mkdir()
    (model_dir / "config.yml").write_text("version: 2\n", encoding="utf-8")
    assert resolve_model_config_path("2", model_dir).name == "config.yml"
    state = environment.model_checkpoint_state("v2", model_dir)
    assert "config.yaml" not in state["missing_required"]
    assert "dvae.pth" in state["missing_required"]


def test_official_v25_bundle_is_ready_in_environment_diagnostics(monkeypatch, tmp_path):
    model_dir = tmp_path / "checkpoints_25"
    _write_v25_bundle(model_dir)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ENGINE_VERSION", "2.5")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V25", str(model_dir))
    report = diagnostics.run_environment_diagnostics()
    item = _item(report, "模型目录 v2.5")
    assert item["status"] == "ok"
    assert item["details"]["config_name"] == "config_v2_5.yaml"
    assert item["details"]["missing_files"] == []


def test_diagnostics_redacts_paths_and_exposes_selected_model_fields(monkeypatch, tmp_path):
    model_dir = tmp_path / "secret" / "v2"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr(diagnostics.config, "get_model_dir", lambda: str(model_dir))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V2", str(model_dir))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_MODEL_DIR_V25", str(tmp_path / "v25"))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ENGINE_VERSION", "v2")

    report = diagnostics.run_environment_diagnostics()
    rendered = json.dumps(report, ensure_ascii=False)
    assert str(model_dir) not in rendered
    assert report["selected_version"] == "v2"
    selected = _item(report, "选中版本匹配")
    assert selected["details"]["selected_version"] == "v2"
    assert "missing_files" in selected["details"]


def test_torch_diagnostics_include_cuda_version_gpu_and_bf16(monkeypatch):
    fake_torch = SimpleNamespace(
        __version__="2.7.0",
        version=SimpleNamespace(cuda="12.8"),
        cuda=SimpleNamespace(
            is_available=lambda: True,
            get_device_name=lambda _: "NVIDIA RTX 5070 Ti",
            is_bf16_supported=lambda: True,
        ),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    item = _item(diagnostics.run_environment_diagnostics(), "Torch / CUDA")
    assert item["details"]["torch_version"] == "2.7.0"
    assert item["details"]["cuda_version"] == "12.8"
    assert item["details"]["gpu_name"] == "NVIDIA RTX 5070 Ti"
    assert item["details"]["bf16_capability"] is True
