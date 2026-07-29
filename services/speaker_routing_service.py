"""Rule-first, resumable v4 speaker routing orchestration."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from domain.v4 import (
    ChapterScript,
    ScriptDocument,
    SemanticSegment,
    Speaker,
    SpeakersDocument,
)
from domain.v4.models import stable_speaker_id
from domain.v4.routing import SpeakerRoutingResponse
from repositories.routing_checkpoint_repository import (
    RoutingBatch,
    RoutingCheckpointRepository,
)


class RoutingAdapter(Protocol):
    name: str
    model: str

    def route(
        self, *, context: str, segment_ids: list[str]
    ) -> SpeakerRoutingResponse: ...


@dataclass(frozen=True)
class RoutingResult:
    script: ScriptDocument
    speakers: SpeakersDocument
    completed_batches: int
    failed_batches: int
    unresolved_segments: int


class SpeakerRoutingService:
    def __init__(
        self,
        adapter: RoutingAdapter,
        checkpoints: RoutingCheckpointRepository,
        *,
        batch_size: int = 24,
        context_radius: int = 400,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.adapter = adapter
        self.checkpoints = checkpoints
        self.batch_size = batch_size
        self.context_radius = max(context_radius, 0)

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
        assignments: list[dict[str, str | None]] = []
        completed = 0
        failed = 0
        segment_index = {item.segment_id: item for item in targets}
        for batch in checkpoints:
            if batch.status == "completed":
                assignments.extend(batch.assignments)
                completed += 1
                continue
            if batch.status == "cancelled":
                continue
            self.checkpoints.mark_running(batch.batch_id)
            try:
                response = self.adapter.route(
                    context=self._context(source_text, batch, segment_index),
                    segment_ids=batch.segment_ids,
                )
                persisted = [item.to_dict() for item in response.assignments]
                self.checkpoints.mark_completed(batch.batch_id, persisted)
                assignments.extend(persisted)
                completed += 1
            except Exception as exc:  # noqa: BLE001 - isolate one remote batch
                self.checkpoints.mark_failed(batch.batch_id, exc)
                failed += 1
        updated_script, updated_speakers = self._apply(script, speakers, assignments)
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
        )

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
        assignments: list[dict[str, str | None]],
    ) -> tuple[ScriptDocument, SpeakersDocument]:
        by_segment = {
            item["segment_id"]: item["speaker"]
            for item in assignments
            if item.get("speaker")
        }
        if not by_segment:
            return script, speakers
        speaker_list = list(speakers.speakers)
        names = {
            name: speaker
            for speaker in speaker_list
            for name in [speaker.display_name, *speaker.aliases]
        }
        changed = False
        chapters: list[ChapterScript] = []
        for chapter in script.chapters:
            segments: list[SemanticSegment] = []
            for segment in chapter.segments:
                speaker_name = by_segment.get(segment.segment_id)
                if (
                    not speaker_name
                    or segment.status != "unresolved"
                    or segment.speaker_source == "manual"
                ):
                    segments.append(segment)
                    continue
                speaker = names.get(speaker_name)
                if speaker is None:
                    speaker = Speaker(
                        speaker_id=stable_speaker_id(speaker_name),
                        display_name=speaker_name,
                        status="confirmed",
                        speaker_type="character",
                    )
                    speaker_list.append(speaker)
                    names[speaker_name] = speaker
                segments.append(
                    replace(
                        segment,
                        speaker_id=speaker.speaker_id,
                        speaker_source="router",
                        status="confirmed",
                    )
                )
                changed = True
            chapters.append(replace(chapter, segments=segments))
        if not changed:
            return script, speakers
        updated_script = replace(
            script, chapters=chapters, revision=script.revision + 1
        )
        updated_speakers = replace(
            speakers,
            speakers=speaker_list,
            revision=speakers.revision + (speaker_list != speakers.speakers),
        )
        return updated_script, updated_speakers
