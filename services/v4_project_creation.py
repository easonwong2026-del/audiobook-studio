"""Application service for local, source-first v4 project creation."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domain.v4 import ProjectManifest
from domain.v4.production import TtsProfile
from repositories.project_v4_repository import ProjectV4Repository
from services.source_import_service import SourceImportService
from services.source_segmenter import SourceSegmenter
from services.v4_project_analysis_pipeline import (
    V4AnalysisResult,
    V4ProjectAnalysisPipeline,
)


@dataclass(frozen=True)
class V4CreationResult:
    project_path: Path
    unresolved_segments: int
    analysis: V4AnalysisResult | None = None
    analysis_error: str = ""


class V4ProjectCreationService:
    def __init__(
        self,
        repository: ProjectV4Repository,
        importer: SourceImportService | None = None,
        segmenter: SourceSegmenter | None = None,
    ):
        self.repository = repository
        self.importer = importer or SourceImportService()
        self.segmenter = segmenter or SourceSegmenter()

    def create_from_source(
        self,
        source_path: str | Path,
        project_name: str,
        *,
        title: str = "",
        author: str = "",
        progress_callback=None,
        auto_analyze: bool = True,
        analysis_pipeline_factory=None,
    ) -> V4CreationResult:
        name = project_name.strip()
        if not name:
            raise ValueError("project name cannot be empty")
        self._report(progress_callback, "正在导入书稿")
        imported = self.importer.import_file(source_path)
        self._report(progress_callback, "正在识别章节")
        segmented = self.segmenter.segment(imported.text)
        project_id = f"project_{uuid.uuid4().hex}"
        directory_name = self._directory_name(name, project_id)
        timestamp = datetime.now(timezone.utc).isoformat()
        manifest = ProjectManifest(
            project_id=project_id,
            name=name,
            title=title.strip() or name,
            author=author.strip(),
            created_at=timestamp,
            updated_at=timestamp,
        )
        profile_path = (
            Path(__file__).resolve().parents[1]
            / "config/tts_profiles/indextts2-rtx5070ti-laptop-12gb-v1.json"
        )
        with profile_path.open("r", encoding="utf-8") as handle:
            profile = TtsProfile.from_dict(json.load(handle))
        path = self.repository.create(
            directory_name=directory_name,
            manifest=manifest,
            source_text=imported.text,
            source_metadata=imported.metadata,
            script=segmented.script,
            speakers=segmented.speakers,
            tts_profile=profile,
        )
        unresolved = sum(
            segment.status == "unresolved"
            for chapter in segmented.script.chapters
            for segment in chapter.segments
        )
        analysis: V4AnalysisResult | None = None
        analysis_error = ""
        final_script = segmented.script
        if auto_analyze:
            try:
                pipeline = (
                    analysis_pipeline_factory(path)
                    if analysis_pipeline_factory is not None
                    else V4ProjectAnalysisPipeline.from_ai_settings(path)
                )
                analysis = pipeline.run(progress_callback=progress_callback)
                final_script = analysis.script
                unresolved = sum(
                    segment.status == "unresolved"
                    for chapter in final_script.chapters
                    for segment in chapter.segments
                )
            except Exception as exc:  # noqa: BLE001 - imported project remains usable
                analysis_error = str(exc)[:500]
                self._report(progress_callback, f"分析未完成：{analysis_error}")
        return V4CreationResult(
            path,
            int(unresolved),
            analysis=analysis,
            analysis_error=analysis_error,
        )

    @staticmethod
    def _report(callback, message: str) -> None:
        if callback is None:
            return
        callback(message)

    @staticmethod
    def _directory_name(name: str, project_id: str) -> str:
        safe = re.sub(r"[^\w\u3400-\u9fff.-]+", "-", name, flags=re.UNICODE).strip(".-")
        return f"{safe or 'project'}-{project_id[-8:]}"
