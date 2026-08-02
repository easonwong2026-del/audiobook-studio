"""Explicit human decisions that move candidates into the formal speaker table."""
from __future__ import annotations

from dataclasses import replace

from domain.v4 import ScriptDocument, Speaker, SpeakersDocument
from domain.v4.character_extraction import (
    CharacterCandidate,
    CharacterCandidatesDocument,
)
from domain.v4.models import stable_speaker_id
from services.speaker_normalization import (
    is_likely_character_name,
    normalize_speaker_name,
)


class CharacterCandidateReviewService:
    @staticmethod
    def confirm(
        script: ScriptDocument,
        speakers: SpeakersDocument,
        candidates: CharacterCandidatesDocument,
        *,
        candidate_id: str,
        target_speaker_id: str | None = None,
    ) -> tuple[ScriptDocument, SpeakersDocument, CharacterCandidatesDocument]:
        candidate = _candidate(candidates, candidate_id)
        if candidate.status != "candidate":
            raise ValueError("candidate is already reviewed")
        speaker_list = list(speakers.speakers)
        if target_speaker_id:
            target_index, target = _speaker(speaker_list, target_speaker_id)
            if target.speaker_type != "character":
                raise ValueError("candidate can only merge into a character")
            aliases = _safe_aliases(
                [target.display_name, *target.aliases, candidate.display_name, *candidate.aliases],
                speaker_list,
                target_speaker_id,
            )
            speaker_list[target_index] = replace(target, aliases=aliases)
        else:
            if _speaker_name_exists(speaker_list, candidate.display_name):
                raise ValueError("candidate name or alias already exists")
            name = normalize_speaker_name(candidate.display_name)
            if not name or not is_likely_character_name(name):
                raise ValueError("candidate is not a valid character name")
            aliases = _safe_aliases(
                [name, *candidate.aliases],
                speaker_list,
                None,
            )
            speaker_list.append(
                Speaker(
                    speaker_id=stable_speaker_id(name),
                    display_name=name,
                    status="confirmed",
                    speaker_type="character",
                    aliases=aliases,
                )
            )
        updated_candidates = _set_candidate_status(candidates, candidate_id, "confirmed")
        return (
            replace(script, revision=script.revision + 1),
            replace(speakers, speakers=speaker_list, revision=speakers.revision + 1),
            updated_candidates,
        )

    @staticmethod
    def reject(
        candidates: CharacterCandidatesDocument,
        *,
        candidate_id: str,
    ) -> CharacterCandidatesDocument:
        candidate = _candidate(candidates, candidate_id)
        if candidate.status != "candidate":
            raise ValueError("candidate is already reviewed")
        return _set_candidate_status(candidates, candidate_id, "rejected")


def candidate_rows(
    candidates: CharacterCandidatesDocument,
) -> list[list[str]]:
    return [
        [
            item.candidate_id,
            item.display_name,
            ", ".join(item.aliases),
            f"{item.confidence:.2f}",
            item.evidence[0].text,
            item.evidence[0].chapter_id,
            item.source,
            item.status,
        ]
        for item in candidates.candidates
    ]


def _candidate(
    document: CharacterCandidatesDocument,
    candidate_id: str,
) -> CharacterCandidate:
    value = next(
        (item for item in document.candidates if item.candidate_id == candidate_id),
        None,
    )
    if value is None:
        raise ValueError("unknown candidate_id")
    return value


def _set_candidate_status(
    document: CharacterCandidatesDocument,
    candidate_id: str,
    status: str,
) -> CharacterCandidatesDocument:
    updated = [
        replace(item, status=status) if item.candidate_id == candidate_id else item
        for item in document.candidates
    ]
    result = replace(document, candidates=updated, revision=document.revision + 1)
    result.validate()
    return result


def _speaker(items: list[Speaker], speaker_id: str) -> tuple[int, Speaker]:
    for index, item in enumerate(items):
        if item.speaker_id == speaker_id:
            return index, item
    raise ValueError("unknown target speaker_id")


def _speaker_name_exists(items: list[Speaker], name: str) -> bool:
    return any(name in [item.display_name, *item.aliases] for item in items)


def _safe_aliases(
    values: list[str],
    speakers: list[Speaker],
    target_speaker_id: str | None,
) -> list[str]:
    existing = {
        value
        for item in speakers
        if item.speaker_id != target_speaker_id
        for value in [item.display_name, *item.aliases]
    }
    display_name = values[0]
    aliases: list[str] = []
    for raw in values[1:]:
        alias = normalize_speaker_name(raw)
        if not alias or alias == display_name:
            continue
        if alias in existing:
            raise ValueError(f"candidate alias conflicts with existing speaker: {alias}")
        if alias not in aliases:
            aliases.append(alias)
    return aliases
