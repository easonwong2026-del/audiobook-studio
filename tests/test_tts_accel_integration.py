"""GPU-free production integration tests for IndexTTS 2.5 GPT Accel."""
from __future__ import annotations

import os
import sys
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import tts_engine


def _module(name: str, file_path: str, **attrs):
    value = types.ModuleType(name)
    value.__file__ = file_path
    for key, item in attrs.items():
        setattr(value, key, item)
    return value


def _install_accel_modules(monkeypatch, overlay, *, triton_version="3.4.0.post21"):
    flash_dir = overlay / "flash_attn"
    triton_dir = overlay / "triton"
    flash_dir.mkdir(parents=True, exist_ok=True)
    (triton_dir / "runtime" / "tcc").mkdir(parents=True, exist_ok=True)
    (triton_dir / "runtime" / "tcc" / "tcc.exe").write_bytes(b"fake")
    monkeypatch.setitem(
        sys.modules,
        "flash_attn",
        _module("flash_attn", str(flash_dir / "__init__.py"), __version__="2.8.3"),
    )
    monkeypatch.setitem(
        sys.modules,
        "triton",
        _module(
            "triton",
            str(triton_dir / "__init__.py"),
            __version__=triton_version,
        ),
    )


@pytest.fixture(autouse=True)
def _reset_accel_state(monkeypatch):
    monkeypatch.delenv(tts_engine.ACCEL_OVERLAY_ENV, raising=False)
    monkeypatch.delenv(tts_engine.ACCEL_DISABLE_ENV, raising=False)
    monkeypatch.delenv("CC", raising=False)
    tts_engine.reset_engine()
    yield
    tts_engine.reset_engine()


def test_v25_without_windows_capability_keeps_baseline(monkeypatch):
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: False)

    report = tts_engine._prepare_v25_acceleration()

    assert report["requested"] is True
    assert report["available"] is False
    assert report["enabled"] is False
    assert report["reason"] == "unsupported_platform"
    assert tts_engine.get_acceleration_status()["fallback"] is True


def test_v25_accel_defaults_enabled_when_config_is_missing(monkeypatch):
    monkeypatch.setattr(tts_engine._cfg, "_read_config", dict)
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: False)

    report = tts_engine._prepare_v25_acceleration()

    assert report["requested"] is True
    assert report["reason"] == "unsupported_platform"


def test_v25_config_disable_skips_accel_dependency_import(monkeypatch):
    monkeypatch.setattr(
        tts_engine._cfg,
        "_read_config",
        lambda: {tts_engine._cfg.INDEXTTS25_GPT_ACCEL_CONFIG_KEY: False},
    )
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    imported = []

    def unexpected_import(name):
        imported.append(name)
        raise AssertionError(f"unexpected accel import: {name}")

    monkeypatch.setattr(tts_engine.importlib, "import_module", unexpected_import)

    report = tts_engine._prepare_v25_acceleration()

    assert report["requested"] is False
    assert report["enabled"] is False
    assert report["active"] is False
    assert report["fallback"] is True
    assert report["reason"] == "user_disabled"
    assert imported == []


def test_v25_ready_overlay_is_prepared_idempotently_and_sets_process_cc(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay" / "Lib" / "site-packages"
    overlay.mkdir(parents=True)
    _install_accel_modules(monkeypatch, overlay)
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    monkeypatch.setenv(tts_engine.ACCEL_OVERLAY_ENV, str(overlay))

    first = tts_engine._prepare_v25_acceleration()
    second = tts_engine._prepare_v25_acceleration()

    assert first["available"] is True
    assert first["enabled"] is True
    assert first["flash_attn_version"] == "2.8.3"
    assert first["triton_version"] == "3.4.0.post21"
    assert sys.path.count(os.path.abspath(str(overlay))) == 1
    assert second["available"] is True
    assert os.environ["CC"].endswith(os.path.join("triton", "runtime", "tcc", "tcc.exe"))


def test_invalid_overlay_is_not_added(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    invalid = tmp_path / "missing-site-packages"
    monkeypatch.setenv(tts_engine.ACCEL_OVERLAY_ENV, str(invalid))

    report = tts_engine._prepare_v25_acceleration()

    assert report["available"] is False
    assert report["reason"] == "overlay_invalid"
    assert os.path.abspath(str(invalid)) not in sys.path


def test_windows_without_accel_dependencies_keeps_baseline(monkeypatch):
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    real_import = tts_engine.importlib.import_module

    def missing(name):
        if name in {"flash_attn", "triton"}:
            raise ModuleNotFoundError(name)
        return real_import(name)

    monkeypatch.setattr(tts_engine.importlib, "import_module", missing)

    report = tts_engine._prepare_v25_acceleration()

    assert report["available"] is False
    assert report["enabled"] is False
    assert report["reason"] == "dependency_missing"
    assert report["fallback"] is True


def test_valid_existing_cc_is_not_overwritten(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    _install_accel_modules(monkeypatch, overlay)
    existing_cc = tmp_path / "custom-cl.exe"
    existing_cc.write_bytes(b"fake")
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    monkeypatch.setenv(tts_engine.ACCEL_OVERLAY_ENV, str(overlay))
    monkeypatch.setenv("CC", str(existing_cc))

    report = tts_engine._prepare_v25_acceleration()

    assert report["available"] is True
    assert os.environ["CC"] == str(existing_cc)


def test_triton_windows_distribution_guard_handles_runtime_module_version(monkeypatch):
    triton = types.SimpleNamespace(__version__="3.4.0")

    def distribution_version(name):
        if name == "triton-windows":
            return "3.4.0.post21"
        raise tts_engine.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(tts_engine.metadata, "version", distribution_version)

    assert tts_engine._triton_version(triton) == "3.4.0.post21"
    assert tts_engine._is_affected_triton_windows(triton) is True


def test_emergency_disable_wins_over_ready_dependencies(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    _install_accel_modules(monkeypatch, overlay)
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    monkeypatch.setenv(tts_engine.ACCEL_OVERLAY_ENV, str(overlay))
    monkeypatch.setenv(tts_engine.ACCEL_DISABLE_ENV, "1")

    report = tts_engine._prepare_v25_acceleration()

    assert report["requested"] is False
    assert report["available"] is False
    assert report["reason"] == "emergency_disabled"
    assert report["fallback"] is True


def test_v25_constructor_accepts_resolved_accel_without_enabling_other_paths():
    kwargs = tts_engine.IndexTTS25Backend.constructor_kwargs(
        cfg_path="v25/config.yaml",
        model_dir="v25",
        precision="BF16",
        use_accel=True,
    )

    assert kwargs["use_accel"] is True
    assert kwargs["use_torch_compile"] is False
    assert kwargs["use_cuda_kernel"] is False
    assert kwargs["use_deepspeed"] is False


def test_v25_preparation_happens_before_backend_import_and_active_state_is_verified(
    monkeypatch, tmp_path
):
    events = []

    class FakeNative:
        def __init__(self, **kwargs):
            events.append(("construct", kwargs))
            self.gpt = types.SimpleNamespace(accel_engine=object())

        def infer(self, **_kwargs):
            return None

    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config_v2_5.yaml").write_text("version: 2.5\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace())
    monkeypatch.setattr(
        tts_engine,
        "_prepare_v25_acceleration",
        lambda: events.append(("prepare", None)) or {
            "requested": True,
            "available": True,
            "enabled": True,
            "active": False,
            "fallback": False,
            "reason": "runtime_ready",
            "flash_attn_version": "2.8.3",
            "triton_version": "3.4.0.post21",
        },
    )
    monkeypatch.setattr(
        tts_engine.IndexTTS25Backend,
        "load_class",
        staticmethod(lambda: events.append(("load_class", None)) or FakeNative),
    )

    from lib.tts_profile import resolve_profile

    tts_engine.init_engine(
        profile=resolve_profile({"engine_version": "2.5", "model_dir": str(model_dir)})
    )

    assert [event[0] for event in events] == ["prepare", "load_class", "construct"]
    assert events[-1][1]["use_accel"] is True
    status = tts_engine.get_acceleration_status()
    assert status["available"] is True
    assert status["active"] is True
    assert status["fallback"] is False


def test_preflight_success_but_missing_backend_accel_fails_explicitly(monkeypatch, tmp_path):
    class FakeNative:
        def __init__(self, **_kwargs):
            self.gpt = types.SimpleNamespace(accel_engine=None)

    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config_v2_5.yaml").write_text("version: 2.5\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace())
    monkeypatch.setattr(
        tts_engine,
        "_prepare_v25_acceleration",
        lambda: {
            "requested": True,
            "available": True,
            "enabled": True,
            "active": False,
            "fallback": False,
            "reason": "runtime_ready",
            "flash_attn_version": "2.8.3",
            "triton_version": "3.4.0.post21",
        },
    )
    monkeypatch.setattr(tts_engine.IndexTTS25Backend, "load_class", staticmethod(lambda: FakeNative))

    from lib.tts_profile import resolve_profile

    with pytest.raises(RuntimeError, match="accel_init_failed"):
        tts_engine.init_engine(
            profile=resolve_profile({"engine_version": "2.5", "model_dir": str(model_dir)})
        )

    status = tts_engine.get_acceleration_status()
    assert status["active"] is False
    assert status["reason"] == "accel_init_failed"
    assert status["fallback"] is False
    assert tts_engine.engine_is_initialized() is False


def test_scoped_triton_workaround_is_version_guarded_and_restored(monkeypatch, tmp_path):
    cache_module = types.ModuleType("triton.runtime.cache")
    cache_module.os = os
    seen_removedirs = []

    class FileCacheManager:
        def put(self):
            seen_removedirs.append(cache_module.os.removedirs)

    cache_module.FileCacheManager = FileCacheManager
    monkeypatch.setitem(sys.modules, "triton.runtime.cache", cache_module)
    monkeypatch.setitem(
        sys.modules,
        "triton",
        _module("triton", str(tmp_path / "triton" / "__init__.py"), __version__="3.4.0.post21"),
    )
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    original_os = cache_module.os
    original_removedirs = os.removedirs

    with tts_engine._scoped_triton_cache_workaround():
        FileCacheManager().put()
        assert cache_module.os is original_os

    assert cache_module.os is original_os
    assert os.removedirs is original_removedirs
    assert seen_removedirs and seen_removedirs[0] is not original_removedirs


def test_scoped_triton_workaround_restores_on_exception(monkeypatch, tmp_path):
    cache_module = types.ModuleType("triton.runtime.cache")
    cache_module.os = os
    cache_module.FileCacheManager = type("FileCacheManager", (), {"put": lambda self: None})
    monkeypatch.setitem(sys.modules, "triton.runtime.cache", cache_module)
    monkeypatch.setitem(
        sys.modules,
        "triton",
        _module("triton", str(tmp_path / "triton" / "__init__.py"), __version__="3.4.0.post21"),
    )
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    original_os = cache_module.os

    with pytest.raises(RuntimeError), tts_engine._scoped_triton_cache_workaround():
        raise RuntimeError("infer failed")

    assert cache_module.os is original_os


def test_scoped_triton_workaround_skips_other_versions(monkeypatch, tmp_path):
    cache_module = types.ModuleType("triton.runtime.cache")
    cache_module.os = os
    cache_module.FileCacheManager = type("FileCacheManager", (), {"put": lambda self: None})
    monkeypatch.setitem(sys.modules, "triton.runtime.cache", cache_module)
    monkeypatch.setitem(
        sys.modules,
        "triton",
        _module("triton", str(tmp_path / "triton" / "__init__.py"), __version__="3.5.0.post21"),
    )
    monkeypatch.setattr(tts_engine, "_is_windows", lambda: True)
    original_os = cache_module.os

    with tts_engine._scoped_triton_cache_workaround():
        assert cache_module.os is original_os
