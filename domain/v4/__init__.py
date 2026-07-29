"""Audiobook Studio v4 source-first domain models."""

from .fakes import FakeSpeakerRouter, FakeTtsAdapter
from .models import (
    ChapterScript,
    ProjectManifest,
    ScriptDocument,
    SemanticSegment,
    SourceMetadata,
    Speaker,
    SpeakersDocument,
    ValidationError,
)

__all__ = [
    "ChapterScript",
    "FakeSpeakerRouter",
    "FakeTtsAdapter",
    "ProjectManifest",
    "ScriptDocument",
    "SemanticSegment",
    "SourceMetadata",
    "Speaker",
    "SpeakersDocument",
    "ValidationError",
]
