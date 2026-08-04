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

from services.chapter_analysis_service import (
    ChapterAnalysisResult,
    ChapterAnalysisService,
)
from services.source_import_service import SourceImportService
from services.source_segmenter import SourceSegmenter
from services.v4_project_analysis_pipeline import V4AnalysisResult


@dataclass(frozen=True)
class V4CreationResult:
    project_path: Path
    unresolved_segments: int
    analysis: V4AnalysisResult | ChapterAnalysisResult | None = None
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
        source_path: str | Path | None,
        project_name: str,
        *,
        title: str = "",
        author: str = "",
        progress_callback=None,
        auto_analyze: bool = True,
        analysis_pipeline_factory=None,
        source_text: str | None = None,
        chapter_title: str = "",
    ) -> V4CreationResult:
        name = project_name.strip()
        if not name:
            raise ValueError("project name cannot be empty")
        self._report(progress_callback, "正在导入书稿")
        if source_text is not None and source_text.strip():
            imported = self.importer.import_text(
                source_text,
                original_filename=(title.strip() or "pasted-chapter") + ".txt",
            )
            self._report(progress_callback, "已接收粘贴的当前章节")
            source_only_chapter = getattr(self.segmenter, "source_only_chapter", None)
            segmented = (
                source_only_chapter(
                    imported.text,
                    title=chapter_title.strip() or "当前章节",
                )
                if callable(source_only_chapter)
                else SourceSegmenter().source_only_chapter(
                    imported.text,
                    title=chapter_title.strip() or "当前章节",
                )
            )
        else:
            if source_path is None:
                raise ValueError("source file or chapter text is required")
            imported = self.importer.import_file(source_path)
            self._report(progress_callback, "按当前章节导入原文")
            # The default fast flow treats the uploaded text as one complete
            # chapter.  The old chapter detector remains available through
            # SourceSegmenter.source_only for advanced/compatibility callers.
            source_only_chapter = getattr(self.segmenter, "source_only_chapter", None)
            segmented = (
                source_only_chapter(
                    imported.text,
                    title=chapter_title.strip() or "当前章节",
                )
                if callable(source_only_chapter)
                else SourceSegmenter().source_only_chapter(
                    imported.text,
                    title=chapter_title.strip() or "当前章节",
                )
            )
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
                if analysis_pipeline_factory is not None:
                    # Test/legacy injection remains supported and explicitly
                    # opts into the old pipeline.
                    pipeline = analysis_pipeline_factory(path)
                    analysis = pipeline.run(progress_callback=progress_callback)
                else:
                    analysis = ChapterAnalysisService.from_ai_settings(path).analyze(
                        progress_callback=progress_callback
                    )
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
