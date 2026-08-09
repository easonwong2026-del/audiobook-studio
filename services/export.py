"""导出服务：包 ``lib.audio_pipeline.export_book``，透传 R2 报错（禁止 import gradio）。

UI 层 ``app.do_export`` 调用本服务，捕获其抛出的 ``ExportError`` / ``RuntimeError``
后显式展示给用户（取代原先 ffmpeg 失败时「静默回退 WAV」导致 UI 收不到信号的行为）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
import wave
from typing import Any

from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from services.quality import QualityService

logger = logging.getLogger(__name__)


class ExportPlanError(RuntimeError):
    """Raised when a formal export fails its readiness policy."""

    def __init__(self, plan: dict[str, Any]) -> None:
        super().__init__("导出前检查未通过")
        self.code = "EXPORT_NOT_READY"
        self.plan = plan


class ExportService:
    """导出成品：委托 ``audio_pipeline.export_book``，错误直接上抛。"""

    @staticmethod
    def export(project_dir: str, fmt: str, bitrate: str = "192k",
               output_dir: str = "",
               *, segment_paths: dict[str, str] | None = None) -> str:
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

    @classmethod
    def plan_export(
        cls,
        project_name: str,
        fmt: str = "wav",
        *,
        qa_policy: str = "require_passed",
        subtitle_formats: tuple[str, ...] | list[str] = (),
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
        meta, script, _bindings = ProjectRepository.load_project(project)
        segments = cls._segments(script)
        project_status = dict(getattr(meta, "segments_status", {}) or {})
        revisions: list[dict[str, Any]] = []
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
        """Run a formal export and persist job/artifact/delivery history."""
        project = str(project_name or "").strip()
        key = str(idempotency_key or "").strip()
        if key:
            replay = QualityRepository.find_history_by_field(
                project, "export_jobs", "idempotency_key", key
            )
            if replay:
                return {"created": False, **cls._public_export(replay)}
        plan = cls.plan_export(
            project,
            fmt,
            qa_policy=qa_policy,
            subtitle_formats=subtitle_formats,
        )
        if not plan["ready"]:
            raise ExportPlanError(plan)
        job = QualityRepository.create_history_record(
            project,
            "export_jobs",
            "export",
            {
                "project": project,
                "status": "pending",
                "format": plan["format"],
                "bitrate": str(bitrate or "192k"),
                "qa_policy": plan["qa_policy"],
                "subtitle_formats": plan["subtitle_formats"],
                "revision_snapshot_hash": plan["revision_snapshot_hash"],
                "revision_snapshot": plan["revision_snapshot"],
                "idempotency_key": key,
                "outputs": [],
                "error": None,
                "manifest_id": "",
            },
        )
        export_id = job["export_id"]
        try:
            QualityRepository.update_history_record(
                project, "export_jobs", export_id, status="running"
            )
            project_dir = ProjectRepository.get_project_dir(project)
            segment_paths = {
                item["segment_id"]: os.path.join(
                    project_dir,
                    *str(item["relative_path"]).split("/"),
                )
                for item in plan["revision_snapshot"]
            }
            output = cls.export(
                project_dir,
                plan["format"],
                bitrate,
                output_dir="",
                segment_paths=segment_paths,
            )
            produced = [output]
            if plan["subtitle_formats"]:
                from lib import audio_pipeline

                produced.extend(audio_pipeline.generate_subtitles(
                    project_dir,
                    formats=plan["subtitle_formats"],
                    segment_paths=segment_paths,
                    require_complete=True,
                ))
            duration = cls._source_duration(list(segment_paths.values()))
            artifacts = [
                {
                    "artifact_id": f"artifact_{uuid.uuid4().hex[:20]}",
                    "format": os.path.splitext(path)[1].lstrip(".").lower(),
                    "relative_path": cls._relative_output(project, path),
                    "size": os.path.getsize(path),
                    "sha256": cls._file_sha256(path),
                    "duration_seconds": duration if path == output else None,
                }
                for path in produced
            ]
            manifest = QualityRepository.create_history_record(
                project,
                "delivery_manifests",
                "manifest",
                {
                    "project": project,
                    "export_id": export_id,
                    "ready": True,
                    "format": plan["format"],
                    "outputs": artifacts,
                    "duration_seconds": duration,
                    "chapters": plan["summary"]["chapters"],
                    "segments": plan["summary"]["segments"],
                    "qa_policy": plan["qa_policy"],
                    "revision_snapshot_hash": plan["revision_snapshot_hash"],
                    "metadata": plan["summary"]["metadata"],
                },
            )
            job = QualityRepository.update_history_record(
                project,
                "export_jobs",
                export_id,
                status="done",
                outputs=artifacts,
                manifest_id=manifest["manifest_id"],
                finished_at=manifest["created_at"],
            )
            return {
                "created": True,
                **cls._public_export(job),
                "delivery_manifest": cls._public_manifest(manifest),
            }
        except Exception as exc:
            QualityRepository.update_history_record(
                project,
                "export_jobs",
                export_id,
                status="error",
                error={
                    "code": type(exc).__name__,
                    "message": "正式导出失败",
                },
            )
            raise

    @staticmethod
    def _public_export(record: dict[str, Any]) -> dict[str, Any]:
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
                "idempotency_key",
                "outputs",
                "manifest_id",
                "error",
                "created_at",
                "updated_at",
                "finished_at",
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
        record = QualityRepository.get_history_record(
            project_name, "export_jobs", export_id
        )
        if not record:
            raise KeyError(f"导出任务不存在: {export_id}")
        return cls._public_export(record)

    @classmethod
    def list_exports(cls, project_name: str) -> list[dict[str, Any]]:
        return [
            cls._public_export(record)
            for record in QualityRepository.list_history(
                project_name, "export_jobs"
            )
        ]

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
