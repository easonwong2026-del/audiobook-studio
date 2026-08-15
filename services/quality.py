"""Unified audio revision, technical QA and human review services."""
from __future__ import annotations

import hashlib
import os
import shutil
import wave
from datetime import datetime, timezone
from typing import Any

import numpy as np

from lib import project_paths, segment_cache
from lib.tts_profile import public_profile, resolve_profile
from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository


HUMAN_REVIEW_STATUSES = frozenset({
    "unreviewed",
    "needs_review",
    "needs_fix",
    "passed",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _script_index(script: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(segment.get("id")): segment
        for chapter in script.get("chapters", [])
        if isinstance(chapter, dict)
        for segment in chapter.get("segments", [])
        if isinstance(segment, dict) and str(segment.get("id") or "").strip()
    }


class QualityService:
    """Public quality façade shared by the Web and MCP adapters."""

    @staticmethod
    def _project_relative(project_name: str, path: str) -> str:
        project_dir = os.path.realpath(ProjectRepository.get_project_dir(project_name))
        absolute = os.path.realpath(os.path.abspath(path))
        try:
            inside = os.path.commonpath([project_dir, absolute]) == project_dir
        except ValueError:
            inside = False
        if not inside:
            raise ValueError("质量记录只能引用项目目录内的音频")
        return os.path.relpath(absolute, project_dir).replace(os.sep, "/")

    @staticmethod
    def _absolute(project_name: str, relative_path: str) -> str:
        project_dir = os.path.realpath(ProjectRepository.get_project_dir(project_name))
        path = os.path.realpath(
            os.path.join(project_dir, *str(relative_path or "").replace("\\", "/").split("/"))
        )
        try:
            inside = os.path.commonpath([project_dir, path]) == project_dir
        except ValueError:
            inside = False
        if not inside:
            raise ValueError("质量记录中的音频路径越界")
        return path

    @staticmethod
    def _segment(project_name: str, segment_id: str) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        meta, script, _bindings = ProjectRepository.load_project(project_name)
        segment = _script_index(script).get(str(segment_id))
        if segment is None:
            raise KeyError(f"段落不存在: {segment_id}")
        return meta, script, segment

    @staticmethod
    def _speaker_path(
        project_name: str,
        segment: dict[str, Any],
        bindings_document: dict[str, Any],
    ) -> str:
        project_dir = ProjectRepository.get_project_dir(project_name)
        role_bindings = (
            bindings_document.get("role_bindings", {})
            if isinstance(bindings_document, dict) else {}
        )
        binding = role_bindings.get(str(segment.get("role_id") or ""))
        if not isinstance(binding, dict):
            role_name = str(segment.get("role") or segment.get("speaker") or "")
            binding = next(
                (
                    item for item in role_bindings.values()
                    if isinstance(item, dict) and item.get("role_name") == role_name
                ),
                None,
            )
        path = ""
        if isinstance(binding, dict):
            path = str(binding.get("project_voice_path") or "")
        if not path and isinstance(bindings_document, dict):
            path = str(
                bindings_document.get("bindings", {}).get(
                    str(segment.get("role") or segment.get("speaker") or "")
                ) or ""
            )
        if path and not os.path.isabs(path):
            path = os.path.join(project_dir, path)
        return path

    @classmethod
    def _identity(
        cls,
        project_name: str,
        segment: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
        speaker_override: str | None = None,
        bindings_document: dict[str, Any] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        if bindings_document is None:
            _meta, _script, bindings = ProjectRepository.load_project(project_name)
        else:
            bindings = bindings_document
        effective = {
            "emotion": segment.get("emotion", "neutral"),
            "emo_alpha": segment.get("emo_alpha", 1.0),
            "speech_rate": segment.get("speech_rate", 1.0),
            "pinyin_hints": segment.get("pinyin_hints"),
            "director_metadata": segment_cache.director_metadata_for(segment),
        }
        supplied_engine = (params or {}).get("engine_snapshot") if isinstance(params, dict) else None
        if supplied_engine:
            effective["engine_snapshot"] = public_profile(resolve_profile(supplied_engine))
        for key, value in (params or {}).items():
            if value is not None and key in effective:
                if key == "engine_snapshot":
                    effective[key] = public_profile(resolve_profile(value))
                else:
                    effective[key] = value
        speaker = str(speaker_override or "") or cls._speaker_path(
            project_name, segment, bindings
        )
        fingerprint = segment_cache.speaker_fingerprint_for_path(speaker) or ""
        cast_active = os.path.isfile(
            os.path.join(ProjectRepository.get_project_dir(project_name), "voice_cast.json")
        )
        cache_identity = segment_cache.segment_cache_key(
            str(segment.get("id")),
            str(effective["emotion"] or "neutral"),
            effective["emo_alpha"],
            effective["speech_rate"],
            effective["pinyin_hints"],
            effective["director_metadata"],
            fingerprint if cast_active or speaker_override else None,
            (effective.get("engine_snapshot") or {}).get("cache_identity")
            or (effective.get("engine_snapshot") or {}).get("engine_identity"),
        )
        return cache_identity, fingerprint, effective

    @classmethod
    def expected_audio_path(
        cls,
        project_name: str,
        segment_id: str,
        *,
        params: dict[str, Any] | None = None,
        speaker_override: str | None = None,
    ) -> tuple[str, str, str, dict[str, Any]]:
        _meta, _script, segment = cls._segment(project_name, segment_id)
        cache_identity, fingerprint, effective = cls._identity(
            project_name,
            segment,
            params=params,
            speaker_override=speaker_override,
        )
        project_dir = ProjectRepository.get_project_dir(project_name)
        segments_dir = project_paths.project_dir(project_dir, "segments", create=True)
        return (
            os.path.join(segments_dir, f"{cache_identity}.wav"),
            cache_identity,
            fingerprint,
            effective,
        )

    @classmethod
    def _find_script_audio(
        cls, project_name: str, segment: dict[str, Any]
    ) -> tuple[str | None, str, str, dict[str, Any]]:
        path, cache_identity, fingerprint, effective = cls.expected_audio_path(
            project_name, str(segment.get("id"))
        )
        if os.path.isfile(path):
            return path, cache_identity, fingerprint, effective
        project_dir = ProjectRepository.get_project_dir(project_name)
        cast_active = os.path.isfile(
            os.path.join(project_dir, "voice_cast.json")
        )
        artifact = segment_cache.resolve_segment_artifact(
            segments_dir=project_paths.project_dir(project_dir, "segments"),
            seg_id=str(segment.get("id")),
            emotion=str(effective["emotion"] or "neutral"),
            emo_alpha=effective["emo_alpha"],
            speech_rate=effective["speech_rate"],
            pinyin_hints=effective["pinyin_hints"],
            director_metadata=effective["director_metadata"],
            # Legacy projects (no voice_cast.json) stay speaker-agnostic so the
            # resolver's non-strict fallback can still match bare/param files.
            speaker_fingerprint=(fingerprint or None) if cast_active else None,
            engine_snapshot=(effective.get("engine_snapshot") or None),
            project_name=project_name,
        )
        resolved = artifact.path if artifact.exists() else None
        if resolved is not None:
            # Record the actual engine provenance of the resolved file so the
            # revision's params/cache_identity match the real artifact instead
            # of a Settings-based guess (segment-level provenance upgrade).
            # Only engine-aware files carry provenance; pre-engine legacy
            # files (param/bare) keep their historical identity untouched.
            if (
                artifact.engine_provenance
                and artifact.matched_class == "engine_aware"
                and not effective.get("engine_snapshot")
            ):
                effective = dict(effective)
                effective["engine_snapshot"] = artifact.engine_provenance
                cache_identity = segment_cache.segment_cache_key(
                    str(segment.get("id")),
                    str(effective["emotion"] or "neutral"),
                    effective["emo_alpha"],
                    effective["speech_rate"],
                    effective["pinyin_hints"],
                    effective["director_metadata"],
                    fingerprint or None,
                    (
                        artifact.engine_provenance.get("cache_identity")
                        or artifact.engine_provenance.get("engine_identity")
                    ),
                )
        return resolved, cache_identity, fingerprint, effective

    @classmethod
    def ensure_active_revision(
        cls,
        project_name: str,
        segment_id: str,
        *,
        engine_snapshot: dict[str, Any] | None = None,
        source_path: str | None = None,
        params: dict[str, Any] | None = None,
        speaker_override: str | None = None,
    ) -> dict[str, Any] | None:
        active = QualityRepository.get_active_revision(project_name, segment_id)
        if active:
            try:
                active_path = cls._absolute(project_name, active.get("relative_path", ""))
                active_engine = (
                    active.get("params", {}).get("engine_snapshot")
                    if isinstance(active.get("params"), dict) else None
                )
                requested_identity = (
                    public_profile(resolve_profile(engine_snapshot)).get("cache_identity")
                    if engine_snapshot else None
                )
                active_identity = (
                    public_profile(resolve_profile(active_engine)).get("cache_identity")
                    if active_engine else None
                )
                source_matches = True
                if source_path:
                    source_matches = os.path.realpath(active_path) == os.path.realpath(
                        os.path.abspath(str(source_path))
                    )
                if os.path.isfile(active_path) and source_matches and (
                    not requested_identity or active_identity == requested_identity
                ):
                    return active
            except ValueError:
                pass
        _meta, _script, segment = cls._segment(project_name, segment_id)
        if source_path:
            path = os.path.abspath(str(source_path))
            project_dir = ProjectRepository.get_project_dir(project_name)
            try:
                if os.path.commonpath((os.path.realpath(path), os.path.realpath(project_dir))) != os.path.realpath(project_dir):
                    return None
            except (OSError, ValueError):
                return None
            identity, fingerprint, effective = cls._identity(
                project_name,
                segment,
                params=params or ({"engine_snapshot": engine_snapshot} if engine_snapshot else None),
                speaker_override=speaker_override,
            )
            params = effective
        elif engine_snapshot:
            path, identity, fingerprint, effective = cls.expected_audio_path(
                project_name,
                segment_id,
                params=params or {"engine_snapshot": engine_snapshot},
                speaker_override=speaker_override,
            )
            if not os.path.isfile(path):
                return None
        else:
            path, identity, fingerprint, effective = cls._find_script_audio(project_name, segment)
            params = effective
        if not path or not os.path.isfile(path):
            return None
        return QualityRepository.create_revision(
            project_name,
            segment_id,
            relative_path=cls._project_relative(project_name, path),
            cache_identity=identity,
            voice_fingerprint=fingerprint,
            params=effective,
            status="ready",
            activate=True,
            metadata={"sha256": _sha256(path), "origin": "legacy_or_production"},
        )

    @classmethod
    def archive_active_revision(
        cls, project_name: str, segment_id: str
    ) -> dict[str, Any] | None:
        active = cls.ensure_active_revision(project_name, segment_id)
        if not active:
            return None
        source = cls._absolute(project_name, active.get("relative_path", ""))
        if not os.path.isfile(source):
            return active
        project_dir = ProjectRepository.get_project_dir(project_name)
        quality_dir = project_paths.project_dir(project_dir, "quality", create=True)
        archive_dir = os.path.join(quality_dir, "revisions", str(segment_id))
        os.makedirs(archive_dir, exist_ok=True)
        target = os.path.join(
            archive_dir,
            f"{active['revision_id']}_{os.path.basename(source)}",
        )
        if os.path.realpath(source) != os.path.realpath(target) and not os.path.isfile(target):
            shutil.copy2(source, target)
        if os.path.isfile(target):
            return QualityRepository.update_revision(
                project_name,
                active["revision_id"],
                relative_path=cls._project_relative(project_name, target),
                metadata={
                    **dict(active.get("metadata") or {}),
                    "sha256": _sha256(target),
                    "archived": True,
                },
            )
        return active

    @classmethod
    def register_completed_revision(
        cls,
        project_name: str,
        revision_id: str,
        audio_path: str,
        *,
        source_task_id: str = "",
    ) -> dict[str, Any]:
        revision = QualityRepository.get_revision(project_name, revision_id)
        if not revision:
            raise KeyError(f"音频 revision 不存在: {revision_id}")
        if not os.path.isfile(audio_path) or os.path.getsize(audio_path) <= 0:
            raise ValueError("新 revision 音频不存在或为空")
        project_dir = ProjectRepository.get_project_dir(project_name)
        quality_dir = project_paths.project_dir(project_dir, "quality", create=True)
        archive_dir = os.path.join(
            quality_dir, "revisions", str(revision.get("segment_id") or "")
        )
        os.makedirs(archive_dir, exist_ok=True)
        target = os.path.join(
            archive_dir,
            f"{revision_id}_{os.path.basename(audio_path)}",
        )
        if os.path.realpath(audio_path) != os.path.realpath(target):
            shutil.copy2(audio_path, target)
        return QualityRepository.update_revision(
            project_name,
            revision_id,
            activate=True,
            relative_path=cls._project_relative(project_name, target),
            source_task_id=source_task_id or revision.get("source_task_id", ""),
            status="ready",
            metadata={
                **dict(revision.get("metadata") or {}),
                "sha256": _sha256(target),
                "size": os.path.getsize(target),
            },
        )

    @classmethod
    def resolve_active_audio(
        cls, project_name: str, segment_id: str
    ) -> str | None:
        revision = cls.ensure_active_revision(project_name, segment_id)
        if not revision:
            return None
        try:
            path = cls._absolute(project_name, revision.get("relative_path", ""))
        except ValueError:
            return None
        return path if os.path.isfile(path) and os.path.getsize(path) > 0 else None

    @staticmethod
    def _decode_pcm(raw: bytes, sample_width: int) -> tuple[np.ndarray, float]:
        if sample_width == 1:
            return np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128, 127.0
        if sample_width == 2:
            return np.frombuffer(raw, dtype="<i2").astype(np.int32), 32767.0
        if sample_width == 3:
            values = np.frombuffer(raw, dtype=np.uint8)
            usable = values[: len(values) - (len(values) % 3)].reshape(-1, 3)
            decoded = (
                usable[:, 0].astype(np.int32)
                | (usable[:, 1].astype(np.int32) << 8)
                | (usable[:, 2].astype(np.int32) << 16)
            )
            decoded = np.where(decoded & 0x800000, decoded - 0x1000000, decoded)
            return decoded, 8388607.0
        if sample_width == 4:
            return np.frombuffer(raw, dtype="<i4").astype(np.int64), 2147483647.0
        raise ValueError(f"不支持的 PCM sample width: {sample_width}")

    @classmethod
    def _audio_metrics(cls, path: str) -> dict[str, Any]:
        with wave.open(path, "rb") as audio:
            channels = audio.getnchannels()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            sample_width = audio.getsampwidth()
            duration = frames / sample_rate if sample_rate else 0.0
            peak = 0.0
            clipped = 0
            samples = 0
            longest_silence = 0
            silence_run = 0
            while True:
                raw = audio.readframes(65536)
                if not raw:
                    break
                values, full_scale = cls._decode_pcm(raw, sample_width)
                if values.size == 0:
                    continue
                normalized = np.abs(values.astype(np.float64)) / full_scale
                peak = max(peak, float(np.max(normalized)))
                clipped += int(np.count_nonzero(normalized >= 0.999))
                samples += int(normalized.size)
                frame_values = (
                    normalized[: len(normalized) - (len(normalized) % channels)]
                    .reshape(-1, channels)
                    .max(axis=1)
                    if channels > 1 else normalized
                )
                for silent in frame_values <= 0.003:
                    if silent:
                        silence_run += 1
                        longest_silence = max(longest_silence, silence_run)
                    else:
                        silence_run = 0
        return {
            "duration_seconds": round(duration, 4),
            "sample_rate": sample_rate,
            "channels": channels,
            "frames": frames,
            "sample_width": sample_width,
            "peak": round(peak, 6),
            "clipping_ratio": round(clipped / samples, 8) if samples else 0.0,
            "longest_silence_seconds": round(
                longest_silence / sample_rate, 4
            ) if sample_rate else 0.0,
        }

    @classmethod
    def _analyze_technical_qa(
        cls,
        project_name: str,
        segment_id: str,
        revision_id: str | None = None,
        *,
        meta: Any | None = None,
        segment: dict[str, Any] | None = None,
        bindings: dict[str, Any] | None = None,
        revision: dict[str, Any] | None = None,
        resolve_missing_revision: bool = True,
    ) -> dict[str, Any]:
        if meta is None or segment is None:
            meta, _script, segment = cls._segment(project_name, segment_id)
        if revision is None and resolve_missing_revision:
            revision = (
                QualityRepository.get_revision(project_name, revision_id)
                if revision_id else cls.ensure_active_revision(project_name, segment_id)
            )
        issues: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}
        if not revision:
            issues.append({
                "code": "AUDIO_MISSING",
                "severity": "error",
                "message": "段落没有可用音频 revision",
            })
            if getattr(meta, "segments_status", {}).get(str(segment_id)) == "done":
                issues.append({
                    "code": "PROJECT_DONE_AUDIO_MISSING",
                    "severity": "error",
                    "message": "project.json 标记 done，但音频缺失",
                })
            return {
                "segment_id": str(segment_id),
                "revision_id": "",
                "outcome": "fail",
                "checks": issues,
                "metrics": metrics,
                "checker_version": 1,
                "checked_at": _now(),
            }
        path = cls._absolute(project_name, revision.get("relative_path", ""))
        if not os.path.isfile(path):
            issues.append({
                "code": "AUDIO_MISSING",
                "severity": "error",
                "message": "revision 音频文件不存在",
            })
        elif os.path.getsize(path) <= 0:
            issues.append({
                "code": "AUDIO_EMPTY",
                "severity": "error",
                "message": "revision 音频文件为 0 字节",
            })
        else:
            try:
                metrics = cls._audio_metrics(path)
            except (OSError, EOFError, ValueError, wave.Error) as exc:
                issues.append({
                    "code": "WAV_UNREADABLE",
                    "severity": "error",
                    "message": f"WAV 无法读取: {type(exc).__name__}",
                })
        if metrics:
            text_length = len("".join(str(segment.get("text") or "").split()))
            minimum = max(0.18, text_length * 0.015)
            maximum = max(30.0, text_length * 1.2 + 10.0)
            if metrics["duration_seconds"] < minimum:
                issues.append({
                    "code": "DURATION_TOO_SHORT",
                    "severity": "warning",
                    "message": "音频时长明显偏短",
                    "expected_min_seconds": round(minimum, 3),
                })
            if metrics["duration_seconds"] > maximum:
                issues.append({
                    "code": "DURATION_TOO_LONG",
                    "severity": "warning",
                    "message": "音频时长明显偏长",
                    "expected_max_seconds": round(maximum, 3),
                })
            if metrics["longest_silence_seconds"] >= 2.5:
                issues.append({
                    "code": "LONG_SILENCE",
                    "severity": "warning",
                    "message": "检测到过长连续静音",
                })
            if metrics["peak"] <= 0.001:
                issues.append({
                    "code": "ALL_SILENCE",
                    "severity": "error",
                    "message": "音频接近全静音",
                })
            if metrics["clipping_ratio"] >= 0.005:
                issues.append({
                    "code": "CLIPPING",
                    "severity": "warning",
                    "message": "音频削波比例过高",
                })
            if metrics["sample_rate"] not in {16000, 22050, 24000, 44100, 48000}:
                issues.append({
                    "code": "SAMPLE_RATE_ABNORMAL",
                    "severity": "warning",
                    "message": "采样率不在支持的常用范围",
                })
            if metrics["channels"] != 1:
                issues.append({
                    "code": "CHANNELS_ABNORMAL",
                    "severity": "warning",
                    "message": "正式段落音频应为单声道",
                })
        expected_identity, current_fingerprint, _params = cls._identity(
            project_name,
            segment,
            params=dict(revision.get("params") or {}),
            bindings_document=bindings,
        )
        if revision.get("cache_identity") and revision.get("cache_identity") != expected_identity:
            issues.append({
                "code": "CACHE_IDENTITY_MISMATCH",
                "severity": "warning",
                "message": "音频与当前合成参数 cache identity 不一致",
            })
        if (
            revision.get("voice_fingerprint")
            and current_fingerprint
            and revision.get("voice_fingerprint") != current_fingerprint
        ):
            issues.append({
                "code": "VOICE_IDENTITY_MISMATCH",
                "severity": "warning",
                "message": "当前角色声音已变化，revision 需要重新确认",
            })
        severities = {item["severity"] for item in issues}
        outcome = "fail" if "error" in severities else ("warning" if issues else "pass")
        result = {
            "segment_id": str(segment_id),
            "revision_id": revision["revision_id"],
            "outcome": outcome,
            "checks": issues,
            "metrics": metrics,
            "checker_version": 1,
            "checked_at": _now(),
        }
        return result

    @classmethod
    def analyze_technical_qa(
        cls,
        project_name: str,
        segment_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Analyze one segment without mutating the quality repository."""
        revision = (
            QualityRepository.get_revision(project_name, revision_id)
            if revision_id
            else QualityRepository.get_active_revision(project_name, segment_id)
        )
        return cls._analyze_technical_qa(
            project_name,
            segment_id,
            revision_id,
            revision=revision,
            resolve_missing_revision=False,
        )

    @classmethod
    def run_technical_qa(
        cls,
        project_name: str,
        segment_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Analyze and persist one segment (backward-compatible API)."""
        result = cls._analyze_technical_qa(project_name, segment_id, revision_id)
        revision_key = str(result.get("revision_id") or "").strip()
        if not revision_key:
            return result
        return QualityRepository.save_technical_qa(project_name, revision_key, result)

    @classmethod
    def analyze_technical_qa_batch(
        cls,
        project_name: str,
        segment_ids: list[str],
        *,
        revision_ids: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Analyze many segments without writing ``quality_state.json``."""
        prepared_ids = [
            str(segment_id).strip()
            for segment_id in segment_ids
            if str(segment_id).strip()
        ]
        revision_map = {
            str(segment_id): str(revision_id)
            for segment_id, revision_id in (revision_ids or {}).items()
            if str(segment_id).strip() and str(revision_id).strip()
        }
        if not prepared_ids:
            return []

        meta, script, bindings = ProjectRepository.load_project(project_name)
        segments = _script_index(script)
        state = QualityRepository.load(project_name)

        # Read the project and quality snapshots once for the read-only
        # analysis loop.  Revision bootstrap, when needed, belongs to
        # ``run_technical_qa_batch``'s preparation step below.
        results: list[dict[str, Any]] = []
        for segment_id in prepared_ids:
            segment = segments.get(segment_id)
            if segment is None:
                # Preserve the single-segment API's KeyError contract for an
                # invalid identifier rather than silently dropping a result.
                results.append(
                    cls._analyze_technical_qa(
                        project_name,
                        segment_id,
                        revision_map.get(segment_id),
                    )
                )
                continue
            selected_revision_id = revision_map.get(segment_id)
            if selected_revision_id:
                selected_revision = state["revisions"].get(selected_revision_id)
            else:
                selected_revision_id = state["active_revisions"].get(segment_id)
                selected_revision = state["revisions"].get(selected_revision_id)
            results.append(
                cls._analyze_technical_qa(
                    project_name,
                    segment_id,
                    selected_revision_id,
                    meta=meta,
                    segment=segment,
                    bindings=bindings,
                    revision=(
                        selected_revision
                        if isinstance(selected_revision, dict) else None
                    ),
                    resolve_missing_revision=False,
                )
            )
        return results

    @classmethod
    def _prepare_technical_qa_batch(
        cls,
        project_name: str,
        segment_ids: list[str],
    ) -> None:
        """Ensure legacy audio has active revisions before a batch run.

        This preparation belongs to the mutating ``run`` operation.  The
        public ``analyze`` methods remain read-only and therefore never
        bootstrap or rewrite ``quality_state.json``.
        """
        state = QualityRepository.load(project_name)
        if any(
            segment_id not in state["active_revisions"]
            or state["active_revisions"].get(segment_id)
            not in state["revisions"]
            for segment_id in segment_ids
        ):
            cls.get_quality_report(project_name)

    @classmethod
    def run_technical_qa_batch(
        cls,
        project_name: str,
        segment_ids: list[str],
        *,
        revision_ids: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Analyze many segments and persist all revisions in one mutation."""
        prepared_ids = [
            str(segment_id).strip()
            for segment_id in segment_ids
            if str(segment_id).strip()
        ]
        if not prepared_ids:
            return []
        cls._prepare_technical_qa_batch(project_name, prepared_ids)
        results = cls.analyze_technical_qa_batch(
            project_name,
            prepared_ids,
            revision_ids=revision_ids,
        )
        persisted = QualityRepository.save_technical_qa_batch(
            project_name,
            results,
        )
        by_revision = {
            str(item.get("revision_id")): item
            for item in persisted
            if str(item.get("revision_id") or "").strip()
        }
        return [
            by_revision.get(str(item.get("revision_id")), item)
            for item in results
        ]

    @classmethod
    def mark_review(
        cls,
        project_name: str,
        segment_id: str,
        review_status: str,
        *,
        issue_type: str = "",
        review_note: str = "",
        reviewed_by: str = "user",
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        status = str(review_status or "").strip()
        if status not in HUMAN_REVIEW_STATUSES:
            raise ValueError(f"不支持的人工 review 状态: {status}")
        revision = (
            QualityRepository.get_revision(project_name, revision_id)
            if revision_id else cls.ensure_active_revision(project_name, segment_id)
        )
        if not revision or revision.get("segment_id") != str(segment_id):
            raise KeyError("找不到段落对应的音频 revision")
        return QualityRepository.save_human_review(
            project_name,
            revision["revision_id"],
            {
                "segment_id": str(segment_id),
                "review_status": status,
                "issue_type": str(issue_type or ""),
                "review_note": str(review_note or ""),
                "reviewed_at": _now(),
                "reviewed_by": str(reviewed_by or "user"),
            },
        )

    @classmethod
    def pass_technically_clean(
        cls,
        project_name: str,
        segment_ids: list[str] | None = None,
        *,
        reviewed_by: str = "web_bulk",
    ) -> dict[str, Any]:
        """Human-pass active revisions whose persisted technical QA is ``pass``."""
        selected = (
            {str(item) for item in segment_ids if str(item)}
            if segment_ids is not None else None
        )
        report = cls.get_quality_report(project_name)
        reviews: list[tuple[str, dict[str, Any]]] = []
        skipped: list[str] = []
        for item in report.get("segments", []):
            segment_id = str(item.get("segment_id") or "")
            if selected is not None and segment_id not in selected:
                continue
            revision = item.get("audio_revision") or {}
            revision_id = str(revision.get("revision_id") or "")
            if (
                revision_id
                and item.get("technical_outcome") == "pass"
                and item.get("review_status") != "passed"
            ):
                reviews.append((
                    revision_id,
                    {
                        "segment_id": segment_id,
                        "review_status": "passed",
                        "issue_type": "",
                        "review_note": "批量通过：技术 QA 已通过",
                        "reviewed_at": _now(),
                        "reviewed_by": str(reviewed_by or "web_bulk"),
                    },
                ))
            else:
                skipped.append(segment_id)
        saved = QualityRepository.save_human_reviews_batch(project_name, reviews)
        return {
            "project": project_name,
            "passed": len(saved),
            "skipped": len(skipped),
            "segment_ids": [
                str(item.get("segment_id") or "") for item in saved
            ],
        }

    @classmethod
    def get_segment_quality(
        cls, project_name: str, segment_id: str
    ) -> dict[str, Any]:
        revision = cls.ensure_active_revision(project_name, segment_id)
        state = QualityRepository.load(project_name)
        return cls._quality_item(project_name, str(segment_id), state, revision)

    @classmethod
    def _quality_item(
        cls,
        project_name: str,
        segment_id: str,
        state: dict[str, Any],
        revision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if revision is None:
            revision_id = state["active_revisions"].get(str(segment_id))
            candidate = state["revisions"].get(revision_id)
            revision = candidate if isinstance(candidate, dict) else None
        if not revision:
            failed = cls._segment_failed(project_name, str(segment_id))
            status = "technical_warning" if failed else "not_started"
            return {
                "segment_id": str(segment_id),
                "audio_revision": None,
                "review_status": "not_started" if not failed else "unreviewed",
                "technical_outcome": "fail" if failed else "none",
                "quality_status": status,
                "technical_qa": None,
                "human_review": None,
            }
        revision_id = revision["revision_id"]
        technical = state["technical_qa"].get(revision_id)
        human = state["human_reviews"].get(revision_id)
        human_status = (
            str(human.get("review_status") or "unreviewed")
            if isinstance(human, dict) else "unreviewed"
        )
        try:
            audio_exists = os.path.isfile(
                cls._absolute(project_name, revision.get("relative_path", ""))
            )
        except ValueError:
            audio_exists = False
        if not audio_exists:
            quality_status = "technical_warning"
            technical_outcome = "fail"
        elif revision.get("status") == "regenerating":
            quality_status = "regenerating"
            technical_outcome = (
                technical.get("outcome")
                if isinstance(technical, dict) else "unreviewed"
            )
        elif human_status == "needs_fix":
            quality_status = "needs_fix"
            technical_outcome = (
                technical.get("outcome")
                if isinstance(technical, dict) else "unreviewed"
            )
        elif isinstance(technical, dict) and technical.get("outcome") in {"fail", "warning"}:
            quality_status = "technical_warning"
            technical_outcome = technical.get("outcome")
        elif human_status == "passed":
            quality_status = "passed"
            technical_outcome = (
                technical.get("outcome")
                if isinstance(technical, dict) else "unreviewed"
            )
        else:
            quality_status = "needs_review"
            technical_outcome = (
                technical.get("outcome")
                if isinstance(technical, dict) else "unreviewed"
            )
        return {
            "segment_id": str(segment_id),
            "audio_revision": {
                key: revision.get(key)
                for key in (
                    "revision_id",
                    "audio_revision",
                    "relative_path",
                    "cache_identity",
                    "voice_fingerprint",
                    "params",
                    "status",
                    "created_at",
                    "metadata",
                )
            },
            "review_status": human_status,
            "technical_outcome": technical_outcome,
            "quality_status": quality_status,
            "technical_qa": technical,
            "human_review": human,
        }

    @staticmethod
    def _segment_failed(project_name: str, segment_id: str) -> bool:
        """True only when the segment has a REAL production failure recorded.

        “从未生产 / 待生产”（包括 partial scope 之外的段落）不算失败——
        只有 meta.segments_status 标记为 failed（合成失败/引擎失败）才算。
        """
        try:
            meta, _script, _bindings = ProjectRepository.load_project(project_name)
        except Exception:
            return False
        statuses = getattr(meta, "segments_status", None) or {}
        return statuses.get(str(segment_id)) == "failed"

    @classmethod
    def get_quality_report(cls, project_name: str) -> dict[str, Any]:
        _meta, script, bindings = ProjectRepository.load_project(project_name)
        segments = _script_index(script)
        state = QualityRepository.load(project_name)
        project_dir = ProjectRepository.get_project_dir(project_name)
        segments_dir = project_paths.project_dir(project_dir, "segments")
        cast_active = os.path.isfile(os.path.join(project_dir, "voice_cast.json"))
        fingerprint_cache: dict[str, str] = {}
        candidates: list[dict[str, Any]] = []
        for segment_id, segment in segments.items():
            active_id = state["active_revisions"].get(segment_id)
            active = state["revisions"].get(active_id)
            if isinstance(active, dict):
                try:
                    if os.path.isfile(cls._absolute(
                        project_name, active.get("relative_path", "")
                    )):
                        continue
                except ValueError:
                    pass
            effective = {
                "emotion": segment.get("emotion", "neutral"),
                "emo_alpha": segment.get("emo_alpha", 1.0),
                "speech_rate": segment.get("speech_rate", 1.0),
                "pinyin_hints": segment.get("pinyin_hints"),
                "director_metadata": segment_cache.director_metadata_for(segment),
            }
            if isinstance(active, dict) and isinstance(active.get("params"), dict):
                active_engine = active["params"].get("engine_snapshot")
                if active_engine:
                    effective["engine_snapshot"] = public_profile(resolve_profile(active_engine))
            speaker = cls._speaker_path(project_name, segment, bindings)
            if speaker not in fingerprint_cache:
                fingerprint_cache[speaker] = (
                    segment_cache.speaker_fingerprint_for_path(speaker) or ""
                )
            fingerprint = fingerprint_cache[speaker]
            identity = segment_cache.segment_cache_key(
                segment_id,
                str(effective["emotion"] or "neutral"),
                effective["emo_alpha"],
                effective["speech_rate"],
                effective["pinyin_hints"],
                effective["director_metadata"],
                fingerprint if cast_active else None,
                (effective.get("engine_snapshot") or {}).get("cache_identity")
                or (effective.get("engine_snapshot") or {}).get("engine_identity"),
            )
            path = os.path.join(segments_dir, f"{identity}.wav")
            if not os.path.isfile(path):
                artifact = segment_cache.resolve_segment_artifact(
                    segments_dir=segments_dir,
                    seg_id=segment_id,
                    emotion=str(effective["emotion"] or "neutral"),
                    emo_alpha=effective["emo_alpha"],
                    speech_rate=effective["speech_rate"],
                    pinyin_hints=effective["pinyin_hints"],
                    director_metadata=effective["director_metadata"],
                    speaker_fingerprint=fingerprint or None,
                    engine_snapshot=(effective.get("engine_snapshot") or None),
                    project_name=project_name,
                )
                path = artifact.path if artifact.exists() else None
            if path and os.path.isfile(path):
                candidates.append({
                    "segment_id": segment_id,
                    "relative_path": cls._project_relative(project_name, path),
                    "cache_identity": identity,
                    "voice_fingerprint": fingerprint,
                    "params": effective,
                    "status": "ready",
                    "metadata": {
                        "sha256": _sha256(path),
                        "origin": "legacy_or_production",
                    },
                })
        if candidates:
            QualityRepository.bootstrap_revisions(project_name, candidates)
            state = QualityRepository.load(project_name)
        items = [
            cls._quality_item(project_name, segment_id, state)
            for segment_id in segments
        ]
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("quality_status") or "needs_review")
            counts[status] = counts.get(status, 0) + 1
        not_started = counts.get("not_started", 0)
        if items and not_started == len(items):
            production_status = "not_started"
            quality_status = "not_available"
        else:
            production_status = "started"
            if counts.get("technical_warning", 0):
                quality_status = "technical_warning"
            elif counts.get("needs_fix", 0):
                quality_status = "needs_fix"
            elif counts.get("needs_review", 0) or counts.get("regenerating", 0):
                quality_status = "needs_review"
            else:
                quality_status = "passed"
        return {
            "project": project_name,
            "production_status": production_status,
            "quality_status": quality_status,
            "summary": {
                "segments": len(items),
                "production_status": production_status,
                "quality_status": quality_status,
                "not_started": not_started,
                "passed": counts.get("passed", 0),
                "needs_review": counts.get("needs_review", 0),
                "needs_fix": counts.get("needs_fix", 0),
                "technical_warning": counts.get("technical_warning", 0),
                "regenerating": counts.get("regenerating", 0),
            },
            "segments": items,
        }


__all__ = ["HUMAN_REVIEW_STATUSES", "QualityService"]
