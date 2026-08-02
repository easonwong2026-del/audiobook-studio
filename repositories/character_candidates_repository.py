"""Atomic persistence for review-only v4 character candidates."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from domain.v4.character_extraction import CharacterCandidatesDocument
from domain.v4.models import source_sha256
from repositories.v4_atomic import atomic_write_json


class CharacterCandidatesRepository:
    """Load/save candidates without making them part of ``speakers.json``."""

    relative_path = Path("script/character_candidates.json")

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)
        self.path = self.project_path / self.relative_path

    @staticmethod
    def _source_hash(source_text_or_sha: str) -> str:
        value = str(source_text_or_sha)
        if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
            return value
        return source_sha256(value)

    def load(self, source_text_or_sha: str) -> CharacterCandidatesDocument:
        expected_sha = self._source_hash(source_text_or_sha)
        if not self.path.is_file():
            return CharacterCandidatesDocument.empty(expected_sha)
        with self.path.open("r", encoding="utf-8") as handle:
            document = CharacterCandidatesDocument.from_dict(json.load(handle))
        # Candidate evidence belongs to one immutable source snapshot.  A changed
        # source starts an empty candidate view; the old file is retained until the
        # next successful save so recovery/revisions remain inspectable.
        if document.source_sha256 != expected_sha:
            return CharacterCandidatesDocument.empty(expected_sha)
        return document

    def save(self, document: CharacterCandidatesDocument) -> None:
        document.validate()
        previous = None
        if self.path.is_file():
            with self.path.open("r", encoding="utf-8") as handle:
                previous = json.load(handle)
        if previous is not None:
            old = CharacterCandidatesDocument.from_dict(previous)
            if (
                old.source_sha256 == document.source_sha256
                and old.to_dict() == document.to_dict()
            ):
                return
            if (
                old.source_sha256 == document.source_sha256
                and document.revision <= old.revision
            ):
                raise ValueError("character candidates revision must increase")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            snapshot = self.project_path / "revisions" / f"character-candidates-{stamp}"
            snapshot.mkdir(parents=True, exist_ok=False)
            atomic_write_json(snapshot / self.path.name, previous)
        atomic_write_json(self.path, document.to_dict())
