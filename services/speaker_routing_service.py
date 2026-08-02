"""Rule-first, resumable v4 speaker routing constrained by confirmed roles."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from typing import Any, Protocol

from domain.v4 import (
    ChapterScript,
    ScriptDocument,
    SemanticSegment,
    SpeakersDocument,
)
from domain.v4.character_extraction import (
    CharacterCandidate,
    CharacterEvidence,
    stable_candidate_id,
)
from domain.v4.routing import SpeakerRoutingResponse
from repositories.routing_checkpoint_repository import (
    RoutingBatch,
    RoutingCheckpointRepository,
)
from services.speaker_normalization import (
    is_likely_character_name,
    normalize_speaker_name,
)

MIN_ROUTING_CONFIDENCE = 0.75


class RoutingAdapter(Protocol):
    name: str
    model: str

    def route(
        self,
        *,
        context: str,
        segment_ids: list[str],
        allowed_speakers: list[dict[str, Any]],
    ) -> SpeakerRoutingResponse: ...


@dataclass(frozen=True)
class RoutingResult:
    script: ScriptDocument
    speakers: SpeakersDocument
    completed_batches: int
    failed_batches: int
    unresolved_segments: int
    candidates: list[CharacterCandidate] = ()


class SpeakerRoutingService:
    def __init__(
        self,
        adapter: RoutingAdapter,
        checkpoints: RoutingCheckpointRepository,
        *,
        batch_size: int = 24,
        context_radius: int = 400,
        min_confidence: float = MIN_ROUTING_CONFIDENCE,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        self.adapter = adapter
        self.checkpoints = checkpoints
        self.batch_size = batch_size
        self.context_radius = max(context_radius, 0)
        self.min_confidence = min_confidence

    def route(
        self,
        source_text: str,
        script: ScriptDocument,
        speakers: SpeakersDocument,
    ) -> RoutingResult:
        script.validate(source_text)
        speakers.validate()
        self.checkpoints.recover_running()
        targets = [
            segment
            for chapter in script.chapters
            for segment in chapter.segments
            if segment.kind == "dialogue"
            and segment.dialogue_type != "quotation"
            and segment.status == "unresolved"
            and segment.speaker_source != "manual"
        ]
        batches = [
            [item.segment_id for item in targets[index:index + self.batch_size]]
            for index in range(0, len(targets), self.batch_size)
        ]
        checkpoints = self.checkpoints.prepare(
            source_sha256=script.source_sha256,
            script_revision=script.revision,
            provider=self.adapter.name,
            model=self.adapter.model,
            batches=batches,
        )
        assignments: list[dict[str, Any]] = []
        completed = 0
        failed = 0
        segment_index = {item.segment_id: item for item in targets}
        allowed_speakers = self._allowed_speakers(speakers)
        allowed_ids = {item["speaker_id"] for item in allowed_speakers}
        for batch in checkpoints:
            if self.checkpoints.is_cancelled(batch.batch_id):
                continue
            if batch.status == "completed":
                try:
                    persisted = self._validated_persisted_assignments(
                        batch, allowed_ids
                    )
                except Exception as exc:  # noqa: BLE001 - invalidate bad checkpoint
                    self.checkpoints.mark_failed(batch.batch_id, exc)
                    failed += 1
                    continue
                assignments.extend(persisted)
                completed += 1
                continue
            if batch.status == "cancelled":
                continue
            self.checkpoints.mark_running(batch.batch_id)
            try:
                response = self._call_adapter(
                    context=self._context(source_text, batch, segment_index),
                    segment_ids=batch.segment_ids,
                    allowed_speakers=allowed_speakers,
                )
                response = self._coerce_response(
                    response,
                    batch.segment_ids,
                    allowed_ids,
                )
                persisted = [item.to_dict() for item in response.assignments]
                self.checkpoints.mark_completed(batch.batch_id, persisted)
                assignments.extend(persisted)
                completed += 1
            except Exception as exc:  # noqa: BLE001 - isolate one remote batch
                self.checkpoints.mark_failed(batch.batch_id, exc)
                failed += 1
        updated_script, updated_speakers, candidates = self._apply_with_candidates(
            source_text,
            script,
            speakers,
            assignments,
            min_confidence=self.min_confidence,
        )
        unresolved = sum(
            item.status == "unresolved"
            for chapter in updated_script.chapters
            for item in chapter.segments
        )
        return RoutingResult(
            script=updated_script,
            speakers=updated_speakers,
            completed_batches=completed,
            failed_batches=failed,
            unresolved_segments=int(unresolved),
            candidates=candidates,
        )

    def _call_adapter(
        self,
        *,
        context: str,
        segment_ids: list[str],
        allowed_speakers: list[dict[str, Any]],
    ) -> SpeakerRoutingResponse:
        route = self.adapter.route
        parameters = inspect.signature(route).parameters
        if "allowed_speakers" in parameters:
            return route(
                context=context,
                segment_ids=segment_ids,
                allowed_speakers=allowed_speakers,
            )
        # Old local adapters can still be resumed, but their free-text names are
        # resolved against the formal table below and can never create Speakers.
        return route(context=context, segment_ids=segment_ids)

    @staticmethod
    def _coerce_response(
        response: SpeakerRoutingResponse | dict[str, Any],
        segment_ids: list[str],
        allowed_ids: set[str],
    ) -> SpeakerRoutingResponse:
        if isinstance(response, SpeakerRoutingResponse):
            response.validate_allowed_speakers(allowed_ids)
            return response
        if not isinstance(response, dict):
            raise TypeError("routing adapter must return a strict response")
        return SpeakerRoutingResponse.from_dict(
            response,
            allowed_segment_ids=set(segment_ids),
            allowed_speaker_ids=allowed_ids,
        )

    @staticmethod
    def _validated_persisted_assignments(
        batch: RoutingBatch,
        allowed_ids: set[str],
    ) -> list[dict[str, Any]]:
        raw = batch.assignments
        schema = "speaker-routing-v1" if any("speaker" in item for item in raw) else "speaker-routing-v2"
        response = SpeakerRoutingResponse.from_dict(
            {"schema_version": schema, "assignments": raw},
            allowed_segment_ids=set(batch.segment_ids),
            allowed_speaker_ids=allowed_ids,
            allow_legacy=True,
        )
        response.validate_allowed_speakers(allowed_ids)
        return [item.to_dict() for item in response.assignments]

    @staticmethod
    def _allowed_speakers(speakers: SpeakersDocument) -> list[dict[str, Any]]:
        return [
            {
                "speaker_id": item.speaker_id,
                "name": item.display_name,
                "aliases": list(item.aliases),
            }
            for item in speakers.speakers
            if item.status == "confirmed"
        ]

    def _context(
        self,
        source_text: str,
        batch: RoutingBatch,
        segment_index: dict[str, SemanticSegment],
    ) -> str:
        selected = [segment_index[item] for item in batch.segment_ids]
        start = max(0, min(item.start for item in selected) - self.context_radius)
        end = min(
            len(source_text),
            max(item.end for item in selected) + self.context_radius,
        )
        pieces: list[str] = []
        cursor = start
        for segment in sorted(selected, key=lambda item: item.start):
            pieces.append(source_text[cursor:segment.start])
            pieces.append(f"[{segment.segment_id}] ")
            pieces.append(source_text[segment.start:segment.end])
            cursor = segment.end
        pieces.append(source_text[cursor:end])
        return "".join(pieces)

    @staticmethod
    def _apply(
        script: ScriptDocument,
        speakers: SpeakersDocument,
        assignments: list[dict[str, Any]],
    ) -> tuple[ScriptDocument, SpeakersDocument]:
        updated_script, updated_speakers, _ = SpeakerRoutingService._apply_with_candidates(
            "", script, speakers, assignments, min_confidence=MIN_ROUTING_CONFIDENCE
        )
        return updated_script, updated_speakers

    @staticmethod
    def _apply_with_candidates(
        source_text: str,
        script: ScriptDocument,
        speakers: SpeakersDocument,
        assignments: list[dict[str, Any]],
        *,
        min_confidence: float,
    ) -> tuple[ScriptDocument, SpeakersDocument, list[CharacterCandidate]]:
        by_segment: dict[str, tuple[str, float]] = {}
        candidate_by_segment: dict[str, tuple[str, float]] = {}
        names = {
            name: speaker.speaker_id
            for speaker in speakers.speakers
            for name in [speaker.display_name, *speaker.aliases]
        }
        allowed_ids = {item.speaker_id for item in speakers.speakers}
        for item in assignments:
            segment_id = item.get("segment_id")
            if not isinstance(segment_id, str):
                continue
            if "speaker_id" in item:
                speaker_id = item.get("speaker_id")
                confidence = item.get("confidence", 0.0)
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    continue
                if speaker_id in allowed_ids and confidence >= min_confidence:
                    by_segment[segment_id] = (speaker_id, confidence)
                elif item.get("candidate_name"):
                    candidate_by_segment[segment_id] = (
                        str(item["candidate_name"]), confidence
                    )
                continue
            # Legacy v1 is read-only compatibility: names can resolve to an
            # existing display name/alias, but an unknown name is discarded.
            raw_name = item.get("speaker")
            if isinstance(raw_name, str):
                speaker_id = names.get(normalize_speaker_name(raw_name))
                if speaker_id:
                    by_segment[segment_id] = (speaker_id, 1.0)
        speaker_changes = False
        chapters: list[ChapterScript] = []
        segment_lookup = {
            segment.segment_id: (chapter, segment)
            for chapter in script.chapters
            for segment in chapter.segments
        }
        for chapter in script.chapters:
            segments: list[SemanticSegment] = []
            for segment in chapter.segments:
                assignment = by_segment.get(segment.segment_id)
                if (
                    assignment is None
                    or segment.status != "unresolved"
                    or segment.speaker_source == "manual"
                ):
                    segments.append(segment)
                    continue
                segments.append(
                    replace(
                        segment,
                        speaker_id=assignment[0],
                        speaker_source="router",
                        status="confirmed",
                    )
                )
                speaker_changes = True
            chapters.append(replace(chapter, segments=segments))
        candidates: list[CharacterCandidate] = []
        for segment_id, (raw_name, confidence) in candidate_by_segment.items():
            if not source_text or segment_id not in segment_lookup:
                continue
            name = normalize_speaker_name(raw_name)
            if not name or not is_likely_character_name(name) or name in names:
                continue
            chapter, segment = segment_lookup[segment_id]
            candidate = CharacterCandidate(
                candidate_id=stable_candidate_id(name),
                display_name=name,
                aliases=[],
                confidence=max(0.0, min(confidence, 1.0)),
                evidence=[
                    CharacterEvidence(
                        chapter_id=chapter.chapter_id,
                        text=source_text[segment.start:segment.end],
                    )
                ],
                source="ai",
            )
            candidate.validate()
            candidates.append(candidate)
        if not speaker_changes:
            return script, speakers, candidates
        return (
            replace(script, chapters=chapters, revision=script.revision + 1),
            speakers,
            candidates,
        )
