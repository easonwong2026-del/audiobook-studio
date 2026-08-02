"""Audiobook Studio v4 source-first domain models."""

from .ai_first import (
    BibleCharacter,
    BibleEvidence,
    BibleRelationship,
    CharacterBibleDocument,
    ReviewPatch,
    ScriptDirectorBatch,
    ScriptDirectorSegment,
    ScriptReviewResponse,
)
from .character_consolidation import (
    CharacterConsolidationRequest,
    CharacterConsolidationResponse,
    ConsolidatedCharacter,
    ConsolidationCandidate,
    UnresolvedCharacterGroup,
)
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
    "BibleCharacter",
    "BibleEvidence",
    "BibleRelationship",
    "ChapterScript",
    "CharacterBibleDocument",
    "CharacterCandidate",
    "CharacterCandidatesDocument",
    "CharacterConsolidationRequest",
    "CharacterConsolidationResponse",
    "CharacterEvidence",
    "CharacterExtractionResponse",
    "ConsolidatedCharacter",
    "ConsolidationCandidate",
    "ExtractedCharacter",
    "ProjectManifest",
    "ReviewPatch",
    "ScriptDirectorBatch",
    "ScriptDirectorSegment",
    "ScriptDocument",
    "ScriptReviewResponse",
    "SemanticSegment",
    "SourceMetadata",
    "Speaker",
    "SpeakersDocument",
    "UnresolvedCharacterGroup",
    "ValidationError",
]
