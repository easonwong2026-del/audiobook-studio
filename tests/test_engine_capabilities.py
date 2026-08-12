"""Capability detection must stay conservative on a Mac/CPU test host."""
from __future__ import annotations

import types

from services.engine_capabilities import detect_engine_capabilities, gpu_snapshot


class _FutureConstructor:
    def __init__(
        self,
        cfg_path,
        model_dir,
        use_cuda_kernel=True,
        use_deepspeed=False,
        use_accel=False,
    ):
        self.cfg_path = cfg_path
        self.model_dir = model_dir

    def infer(self, text, output_path, **generation_kwargs):
        return generation_kwargs


def _cpu_torch():
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
    )


def test_no_cuda_snapshot_is_small_and_safe():
    assert gpu_snapshot(_cpu_torch()) == {"available": False}
    assert gpu_snapshot(types.SimpleNamespace()) == {"available": False}


def test_capability_report_distinguishes_supported_installed_enabled_verified():
    report = detect_engine_capabilities(
        constructor=_FutureConstructor,
        engine=None,
        torch_module=_cpu_torch(),
        package_presence={
            "torch": True,
            "indextts": True,
            "flash_attn": True,
            "deepspeed": False,
        },
        enabled_options={"cuda_kernel": True, "accel": True, "num_beams": True},
        verified_options={"cuda_kernel": True, "accel": True, "num_beams": True},
    )

    assert report["cuda"]["installed"] is True
    assert report["cuda"]["enabled"] is False
    assert report["cuda"]["verified"] is False
    assert report["cuda_kernel"] == {
        "supported": True,
        "installed": True,
        "enabled": True,
        "verified": False,
    }
    assert report["accel"]["supported"] is True
    assert report["accel"]["installed"] is True
    assert report["deepspeed"]["supported"] is True
    assert report["deepspeed"]["installed"] is False
    assert report["deepspeed"]["verified"] is False


def test_infer_var_keyword_is_detected_as_num_beams_support_without_importing_model():
    engine = _FutureConstructor("cfg", "model")
    report = detect_engine_capabilities(
        engine=engine,
        torch_module=_cpu_torch(),
        package_presence={"torch": True, "indextts": True},
        enabled_options={"num_beams": False},
    )
    assert report["num_beams"]["supported"] is True
    assert report["num_beams"]["installed"] is True
    assert report["num_beams"]["enabled"] is False
    assert report["num_beams"]["verified"] is False


def test_verified_is_never_raised_by_importability_on_cpu_host():
    report = detect_engine_capabilities(
        constructor=_FutureConstructor,
        torch_module=_cpu_torch(),
        package_presence={"torch": True, "indextts": True, "flash_attn": True},
        enabled_options={"accel": True},
        verified_options={"accel": True},
    )
    # This is a capability/signature fact, not a claim that a real CUDA run
    # succeeded; accel is not CUDA-gated in this generic helper, so the caller
    # should only pass verified evidence after a real benchmark.  CUDA itself
    # remains unverified and unavailable here.
    assert report["cuda"]["verified"] is False
    assert report["cuda"]["enabled"] is False
