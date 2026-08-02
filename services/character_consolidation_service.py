"""Book-wide identity consolidation and conservative automatic confirmation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from domain.v4 import Speaker, SpeakersDocument
from domain.v4.character_consolidation import (
    CharacterConsolidationRequest,
    CharacterConsolidationResponse,
    ConsolidatedCharacter,
    ConsolidationCandidate,
    UnresolvedCharacterGroup,
)
from domain.v4.character_extraction import (
    CharacterCandidate,
    CharacterCandidatesDocument,
)
from domain.v4.models import stable_speaker_id
from repositories.character_consolidation_checkpoint_repository import (
    CharacterConsolidationCheckpointRepository,
)
from services.speaker_normalization import (
    is_likely_character_name,
    normalize_speaker_name,
)
from services.v4_analysis_config import DEFAULT_V4_ANALYSIS_CONFIG


class CharacterConsolidationAdapter(Protocol):
    name: str
    model: str

    def consolidate(
        self, request: CharacterConsolidationRequest
    ) -> CharacterConsolidationResponse | dict[str, Any]: ...


@dataclass(frozen=True)
class AutoConfirmation:
    canonical_name: str
    candidate_ids: list[str]
    speaker_id: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class CharacterConsolidationResult:
    request: CharacterConsolidationRequest
    response: CharacterConsolidationResponse
    speakers: SpeakersDocument
    candidates: CharacterCandidatesDocument
    auto_confirmed: list[AutoConfirmation]


class CharacterConsolidationService:
    def __init__(
        self,
        adapter: CharacterConsolidationAdapter | None = None,
        checkpoint: CharacterConsolidationCheckpointRepository | None = None,
        *,
        auto_confirm_threshold: float = DEFAULT_V4_ANALYSIS_CONFIG.auto_confirm_threshold,
    ):
        if not 0.0 <= auto_confirm_threshold <= 1.0:
            raise ValueError("auto_confirm_threshold must be between 0 and 1")
        self.adapter = adapter
        self.checkpoint = checkpoint
        self.auto_confirm_threshold = auto_confirm_threshold

    @staticmethod
    def build_request(
        source_sha256: str,
        candidates: CharacterCandidatesDocument,
        speakers: SpeakersDocument,
    ) -> CharacterConsolidationRequest:
        values = [
            ConsolidationCandidate(
                candidate_id=item.candidate_id,
                name=item.display_name,
                aliases=list(item.aliases),
                confidence=item.confidence,
                evidence=list(item.evidence),
                source=item.source,
                status=item.status,
            )
            for item in candidates.candidates
        ]
        existing = [
            {
                "speaker_id": item.speaker_id,
                "name": item.display_name,
                "aliases": list(item.aliases),
                "locked": item.locked,
            }
            for item in speakers.speakers
            if item.speaker_type == "character"
        ]
        request = CharacterConsolidationRequest(
            candidates=values,
            existing_speakers=existing,
            source_sha256=source_sha256,
        )
        request.validate()
        return request

    def consolidate(
        self,
        source_sha256: str,
        candidates: CharacterCandidatesDocument,
        speakers: SpeakersDocument,
        *,
        response: CharacterConsolidationResponse | dict[str, Any] | None = None,
    ) -> CharacterConsolidationResult:
        request = self.build_request(source_sha256, candidates, speakers)
        allowed = {item.candidate_id for item in request.candidates}
        if not allowed:
            empty = CharacterConsolidationResponse(characters=[], unresolved_groups=[])
            return CharacterConsolidationResult(
                request, empty, speakers, candidates, []
            )

        cached = None
        if response is None and self.checkpoint is not None:
            cached = self.checkpoint.load(
                source_sha256=source_sha256,
                input_fingerprint=request.fingerprint(),
                allowed_candidate_ids=allowed,
            )
        if cached is not None:
            response = cached
        if response is None:
            if self.adapter is None:
                response = self._deterministic_response(request)
            else:
                response = self.adapter.consolidate(request)
        parsed = (
            response
            if isinstance(response, CharacterConsolidationResponse)
            else CharacterConsolidationResponse.from_dict(
                response, allowed_candidate_ids=allowed
            )
        )
        parsed.validate(allowed)
        self._require_complete(parsed, allowed)
        if cached is None and self.checkpoint is not None:
            provider = getattr(self.adapter, "name", "deterministic")
            model = getattr(self.adapter, "model", "v1")
            self.checkpoint.save(
                source_sha256=source_sha256,
                input_fingerprint=request.fingerprint(),
                provider=provider,
                model=model,
                response=parsed,
                allowed_candidate_ids=allowed,
            )
        updated_speakers, updated_candidates, confirmations = self.apply(
            parsed, candidates, speakers
        )
        return CharacterConsolidationResult(
            request,
            parsed,
            updated_speakers,
            updated_candidates,
            confirmations,
        )

    def apply(
        self,
        response: CharacterConsolidationResponse,
        candidates: CharacterCandidatesDocument,
        speakers: SpeakersDocument,
    ) -> tuple[SpeakersDocument, CharacterCandidatesDocument, list[AutoConfirmation]]:
        allowed = {item.candidate_id for item in candidates.candidates}
        response.validate(allowed)
        by_candidate = {item.candidate_id: item for item in candidates.candidates}
        speaker_list = list(speakers.speakers)
        confirmations: list[AutoConfirmation] = []
        used_group_ids: set[str] = set()
        candidate_aliases: dict[str, set[str]] = {}
        for group in response.characters:
            for candidate_id in group.candidate_ids:
                candidate_aliases.setdefault(candidate_id, set()).update(
                    {group.canonical_name, *group.aliases}
                )

        for group in response.characters:
            group_candidates = [by_candidate[item] for item in group.candidate_ids]
            decision = self._confirmation_decision(
                group, group_candidates, speaker_list, response
            )
            if decision is None:
                continue
            existing_id = decision[0]
            if existing_id is None:
                name = normalize_speaker_name(group.canonical_name)
                aliases = self._new_aliases(
                    group,
                    group_candidates,
                    speaker_list,
                )
                if name is None or aliases is None:
                    continue
                speaker = Speaker(
                    speaker_id=stable_speaker_id(name),
                    display_name=name,
                    status="confirmed",
                    speaker_type="character",
                    aliases=aliases,
                )
                speaker_list.append(speaker)
                existing_id = speaker.speaker_id
            else:
                self._extend_existing_aliases(
                    speaker_list, existing_id, group, group_candidates
                )
            confirmations.append(
                AutoConfirmation(
                    canonical_name=group.canonical_name,
                    candidate_ids=list(group.candidate_ids),
                    speaker_id=existing_id,
                    confidence=group.confidence,
                    reason=group.reason,
                )
            )
            used_group_ids.update(group.candidate_ids)

        changed = False
        updated_items: list[CharacterCandidate] = []
        for item in candidates.candidates:
            if item.candidate_id in used_group_ids and item.status == "candidate":
                updated_items.append(replace(item, status="confirmed"))
                changed = True
            else:
                updated_items.append(item)
        updated_candidates = candidates
        if changed:
            updated_candidates = replace(
                candidates,
                candidates=updated_items,
                revision=candidates.revision + 1,
            )
            updated_candidates.validate()
        updated_speakers = replace(
            speakers,
            speakers=speaker_list,
            revision=speakers.revision + (1 if speaker_list != speakers.speakers else 0),
        )
        updated_speakers.validate()
        return updated_speakers, updated_candidates, confirmations

    def _confirmation_decision(
        self,
        group: ConsolidatedCharacter,
        group_candidates: list[CharacterCandidate],
        speakers: list[Speaker],
        response: CharacterConsolidationResponse,
    ) -> tuple[str | None, str] | None:
        if group.confidence < self.auto_confirm_threshold:
            return None
        # A manual decision is an identity boundary.  A previously confirmed
        # candidate may be mapped to its existing speaker, but never renamed.
        if any(item.status == "rejected" for item in group_candidates):
            return None
        if any(not item.evidence for item in group_candidates):
            return None
        if len(group_candidates) > 1 and not self._has_identity_support(
            group, group_candidates
        ):
            return None
        name = normalize_speaker_name(group.canonical_name)
        if not name or not is_likely_character_name(name):
            return None
        existing = self._find_existing(name, speakers)
        if any(item.status == "confirmed" for item in group_candidates) and (
            existing is None or name not in {existing.display_name, *existing.aliases}
        ):
            return None
        conflicting_group = self._conflicting_group(group, response)
        if conflicting_group:
            return None
        if existing is not None:
            if existing.display_name != name and name not in existing.aliases:
                return None
            return existing.speaker_id, group.reason
        all_names = {
            value
            for item in speakers
            for value in [item.display_name, *item.aliases]
        }
        if name in all_names:
            return None
        if any(
            value in all_names
            for item in group_candidates
            for value in [item.display_name, *item.aliases]
            if normalize_speaker_name(value) not in {name}
        ):
            # A candidate alias matching another formal role is ambiguous.
            return None
        return None, group.reason

    @staticmethod
    def _find_existing(name: str, speakers: list[Speaker]) -> Speaker | None:
        return next(
            (
                item
                for item in speakers
                if name == item.display_name or name in item.aliases
            ),
            None,
        )

    @staticmethod
    def _has_identity_support(
        group: ConsolidatedCharacter,
        candidates: list[CharacterCandidate],
    ) -> bool:
        explicit_link = any(
            normalize_speaker_name(left.display_name)
            in {
                normalize_speaker_name(alias)
                for alias in right.aliases
                if normalize_speaker_name(alias)
            }
            or normalize_speaker_name(right.display_name)
            in {
                normalize_speaker_name(alias)
                for alias in left.aliases
                if normalize_speaker_name(alias)
            }
            or normalize_speaker_name(left.display_name)
            == normalize_speaker_name(right.display_name)
            for index, left in enumerate(candidates)
            for right in candidates[index + 1:]
        )
        if explicit_link:
            return True
        reason = group.reason.casefold()
        return any(
            token in reason
            for token in ("身份", "证据", "同一", "昵称", "称呼", "identity", "evidence")
        ) and not any(
            token in reason
            for token in ("仅名称", "名称相似", "名字相似", "name similarity")
        )

    @staticmethod
    def _extend_existing_aliases(
        speakers: list[Speaker],
        speaker_id: str,
        group: ConsolidatedCharacter,
        candidates: list[CharacterCandidate],
    ) -> None:
        """Add proven aliases while preserving a manual/locked identity."""
        index = next(
            (index for index, item in enumerate(speakers) if item.speaker_id == speaker_id),
            None,
        )
        if index is None or speakers[index].locked:
            return
        target = speakers[index]
        occupied = {
            value
            for item in speakers
            if item.speaker_id != speaker_id
            for value in [item.display_name, *item.aliases]
        }
        aliases = list(target.aliases)
        values = [*group.aliases]
        for candidate in candidates:
            values.extend([candidate.display_name, *candidate.aliases])
        for raw in values:
            alias = normalize_speaker_name(raw)
            if not alias or alias == target.display_name or alias in aliases:
                continue
            if alias in occupied or not is_likely_character_name(alias):
                continue
            aliases.append(alias)
        if aliases != target.aliases:
            speakers[index] = replace(target, aliases=aliases)

    @staticmethod
    def _conflicting_group(
        group: ConsolidatedCharacter,
        response: CharacterConsolidationResponse,
    ) -> bool:
        own = set(group.candidate_ids)
        names = {group.canonical_name, *group.aliases}
        for other in response.characters:
            if other is group or own & set(other.candidate_ids):
                continue
            if names & {other.canonical_name, *other.aliases}:
                return True
        return False

    @staticmethod
    def _new_aliases(
        group: ConsolidatedCharacter,
        candidates: list[CharacterCandidate],
        speakers: list[Speaker],
    ) -> list[str] | None:
        name = normalize_speaker_name(group.canonical_name)
        if not name:
            return None
        existing = {
            value
            for item in speakers
            for value in [item.display_name, *item.aliases]
        }
        values = [*group.aliases]
        for item in candidates:
            values.extend([item.display_name, *item.aliases])
        aliases: list[str] = []
        for value in values:
            alias = normalize_speaker_name(value)
            if not alias or alias == name:
                continue
            if alias in aliases:
                continue
            if alias in existing:
                return None
            if not is_likely_character_name(alias):
                return None
            aliases.append(alias)
        return aliases

    @staticmethod
    def _require_complete(
        response: CharacterConsolidationResponse, allowed: set[str]
    ) -> None:
        represented = {
            candidate_id
            for item in response.characters
            for candidate_id in item.candidate_ids
        } | {
            candidate_id
            for item in response.unresolved_groups
            for candidate_id in item.candidate_ids
        }
        missing = allowed - represented
        if missing:
            raise ValueError(
                f"character consolidation omitted candidate_id: {min(missing)}"
            )

    @staticmethod
    def _deterministic_response(
        request: CharacterConsolidationRequest,
    ) -> CharacterConsolidationResponse:
        """Safe fallback for resumed/offline runs: exact names and aliases only."""
        by_name: dict[str, list[ConsolidationCandidate]] = {}
        for item in request.candidates:
            key = normalize_speaker_name(item.name)
            if key:
                by_name.setdefault(key, []).append(item)
        characters: list[ConsolidatedCharacter] = []
        used: set[str] = set()
        for items in by_name.values():
            first = items[0]
            related = [
                item
                for item in request.candidates
                if item.candidate_id not in used
                and (
                    normalize_speaker_name(item.name) == normalize_speaker_name(first.name)
                    or normalize_speaker_name(item.name) in {
                        normalize_speaker_name(alias) for alias in first.aliases
                    }
                    or normalize_speaker_name(first.name) in {
                        normalize_speaker_name(alias) for alias in item.aliases
                    }
                )
            ]
            ids = [item.candidate_id for item in related]
            used.update(ids)
            aliases = list(
                dict.fromkeys(
                    value
                    for item in related
                    for value in [item.name, *item.aliases]
                    if value != first.name
                )
            )
            characters.append(
                ConsolidatedCharacter(
                    canonical_name=first.name,
                    aliases=aliases,
                    candidate_ids=ids,
                    confidence=min(item.confidence for item in related),
                    importance="major" if len(related) > 1 else "minor",
                    reason="名称或显式别名一致（离线保守整理）",
                )
            )
        unresolved = [
            UnresolvedCharacterGroup(
                candidate_ids=[item.candidate_id],
                reason="无法仅凭名称和显式别名确认全书身份",
            )
            for item in request.candidates
            if item.candidate_id not in used
        ]
        value = CharacterConsolidationResponse(characters, unresolved)
        value.validate({item.candidate_id for item in request.candidates})
        return value
