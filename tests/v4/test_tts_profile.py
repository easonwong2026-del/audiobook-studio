import json
from pathlib import Path


def test_5070ti_profile_is_conservative_and_unbenchmarked():
    root = Path(__file__).resolve().parents[2]
    profile = json.loads(
        (root / "config/tts_profiles/indextts2-rtx5070ti-laptop-12gb-v1.json")
        .read_text(encoding="utf-8")
    )
    assert profile["schema_version"] == "audiobook-tts-profile-v1"
    assert profile["status"] == "provisional-unbenchmarked"
    assert profile["hardware"]["vram_gb"] == 12
    assert profile["limits"]["preferred"] == 80
    assert profile["limits"]["maximum"] == 100
    assert profile["limits"]["absolute"] == 120
    assert profile["runtime"]["concurrency"] == 1
    assert profile["runtime"]["clear_cuda_cache_after_oom"] is True
    assert profile["runtime"]["restart_worker_after_tasks"] == 100
    assert profile["runtime"]["restart_on_vram_growth_mb"] == 1536
    assert "clear_cache_after_oom" not in profile["runtime"]
    assert "restart_engine_after_tasks" not in profile["runtime"]
    assert profile["options"]["cuda_kernel"] is False
