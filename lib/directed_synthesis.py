"""执行 structured_script v3 的段内停顿和边界留白。"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path


def _value(segment, name: str, default=None):
    if isinstance(segment, dict):
        return segment.get(name, default)
    return getattr(segment, name, default)


def text_parts(segment) -> list[tuple[str, int]]:
    """按 pauses 字符位置切分文本，返回 ``(文本, 后置停顿毫秒)``。"""
    text = str(_value(segment, "text", "") or "")
    if not text:
        raise ValueError("导演 segment 文本为空")
    pauses = sorted(
        (
            max(0, min(len(text), int(item.get("position", 0)))),
            max(0, min(3000, int(item.get("duration", 0)))),
        )
        for item in (_value(segment, "pauses", []) or [])
        if isinstance(item, dict)
    )
    parts = []
    cursor = 0
    for position, duration in pauses:
        if position <= cursor:
            continue
        chunk = text[cursor:position].strip()
        if chunk:
            parts.append((chunk, duration))
        cursor = position
    tail = text[cursor:].strip()
    if tail:
        parts.append((tail, 0))
    return parts or [(text, 0)]


def synthesize(
    segment,
    speaker_audio: str,
    output_path: str,
    *,
    emotion: str,
    emo_alpha: float,
    speech_rate: float,
    pinyin_hints=None,
    num_beams: int = 2,
    engine=None,
    trace=None,
    trace_chapter_id: str | None = None,
) -> str:
    """合成一个 segment；v3 停顿存在时拆段合成并拼回单个 WAV。"""
    if engine is None:
        from . import tts_engine as engine

    parts = text_parts(segment)
    pause_before = max(0, min(3000, int(_value(segment, "pause_before", 0) or 0)))
    pause_after = max(0, min(3000, int(_value(segment, "pause_after", 0) or 0)))
    has_directing = (
        pause_before > 0
        or pause_after > 0
        or any(duration > 0 for _, duration in parts)
    )
    if not has_directing and len(parts) == 1:
        engine_kwargs = {
            "text": parts[0][0],
            "speaker_audio": speaker_audio,
            "emotion": emotion,
            "emo_alpha": emo_alpha,
            "speech_rate": speech_rate,
            "pinyin_hints": pinyin_hints,
            "output_path": output_path,
            "num_beams": num_beams,
        }
        if trace is not None:
            engine_kwargs.update({
                "trace": trace,
                "trace_segment_id": _value(segment, "id", ""),
                "trace_chapter_id": trace_chapter_id,
                "trace_part_index": 0,
            })
        started = None
        if trace is not None:
            started = time.perf_counter()
        try:
            return engine.synthesize_segment(**engine_kwargs)
        finally:
            if trace is not None and started is not None:
                try:
                    trace.add_timing(
                        "directed_synthesis_total",
                        time.perf_counter() - started,
                        scope="segment",
                        chapter_id=trace_chapter_id,
                        segment_id=_value(segment, "id", ""),
                    )
                except Exception as exc:  # noqa: BLE001  # diagnostics must not alter TTS
                    del exc

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    task_dir = target.parent / f".{target.stem}_parts_{uuid.uuid4().hex}"
    task_dir.mkdir(parents=True, exist_ok=True)
    wav_parts = []
    directed_started = None
    if trace is not None:
        directed_started = time.perf_counter()
    try:
        for index, (text, internal_pause) in enumerate(parts):
            part_path = task_dir / f"{index:03d}.wav"
            engine_kwargs = {
                "text": text,
                "speaker_audio": speaker_audio,
                "emotion": emotion,
                "emo_alpha": emo_alpha,
                "speech_rate": speech_rate,
                "pinyin_hints": pinyin_hints,
                "output_path": str(part_path),
                "num_beams": num_beams,
            }
            if trace is not None:
                engine_kwargs.update({
                    "trace": trace,
                    "trace_segment_id": _value(segment, "id", ""),
                    "trace_chapter_id": trace_chapter_id,
                    "trace_part_index": index,
                })
            engine.synthesize_segment(**engine_kwargs)
            wav_parts.append((str(part_path), internal_pause))
        compose_started = None
        if trace is not None:
            compose_started = time.perf_counter()
        try:
            compose(
                wav_parts,
                pause_before=pause_before,
                pause_after=pause_after,
                output_path=output_path,
            )
        finally:
            if trace is not None and compose_started is not None:
                try:
                    trace.add_timing(
                        "wav_compose",
                        time.perf_counter() - compose_started,
                        scope="segment",
                        chapter_id=trace_chapter_id,
                        segment_id=_value(segment, "id", ""),
                    )
                except Exception as exc:  # noqa: BLE001  # diagnostics must not alter TTS
                    del exc
    finally:
        for part_path, _ in wav_parts:
            try:
                os.remove(part_path)
            except OSError:
                pass
        try:
            task_dir.rmdir()
        except OSError:
            pass
        if trace is not None and directed_started is not None:
            try:
                trace.add_timing(
                    "directed_synthesis_total",
                    time.perf_counter() - directed_started,
                    scope="segment",
                    chapter_id=trace_chapter_id,
                    segment_id=_value(segment, "id", ""),
                )
            except Exception as exc:  # noqa: BLE001  # diagnostics must not alter TTS
                del exc
    return output_path


def compose(
    wav_parts: list[tuple[str, int]],
    *,
    pause_before: int,
    pause_after: int,
    output_path: str,
) -> None:
    """拼接语音片段并插入前置、内部和后置静音。"""
    import numpy as np

    from . import audio_format

    if not wav_parts:
        raise ValueError("导演合成没有产生任何音频片段")
    rate = audio_format.DEFAULT_TARGET_RATE
    arrays = []
    if pause_before > 0:
        arrays.append(np.zeros(int(rate * pause_before / 1000), dtype=np.int16))
    for wav_path, internal_pause in wav_parts:
        audio = audio_format.load_and_normalize_wav(wav_path, target_rate=rate)
        arrays.append(audio.data)
        if internal_pause > 0:
            arrays.append(
                np.zeros(int(rate * internal_pause / 1000), dtype=np.int16)
            )
    if pause_after > 0:
        arrays.append(np.zeros(int(rate * pause_after / 1000), dtype=np.int16))
    temp_path = f"{output_path}.{uuid.uuid4().hex}.tmp.wav"
    audio_format.write_wav(temp_path, np.concatenate(arrays), rate)
    os.replace(temp_path, output_path)
