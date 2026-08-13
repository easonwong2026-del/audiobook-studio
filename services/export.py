"""导出服务：包 ``lib.audio_pipeline.export_book``，透传 R2 报错（禁止 import gradio）。

UI 层 ``app.do_export`` 调用本服务，捕获其抛出的 ``ExportError`` / ``RuntimeError``
后显式展示给用户（取代原先 ffmpeg 失败时「静默回退 WAV」导致 UI 收不到信号的行为）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import uuid
import wave
from datetime import datetime, timezone
from typing import Any

from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from lib.procutil import run_no_window
from repositories.task_repo import TaskRecord, TaskRepository
from services.delivery import compute_delivery_input_snapshot
from services.quality import QualityService
from lib.tts_profile import public_profile, resolve_profile

logger = logging.getLogger(__name__)


class ExportPlanError(RuntimeError):
    """Raised when a formal export fails its readiness policy."""

    def __init__(self, plan: dict[str, Any]) -> None:
        super().__init__("导出前检查未通过")
        self.code = "EXPORT_NOT_READY"
        self.plan = plan

    def as_payload(self) -> dict[str, Any]:
        blockers = self.plan.get("blockers", []) if isinstance(self.plan, dict) else []
        first = blockers[0] if blockers and isinstance(blockers[0], dict) else {}
        code = str(first.get("code") or self.code)
        return {
            "error": {
                "code": code,
                "message": str(first.get("message") or "交付准备度检查未通过"),
                "fix_hint": "处理 blockers 后重新调用 plan_export。",
                "details": {"blockers": blockers},
            }
        }


class ExportIdempotencyConflict(RuntimeError):
    code = "IDEMPOTENCY_CONFLICT"

    def __init__(self, export_id: str) -> None:
        super().__init__("相同 idempotency_key 已对应不同的导出参数")
        self.export_id = str(export_id)

    def as_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": str(self),
                "fix_hint": "更换 idempotency_key，或使用原始导出参数重试。",
                "details": {"export_id": self.export_id},
            }
        }


class DeliveryInputChanged(RuntimeError):
    code = "DELIVERY_INPUT_CHANGED"

    def __init__(self) -> None:
        super().__init__("导出计划与执行时的交付输入不一致")

    def as_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": str(self),
                "fix_hint": "重新调用 plan_export，再创建新的导出任务。",
                "details": {},
            }
        }


class ExportCancelled(RuntimeError):
    code = "EXPORT_CANCELLED"


class ExportOwnershipLost(RuntimeError):
    """Raised when an export worker is no longer fenced to its runtime."""

    code = "EXPORT_OWNERSHIP_LOST"


class ExportService:
    """导出成品：委托 ``audio_pipeline.export_book``，错误直接上抛。"""

    @staticmethod
    def export(project_dir: str, fmt: str, bitrate: str = "192k",
               output_dir: str = "",
               *, segment_paths: dict[str, str] | None = None,
               streaming_postprocess: bool = False,
               atomic_publish: bool = False) -> str:
        """导出指定格式成品。

        Args:
            project_dir: 项目目录（含 ``structured_script.json`` 与 ``segments/``）。
            fmt: 导出格式 wav / mp3 / m4b。
            bitrate: mp3 / m4b 比特率，默认 192k。
            output_dir: 输出目录（留空用项目内 ``output/``）。

        Returns:
            导出文件绝对路径。

        Raises:
            ExportError: ffmpeg 缺失 / 转码失败（由 ``audio_pipeline`` 抛出，原样透传）。
            RuntimeError: 存在未合成段落（由 ``audio_pipeline`` 抛出）。
        """
        from lib import audio_pipeline

        return audio_pipeline.export_book(
            project_dir,
            format=fmt,
            bitrate=bitrate,
            output_dir=output_dir,
            segment_paths=segment_paths,
            streaming_postprocess=streaming_postprocess,
            atomic_publish=atomic_publish,
        )

    @staticmethod
    def export_subtitles(
        project_dir: str,
        formats=("srt", "lrc"),
        output_dir: str = "",
        *,
        segment_paths: dict[str, str] | None = None,
        require_complete: bool = True,
    ) -> list:
        """生成字幕（srt / lrc），委托 ``lib.audio_pipeline.generate_subtitles``。

        Args:
            project_dir: 项目目录（含 ``structured_script.json`` 与 ``segments/``）。
            formats: 要生成的字幕格式集合，可含 ``"srt"`` / ``"lrc"``。
            output_dir: 输出目录（留空用项目内 ``output/``）。

        Returns:
            生成的字幕文件路径列表（按请求格式）。
        """
        from lib import audio_pipeline

        return audio_pipeline.generate_subtitles(
            project_dir,
            formats=formats,
            output_dir=output_dir,
            segment_paths=segment_paths,
            require_complete=require_complete,
        )

    @staticmethod
    def _segments(script: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            segment
            for chapter in script.get("chapters", [])
            if isinstance(chapter, dict)
            for segment in chapter.get("segments", [])
            if isinstance(segment, dict)
        ]

    @staticmethod
    def _snapshot_hash(items: list[dict[str, Any]]) -> str:
        payload = json.dumps(
            items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _active_blockers(
        project: str,
        *,
        exclude_task_id: str = "",
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        for record in TaskRepository.list_tasks(project=project):
            if record.task_id == exclude_task_id:
                continue
            if record.status not in {
                "pending", "running", "pausing", "paused", "cancelling",
            }:
                continue
            if record.task_type == "export":
                blockers.append({
                    "code": "EXPORT_ACTIVE",
                    "message": "项目存在正在运行的导出任务",
                    "task_id": record.task_id,
                    "status": record.status,
                })
            else:
                blockers.append({
                    "code": "PRODUCTION_ACTIVE",
                    "message": "项目存在正在运行的生产任务",
                    "task_id": record.task_id,
                    "status": record.status,
                })
        repairs = QualityRepository.list_history(project, "repair_history")
        for repair in repairs:
            if repair.get("status") not in {
                "preparing", "submitting", "pending", "running",
                "pausing", "paused", "cancelling",
            }:
                continue
            task_id = str(repair.get("task_id") or "")
            if task_id and task_id == exclude_task_id:
                continue
            blockers.append({
                "code": "REPAIR_ACTIVE",
                "message": "项目存在正在运行的修复任务",
                "repair_id": repair.get("repair_id"),
                "task_id": task_id,
                "status": repair.get("status"),
            })
        state = QualityRepository.load(project)
        regenerating = [
            str(item.get("segment_id") or "")
            for item in state.get("revisions", {}).values()
            if isinstance(item, dict) and item.get("status") == "regenerating"
        ]
        if regenerating:
            blockers.append({
                "code": "REVISION_REGENERATING",
                "message": "项目存在尚未完成的音频 revision",
                "segment_ids": sorted(set(regenerating)),
            })
        return blockers

    @staticmethod
    def _freshness(project: str) -> dict[str, Any]:
        snapshot = compute_delivery_input_snapshot(project)
        if not isinstance(snapshot, dict):
            raise ValueError("delivery input snapshot 必须是 JSON object")
        return snapshot

    @classmethod
    def plan_export(
        cls,
        project_name: str,
        fmt: str = "wav",
        *,
        qa_policy: str = "require_passed",
        subtitle_formats: tuple[str, ...] | list[str] = (),
        exclude_task_id: str = "",
        engine_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a machine-readable readiness plan without exporting files."""
        project = str(project_name or "").strip()
        export_format = str(fmt or "").lower()
        policy = str(qa_policy or "require_passed").lower()
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if export_format not in {"wav", "mp3", "m4b"}:
            blockers.append({
                "code": "FORMAT_UNSUPPORTED",
                "message": f"不支持的导出格式: {export_format}",
            })
        if policy not in {"require_passed", "technical", "allow_unreviewed"}:
            blockers.append({
                "code": "QA_POLICY_UNSUPPORTED",
                "message": f"不支持的 QA 策略: {policy}",
            })
        blockers.extend(cls._active_blockers(project, exclude_task_id=exclude_task_id))
        meta, script, _bindings = ProjectRepository.load_project(project)
        segments = cls._segments(script)
        project_status = dict(getattr(meta, "segments_status", {}) or {})
        revisions: list[dict[str, Any]] = []
        requested_engine = (
            public_profile(resolve_profile(engine_snapshot))
            if isinstance(engine_snapshot, dict) and engine_snapshot
            else None
        )
        requested_cache_identity = (
            str(requested_engine.get("cache_identity") or "")
            if requested_engine else ""
        )
        quality_report = QualityService.get_quality_report(project)
        quality_by_segment = {
            str(item.get("segment_id") or ""): item
            for item in quality_report.get("segments", [])
        }
        project_dir = ProjectRepository.get_project_dir(project)
        for segment in segments:
            segment_id = str(segment.get("id"))
            quality = quality_by_segment.get(segment_id, {})
            revision = quality.get("audio_revision")
            if not revision:
                blockers.append({
                    "code": "AUDIO_MISSING",
                    "message": f"段落缺少可用音频: {segment_id}",
                    "segment_id": segment_id,
                })
                continue
            relative_path = str(revision.get("relative_path") or "")
            path = (
                os.path.join(project_dir, *relative_path.split("/"))
                if relative_path else ""
            )
            if not path or not os.path.isfile(path):
                blockers.append({
                    "code": "AUDIO_MISSING",
                    "message": f"active revision 音频缺失: {segment_id}",
                    "segment_id": segment_id,
                })
                continue
            revision_id = str(revision.get("revision_id") or "")
            revision_engine = {}
            raw_params = revision.get("params")
            if isinstance(raw_params, dict):
                raw_engine = raw_params.get("engine_snapshot")
                if isinstance(raw_engine, dict):
                    revision_engine = public_profile(resolve_profile(raw_engine))
            revision_cache_identity = str(revision_engine.get("cache_identity") or "")
            if requested_cache_identity and revision_cache_identity and revision_cache_identity != requested_cache_identity:
                blockers.append({
                    "code": "ENGINE_PROVENANCE_MISMATCH",
                    "message": f"段落 active revision 引擎与导出任务冻结引擎不一致: {segment_id}",
                    "segment_id": segment_id,
                    "expected_engine": requested_engine,
                    "actual_engine": revision_engine,
                })
            elif requested_cache_identity and not revision_cache_identity:
                warnings.append({
                    "code": "ENGINE_PROVENANCE_UNKNOWN",
                    "message": f"段落 active revision 没有历史 engine provenance: {segment_id}",
                    "segment_id": segment_id,
                })
            technical_outcome = str(
                quality.get("technical_outcome") or "unreviewed"
            )
            human_status = str(quality.get("review_status") or "unreviewed")
            if policy == "require_passed":
                if technical_outcome != "pass":
                    blockers.append({
                        "code": "TECHNICAL_QA_NOT_PASSED",
                        "message": f"段落技术 QA 未通过: {segment_id}",
                        "segment_id": segment_id,
                        "outcome": technical_outcome,
                    })
                if human_status != "passed":
                    blockers.append({
                        "code": "HUMAN_REVIEW_NOT_PASSED",
                        "message": f"段落人工 Review 未通过: {segment_id}",
                        "segment_id": segment_id,
                        "review_status": human_status,
                    })
            elif policy == "technical" and technical_outcome != "pass":
                blockers.append({
                    "code": "TECHNICAL_QA_NOT_PASSED",
                    "message": f"段落技术 QA 未通过: {segment_id}",
                    "segment_id": segment_id,
                    "outcome": technical_outcome,
                })
            elif policy == "allow_unreviewed":
                if technical_outcome == "fail" or human_status == "needs_fix":
                    blockers.append({
                        "code": "QUALITY_FIX_REQUIRED",
                        "message": f"段落存在阻断性质量问题: {segment_id}",
                        "segment_id": segment_id,
                    })
                elif technical_outcome != "pass" or human_status != "passed":
                    warnings.append({
                        "code": "QUALITY_NOT_FULLY_REVIEWED",
                        "message": f"段落尚未完全通过 Review: {segment_id}",
                        "segment_id": segment_id,
                    })
            revisions.append({
                "segment_id": segment_id,
                "revision_id": revision_id,
                "audio_revision": int(revision.get("audio_revision", 0) or 0),
                "relative_path": relative_path,
                "cache_identity": str(revision.get("cache_identity") or ""),
                "sha256": str((revision.get("metadata") or {}).get("sha256") or ""),
                "engine_snapshot": revision_engine,
            })
        failed_ids = [
            segment_id
            for segment_id, status in project_status.items()
            if status == "failed"
        ]
        if failed_ids:
            blockers.append({
                "code": "SYNTHESIS_FAILED",
                "message": f"项目仍有 {len(failed_ids)} 个生产失败段落",
                "segment_ids": failed_ids,
            })
        incomplete_ids = [
            str(segment.get("id"))
            for segment in segments
            if project_status.get(str(segment.get("id"))) != "done"
        ]
        if incomplete_ids:
            blockers.append({
                "code": "PROJECT_NOT_COMPLETE",
                "message": f"仍有 {len(incomplete_ids)} 个必需段落未完成生产",
                "segment_ids": incomplete_ids,
            })
        script_meta = script.get("meta") if isinstance(script.get("meta"), dict) else {}
        if not str(script_meta.get("title") or "").strip():
            blockers.append({
                "code": "TITLE_REQUIRED",
                "message": "正式导出需要书名 metadata",
            })
        if not str(script_meta.get("author") or "").strip():
            warnings.append({
                "code": "AUTHOR_MISSING",
                "message": "作者 metadata 缺失，将使用兼容占位。",
            })
        from lib import config

        ffmpeg_executable = str(config.get_ffmpeg_path() or "ffmpeg")
        ffmpeg_ready = bool(
            shutil.which(ffmpeg_executable)
            or (os.path.isabs(ffmpeg_executable) and os.path.isfile(ffmpeg_executable))
        )
        if export_format in {"mp3", "m4b"} and not ffmpeg_ready:
            blockers.append({
                "code": "FFMPEG_REQUIRED",
                "message": f"{export_format} 导出需要 FFmpeg",
            })
        subtitles = [
            str(item).lower()
            for item in subtitle_formats
            if str(item).lower() in {"srt", "lrc"}
        ]
        revision_snapshot = sorted(
            revisions, key=lambda item: item["segment_id"]
        )
        try:
            delivery_snapshot = cls._freshness(project)
            delivery_input_hash = str(
                delivery_snapshot.get("delivery_input_hash") or ""
            )
            if not delivery_input_hash:
                raise ValueError("delivery_input_hash 为空")
        except Exception as exc:
            delivery_snapshot = {}
            delivery_input_hash = ""
            blockers.append({
                "code": "DELIVERY_INPUT_UNAVAILABLE",
                "message": "无法建立当前交付输入快照",
                "details": {"type": type(exc).__name__},
            })
        return {
            "ready": not blockers,
            "project": project,
            "format": export_format,
            "qa_policy": policy,
            "subtitle_formats": subtitles,
            "summary": {
                "chapters": len(script.get("chapters", [])),
                "segments": len(segments),
                "active_revisions": len(revisions),
                "failed_segments": len(failed_ids),
                "metadata": {
                    "title": str(script_meta.get("title") or ""),
                    "author_present": bool(str(script_meta.get("author") or "").strip()),
                },
                "ffmpeg_ready": ffmpeg_ready,
            },
            "revision_snapshot": revision_snapshot,
            "revision_snapshot_hash": cls._snapshot_hash(revision_snapshot),
            "delivery_input_snapshot": delivery_snapshot,
            "delivery_input_hash": delivery_input_hash,
            "engine_snapshot": requested_engine or (
                delivery_snapshot.get("engine_provenance", {}).get("engine_snapshot", {})
                if isinstance(delivery_snapshot, dict) else {}
            ),
            "engine_provenance": (
                delivery_snapshot.get("engine_provenance", {})
                if isinstance(delivery_snapshot, dict) else {}
            ),
            "blockers": blockers,
            "warnings": warnings,
        }

    @staticmethod
    def _relative_output(project_name: str, path: str) -> str:
        return QualityService._project_relative(project_name, path)

    @staticmethod
    def _file_sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _source_duration(paths: list[str]) -> float:
        duration = 0.0
        for path in paths:
            try:
                with wave.open(path, "rb") as audio:
                    rate = audio.getframerate()
                    duration += audio.getnframes() / rate if rate else 0.0
            except (OSError, wave.Error):
                continue
        return round(duration, 3)

    @staticmethod
    def _parse_duration(value: Any) -> float | None:
        """Return a finite non-negative duration, or ``None`` for bad metadata."""
        try:
            duration = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(duration) or duration < 0:
            return None
        return duration

    @classmethod
    def _artifact_duration(
        cls,
        path: str,
        fmt: str | None = None,
        *,
        fallback: float | None = None,
    ) -> float:
        """Read duration from a published artifact without loading its payload.

        WAV duration is derived from its RIFF frame metadata.  Compressed
        artifacts are inspected with ``ffprobe`` first and ``mutagen`` second;
        both read container/frame metadata only.  ``fallback`` is deliberately
        explicit and is expected to be the pipeline's timing-aware duration
        (including inserted segment/chapter silence), never a plain sum of
        source segment lengths.
        """
        artifact = os.path.abspath(str(path or ""))
        suffix = str(fmt or os.path.splitext(artifact)[1].lstrip(".")).lower()
        if suffix == "wav":
            try:
                with wave.open(artifact, "rb") as audio:
                    rate = int(audio.getframerate())
                    frames = int(audio.getnframes())
                if rate > 0 and frames >= 0:
                    return round(frames / rate, 3)
            except (OSError, wave.Error, EOFError, ValueError):
                # A malformed WAV can still be handled by ffprobe/mutagen or
                # the explicit timing-aware fallback below.
                pass

        # A configured ffmpeg path may live beside a matching ffprobe binary.
        # Keep this local import so the service remains importable without the
        # optional media tools installed.
        probe_candidates: list[str] = []
        try:
            from lib import config

            configured = str(config.get_ffmpeg_path() or "")
            if configured and os.path.isabs(configured):
                directory = os.path.dirname(configured)
                basename = os.path.basename(configured)
                if basename.lower().startswith("ffmpeg"):
                    probe_candidates.extend([
                        os.path.join(directory, "ffprobe"),
                        os.path.join(directory, "ffprobe.exe"),
                    ])
        except Exception:  # pragma: no cover - defensive config isolation
            configured = ""
        for name in ("ffprobe", "ffprobe.exe"):
            located = shutil.which(name)
            if located:
                probe_candidates.append(located)
        seen_probes: set[str] = set()
        for probe in probe_candidates:
            probe = os.path.abspath(probe) if os.path.isabs(probe) else probe
            if probe in seen_probes or not (
                os.path.isabs(probe) and os.path.isfile(probe)
            ) and not shutil.which(probe):
                continue
            seen_probes.add(probe)
            try:
                result = run_no_window(
                    [
                        probe,
                        "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        artifact,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            duration = cls._parse_duration(result.stdout.strip())
            if result.returncode == 0 and duration is not None:
                return round(duration, 3)

        try:
            from mutagen import File as mutagen_file

            media = mutagen_file(artifact)
            info = getattr(media, "info", None) if media is not None else None
            duration = cls._parse_duration(getattr(info, "length", None))
            if duration is not None:
                return round(duration, 3)
        except Exception as exc:  # optional mutagen raises format-specific errors
            logger.debug("读取压缩 artifact 时长失败，将使用 timing fallback: %s", exc)

        duration = cls._parse_duration(fallback)
        if duration is not None:
            return round(duration, 3)
        raise ValueError(f"无法读取正式 artifact 时长: {path}")

    @classmethod
    def _timed_export_duration(
        cls,
        project_dir: str,
        segment_paths: dict[str, str],
    ) -> float:
        """Calculate a timing-aware fallback including export silence rules."""
        script_path = os.path.join(project_dir, "structured_script.json")
        with open(script_path, encoding="utf-8") as file:
            script = json.load(file)
        from lib import audio_pipeline

        director_timing = audio_pipeline._uses_director_timing(script)
        previous_chapter: int | None = None
        duration = 0.0
        for chapter_index, chapter in enumerate(script.get("chapters", [])):
            if not isinstance(chapter, dict):
                continue
            for segment in chapter.get("segments", []):
                if not isinstance(segment, dict):
                    continue
                path = segment_paths.get(str(segment.get("id") or ""))
                if not path or not os.path.isfile(path):
                    continue
                if previous_chapter is not None and not director_timing:
                    duration += (
                        audio_pipeline.CH_SILENCE_SEC
                        if chapter_index != previous_chapter
                        else audio_pipeline.SEG_SILENCE_SEC
                    )
                duration += cls._source_duration([path])
                previous_chapter = chapter_index
        return round(duration, 3)

    @classmethod
    def _task_options(
        cls,
        plan: dict[str, Any],
        *,
        bitrate: str,
        engine_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = engine_snapshot or plan.get("engine_snapshot") or {}
        frozen = resolve_profile(snapshot) if snapshot else {}
        return {
            "format": str(plan["format"]),
            "bitrate": str(bitrate or "192k"),
            "qa_policy": str(plan["qa_policy"]),
            "subtitle_formats": list(plan.get("subtitle_formats") or []),
            "revision_snapshot_hash": str(plan.get("revision_snapshot_hash") or ""),
            "revision_snapshot": list(plan.get("revision_snapshot") or []),
            "delivery_input_hash": str(plan.get("delivery_input_hash") or ""),
            "delivery_input_snapshot": dict(
                plan.get("delivery_input_snapshot") or {}
            ),
            "engine_snapshot": frozen,
        }

    @classmethod
    def _history_for_task(
        cls, project: str, task_id: str
    ) -> dict[str, Any] | None:
        return QualityRepository.find_history_by_field(
            project, "export_jobs", "task_id", str(task_id)
        )

    @classmethod
    def _ensure_history(
        cls,
        record: TaskRecord,
    ) -> dict[str, Any]:
        existing = cls._history_for_task(record.project, record.task_id)
        if existing:
            return existing
        options = record.options if isinstance(record.options, dict) else {}
        return QualityRepository.create_history_record(
            record.project,
            "export_jobs",
            "export",
            {
                "project": record.project,
                "task_id": record.task_id,
                "status": record.status,
                "format": options.get("format", "wav"),
                "bitrate": options.get("bitrate", "192k"),
                "qa_policy": options.get("qa_policy", "require_passed"),
                "subtitle_formats": list(options.get("subtitle_formats") or []),
                "revision_snapshot_hash": options.get("revision_snapshot_hash", ""),
                "revision_snapshot": list(options.get("revision_snapshot") or []),
                "delivery_input_hash": options.get("delivery_input_hash", ""),
                "delivery_input_snapshot": dict(
                    options.get("delivery_input_snapshot") or {}
                ),
                "engine_provenance": (
                    dict((options.get("delivery_input_snapshot") or {}).get("engine_provenance") or {})
                    if isinstance(options.get("delivery_input_snapshot"), dict) else {}
                ),
                "engine_snapshot": public_profile(options.get("engine_snapshot"))
                if isinstance(options.get("engine_snapshot"), dict) and options.get("engine_snapshot") else {},
                "idempotency_key": record.idempotency_key,
                "outputs": [],
                "error": None,
                "manifest_id": "",
            },
        )

    @classmethod
    def _remove_partial_outputs(cls, paths: list[str]) -> None:
        for path in paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                logger.warning("清理导出临时文件失败: %s", path)

    @staticmethod
    def _assert_export_ownership(
        record: TaskRecord,
        owner_id: str | None,
    ) -> None:
        """Fence publication to the runtime that claimed the durable task."""
        if not owner_id:
            return
        current = TaskRepository.load_task(record.task_id)
        if current is None or current.owner_id != str(owner_id):
            raise ExportOwnershipLost()
        if current.status == "cancelling" and current.control_intent == "cancel":
            raise ExportCancelled("正式导出已取消")
        if current.status != "running":
            raise ExportOwnershipLost()

    @classmethod
    def _validate_execution_snapshot(
        cls,
        record: TaskRecord,
    ) -> dict[str, Any]:
        options = record.options if isinstance(record.options, dict) else {}
        current = cls.plan_export(
            record.project,
            str(options.get("format") or "wav"),
            qa_policy=str(options.get("qa_policy") or "require_passed"),
            subtitle_formats=list(options.get("subtitle_formats") or []),
            exclude_task_id=record.task_id,
            engine_snapshot=options.get("engine_snapshot")
            if isinstance(options.get("engine_snapshot"), dict) else None,
        )
        expected_hash = str(options.get("delivery_input_hash") or "")
        expected_revision_hash = str(options.get("revision_snapshot_hash") or "")
        if (
            not expected_hash
            or expected_hash != str(current.get("delivery_input_hash") or "")
            or expected_revision_hash != str(current.get("revision_snapshot_hash") or "")
        ):
            raise DeliveryInputChanged()
        if not current.get("ready"):
            raise ExportPlanError(current)
        return current

    @classmethod
    def execute_export_job(
        cls,
        record: TaskRecord,
        *,
        is_cancelled: Any = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute one immutable export snapshot inside the singleton runtime."""
        if callable(is_cancelled) and is_cancelled():
            raise ExportCancelled("正式导出已取消")
        cls._assert_export_ownership(record, owner_id)
        cls._validate_execution_snapshot(record)
        options = record.options if isinstance(record.options, dict) else {}
        history = cls._ensure_history(record)
        QualityRepository.update_history_record(
            record.project,
            "export_jobs",
            history["export_id"],
            status="running",
        )
        project = record.project
        project_dir = ProjectRepository.get_project_dir(project)
        export_dir = os.path.join(
            project_dir, "exports", str(record.task_id)
        )
        os.makedirs(export_dir, exist_ok=True)
        segment_paths = {
            str(item["segment_id"]): os.path.join(
                project_dir, *str(item["relative_path"]).split("/")
            )
            for item in options.get("revision_snapshot", [])
            if isinstance(item, dict)
        }
        produced: list[str] = []
        manifest: dict[str, Any] | None = None
        try:
            if callable(is_cancelled) and is_cancelled():
                raise ExportCancelled("正式导出已取消")
            output = cls.export(
                project_dir,
                str(options.get("format") or "wav"),
                str(options.get("bitrate") or "192k"),
                output_dir=export_dir,
                segment_paths=segment_paths,
                streaming_postprocess=True,
                atomic_publish=True,
            )
            produced.append(output)
            if callable(is_cancelled) and is_cancelled():
                raise ExportCancelled("正式导出已取消")
            cls._assert_export_ownership(record, owner_id)
            if options.get("subtitle_formats"):
                from lib import audio_pipeline

                if callable(is_cancelled) and is_cancelled():
                    raise ExportCancelled("正式导出已取消")
                cls._assert_export_ownership(record, owner_id)
                produced.extend(audio_pipeline.generate_subtitles(
                    project_dir,
                    formats=options["subtitle_formats"],
                    output_dir=export_dir,
                    segment_paths=segment_paths,
                    require_complete=True,
                    atomic_publish=True,
                ))
            cls._assert_export_ownership(record, owner_id)
            final = cls._validate_execution_snapshot(record)
            if str(final.get("delivery_input_hash") or "") != str(
                options.get("delivery_input_hash") or ""
            ):
                raise DeliveryInputChanged()
            duration_fallback = cls._timed_export_duration(
                project_dir,
                segment_paths,
            )
            duration = cls._artifact_duration(
                output,
                options.get("format"),
                fallback=duration_fallback,
            )
            artifacts = []
            for path in produced:
                if not os.path.isfile(path) or os.path.getsize(path) <= 0:
                    raise RuntimeError("正式导出未生成有效 artifact")
                artifacts.append({
                    "artifact_id": f"artifact_{uuid.uuid4().hex[:20]}",
                    "format": os.path.splitext(path)[1].lstrip(".").lower(),
                    "relative_path": cls._relative_output(project, path),
                    "size": os.path.getsize(path),
                    "sha256": cls._file_sha256(path),
                    "duration_seconds": duration if path == output else None,
                })
            cls._assert_export_ownership(record, owner_id)
            manifest = QualityRepository.create_history_record(
                project,
                "delivery_manifests",
                "manifest",
                {
                    "project": project,
                    "export_id": record.task_id,
                    # Publish the manifest in two phases.  A worker that loses
                    # its fence may leave audit history, but never a ready
                    # Delivery Manifest or a done task.
                    "ready": False,
                    "format": options.get("format", "wav"),
                    "outputs": artifacts,
                    "duration_seconds": duration,
                    "chapters": final["summary"]["chapters"],
                    "segments": final["summary"]["segments"],
                    "qa_policy": options.get("qa_policy", "require_passed"),
                    "revision_snapshot_hash": options.get("revision_snapshot_hash", ""),
                    "revision_snapshot": options.get("revision_snapshot", []),
                    "delivery_input_hash": options.get("delivery_input_hash", ""),
                    "delivery_input_snapshot": options.get("delivery_input_snapshot", {}),
                    "engine_provenance": (
                        dict((options.get("delivery_input_snapshot") or {}).get("engine_provenance") or {})
                        if isinstance(options.get("delivery_input_snapshot"), dict) else {}
                    ),
                    "engine_snapshot": public_profile(options.get("engine_snapshot"))
                    if isinstance(options.get("engine_snapshot"), dict) and options.get("engine_snapshot") else {},
                    "metadata": final["summary"]["metadata"],
                },
            )
            cls._assert_export_ownership(record, owner_id)
            manifest = QualityRepository.update_history_record(
                project,
                "delivery_manifests",
                manifest["manifest_id"],
                ready=True,
            )
            cls._assert_export_ownership(record, owner_id)
            updated = QualityRepository.update_history_record(
                project,
                "export_jobs",
                history["export_id"],
                status="done",
                outputs=artifacts,
                manifest_id=manifest["manifest_id"],
                finished_at=manifest["created_at"],
                delivery_input_hash=options.get("delivery_input_hash", ""),
            )
            cls._assert_export_ownership(record, owner_id)
            return {
                **cls._public_export(updated),
                "delivery_manifest": cls._public_manifest(manifest),
            }
        except Exception as exc:
            cls._remove_partial_outputs(produced)
            # Each durable export owns a unique directory, so an interrupted
            # or failed run can safely remove every `.part`/intermediate file
            # without touching a prior official artifact.
            shutil.rmtree(export_dir, ignore_errors=True)
            try:
                if manifest is not None:
                    QualityRepository.update_history_record(
                        project,
                        "delivery_manifests",
                        manifest["manifest_id"],
                        ready=False,
                        error={
                            "code": getattr(exc, "code", type(exc).__name__),
                            "message": "正式导出未完成",
                        },
                    )
                history_error = None if isinstance(exc, ExportCancelled) else {
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": "正式导出未完成",
                }
                QualityRepository.update_history_record(
                    project,
                    "export_jobs",
                    history["export_id"],
                    status=("cancelled" if isinstance(exc, ExportCancelled) else "error"),
                    error=history_error,
                )
            except Exception:
                logger.exception("更新导出历史失败: %s", record.task_id)
            raise

    @classmethod
    def start_export(
        cls,
        project_name: str,
        fmt: str = "wav",
        *,
        bitrate: str = "192k",
        qa_policy: str = "require_passed",
        subtitle_formats: tuple[str, ...] | list[str] = (),
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Create a durable export job and return without waiting for the book."""
        project = str(project_name or "").strip()
        key = str(idempotency_key or "").strip()
        existing = (
            TaskRepository.find_by_idempotency(project, "export", key)
            if key else None
        )
        if existing is not None:
            replay_plan = cls.plan_export(
                project,
                fmt,
                qa_policy=qa_policy,
                subtitle_formats=subtitle_formats,
                exclude_task_id=existing.task_id,
                engine_snapshot=(
                    existing.options.get("engine_snapshot")
                    if isinstance(existing.options, dict) else None
                ),
            )
            replay_options = cls._task_options(replay_plan, bitrate=bitrate)
            if isinstance(existing.options, dict) and isinstance(existing.options.get("engine_snapshot"), dict):
                replay_options["engine_snapshot"] = existing.options["engine_snapshot"]
            if existing.options == replay_options:
                return {"created": False, **cls._public_export(existing)}
            raise ExportIdempotencyConflict(existing.task_id)
        plan = cls.plan_export(
            project,
            fmt,
            qa_policy=qa_policy,
            subtitle_formats=subtitle_formats,
        )
        if not plan["ready"]:
            raise ExportPlanError(plan)
        # Export does not load TTS, but its provenance is the engine identity
        # of the active revisions it will consume.  Freeze that identity into
        # the task row and let execution revalidate it against the same plan.
        provenance = plan.get("engine_provenance") if isinstance(plan, dict) else {}
        frozen_engine = (
            provenance.get("engine_snapshot")
            if isinstance(provenance, dict) and provenance.get("status") == "uniform"
            else None
        )
        options = cls._task_options(plan, bitrate=bitrate, engine_snapshot=frozen_engine)
        export_id = f"export_{uuid.uuid4().hex[:20]}"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        task = TaskRecord(
            task_id=export_id,
            task_type="export",
            project=project,
            status="pending",
            source="mcp",
            scope={"all": True, "chapter_ids": [], "segment_ids": []},
            options=options,
            progress={"total": 1, "completed": 0, "failed": 0, "percent": 0.0},
            idempotency_key=key,
            created_at=now,
            updated_at=now,
        )
        outcome, durable = TaskRepository.create_runtime_task(task)
        if outcome == "idempotent":
            return {"created": False, **cls._public_export(durable)}
        if outcome == "idempotency_conflict":
            raise ExportIdempotencyConflict(durable.task_id)
        if outcome == "active":
            plan["ready"] = False
            plan.setdefault("blockers", []).append({
                "code": "EXPORT_ACTIVE" if durable.task_type == "export" else "PRODUCTION_ACTIVE",
                "message": "项目存在正在运行的任务",
                "task_id": durable.task_id,
                "status": durable.status,
            })
            raise ExportPlanError(plan)
        cls._ensure_history(durable)
        from services.production_runtime import ProductionRuntimeClient

        ProductionRuntimeClient.ensure_running()
        return {"created": True, **cls._public_export(durable)}

    @staticmethod
    def _public_export(record: dict[str, Any]) -> dict[str, Any]:
        if isinstance(record, TaskRecord):
            options = record.options if isinstance(record.options, dict) else {}
            error_code = "EXPORT_INTERRUPTED" if record.status == "interrupted" else "EXPORT_ERROR"
            if record.error_summary and ":" in record.error_summary:
                candidate = record.error_summary.split(":", 1)[0].strip()
                if candidate and candidate.replace("_", "").isalnum():
                    error_code = candidate
            return {
                "export_id": record.task_id,
                "task_id": record.task_id,
                "project": record.project,
                "status": record.status,
                "format": options.get("format", "wav"),
                "bitrate": options.get("bitrate", "192k"),
                "qa_policy": options.get("qa_policy", "require_passed"),
                "subtitle_formats": list(options.get("subtitle_formats") or []),
                "revision_snapshot_hash": options.get("revision_snapshot_hash", ""),
                "delivery_input_hash": options.get("delivery_input_hash", ""),
                "idempotency_key": record.idempotency_key,
                "outputs": record.progress.get("result", {}).get("outputs", [])
                if isinstance(record.progress, dict)
                else [],
                "manifest_id": record.progress.get("result", {}).get("manifest_id", "")
                if isinstance(record.progress, dict)
                else "",
                "error": {
                    "code": error_code,
                    "message": record.error_summary,
                } if record.error_summary else None,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "finished_at": record.finished_at,
                "engine_snapshot": public_profile(options.get("engine_snapshot"))
                if isinstance(options.get("engine_snapshot"), dict) and options.get("engine_snapshot") else {},
            }
        return {
            key: record.get(key)
            for key in (
                "export_id",
                "project",
                "status",
                "format",
                "bitrate",
                "qa_policy",
                "subtitle_formats",
                "revision_snapshot_hash",
                "delivery_input_hash",
                "idempotency_key",
                "outputs",
                "manifest_id",
                "error",
                "created_at",
                "updated_at",
                "finished_at",
                "engine_snapshot",
            )
            if key in record
        }

    @staticmethod
    def _public_manifest(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record.get(key)
            for key in (
                "manifest_id",
                "project",
                "export_id",
                "ready",
                "format",
                "outputs",
                "duration_seconds",
                "chapters",
                "segments",
                "qa_policy",
                "revision_snapshot_hash",
                "delivery_input_hash",
                "delivery_input_snapshot",
                "engine_provenance",
                "engine_snapshot",
                "metadata",
                "created_at",
                "updated_at",
            )
            if key in record
        }

    @classmethod
    def get_export_task(
        cls, project_name: str, export_id: str
    ) -> dict[str, Any]:
        record = TaskRepository.load_task(export_id)
        if record is not None and record.task_type == "export":
            return cls._public_export(record)
        record = QualityRepository.get_history_record(project_name, "export_jobs", export_id)
        if not record:
            raise KeyError(f"导出任务不存在: {export_id}")
        return cls._public_export(record)

    @classmethod
    def list_exports(cls, project_name: str) -> list[dict[str, Any]]:
        durable = [
            cls._public_export(record)
            for record in TaskRepository.list_tasks(
                project=project_name, task_type="export"
            )
        ]
        durable_ids = {str(item.get("export_id")) for item in durable}
        history = [
            cls._public_export(record)
            for record in QualityRepository.list_history(
                project_name, "export_jobs"
            )
            if str(record.get("task_id") or record.get("export_id") or "")
            not in durable_ids
        ]
        return durable + history

    @classmethod
    def get_delivery_manifest(
        cls, project_name: str, manifest_id: str | None = None
    ) -> dict[str, Any] | None:
        if manifest_id:
            record = QualityRepository.get_history_record(
                project_name, "delivery_manifests", manifest_id
            )
            if not record:
                record = QualityRepository.find_history_by_field(
                    project_name,
                    "delivery_manifests",
                    "export_id",
                    manifest_id,
                )
            return cls._public_manifest(record) if record else None
        manifests = QualityRepository.list_history(
            project_name, "delivery_manifests"
        )
        return cls._public_manifest(manifests[0]) if manifests else None
