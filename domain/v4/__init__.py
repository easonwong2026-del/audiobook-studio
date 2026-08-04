"""Audiobook Studio v4 source-first domain models."""

from .acting import (
    ACTING_REQUEST_SCHEMA,
    ACTING_RESPONSE_SCHEMA,
    ActingResponse,
    ActingSegment,
)
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
from .chapter_analysis import (
    CHAPTER_ANALYSIS_REQUEST_SCHEMA,
    CHAPTER_ANALYSIS_RESPONSE_SCHEMA,
    ChapterAnalysisRequest,
    ChapterAnalysisResponse,
    ChapterAnalysisSegment,
    ChapterCharacterUpdate,
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
    "ACTING_REQUEST_SCHEMA",
    "ACTING_RESPONSE_SCHEMA",
    "CHAPTER_ANALYSIS_REQUEST_SCHEMA",
    "CHAPTER_ANALYSIS_RESPONSE_SCHEMA",
    "ActingResponse",
    "ActingSegment",
    "BibleCharacter",
    "BibleEvidence",
    "BibleRelationship",
    "ChapterAnalysisRequest",
    "ChapterAnalysisResponse",
    "ChapterAnalysisSegment",
    "ChapterCharacterUpdate",
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
