"""Best-effort cleanup without unloading the resident model."""
from __future__ import annotations

import gc
import sys


def release_inference_memory(*, clear_cuda_cache: bool = False) -> None:
    gc.collect()
    if not clear_cuda_cache:
        return
    torch = sys.modules.get("torch")
    cuda = getattr(torch, "cuda", None) if torch is not None else None
    try:
        if cuda is not None and cuda.is_available():
            cuda.empty_cache()
    except Exception:  # noqa: BLE001 - cleanup must never hide original failure
        return
