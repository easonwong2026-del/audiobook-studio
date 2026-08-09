#!/usr/bin/env python3
"""Real-GPU benchmark for IndexTTS2 cache-clear policies.

This script intentionally requires a configured model, CUDA and a real speaker
sample.  It does not change the application's current policy; use its JSON
output to choose a policy only after running on the target Windows GPU.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import tts_engine


def _cuda_metrics(torch: Any) -> dict[str, float]:
    return {
        "allocated_mb": round(torch.cuda.memory_allocated() / (1024 * 1024), 2),
        "reserved_mb": round(torch.cuda.memory_reserved() / (1024 * 1024), 2),
        "peak_allocated_mb": round(
            torch.cuda.max_memory_allocated() / (1024 * 1024), 2
        ),
        "peak_reserved_mb": round(
            torch.cuda.max_memory_reserved() / (1024 * 1024), 2
        ),
    }


def _run_strategy(
    strategy: str,
    *,
    speaker: str,
    text: str,
    segments: int,
    chapter_size: int,
) -> dict[str, Any]:
    import torch

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    original_empty_cache = tts_engine.empty_cache
    tts_engine.empty_cache = lambda: None
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix=f"tts-vram-{strategy}-") as root:
            for index in range(segments):
                tts_engine.synthesize_segment(
                    text=text,
                    speaker_audio=speaker,
                    output_path=os.path.join(root, f"{index:05d}.wav"),
                    num_beams=2,
                )
                if strategy == "every_segment":
                    torch.cuda.empty_cache()
                elif strategy == "chapter_boundary" and (
                    (index + 1) % max(chapter_size, 1) == 0
                ):
                    torch.cuda.empty_cache()
                elif strategy == "threshold":
                    reserved = torch.cuda.memory_reserved()
                    allocated = torch.cuda.memory_allocated()
                    if reserved and (reserved - allocated) / reserved >= 0.35:
                        torch.cuda.empty_cache()
            if strategy == "task_boundary":
                torch.cuda.empty_cache()
        elapsed = time.perf_counter() - started
        steady = _cuda_metrics(torch)
        torch.cuda.empty_cache()
        post = _cuda_metrics(torch)
        return {
            "strategy": strategy,
            "segments": segments,
            "elapsed_seconds": round(elapsed, 3),
            "segments_per_minute": round((segments / elapsed) * 60, 3),
            "steady": steady,
            "post_task": post,
        }
    finally:
        tts_engine.empty_cache = original_empty_cache


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speaker", required=True)
    parser.add_argument("--text", default="这是显存策略基准测试句。")
    parser.add_argument("--segments", type=int, default=20)
    parser.add_argument("--chapter-size", type=int, default=5)
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=["every_segment", "chapter_boundary", "task_boundary", "threshold"],
        default=["every_segment", "chapter_boundary", "task_boundary", "threshold"],
    )
    arguments = parser.parse_args()
    if not os.path.isfile(arguments.speaker):
        parser.error("--speaker 必须是存在的参考音频")
    import torch

    if not torch.cuda.is_available():
        parser.error("需要真实 CUDA GPU；不得把 CPU/fake engine 结果当作 VRAM 验收")
    tts_engine.init_engine()
    model_loaded = _cuda_metrics(torch)
    results = [
        _run_strategy(
            strategy,
            speaker=os.path.abspath(arguments.speaker),
            text=arguments.text,
            segments=max(arguments.segments, 1),
            chapter_size=max(arguments.chapter_size, 1),
        )
        for strategy in arguments.strategies
    ]
    print(json.dumps({
        "model_loaded": model_loaded,
        "results": results,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
