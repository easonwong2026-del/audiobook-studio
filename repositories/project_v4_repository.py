"""Atomic repository for source-first v4 projects."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from domain.v4 import (
    ProjectManifest,
    ScriptDocument,
    SourceMetadata,
    SpeakersDocument,
)
from repositories.runtime_repository import RuntimeRepository
from repositories.v4_atomic import atomic_write_json, atomic_write_text

_DIRECTORIES = (
    "source",
    "script",
    "production",
    "runtime/benchmarks",
    "audio/chunks",
    "audio/chapters",
    "audio/previews",
    "output",
    "revisions",
)


class V4ProjectAlreadyExistsError(FileExistsError):
    pass


class ProjectV4Repository:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def create(
        self,
        directory_name: str,
        manifest: ProjectManifest,
        source_text: str,
        source_metadata: SourceMetadata,
        script: ScriptDocument,
        speakers: SpeakersDocument,
    ) -> Path:
        self._validate_directory_name(directory_name)
        target = self.root / directory_name
        if target.exists():
            raise V4ProjectAlreadyExistsError(f"project already exists: {directory_name}")
        source_metadata.validate(source_text)
        script.validate(source_text)
        speakers.validate()
        manifest.validate()
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".tmp_v4_{directory_name}_{uuid.uuid4().hex}"
        try:
            for directory in _DIRECTORIES:
                (temporary / directory).mkdir(parents=True, exist_ok=True)
            atomic_write_json(temporary / "project.json", manifest.to_dict())
            atomic_write_text(temporary / "source/source.txt", source_text)
            atomic_write_json(
                temporary / "source/source.meta.json", source_metadata.to_dict()
            )
            atomic_write_json(temporary / "script/script.json", script.to_dict())
            atomic_write_json(temporary / "script/speakers.json", speakers.to_dict())
            RuntimeRepository(temporary / "runtime/runtime.db").initialize()
            os.replace(temporary, target)
            return target
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def load_manifest(self, project_path: str | Path) -> ProjectManifest:
        path = Path(project_path) / "project.json"
        with path.open("r", encoding="utf-8") as handle:
            return ProjectManifest.from_dict(json.load(handle))

    def cleanup_temporary_projects(self) -> list[Path]:
        removed: list[Path] = []
        if not self.root.exists():
            return removed
        for path in self.root.iterdir():
            if path.is_dir() and path.name.startswith(".tmp_v4_"):
                shutil.rmtree(path)
                removed.append(path)
        return removed

    @staticmethod
    def _validate_directory_name(value: str) -> None:
        if (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or Path(value).is_absolute()
        ):
            raise ValueError("project directory name must be a single safe component")
