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


@dataclass(frozen=True)
class V4CreationResult:
    project_path: Path
    unresolved_segments: int


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
    ) -> V4CreationResult:
        name = project_name.strip()
        if not name:
            raise ValueError("project name cannot be empty")
        imported = self.importer.import_file(source_path)
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
        return V4CreationResult(path, int(unresolved))

    @staticmethod
    def _directory_name(name: str, project_id: str) -> str:
        safe = re.sub(r"[^\w\u3400-\u9fff.-]+", "-", name, flags=re.UNICODE).strip(".-")
        return f"{safe or 'project'}-{project_id[-8:]}"
