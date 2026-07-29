import json
import sys
from types import SimpleNamespace

from services import environment_diagnostics as diagnostics
from lib import environment
import launcher


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


def _disable_path(monkeypatch):
    monkeypatch.setattr(environment.shutil, "which", lambda _: None)


def test_python_resolution_environment(monkeypatch, tmp_path):
    executable = tmp_path / "custom-python"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_PYTHON", str(executable))
    result = environment.resolve_python_interpreter()
    assert result.executable == str(executable)
    assert result.source == "environment"


def test_invalid_environment_falls_back_to_unix_sibling(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_PYTHON", str(tmp_path / "missing"))
    monkeypatch.setattr(environment, "PROGRAM_DIR", tmp_path / "studio")
    executable = tmp_path / "index-tts" / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    _disable_path(monkeypatch)
    result = environment.resolve_python_interpreter()
    assert result.executable == str(executable)
    assert result.source == "sibling_venv"
    assert result.warnings


def test_windows_sibling_venv(monkeypatch, tmp_path):
    monkeypatch.delenv("AUDIOBOOK_STUDIO_PYTHON", raising=False)
    monkeypatch.setattr(environment, "PROGRAM_DIR", tmp_path / "studio")
    executable = tmp_path / "index-tts" / ".venv" / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    _disable_path(monkeypatch)
    result = environment.resolve_python_interpreter()
    assert result.executable == str(executable)
    assert result.source == "sibling_venv"


def test_python_and_python3_path_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("AUDIOBOOK_STUDIO_PYTHON", raising=False)
    monkeypatch.setattr(environment, "PROGRAM_DIR", tmp_path / "studio")
    for available in ("python", "python3"):
        monkeypatch.setattr(
            environment.shutil,
            "which",
            lambda command, selected=available: f"/bin/{command}" if command == selected else None,
        )
        result = environment.resolve_python_interpreter()
        assert result.executable == f"/bin/{available}"
        assert result.source == "path"


def test_python_resolution_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("AUDIOBOOK_STUDIO_PYTHON", raising=False)
    monkeypatch.setattr(environment, "PROGRAM_DIR", tmp_path / "studio")
    _disable_path(monkeypatch)
    result = environment.resolve_python_interpreter()
    assert result.executable is None
    assert result.source == "missing"


def test_launcher_and_diagnostics_share_resolver(monkeypatch, tmp_path):
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")
    resolution = environment.PythonResolution(str(executable), "environment", [])
    monkeypatch.setattr(launcher, "resolve_python_interpreter", lambda: resolution)
    monkeypatch.setattr(diagnostics, "resolve_python_interpreter", lambda: resolution)
    assert launcher._resolve_python() == str(executable)
    item = _item(diagnostics.run_environment_diagnostics(), "IndexTTS2 Python")
    assert item["status"] == "ok"
    assert item["suggestion"] == ""
    assert item["details"]["executable"] == str(executable)
    assert item["details"]["source"] == "environment"


def test_diagnostics_python_sibling_success_has_no_suggestion(monkeypatch):
    resolution = environment.PythonResolution(
        "/path/to/python", "sibling_venv", [],
    )
    monkeypatch.setattr(diagnostics, "resolve_python_interpreter", lambda: resolution)
    item = _item(diagnostics.run_environment_diagnostics(), "IndexTTS2 Python")
    assert item["status"] == "ok"
    assert item["suggestion"] == ""
    assert item["details"]["source"] == "sibling_venv"


def test_diagnostics_invalid_env_fallback_remains_ok(monkeypatch, tmp_path):
    warning = "AUDIOBOOK_STUDIO_PYTHON 指向的文件不存在：/missing/python"
    resolution = environment.PythonResolution(
        "/path/to/index-tts/.venv/bin/python", "sibling_venv", [warning],
    )
    monkeypatch.setattr(diagnostics, "resolve_python_interpreter", lambda: resolution)

    data_dir = tmp_path / "data"
    projects = data_dir / "projects"
    projects.mkdir(parents=True)
    model_dir = tmp_path / "index-tts" / "checkpoints"
    model_dir.mkdir(parents=True)
    (model_dir / "config.yaml").write_text("test", encoding="utf-8")
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text("", encoding="utf-8")
    monkeypatch.setattr(diagnostics.config, "get_data_dir", lambda: str(data_dir))
    monkeypatch.setattr(diagnostics.config, "get_projects_root", lambda: str(projects))
    monkeypatch.setattr(diagnostics.config, "get_model_dir", lambda: str(model_dir))
    monkeypatch.setattr(diagnostics.config, "get_ffmpeg_path", lambda: str(ffmpeg))
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "P", (), {"returncode": 0, "stdout": "ffmpeg version 7.1\n", "stderr": ""},
        )(),
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        diagnostics.AiSettingsService,
        "get_provider_config",
        lambda: {"default_provider": "local"},
    )
    monkeypatch.setattr(
        diagnostics.AiSettingsService, "has_api_key", lambda _: False,
    )
    monkeypatch.setattr(
        diagnostics.AiSettingsService, "get_api_key_source", lambda _: "none",
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            __version__="test",
            cuda=SimpleNamespace(
                is_available=lambda: False,
                get_device_name=lambda _: None,
            ),
        ),
    )

    report = diagnostics.run_environment_diagnostics()
    item = _item(report, "IndexTTS2 Python")
    assert item["status"] == "ok"
    assert item["suggestion"] == ""
    assert item["details"]["warnings"] == [warning]
    assert report["status"] != "error", [
        (check["name"], check["message"])
        for check in report["checks"]
        if check["status"] == "error"
    ]


def test_diagnostics_python_missing_has_actionable_error(monkeypatch):
    resolution = environment.PythonResolution(None, "missing", [])
    monkeypatch.setattr(diagnostics, "resolve_python_interpreter", lambda: resolution)
    item = _item(diagnostics.run_environment_diagnostics(), "IndexTTS2 Python")
    assert item["status"] == "error"
    assert item["message"] == "解释器不存在"
    assert item["suggestion"]
