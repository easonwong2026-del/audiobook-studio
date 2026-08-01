"""Atomic repository for source-first v4 projects."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from domain.v4 import (
    ProjectManifest,
    ScriptDocument,
    SourceMetadata,
    SpeakersDocument,
)
from domain.v4.production import TtsProfile
from repositories.production_repository import ProductionRepository
from repositories.runtime_repository import RuntimeRepository
from repositories.v4_atomic import (
    _filesystem_path,
    _short_tmp,
    atomic_write_json,
    atomic_write_text,
    replace_with_retry,
)

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
        self.root = _filesystem_path(Path(root))

    def create(
        self,
        directory_name: str,
        manifest: ProjectManifest,
        source_text: str,
        source_metadata: SourceMetadata,
        script: ScriptDocument,
        speakers: SpeakersDocument,
        tts_profile: TtsProfile | None = None,
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
        temporary = self.root / f".tmp_v4_{_short_tmp()}"
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
            if tts_profile is not None:
                ProductionRepository(temporary).initialize(tts_profile)
            RuntimeRepository(temporary / "runtime/runtime.db").initialize()
            replace_with_retry(temporary, target)
            return target
        except Exception as exc:
            temporary_path = _filesystem_path(temporary)
            try:
                if temporary_path.exists():
                    shutil.rmtree(temporary_path)
            except OSError as cleanup_error:
                raise exc from cleanup_error
            raise

    def load_manifest(self, project_path: str | Path) -> ProjectManifest:
        path = Path(project_path) / "project.json"
        with path.open("r", encoding="utf-8") as handle:
            return ProjectManifest.from_dict(json.load(handle))

    def save_script_and_speakers(
        self,
        project_path: str | Path,
        source_text: str,
        script: ScriptDocument,
        speakers: SpeakersDocument,
    ) -> None:
        """Persist reviewed routing data with immutable pre-edit snapshots."""
        project = Path(project_path)
        current_script_path = project / "script/script.json"
        current_speakers_path = project / "script/speakers.json"
        with current_script_path.open("r", encoding="utf-8") as handle:
            previous_script = json.load(handle)
        with current_speakers_path.open("r", encoding="utf-8") as handle:
            previous_speakers = json.load(handle)
        old_script = ScriptDocument.from_dict(previous_script, source_text)
        old_speakers = SpeakersDocument.from_dict(previous_speakers)
        script.validate(source_text)
        speakers.validate()
        if script.revision <= old_script.revision:
            raise ValueError("script revision must increase")
        if speakers.revision < old_speakers.revision:
            raise ValueError("speakers revision cannot decrease")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        snapshot = project / "revisions" / f"routing-{stamp}"
        snapshot.mkdir(parents=True, exist_ok=False)
        atomic_write_json(snapshot / "script.json", previous_script)
        atomic_write_json(snapshot / "speakers.json", previous_speakers)
        atomic_write_json(current_script_path, script.to_dict())
        atomic_write_json(current_speakers_path, speakers.to_dict())

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
