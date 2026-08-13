import json
import sys
from types import SimpleNamespace

from lib import environment
from services import environment_diagnostics as diagnostics


def _item(report, name):
    return next(item for item in report["checks"] if item["name"] == name)


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
    (model_dir / "config.yaml").write_text("model_version: 2.5\n", encoding="utf-8")
    (model_dir / "gpt.pth").write_bytes(b"stub")

    state = environment.model_checkpoint_state("v2.5", model_dir)
    assert state["directory"] is True
    assert "s2mel.pth" in state["missing_required"]
    assert "gpt.pth" in state["present_required"]
    assert environment.detect_model_version(model_dir) == "v2.5"


def test_checkpoint_check_accepts_the_v25_core_layout(tmp_path):
    model_dir = tmp_path / "checkpoints-v2.5"
    model_dir.mkdir()
    for name in (
        "config.yaml",
        "gpt.pth",
        "s2mel.pth",
        "dvae.pth",
        "bpe.model",
        "feat1.pt",
        "feat2.pt",
        "campplus.onnx",
        "wav2vec2bert_stats.pt",
    ):
        (model_dir / name).write_bytes(b"stub")

    state = environment.model_checkpoint_state("v2.5", model_dir)
    assert state["missing_required"] == []


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
