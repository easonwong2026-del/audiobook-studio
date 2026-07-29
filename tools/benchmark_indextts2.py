#!/usr/bin/env python3
"""Audit and benchmark a real IndexTTS2 checkout on the target Windows host.

The default mode is audit-only. Inference requires explicit module/class/model,
speaker prompt and --run-inference; this prevents an unattended benchmark from
guessing an upstream API or raising production limits.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import platform
import subprocess
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOKEN_TIERS = (40, 60, 80, 100, 120)


def _run(command: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return (result.stdout or result.stderr).strip()


def _gpu_snapshot() -> dict[str, Any]:
    query = "name,memory.total,memory.used,memory.free"
    output = _run(
        ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"]
    )
    values = [item.strip() for item in output.splitlines()[0].split(",")] if output else []
    if len(values) != 4:
        return {"available": False, "raw": output}
    return {
        "available": True,
        "name": values[0],
        "total_mb": int(values[1]),
        "used_mb": int(values[2]),
        "free_mb": int(values[3]),
    }


def _audio_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except (wave.Error, OSError, ZeroDivisionError):
        return None


def _provenance(checkout: Path, engine_class: type[Any] | None) -> dict[str, Any]:
    signatures: dict[str, str] = {}
    if engine_class is not None:
        signatures = {
            "constructor": str(inspect.signature(engine_class)),
            "infer": str(inspect.signature(engine_class.infer)),
        }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "checkout": str(checkout),
        "git_sha": _run(["git", "rev-parse", "HEAD"], checkout),
        "git_status": _run(["git", "status", "--short"], checkout),
        "git_log": _run(["git", "log", "-1", "--decorate", "--oneline"], checkout),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "gpu": _gpu_snapshot(),
        "signatures": signatures,
    }


def _load_class(module_name: str, class_name: str) -> type[Any]:
    module = importlib.import_module(module_name)
    value = getattr(module, class_name)
    if not inspect.isclass(value):
        raise TypeError(f"{module_name}.{class_name} is not a class")
    return value


def _invoke(
    engine: Any,
    text: str,
    output: Path,
    speaker_prompt: Path,
    max_tokens: int,
) -> dict[str, Any]:
    signature = inspect.signature(engine.infer)
    candidates = {
        "spk_audio_prompt": str(speaker_prompt),
        "text": text,
        "output_path": str(output),
        "max_text_tokens_per_segment": max_tokens,
    }
    kwargs = {
        name: value for name, value in candidates.items() if name in signature.parameters
    }
    required = {"spk_audio_prompt", "text", "output_path"}
    if not required.issubset(kwargs):
        raise TypeError(f"unsupported infer signature: {signature}")
    before = _gpu_snapshot()
    started = time.perf_counter()
    try:
        engine.infer(**kwargs)
        success = True
        error_type = None
        error_message = None
    except Exception as exc:  # noqa: BLE001 - benchmark preserves engine failures
        success = False
        error_type = type(exc).__name__
        error_message = str(exc)
    elapsed = time.perf_counter() - started
    after = _gpu_snapshot()
    return {
        "success": success,
        "error_type": error_type,
        "error_message": error_message,
        "chars": len(text),
        "token_limit": max_tokens,
        "elapsed_seconds": elapsed,
        "audio_seconds": _audio_seconds(output) if success else None,
        "gpu_before": before,
        "gpu_after": after,
        "output_path": str(output),
    }


def _sample_text(token_limit: int) -> str:
    unit = "这是用于验证有声书合成稳定性、显存变化与任务恢复能力的基准文本。"
    return (unit * max(1, token_limit // len(unit) + 1))[:token_limit]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--checkout",
        type=Path,
        default=Path(r"D:\AudiobookStudio\project\index-tts"),
    )
    parser.add_argument("--module", default="indextts.infer_v2")
    parser.add_argument("--class-name", default="IndexTTS2")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--speaker-prompt", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/benchmarks"))
    parser.add_argument("--run-inference", action="store_true")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--recommended-repeat", type=int, default=100)
    args = parser.parse_args()

    engine_class: type[Any] | None = None
    load_error = None
    try:
        engine_class = _load_class(args.module, args.class_name)
    except Exception as exc:
        load_error = f"{type(exc).__name__}: {exc}"
        if args.run_inference:
            raise

    report: dict[str, Any] = {
        "schema_version": "audiobook-indextts-benchmark-v1",
        "mode": "inference" if args.run_inference else "audit-only",
        "provenance": _provenance(args.checkout, engine_class),
        "load_error": load_error,
        "tiers": list(TOKEN_TIERS),
        "runs": [],
        "recommendation": {
            "max_text_tokens": 100,
            "policy": "automatic adjustment may lower but never raise above 100",
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.run_inference:
        if not args.model_dir or not args.speaker_prompt:
            parser.error("--model-dir and --speaker-prompt are required for inference")
        constructor = inspect.signature(engine_class)
        kwargs = {}
        if "model_dir" in constructor.parameters:
            kwargs["model_dir"] = str(args.model_dir)
        engine = engine_class(**kwargs)
        for tier in TOKEN_TIERS:
            repeats = args.recommended_repeat if tier == 100 else args.repeat
            for run_index in range(repeats + 1):  # one cold + repeated runs
                output = args.output_dir / f"tier-{tier}-run-{run_index:03d}.wav"
                result = _invoke(
                    engine, _sample_text(tier), output, args.speaker_prompt, tier
                )
                result.update({"tier": tier, "run": run_index, "cold": run_index == 0})
                report["runs"].append(result)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = args.output_dir / f"indextts2-benchmark-{stamp}.json"
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
