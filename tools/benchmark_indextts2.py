#!/usr/bin/env python3
"""Audit and benchmark the real IndexTTS2 checkout on the target Windows host.

Audit-only is the safe default. Inference requires an explicit model directory,
speaker prompt and --run-inference. The report never stores API keys, voice
bytes, or more than a short sample label/hash for synthesized text.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
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
SAFE_AUTOMATIC_CEILING = 100
MINIMUM_FREE_VRAM_MB = 1536
MAX_VRAM_RATIO = 0.85
GROWTH_LIMIT_MB = 1536
PROFILE_NAME = "indextts2-rtx5070ti-laptop-12gb"

SAMPLES = (
    ("chinese_narration", "夜色沿着山脊缓缓落下，远处的灯火一盏接一盏亮了起来。"),
    ("chinese_dialogue", "林晚问道：“你真的决定明天出发吗？”陈默点了点头。"),
    ("mixed_language", "系统显示 Ready，随后 Alice 输入了 restart worker 命令。"),
    ("numbers_dates", "会议定在2026年7月30日14点30分，订单编号为5070-1208。"),
    ("pinyin_hint", "重庆应读作 chóng qìng，单老师的姓读作 shàn。"),
    ("long_unpunctuated", "这是一段用于检查超长无标点文本切分与显存稳定性的连续旁白"),
    ("auto_emotion", "“别走！”她压低声音，停了一会儿才说，“我还有话没有讲完。”"),
)


def _run(command: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return (result.stdout or result.stderr).strip()


def _nvidia_snapshot() -> dict[str, Any]:
    query = "name,memory.total,memory.used,memory.free"
    output = _run(
        ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"]
    )
    values = [item.strip() for item in output.splitlines()[0].split(",")] if output else []
    try:
        if len(values) == 4:
            return {
                "available": True,
                "name": values[0],
                "total_mb": int(values[1]),
                "used_mb": int(values[2]),
                "free_mb": int(values[3]),
            }
    except ValueError:
        pass
    return {"available": False, "raw": output[:500]}


def _torch_cuda():
    try:
        import torch

        return torch.cuda if torch.cuda.is_available() else None
    except (ImportError, RuntimeError, AttributeError):
        return None


def _memory_snapshot() -> dict[str, Any]:
    cuda = _torch_cuda()
    nvidia = _nvidia_snapshot()
    snapshot = {
        "memory_allocated": None,
        "memory_reserved": None,
        "max_memory_allocated": None,
        "free_vram": (
            nvidia.get("free_mb") * 1024 * 1024
            if nvidia.get("available")
            else None
        ),
        "total_vram": (
            nvidia.get("total_mb") * 1024 * 1024
            if nvidia.get("available")
            else None
        ),
    }
    if cuda is not None:
        try:
            free_bytes, total_bytes = cuda.mem_get_info()
            snapshot.update(
                {
                    "memory_allocated": int(cuda.memory_allocated()),
                    "memory_reserved": int(cuda.memory_reserved()),
                    "max_memory_allocated": int(cuda.max_memory_allocated()),
                    "free_vram": int(free_bytes),
                    "total_vram": int(total_bytes),
                }
            )
        except RuntimeError:
            pass
    return snapshot


def _reset_peak_memory() -> None:
    cuda = _torch_cuda()
    if cuda is not None:
        try:
            cuda.reset_peak_memory_stats()
        except RuntimeError:
            pass


def _release_engine(engine: Any | None) -> None:
    del engine
    gc.collect()
    cuda = _torch_cuda()
    if cuda is not None:
        try:
            cuda.empty_cache()
        except RuntimeError:
            pass


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
        "gpu": _nvidia_snapshot(),
        "signatures": signatures,
    }


def _load_class(module_name: str, class_name: str) -> type[Any]:
    module = importlib.import_module(module_name)
    value = getattr(module, class_name)
    if not inspect.isclass(value):
        raise TypeError(f"{module_name}.{class_name} is not a class")
    return value


def _signature_kwargs(
    callable_value: Any,
    candidates: dict[str, Any],
) -> dict[str, Any]:
    parameters = inspect.signature(callable_value).parameters
    return {
        name: value
        for name, value in candidates.items()
        if value is not None and name in parameters
    }


def _new_engine(engine_class: type[Any], model_dir: Path) -> Any:
    cfg_path = model_dir / "config.yaml"
    candidates = {
        "cfg_path": str(cfg_path),
        "model_dir": str(model_dir),
        "use_fp16": True,
        "use_deepspeed": False,
        "use_cuda_kernel": False,
        "use_accel": False,
        "use_torch_compile": False,
    }
    return engine_class(**_signature_kwargs(engine_class, candidates))


def _tokenizer(engine: Any):
    for name in ("tokenizer", "text_tokenizer", "semantic_tokenizer"):
        tokenizer = getattr(engine, name, None)
        if tokenizer is not None and callable(getattr(tokenizer, "encode", None)):
            return tokenizer
    return None


def _measure_tokens(engine: Any, text: str) -> tuple[int, str]:
    tokenizer = _tokenizer(engine)
    if tokenizer is not None:
        try:
            encoded = tokenizer.encode(text)
            if hasattr(encoded, "input_ids"):
                encoded = encoded.input_ids
            return len(encoded), type(tokenizer).__name__
        except Exception:  # noqa: BLE001 - fall through to conservative estimate
            tokenizer = None
    count = sum(
        1
        for index, character in enumerate(text)
        if not character.isspace()
        and (
            "\u3400" <= character <= "\u9fff"
            or not character.isascii()
            or index == 0
            or not text[index - 1].isascii()
            or text[index - 1].isspace()
        )
    )
    return max(count, 1), "conservative-fallback"


def _text_for_tier(engine: Any, sample: str, tier: int) -> tuple[str, int, str]:
    text = sample
    for _ in range(12):
        count, source = _measure_tokens(engine, text)
        if count >= tier:
            break
        text += sample
    count, source = _measure_tokens(engine, text)
    if count > tier:
        low, high = 1, len(text)
        while low < high:
            middle = (low + high) // 2
            measured, _ = _measure_tokens(engine, text[:middle])
            if measured < tier:
                low = middle + 1
            else:
                high = middle
        text = text[:low]
        count, source = _measure_tokens(engine, text)
    return text, count, source


def _invoke(
    engine: Any,
    text: str,
    output: Path,
    speaker_prompt: Path,
    tier: int,
    sample_name: str,
) -> dict[str, Any]:
    signature = inspect.signature(engine.infer)
    token_count, token_source = _measure_tokens(engine, text)
    candidates = {
        "spk_audio_prompt": str(speaker_prompt),
        "text": text,
        "output_path": str(output),
        "max_text_tokens_per_segment": tier,
        "use_emo_text": True,
        "emo_text": text,
        "emo_alpha": 0.55,
        "use_random": False,
        "do_sample": False,
    }
    kwargs = _signature_kwargs(engine.infer, candidates)
    required = {"spk_audio_prompt", "text", "output_path"}
    if not required.issubset(kwargs):
        raise TypeError(f"unsupported infer signature: {signature}")
    _reset_peak_memory()
    before = _memory_snapshot()
    started = time.perf_counter()
    try:
        engine.infer(**kwargs)
        success = output.is_file() and (_audio_seconds(output) or 0) > 0
        error_type = None if success else "EmptyAudioError"
        error_message = None if success else "output is missing or empty"
    except Exception as exc:  # noqa: BLE001 - preserve target engine failure type
        success = False
        error_type = type(exc).__name__
        error_message = str(exc).replace(text, "<benchmark-text>")[:500]
    elapsed = time.perf_counter() - started
    after = _memory_snapshot()
    return {
        "sample": sample_name,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_chars": len(text),
        "text_tokens": token_count,
        "tokenizer": token_source,
        "elapsed_seconds": elapsed,
        "audio_duration": _audio_seconds(output) if success else None,
        "memory_allocated_before": before["memory_allocated"],
        "memory_allocated_after": after["memory_allocated"],
        "memory_reserved_before": before["memory_reserved"],
        "memory_reserved_after": after["memory_reserved"],
        "max_memory_allocated": after["max_memory_allocated"],
        "free_vram_before": before["free_vram"],
        "free_vram_after": after["free_vram"],
        "total_vram": after["total_vram"] or before["total_vram"],
        "success": success,
        "error_type": error_type,
        "error_message": error_message,
    }


def _tier_is_safe(runs: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not runs or any(not item["success"] for item in runs):
        reasons.append("inference failure")
    if runs and not any(
        item.get("total_vram") is not None
        and item.get("max_memory_allocated") is not None
        and item.get("free_vram_after") is not None
        for item in runs
    ):
        reasons.append("VRAM telemetry unavailable")
    if any(
        "out of memory" in str(item.get("error_message", "")).lower()
        or "outofmemory" in str(item.get("error_type", "")).lower()
        for item in runs
    ):
        reasons.append("OOM observed")
    for item in runs:
        peak = item.get("max_memory_allocated")
        total = item.get("total_vram")
        if peak is not None and total and peak / total > MAX_VRAM_RATIO:
            reasons.append("peak VRAM exceeded 85%")
            break
    free_values = [
        item["free_vram_after"]
        for item in runs
        if item.get("free_vram_after") is not None
    ]
    if free_values and min(free_values) < MINIMUM_FREE_VRAM_MB * 1024 * 1024:
        reasons.append("free VRAM fell below 1536 MB")
    reserved = [
        item["memory_reserved_after"]
        for item in runs
        if item.get("memory_reserved_after") is not None
    ]
    if len(reserved) >= 2 and reserved[-1] - reserved[0] > GROWTH_LIMIT_MB * 1024 * 1024:
        reasons.append("reserved VRAM grew by more than 1536 MB")
    return not reasons, sorted(set(reasons))


def _recommend(runs: list[dict[str, Any]]) -> dict[str, Any]:
    tiers: dict[int, dict[str, Any]] = {}
    for tier in TOKEN_TIERS:
        tier_runs = [item for item in runs if item["tier"] == tier]
        safe, reasons = _tier_is_safe(tier_runs)
        tiers[tier] = {"safe": safe, "reasons": reasons, "runs": len(tier_runs)}
    selected = next(
        (
            tier
            for tier in (100, 80, 60, 40)
            if tiers[tier]["safe"]
        ),
        40,
    )
    return {
        "max_text_tokens": min(selected, SAFE_AUTOMATIC_CEILING),
        "verified": bool(runs) and tiers[selected]["safe"],
        "policy": "may lower from 100; never automatically raise above 100",
        "tiers": {str(key): value for key, value in tiers.items()},
    }


def _write_markdown(report: dict[str, Any], destination: Path) -> None:
    provenance = report["provenance"]
    recommendation = report["recommendation"]
    lines = [
        "# IndexTTS2 目标机 Benchmark 结果",
        "",
        f"- 捕获时间：`{provenance['captured_at']}`",
        f"- Git SHA：`{provenance['git_sha']}`",
        f"- Git 状态：`{provenance['git_status'] or 'clean'}`",
        f"- GPU：`{provenance['gpu'].get('name', 'unavailable')}`",
        f"- 推荐最大 Token：**{recommendation['max_text_tokens']}**",
        f"- 已验证：**{recommendation['verified']}**",
        "",
        "## 档位判定",
        "",
        "| Token | 安全 | Runs | 排除原因 |",
        "|---:|:---:|---:|---|",
    ]
    for tier in TOKEN_TIERS:
        item = recommendation["tiers"][str(tier)]
        lines.append(
            f"| {tier} | {'是' if item['safe'] else '否'} | {item['runs']} | "
            f"{'、'.join(item['reasons']) or '-'} |"
        )
    lines.extend(
        [
            "",
            "构造器签名：",
            "",
            f"`{provenance['signatures'].get('constructor', 'unavailable')}`",
            "",
            "infer 签名：",
            "",
            f"`{provenance['signatures'].get('infer', 'unavailable')}`",
            "",
        ]
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")


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
    parser.add_argument(
        "--markdown-report",
        type=Path,
        default=Path("docs/v4/indextts2-benchmark-results.md"),
    )
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
        "schema_version": "audiobook-indextts-benchmark-v2",
        "mode": "inference" if args.run_inference else "audit-only",
        "provenance": _provenance(args.checkout, engine_class),
        "load_error": load_error,
        "tiers": list(TOKEN_TIERS),
        "sample_categories": [item[0] for item in SAMPLES],
        "runs": [],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.run_inference:
        if not args.model_dir or not args.speaker_prompt:
            parser.error("--model-dir and --speaker-prompt are required for inference")
        if args.repeat < 10 or args.recommended_repeat < 100:
            parser.error("--repeat must be >=10 and --recommended-repeat must be >=100")
        assert engine_class is not None
        for tier in TOKEN_TIERS:
            cold_engine = _new_engine(engine_class, args.model_dir)
            sample_name, sample = SAMPLES[tier % len(SAMPLES)]
            text, _tokens, _source = _text_for_tier(cold_engine, sample, tier)
            cold_output = args.output_dir / f"tier-{tier}-cold.wav"
            cold = _invoke(
                cold_engine,
                text,
                cold_output,
                args.speaker_prompt,
                tier,
                sample_name,
            )
            cold.update({"tier": tier, "run": 0, "cold": True})
            report["runs"].append(cold)
            _release_engine(cold_engine)
            cold_engine = None

            engine = _new_engine(engine_class, args.model_dir)
            repeats = args.recommended_repeat if tier == 100 else args.repeat
            for run_index in range(1, repeats + 1):
                sample_name, sample = SAMPLES[(run_index - 1) % len(SAMPLES)]
                text, _tokens, _source = _text_for_tier(engine, sample, tier)
                output = args.output_dir / f"tier-{tier}-run-{run_index:03d}.wav"
                result = _invoke(
                    engine,
                    text,
                    output,
                    args.speaker_prompt,
                    tier,
                    sample_name,
                )
                result.update(
                    {"tier": tier, "run": run_index, "cold": False}
                )
                report["runs"].append(result)
            _release_engine(engine)
            engine = None

    report["recommendation"] = _recommend(report["runs"])
    destination = args.output_dir / f"{PROFILE_NAME}.json"
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_markdown(report, args.markdown_report)
    print(destination)
    print(args.markdown_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
