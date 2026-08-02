"""Audiobook Studio v4 source-first domain models."""

from .character_extraction import (
    CharacterCandidate,
    CharacterCandidatesDocument,
    CharacterEvidence,
    CharacterExtractionResponse,
    ExtractedCharacter,
)
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
    "CharacterCandidate",
    "CharacterCandidatesDocument",
    "CharacterEvidence",
    "CharacterExtractionResponse",
    "ExtractedCharacter",
    "ProjectManifest",
    "ScriptDocument",
    "SemanticSegment",
    "SourceMetadata",
    "Speaker",
    "SpeakersDocument",
    "ValidationError",
]
