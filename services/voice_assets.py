"""Stable voice asset discovery and safe TTS reference derivation."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import time
import uuid
from typing import Any

from lib import config, voice_lib
from lib.procutil import run_no_window
from repositories._atomic import atomic_write
from repositories._file_lock import RepositoryFileLock

AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"})
REFERENCE_VERSION = 1
REFERENCE_MIN_SECONDS = 6.0
REFERENCE_TARGET_SECONDS = 8.0
REFERENCE_MAX_SECONDS = 10.0
REFERENCE_SAMPLE_RATE = 22050
REFERENCE_CHANNELS = 1
REFERENCE_SAMPLE_WIDTH = 2
REFERENCE_FRAME_MS = 25

_REFERENCE_WAV_SUFFIX = ".reference.wav"
_REFERENCE_JSON_SUFFIX = ".json"
_REFERENCE_READY = "ready"
_REFERENCE_NEEDS = "needs_reference"
_REFERENCE_MANUAL = "manual_required"
_REFERENCE_ERROR = "error"

logger = logging.getLogger(__name__)


class VoiceAssetError(ValueError):
    """Structured domain error raised by the voice asset service."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_issue(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **self.details}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _category(filename: str) -> str:
    try:
        return str(voice_lib._category_of(filename))
    except AttributeError:
        stem = os.path.basename(filename).split("_", 1)[0]
        return stem or "未分类"


def _is_reference_filename(filename: str) -> bool:
    return str(filename or "").lower().endswith(_REFERENCE_WAV_SUFFIX)


def _reference_path(source_path: str) -> str:
    stem, _ = os.path.splitext(os.path.abspath(source_path))
    return stem + _REFERENCE_WAV_SUFFIX


def _metadata_path(reference_path: str) -> str:
    if reference_path.lower().endswith(".wav"):
        return reference_path[:-4] + _REFERENCE_JSON_SUFFIX
    return reference_path + _REFERENCE_JSON_SUFFIX


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _reference_path_for_source(source_path: str, source_hash: str) -> str:
    """Keep same-stem sources from overwriting one another's reference."""
    primary = _reference_path(source_path)
    metadata = _read_json(_metadata_path(primary))
    if metadata.get("source_file") not in (None, "", os.path.basename(source_path)):
        stem, _ = os.path.splitext(os.path.abspath(source_path))
        return f"{stem}.{source_hash[:8]}{_REFERENCE_WAV_SUFFIX}"
    return primary


def _ffmpeg() -> str | None:
    candidate = str(config.get_ffmpeg_path() or "ffmpeg")
    if os.path.isabs(candidate) and os.path.isfile(candidate):
        return candidate
    return shutil.which(candidate)


def _normalize_pcm(data: Any, rate: int) -> tuple[Any, int]:
    """Return mono PCM16 at the sample rate IndexTTS uses by default."""
    import numpy as np

    from lib import audio_format

    array = np.asarray(data)
    if array.size == 0:
        raise ValueError("音频为空")
    if array.ndim > 1:
        array = array.mean(axis=1)
    if np.issubdtype(array.dtype, np.integer):
        info = np.iinfo(array.dtype)
        if np.issubdtype(array.dtype, np.unsignedinteger):
            midpoint = (float(info.max) + 1.0) / 2.0
            array = (array.astype(np.float64) - midpoint) / midpoint
        else:
            array = array.astype(np.float64) / max(abs(float(info.min)), abs(float(info.max)))
    else:
        array = array.astype(np.float64)
    if not np.all(np.isfinite(array)):
        raise ValueError("音频包含非有限采样值")
    array = np.clip(array, -1.0, 1.0)
    if int(rate) != REFERENCE_SAMPLE_RATE:
        array = audio_format._resample_linear(array, int(rate), REFERENCE_SAMPLE_RATE)
        rate = REFERENCE_SAMPLE_RATE
    return (np.clip(array, -1.0, 1.0) * 32767.0).astype(np.int16), int(rate)


def _decode_audio(path: str) -> tuple[Any, int]:
    """Decode WAV directly and use the existing FFmpeg install for other formats."""
    from scipy.io import wavfile

    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"音频文件不存在: {path}")
    try:
        rate, data = wavfile.read(path)
        return _normalize_pcm(data, int(rate))
    except Exception as wav_error:
        executable = _ffmpeg()
        if not executable:
            raise RuntimeError(f"无法读取音频，且未检测到 ffmpeg: {path}") from wav_error
        try:
            result = run_no_window(
                [
                    executable, "-v", "error", "-i", path,
                    "-ac", "1", "-ar", str(REFERENCE_SAMPLE_RATE),
                    "-f", "s16le", "-",
                ],
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise RuntimeError(f"ffmpeg 解码失败: {path}") from exc
        if result.returncode != 0 or not result.stdout:
            detail = (result.stderr or b"")[-300:]
            raise RuntimeError(f"ffmpeg 解码失败: {path} ({detail!r})") from wav_error
        try:
            import numpy as np

            data = np.frombuffer(result.stdout, dtype="<i2").copy()
            return _normalize_pcm(data, REFERENCE_SAMPLE_RATE)
        except Exception as exc:
            raise RuntimeError(f"ffmpeg 输出不是有效 PCM: {path}") from exc


def _frame_stats(data: Any, rate: int) -> tuple[Any, Any, Any, float]:
    import numpy as np

    frame_size = max(1, round(rate * REFERENCE_FRAME_MS / 1000.0))
    frame_count = max(1, math.ceil(len(data) / frame_size))
    padded = np.pad(
        np.asarray(data, dtype=np.float64) / 32768.0,
        (0, frame_count * frame_size - len(data)),
    )
    frames = padded.reshape(frame_count, frame_size)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    peaks = np.max(np.abs(frames), axis=1)
    floor = float(np.percentile(rms, 20))
    upper = float(np.percentile(rms, 80))
    middle = float(np.percentile(rms, 40))
    # Keep an all-voice clip voiced while still separating speech from a
    # quiet/noisy floor in mixed recordings.  Cap the threshold so a loud
    # clipped section cannot hide a quieter clean section.
    threshold = max(0.006, min(floor * 2.5, upper * 0.4, 0.02), middle * 0.18)
    voiced = rms >= threshold
    return rms, peaks, voiced, threshold


def _longest_run(mask: Any) -> int:
    longest = current = 0
    for value in mask:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def _window_metrics(rms: Any, peaks: Any, voiced: Any, start: int, end: int, threshold: float) -> dict[str, float]:
    import numpy as np

    window_rms = rms[start:end]
    window_peaks = peaks[start:end]
    window_voiced = voiced[start:end]
    voiced_ratio = float(np.mean(window_voiced)) if len(window_voiced) else 0.0
    voiced_rms = window_rms[window_voiced]
    mean_rms = float(np.mean(voiced_rms)) if len(voiced_rms) else 0.0
    stability = 0.0
    if len(voiced_rms):
        stability = max(0.0, 1.0 - float(np.std(np.log(np.maximum(voiced_rms, 1e-6)))) / 1.2)
    reference = max(float(np.median(voiced_rms)) if len(voiced_rms) else threshold, 1e-6)
    boundary = 1.0 - min(
        1.0,
        (float(window_rms[0]) + float(window_rms[-1])) / (2.0 * reference),
    ) if len(window_rms) else 0.0
    return {
        "voiced_ratio": voiced_ratio,
        "silence_ratio": 1.0 - voiced_ratio,
        "continuity": _longest_run(window_voiced) / max(len(window_voiced), 1),
        "stability": stability,
        "clipping_ratio": float(np.mean(window_peaks >= 0.98)) if len(window_peaks) else 1.0,
        "mean_rms": mean_rms,
        "boundary": max(0.0, boundary),
    }


def _select_reference(data: Any, rate: int) -> tuple[Any, dict[str, Any]]:
    """Choose one deterministic continuous speech window without diarization."""
    import numpy as np

    duration = len(data) / max(rate, 1)
    rms, peaks, voiced, threshold = _frame_stats(data, rate)
    frame_size = max(1, round(rate * REFERENCE_FRAME_MS / 1000.0))
    if duration < REFERENCE_MIN_SECONDS:
        raise VoiceAssetError(
            "REFERENCE_AUDIO_MANUAL_REQUIRED",
            "原始音频不足 6 秒，无法可靠生成 TTS 参考音频",
            reference_status=_REFERENCE_MANUAL,
            original_duration=duration,
        )

    window_seconds = min(REFERENCE_TARGET_SECONDS, duration)
    window_samples = min(len(data), max(1, round(window_seconds * rate)))
    window_frames = max(1, math.ceil(window_samples / frame_size))
    max_start = max(0, len(rms) - window_frames)
    step = max(1, round(0.25 * rate / frame_size))
    starts = list(range(0, max_start + 1, step))
    if max_start not in starts:
        starts.append(max_start)

    candidates: list[tuple[float, int, int, dict[str, float]]] = []
    for start in starts:
        end = min(len(rms), start + window_frames)
        metrics = _window_metrics(rms, peaks, voiced, start, end, threshold)
        edge_seconds = min(start * frame_size / rate, max((len(data) - end * frame_size) / rate, 0.0))
        edge_score = min(edge_seconds / 1.0, 1.0)
        volume_score = min(metrics["mean_rms"] / 0.05, 1.0)
        score = (
            4.0 * metrics["voiced_ratio"]
            + 2.0 * metrics["continuity"]
            + 1.5 * metrics["stability"]
            + 0.6 * metrics["boundary"]
            + 0.4 * edge_score
            + 0.5 * volume_score
            - 4.0 * metrics["silence_ratio"]
            - 6.0 * metrics["clipping_ratio"]
        )
        candidates.append((score, start, end, metrics))
    if not candidates:
        raise VoiceAssetError(
            "REFERENCE_AUDIO_MANUAL_REQUIRED",
            "无法可靠自动生成 TTS 参考音频",
            reference_status=_REFERENCE_MANUAL,
            original_duration=duration,
        )
    score, start, end, metrics = max(candidates, key=lambda item: (item[0], -item[1]))
    if (
        metrics["voiced_ratio"] < 0.65
        or metrics["continuity"] < 0.35
        or metrics["mean_rms"] < 0.005
        or metrics["clipping_ratio"] > 0.15
    ):
        raise VoiceAssetError(
            "REFERENCE_AUDIO_MANUAL_REQUIRED",
            "无法可靠自动生成 TTS 参考音频。请提供一段 6–10 秒、单人、清晰、无背景音乐的语音。",
            reference_status=_REFERENCE_MANUAL,
            original_duration=duration,
            selection_score=round(score, 4),
        )
    selected = np.asarray(data)[
        start * frame_size:min(len(data), start * frame_size + window_samples)
    ]
    return selected.astype(np.int16, copy=False), {
        "start_seconds": round(start * frame_size / rate, 3),
        "duration": round(len(selected) / rate, 3),
        "score": round(score, 4),
        **{key: round(value, 4) for key, value in metrics.items()},
    }


def _validate_reference(path: str) -> dict[str, Any]:
    import wave

    import numpy as np
    from scipy.io import wavfile

    try:
        with wave.open(path, "rb") as audio:
            channels = int(audio.getnchannels())
            width = int(audio.getsampwidth())
            rate = int(audio.getframerate())
            frames = int(audio.getnframes())
        actual_rate, data = wavfile.read(path)
    except Exception as exc:
        raise ValueError("reference 不是可读取 WAV") from exc
    values = np.asarray(data)
    duration = frames / rate if rate else 0.0
    if (
        channels != REFERENCE_CHANNELS
        or width != REFERENCE_SAMPLE_WIDTH
        or int(actual_rate) != REFERENCE_SAMPLE_RATE
        or not (REFERENCE_MIN_SECONDS <= duration <= REFERENCE_MAX_SECONDS)
        or values.size == 0
    ):
        raise ValueError("reference WAV 规格或时长不符合要求")
    if not np.all(np.isfinite(values)) or not np.any(values):
        raise ValueError("reference WAV 为空或包含非有限采样值")
    peak = float(np.max(np.abs(values.astype(np.float64)))) / 32768.0
    if peak >= 0.999:
        raise ValueError("reference WAV 存在 gross clipping")
    _, _, voiced, _ = _frame_stats(values, int(actual_rate))
    if float(np.mean(voiced)) < 0.55:
        raise ValueError("reference WAV 主要是静音")
    return {
        "duration": round(duration, 3),
        "sample_rate": int(actual_rate),
        "channels": channels,
        "sample_width": width,
        "peak": round(peak, 5),
        "voiced_ratio": round(float(np.mean(voiced)), 4),
    }


class VoiceAssetService:
    """Scan the global voice library and own derived TTS references.

    ``voice_asset_id`` is ``voice_`` plus the first 12 hexadecimal characters
    of the file's full SHA-256 for new assets.  Once a reference sidecar exists,
    that ID remains stable across source-byte edits so cast bindings do not
    change.  Two byte-identical files initially represent the same reusable
    asset.
    """

    @staticmethod
    def _scan_records() -> list[dict[str, Any]]:
        root = config.get_voice_library()
        if not root or not os.path.isdir(root):
            return []
        records: list[dict[str, Any]] = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return []
        for filename in names:
            path = os.path.join(root, filename)
            if (
                not os.path.isfile(path)
                or _is_reference_filename(filename)
                or os.path.splitext(filename)[1].lower() not in AUDIO_EXTENSIONS
            ):
                continue
            try:
                size_bytes = os.path.getsize(path)
                digest = _sha256(path)
            except OSError:
                # A file can disappear while the library is being scanned.
                continue
            stem = os.path.splitext(filename)[0]
            category = _category(filename)
            default_reference = _reference_path_for_source(path, digest)
            metadata = _read_json(_metadata_path(default_reference))
            metadata_matches = metadata.get("source_hash") == digest
            records.append({
                # A first-seen asset keeps the historical hash ID.  Once a
                # reference sidecar exists, its ID survives source-byte edits.
                "voice_asset_id": (
                    str(metadata.get("voice_asset_id") or f"voice_{digest[:12]}")
                    if metadata.get("source_file") in (None, "", filename)
                    else f"voice_{digest[:12]}"
                ),
                "name": stem,
                "category": category,
                "tags": [],
                "file_name": filename,
                "sha256": digest,
                "size_bytes": size_bytes,
                "original_audio": filename,
                "original_duration": metadata.get("original_duration") if metadata_matches else None,
                "reference_audio": os.path.basename(default_reference),
                "reference_duration": metadata.get("reference_duration") if metadata_matches else None,
                "source_hash": digest,
                "reference_version": metadata.get("reference_version") if metadata_matches else None,
                "reference_status": (
                    str(metadata.get("reference_status") or _REFERENCE_NEEDS)
                    if metadata_matches
                    and (
                        os.path.isfile(default_reference)
                        or metadata.get("reference_status") in {_REFERENCE_MANUAL, _REFERENCE_ERROR}
                    )
                    else _REFERENCE_NEEDS
                ),
                "reference_method": str(metadata.get("reference_method") or "") if metadata_matches else "",
                "_path": os.path.abspath(path),
            })
        return records

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record[key]
            for key in (
                "voice_asset_id", "name", "category", "tags", "file_name",
                "sha256", "size_bytes", "original_audio", "original_duration",
                "reference_audio", "reference_duration", "source_hash",
                "reference_version", "reference_status", "reference_method",
            )
        }

    @classmethod
    def list_assets(cls, search: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        query = str(search or "").strip().casefold()
        requested_category = str(category or "").strip()
        result: list[dict[str, Any]] = []
        for record in cls._scan_records():
            if requested_category and record["category"] != requested_category:
                continue
            if query:
                haystack = " ".join(
                    [record["name"], record["file_name"], record["category"], *record["tags"]]
                ).casefold()
                if query not in haystack:
                    continue
            result.append(cls._public(record))
        return result

    @classmethod
    def list_voice_assets(cls, search: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        """Named alias used by the MCP adapter and external callers."""
        return cls.list_assets(search, category)

    @classmethod
    def get_record(cls, voice_asset_id: str) -> dict[str, Any]:
        asset_id = str(voice_asset_id or "").strip()
        if not asset_id:
            raise VoiceAssetError("VOICE_ASSET_ID_REQUIRED", "voice_asset_id 不能为空")
        matches = [record for record in cls._scan_records() if record["voice_asset_id"] == asset_id]
        if not matches:
            raise VoiceAssetError(
                "VOICE_ASSET_NOT_FOUND",
                "指定音色资产不存在",
                voice_asset_id=asset_id,
            )
        # Identical files share an ID; choose the first deterministic filename
        # while exposing all duplicates only through list_voice_assets.
        return matches[0]

    @classmethod
    def get_asset(cls, voice_asset_id: str) -> dict[str, Any]:
        return cls._public(cls.get_record(voice_asset_id))

    @classmethod
    def get_voice_asset(cls, voice_asset_id: str) -> dict[str, Any]:
        return cls.get_asset(voice_asset_id)

    @classmethod
    def resolve_path(cls, voice_asset_id: str) -> str:
        record = cls.get_record(voice_asset_id)
        path = record["_path"]
        if not os.path.isfile(path):
            raise VoiceAssetError(
                "VOICE_ASSET_AUDIO_MISSING",
                "音色资产文件不存在",
                voice_asset_id=voice_asset_id,
                file_name=record["file_name"],
            )
        return path

    @classmethod
    def asset_id_for_path(cls, path: str) -> str:
        """Return the same ID rule for a validated audio path."""
        record = cls._record_for_path(path)
        if record is not None:
            return str(record["voice_asset_id"])
        try:
            digest = _sha256(path)
        except (OSError, TypeError) as exc:
            raise VoiceAssetError("VOICE_ASSET_AUDIO_MISSING", "音频文件无法读取", path=str(path)) from exc
        return f"voice_{digest[:12]}"

    @staticmethod
    def _record_status(record: dict[str, Any], *, validate: bool = True) -> dict[str, Any]:
        source = str(record.get("_path") or "")
        source_hash = str(record.get("sha256") or "")
        reference = _reference_path_for_source(source, source_hash)
        metadata = _read_json(_metadata_path(reference))
        metadata_matches = metadata.get("source_hash") == source_hash
        result: dict[str, Any] = {
            "voice_asset_id": record.get("voice_asset_id"),
            "original_audio": record.get("file_name"),
            "original_duration": metadata.get("original_duration") if metadata_matches else None,
            "reference_audio": os.path.basename(reference),
            "reference_duration": metadata.get("reference_duration") if metadata_matches else None,
            "source_hash": source_hash,
            "reference_version": metadata.get("reference_version") if metadata_matches else None,
            "reference_method": str(metadata.get("reference_method") or "") if metadata_matches else "",
            "reference_status": _REFERENCE_NEEDS,
            "_source_path": source,
            "_reference_path": reference,
        }
        if metadata_matches:
            result["original_duration"] = metadata.get("original_duration")
            result["reference_audio"] = str(metadata.get("reference_audio") or os.path.basename(reference))
            result["reference_duration"] = metadata.get("reference_duration")
            result["reference_version"] = metadata.get("reference_version")
            result["reference_method"] = str(metadata.get("reference_method") or "")
            status = str(metadata.get("reference_status") or _REFERENCE_NEEDS)
            if (
                status in {_REFERENCE_MANUAL, _REFERENCE_ERROR}
                and metadata.get("reference_version") == REFERENCE_VERSION
            ):
                result["reference_status"] = status
                result["reference_error"] = str(metadata.get("error") or "")
                return result
            if (
                status == _REFERENCE_READY
                and metadata.get("reference_version") == REFERENCE_VERSION
                and os.path.isfile(reference)
            ):
                if not validate:
                    result["reference_status"] = _REFERENCE_READY
                    return result
                try:
                    details = _validate_reference(reference)
                except (OSError, ValueError) as exc:
                    result["reference_error"] = str(exc)
                else:
                    result["reference_status"] = _REFERENCE_READY
                    result["reference_duration"] = details["duration"]
                    return result
        try:
            data, rate = _decode_audio(source)
        except Exception as exc:  # noqa: BLE001
            result["reference_status"] = _REFERENCE_ERROR
            result["reference_error"] = str(exc)
            return result
        duration = len(data) / max(rate, 1)
        result["original_duration"] = round(duration, 3)
        try:
            _select_reference(data, rate)
        except VoiceAssetError as exc:
            result["reference_status"] = (
                _REFERENCE_MANUAL
                if exc.code == "REFERENCE_AUDIO_MANUAL_REQUIRED"
                else _REFERENCE_ERROR
            )
            result["reference_error"] = str(exc)
        else:
            result["reference_status"] = _REFERENCE_NEEDS
        return result

    @classmethod
    def get_asset_status(cls, voice_asset_id: str) -> dict[str, Any]:
        """Return validated reference state without exposing absolute paths."""
        record = cls.get_record(voice_asset_id)
        status = cls._record_status(record)
        status.pop("_source_path", None)
        status.pop("_reference_path", None)
        return {**cls._public(record), **status}

    @classmethod
    def status_for_path(cls, source_path: str) -> dict[str, Any]:
        """Return reference state for a project snapshot or global source path."""
        source = os.path.abspath(str(source_path or ""))
        if not os.path.isfile(source):
            raise VoiceAssetError("VOICE_ASSET_AUDIO_MISSING", "音频文件不存在", path=source)
        digest = _sha256(source)
        global_record = next(
            (item for item in cls._scan_records() if item.get("sha256") == digest),
            None,
        )
        if global_record is not None:
            return cls._record_status(global_record)
        return cls._record_status({
            "voice_asset_id": f"voice_{digest[:12]}",
            "file_name": os.path.basename(source),
            "sha256": digest,
            "size_bytes": os.path.getsize(source),
            "name": os.path.splitext(os.path.basename(source))[0],
            "category": _category(os.path.basename(source)),
            "tags": [],
            "_path": source,
        })

    @classmethod
    def _record_for_path(cls, source_path: str) -> dict[str, Any] | None:
        source = os.path.abspath(str(source_path or ""))
        if not os.path.isfile(source):
            return None
        digest = _sha256(source)
        return next(
            (item for item in cls._scan_records() if item.get("sha256") == digest),
            None,
        )

    @classmethod
    def ensure_reference(
        cls,
        voice_asset_id: str | None = None,
        source_path: str | None = None,
        *,
        force: bool = False,
    ) -> str:
        """Return one validated derived reference, generating it atomically on demand."""
        if voice_asset_id:
            record = cls.get_record(voice_asset_id)
            source = str(record["_path"])
        else:
            source = os.path.abspath(str(source_path or ""))
            if not os.path.isfile(source):
                raise VoiceAssetError("VOICE_ASSET_AUDIO_MISSING", "音频文件不存在", path=source)
            record = cls._record_for_path(source)
            if record is not None:
                source = str(record["_path"])
        source_hash = _sha256(source)
        stable_asset_id = str(
            voice_asset_id
            or (record or {}).get("voice_asset_id")
            or f"voice_{source_hash[:12]}"
        )
        reference = _reference_path_for_source(source, source_hash)
        metadata_path = _metadata_path(reference)
        with RepositoryFileLock(metadata_path + ".lock", timeout=120.0):
            metadata = _read_json(metadata_path)
            if (
                not force
                and metadata.get("source_hash") == source_hash
                and metadata.get("reference_version") == REFERENCE_VERSION
            ):
                status = str(metadata.get("reference_status") or _REFERENCE_NEEDS)
                if status == _REFERENCE_MANUAL:
                    raise VoiceAssetError(
                        "REFERENCE_AUDIO_MANUAL_REQUIRED",
                        "无法可靠自动生成 TTS 参考音频。请提供一段 6–10 秒、单人、清晰、无背景音乐的语音。",
                        voice_asset_id=stable_asset_id,
                        reference_status=_REFERENCE_MANUAL,
                    )
                if status == _REFERENCE_ERROR:
                    raise VoiceAssetError(
                        "REFERENCE_AUDIO_GENERATION_FAILED",
                        str(metadata.get("error") or "TTS 参考音频生成失败"),
                        voice_asset_id=stable_asset_id,
                        reference_status=_REFERENCE_ERROR,
                    )
                if status == _REFERENCE_READY and os.path.isfile(reference):
                    try:
                        _validate_reference(reference)
                    except (OSError, ValueError):
                        pass
                    else:
                        _emit_reference_event(
                            "resolved", stable_asset_id,
                            metadata.get("original_duration"), metadata.get("reference_duration"),
                            metadata.get("reference_method"), True,
                        )
                        return reference
            try:
                data, rate = _decode_audio(source)
                original_duration = len(data) / max(rate, 1)
                selected, selection = _select_reference(data, rate)
                temp = f"{reference}.{uuid.uuid4().hex}.part"
                from lib.audio_format import write_wav

                write_wav(temp, selected, REFERENCE_SAMPLE_RATE)
                try:
                    details = _validate_reference(temp)
                    if os.path.getsize(temp) <= 44:
                        raise ValueError("reference WAV 为空")
                    if _sha256(source) != source_hash:
                        raise ValueError("原始音频在生成期间发生变化")
                    os.replace(temp, reference)
                finally:
                    if os.path.isfile(temp):
                        os.remove(temp)
            except VoiceAssetError as exc:
                _write_reference_status(
                    metadata_path, source, source_hash, _REFERENCE_MANUAL,
                    voice_asset_id=stable_asset_id,
                    reference_path=reference,
                    original_duration=exc.details.get("original_duration"),
                    error=str(exc),
                )
                _emit_reference_event(
                    "failed", stable_asset_id,
                    exc.details.get("original_duration"), None, "auto_vad", False,
                )
                raise
            except Exception as exc:  # pylint: disable=broad-except
                _write_reference_status(
                    metadata_path, source, source_hash, _REFERENCE_ERROR,
                    voice_asset_id=stable_asset_id,
                    reference_path=reference,
                    error=str(exc),
                )
                _emit_reference_event(
                    "failed", stable_asset_id,
                    None, None, "auto_vad", False,
                )
                raise VoiceAssetError(
                    "REFERENCE_AUDIO_GENERATION_FAILED",
                    f"TTS 参考音频生成失败: {exc}",
                    voice_asset_id=stable_asset_id,
                    reference_status=_REFERENCE_ERROR,
                ) from exc
            metadata = {
                "voice_asset_id": stable_asset_id,
                "reference_version": REFERENCE_VERSION,
                "source_hash": source_hash,
                "source_file": os.path.basename(source),
                "original_duration": round(original_duration, 3),
                "reference_audio": os.path.basename(reference),
                "reference_duration": details["duration"],
                "reference_sample_rate": REFERENCE_SAMPLE_RATE,
                "reference_channels": REFERENCE_CHANNELS,
                "reference_sample_width": REFERENCE_SAMPLE_WIDTH,
                "reference_status": _REFERENCE_READY,
                "reference_method": "reuse_short" if original_duration <= REFERENCE_MAX_SECONDS else "auto_vad",
                "selection": selection,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            atomic_write(metadata_path, metadata)
            _emit_reference_event(
                "resolved", stable_asset_id,
                metadata["original_duration"], metadata["reference_duration"],
                metadata["reference_method"], False,
            )
            return reference

    @classmethod
    def resolve_tts_reference(
        cls,
        voice_asset_id: str | None = None,
        source_path: str | None = None,
        allow_legacy_short: bool = False,
    ) -> str:
        """Resolve the only path allowed to reach IndexTTS."""
        source = source_path
        if voice_asset_id:
            source = cls.get_record(voice_asset_id)["_path"]
        try:
            return cls.ensure_reference(voice_asset_id=voice_asset_id, source_path=source)
        except VoiceAssetError:
            if allow_legacy_short and source and _is_short_audio(source):
                return os.path.abspath(str(source))
            raise

    @classmethod
    def prepare_reference(cls, **kwargs: Any) -> str | None:
        """Best-effort binding-time preparation; synthesis remains fail-closed."""
        try:
            return cls.ensure_reference(**kwargs)
        except VoiceAssetError as exc:
            logger.info("voice reference deferred: %s", exc)
            return None

    @classmethod
    def check_library(cls) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        counts = {
            key: 0
            for key in (_REFERENCE_READY, _REFERENCE_NEEDS, _REFERENCE_MANUAL, _REFERENCE_ERROR)
        }
        for record in cls._scan_records():
            status = cls._record_status(record)
            item = {
                **cls._public(record),
                **{key: value for key, value in status.items() if not key.startswith("_")},
            }
            items.append(item)
            state = status["reference_status"]
            counts[state] = counts.get(state, 0) + 1
        return {"total": len(items), **counts, "items": items}

    @classmethod
    def generate_missing_references(cls) -> dict[str, Any]:
        for record in cls._scan_records():
            status = cls._record_status(record)
            if status["reference_status"] != _REFERENCE_NEEDS:
                continue
            try:
                cls.ensure_reference(voice_asset_id=record["voice_asset_id"])
            except VoiceAssetError:
                continue
        return cls.check_library()


def _is_short_audio(path: str) -> bool:
    try:
        data, rate = _decode_audio(path)
    except Exception:  # noqa: BLE001
        return False
    return len(data) / max(rate, 1) < REFERENCE_MIN_SECONDS


def _write_reference_status(
    path: str,
    source: str,
    source_hash: str,
    status: str,
    *,
    voice_asset_id: str = "",
    reference_path: str = "",
    original_duration: float | None = None,
    error: str = "",
) -> None:
    atomic_write(path, {
        "voice_asset_id": voice_asset_id,
        "reference_version": REFERENCE_VERSION,
        "source_hash": source_hash,
        "source_file": os.path.basename(source),
        "original_duration": round(original_duration, 3) if original_duration is not None else None,
        "reference_audio": os.path.basename(reference_path or _reference_path(source)),
        "reference_status": status,
        "reference_method": "auto_vad",
        "error": str(error or ""),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })


def _emit_reference_event(
    event: str,
    asset_id: str,
    source_duration: Any,
    reference_duration: Any,
    method: Any,
    cache_hit: bool,
) -> None:
    logger.info(
        "voice_reference_event=%s asset_id=%s source_duration=%s reference_duration=%s method=%s cache_hit=%s",
        event,
        asset_id,
        source_duration if source_duration is not None else "",
        reference_duration if reference_duration is not None else "",
        method or "",
        str(bool(cache_hit)).lower(),
    )


def list_voice_assets(search: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
    return VoiceAssetService.list_assets(search, category)


def get_voice_asset(voice_asset_id: str) -> dict[str, Any]:
    return VoiceAssetService.get_asset(voice_asset_id)


def ensure_reference(
    voice_asset_id: str | None = None,
    source_path: str | None = None,
    *,
    force: bool = False,
) -> str:
    return VoiceAssetService.ensure_reference(
        voice_asset_id=voice_asset_id,
        source_path=source_path,
        force=force,
    )


def resolve_tts_reference(
    voice_asset_id: str | None = None,
    source_path: str | None = None,
    allow_legacy_short: bool = False,
) -> str:
    return VoiceAssetService.resolve_tts_reference(
        voice_asset_id=voice_asset_id,
        source_path=source_path,
        allow_legacy_short=allow_legacy_short,
    )


__all__ = [
    "AUDIO_EXTENSIONS",
    "REFERENCE_MAX_SECONDS",
    "REFERENCE_MIN_SECONDS",
    "REFERENCE_SAMPLE_RATE",
    "REFERENCE_TARGET_SECONDS",
    "REFERENCE_VERSION",
    "VoiceAssetError",
    "VoiceAssetService",
    "ensure_reference",
    "get_voice_asset",
    "list_voice_assets",
    "resolve_tts_reference",
]
