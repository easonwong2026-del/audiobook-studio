"""Chapter-scoped character extraction and book-wide candidate consolidation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from domain.v4 import ChapterScript, ScriptDocument
from domain.v4.character_extraction import (
    CharacterCandidate,
    CharacterCandidatesDocument,
    CharacterExtractionResponse,
    ExtractedCharacter,
    stable_candidate_id,
)
from repositories.character_candidates_repository import CharacterCandidatesRepository
from repositories.character_extraction_checkpoint_repository import (
    CharacterExtractionCheckpoint,
    CharacterExtractionCheckpointRepository,
)
from services.speaker_normalization import (
    is_likely_character_name,
    normalize_speaker_name,
)


class CharacterExtractionAdapter(Protocol):
    name: str
    model: str

    def extract(
        self, *, chapter_id: str, context: str
    ) -> CharacterExtractionResponse | dict[str, Any]: ...


@dataclass(frozen=True)
class CharacterExtractionResult:
    candidates: CharacterCandidatesDocument
    completed_chapters: int
    failed_chapters: int
    filtered_noise_count: int = 0


class CharacterExtractionService:
    def __init__(
        self,
        adapter: CharacterExtractionAdapter,
        checkpoints: CharacterExtractionCheckpointRepository,
        candidates: CharacterCandidatesRepository | None = None,
    ):
        self.adapter = adapter
        self.checkpoints = checkpoints
        self.candidates = candidates

    def extract(
        self,
        source_text: str,
        script: ScriptDocument,
    ) -> CharacterExtractionResult:
        script.validate(source_text)
        self.checkpoints.recover_running()
        chapters = list(script.chapters)
        chapter_ids = [chapter.chapter_id for chapter in chapters]
        checkpoints = self.checkpoints.prepare(
            source_sha256=script.source_sha256,
            provider=self.adapter.name,
            model=self.adapter.model,
            chapter_ids=chapter_ids,
        )
        chapter_by_id = {chapter.chapter_id: chapter for chapter in chapters}
        current = (
            self.candidates.load(script.source_sha256)
            if self.candidates is not None
            else CharacterCandidatesDocument.empty(script.source_sha256)
        )
        completed = 0
        failed = 0
        filtered_noise = 0
        for checkpoint in checkpoints:
            chapter = chapter_by_id[checkpoint.chapter_id]
            try:
                if checkpoint.status == "completed":
                    response = self._parse_checkpoint(checkpoint, chapter, source_text)
                else:
                    self.checkpoints.mark_running(checkpoint.batch_id)
                    raw = self.adapter.extract(
                        chapter_id=chapter.chapter_id,
                        context=source_text[chapter.start:chapter.end],
                    )
                    response = self._parse_response(raw, chapter, source_text)
                    self.checkpoints.mark_completed(
                        checkpoint.batch_id, response.to_dict()
                    )
                extracted = self._candidate_values(response.characters)
                filtered_noise += sum(
                    1
                    for item in response.characters
                    if not item.is_character
                    or not normalize_speaker_name(item.name)
                    or not is_likely_character_name(normalize_speaker_name(item.name))
                )
                merged = merge_character_candidates(current.candidates, extracted)
                changed = merged != current.candidates
                current = CharacterCandidatesDocument(
                    source_sha256=script.source_sha256,
                    candidates=merged,
                    revision=current.revision + (1 if changed else 0),
                )
                current.validate()
                if self.candidates is not None:
                    self.candidates.save(current)
                completed += 1
            except Exception as exc:  # noqa: BLE001 - isolate one chapter
                self.checkpoints.mark_failed(checkpoint.batch_id, exc)
                failed += 1
        return CharacterExtractionResult(
            candidates=current,
            completed_chapters=completed,
            failed_chapters=failed,
            filtered_noise_count=filtered_noise,
        )

    @staticmethod
    def _parse_checkpoint(
        checkpoint: CharacterExtractionCheckpoint,
        chapter: ChapterScript,
        source_text: str,
    ) -> CharacterExtractionResponse:
        return CharacterExtractionResponse.from_dict(
            checkpoint.response,
            allowed_chapter_id=chapter.chapter_id,
            chapter_text=source_text[chapter.start:chapter.end],
        )

    @staticmethod
    def _parse_response(
        raw: CharacterExtractionResponse | dict[str, Any],
        chapter: ChapterScript,
        source_text: str,
    ) -> CharacterExtractionResponse:
        if isinstance(raw, CharacterExtractionResponse):
            raw.validate(
                allowed_chapter_id=chapter.chapter_id,
                chapter_text=source_text[chapter.start:chapter.end],
            )
            return raw
        return CharacterExtractionResponse.from_dict(
            raw,
            allowed_chapter_id=chapter.chapter_id,
            chapter_text=source_text[chapter.start:chapter.end],
        )

    @staticmethod
    def _candidate_values(
        characters: list[ExtractedCharacter],
    ) -> list[CharacterCandidate]:
        values: list[CharacterCandidate] = []
        for item in characters:
            if not item.is_character:
                continue
            name = normalize_speaker_name(item.name)
            if not name or not is_likely_character_name(name):
                continue
            aliases: list[str] = []
            for raw_alias in item.aliases:
                alias = normalize_speaker_name(raw_alias)
                if (
                    alias
                    and alias != name
                    and is_likely_character_name(alias)
                    and alias not in aliases
                ):
                    aliases.append(alias)
            value = CharacterCandidate(
                candidate_id=stable_candidate_id(name),
                display_name=name,
                aliases=aliases,
                confidence=item.confidence,
                evidence=list(item.evidence),
                source="ai",
            )
            value.validate()
            values.append(value)
        return values


def merge_character_candidates(
    existing: list[CharacterCandidate],
    incoming: list[CharacterCandidate],
) -> list[CharacterCandidate]:
    """Merge exact names and explicit alias links only.

    Similar-looking names never establish identity.  A candidate is merged only
    when one canonical name is explicitly present as the other's alias, or when
    the canonical names are identical.
    """
    values = list(existing) + list(incoming)
    changed = True
    while changed:
        changed = False
        for left_index in range(len(values)):
            for right_index in range(left_index + 1, len(values)):
                left = values[left_index]
                right = values[right_index]
                if not _candidates_related(left, right):
                    continue
                values[left_index] = _merge_pair(left, right)
                values.pop(right_index)
                changed = True
                break
            if changed:
                break
    return sorted(values, key=lambda item: (item.status != "candidate", item.display_name))


def _candidates_related(left: CharacterCandidate, right: CharacterCandidate) -> bool:
    if left.status == "confirmed" or right.status == "confirmed":
        # A user-confirmed candidate is a frozen identity boundary.  A replay may
        # add evidence for the exact same canonical name, but cannot alias-merge
        # or rename it automatically.
        return left.display_name == right.display_name
    return (
        left.display_name == right.display_name
        or left.display_name in right.aliases
        or right.display_name in left.aliases
    )


def _merge_pair(left: CharacterCandidate, right: CharacterCandidate) -> CharacterCandidate:
    if left.status == "confirmed" or right.status == "confirmed":
        confirmed = left if left.status == "confirmed" else right
        evidence = list(confirmed.evidence)
        other = right if confirmed is left else left
        for item in other.evidence:
            if item not in evidence:
                evidence.append(item)
        return CharacterCandidate(
            candidate_id=confirmed.candidate_id,
            display_name=confirmed.display_name,
            aliases=list(confirmed.aliases),
            confidence=max(confirmed.confidence, other.confidence),
            evidence=evidence,
            source=confirmed.source,
            status="confirmed",
        )
    if left.display_name == right.display_name:
        display_name = (
            left.display_name
            if left.confidence >= right.confidence
            else right.display_name
        )
    elif left.display_name in right.aliases:
        display_name = right.display_name
    elif right.display_name in left.aliases:
        display_name = left.display_name
    else:
        display_name = left.display_name
    names = [left.display_name, right.display_name, *left.aliases, *right.aliases]
    aliases = list(dict.fromkeys(name for name in names if name != display_name))
    evidence = list(left.evidence)
    for item in right.evidence:
        if item not in evidence:
            evidence.append(item)
    status = _merged_status(left.status, right.status)
    source = _merged_source(left.source, right.source)
    result = CharacterCandidate(
        candidate_id=stable_candidate_id(display_name),
        display_name=display_name,
        aliases=aliases,
        confidence=max(left.confidence, right.confidence),
        evidence=evidence,
        source=source,
        status=status,
    )
    result.validate()
    return result


def _merged_status(left: str, right: str) -> str:
    # A manual decision survives a later checkpoint replay.
    if "confirmed" in {left, right}:
        return "confirmed"
    if "rejected" in {left, right}:
        return "rejected"
    return "candidate"


def _merged_source(left: str, right: str) -> str:
    for source in ("manual", "rule", "ai"):
        if source in {left, right}:
            return source
    return "ai"
