import json

from services import environment_diagnostics as diagnostics


def _item(report, name):
    return next(item for item in report["checks"] if item["name"] == name)


def test_ffmpeg_exists(monkeypatch, tmp_path):
    binary = tmp_path / "ffmpeg"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(diagnostics.config, "get_ffmpeg_path", lambda: str(binary))
    monkeypatch.setattr(diagnostics.subprocess, "run", lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "ffmpeg version 7.1\n", "stderr": ""})())
    assert _item(diagnostics.run_environment_diagnostics(), "FFmpeg")["status"] == "ok"


def test_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(diagnostics.config, "get_ffmpeg_path", lambda: "definitely-missing-ffmpeg")
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    assert _item(diagnostics.run_environment_diagnostics(), "FFmpeg")["status"] == "error"


def test_index_dir_missing_and_model_empty(monkeypatch, tmp_path):
    model = tmp_path / "index-tts" / "checkpoints"
    monkeypatch.setattr(diagnostics.config, "get_model_dir", lambda: str(model))
    report = diagnostics.run_environment_diagnostics()
    assert _item(report, "IndexTTS2 项目目录")["status"] == "error"
    model.mkdir(parents=True)
    report = diagnostics.run_environment_diagnostics()
    assert _item(report, "模型目录")["status"] == "warning"


def test_torch_missing_or_cuda_unavailable_does_not_crash(monkeypatch):
    real_import = __import__
    def guarded(name, *args, **kwargs):
        if name == "torch":
            raise ImportError
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr("builtins.__import__", guarded)
    assert _item(diagnostics.run_environment_diagnostics(), "Torch / CUDA")["status"] == "warning"


def test_platform_branches_do_not_crash(monkeypatch):
    for name in ("Windows", "Darwin", "Linux"):
        monkeypatch.setattr(diagnostics.platform, "system", lambda n=name: n)
        assert diagnostics.run_environment_diagnostics()["checks"]


def test_api_keys_never_leak(monkeypatch):
    secret = "sk-super-secret-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    report = diagnostics.run_environment_diagnostics()
    rendered = json.dumps(report, ensure_ascii=False) + diagnostics.diagnostics_to_markdown(report)
    assert secret not in rendered
    assert _item(report, "API Provider")["details"]["openai_key_configured"] is True


def test_single_check_exception_isolated(monkeypatch):
    monkeypatch.setattr(diagnostics.platform, "platform", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    report = diagnostics.run_environment_diagnostics()
    assert _item(report, "操作系统")["status"] == "error"
    assert _item(report, "Python")["message"]
