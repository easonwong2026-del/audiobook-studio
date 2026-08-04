"""Pure application service for reviewing and locking v4 speaker assignments."""
from __future__ import annotations

from dataclasses import replace

from domain.v4 import ChapterScript, ScriptDocument, Speaker, SpeakersDocument
from domain.v4.models import stable_speaker_id


class SpeakerReviewService:
    @staticmethod
    def unresolved_rows(
        source_text: str, script: ScriptDocument
    ) -> list[dict[str, str]]:
        return [
            {
                "segment_id": segment.segment_id,
                "chapter_id": chapter.chapter_id,
                "text": source_text[segment.start:segment.end],
            }
            for chapter in script.chapters
            for segment in chapter.segments
            if segment.status == "unresolved"
            and segment.dialogue_type != "unanalysed"
        ]

    @staticmethod
    def assign(
        script: ScriptDocument,
        speakers: SpeakersDocument,
        *,
        segment_ids: list[str],
        speaker_id: str | None = None,
        new_speaker_name: str = "",
        lock_speaker: bool = False,
    ) -> tuple[ScriptDocument, SpeakersDocument]:
        selected = set(segment_ids)
        if not selected:
            raise ValueError("at least one segment must be selected")
        speaker_list = list(speakers.speakers)
        if new_speaker_name.strip():
            name = new_speaker_name.strip()
            if any(
                name in [item.display_name, *item.aliases] for item in speaker_list
            ):
                raise ValueError("speaker name or alias already exists")
            speaker = Speaker(
                speaker_id=stable_speaker_id(name),
                display_name=name,
                status="confirmed",
                speaker_type="character",
                locked=lock_speaker,
            )
            speaker_list.append(speaker)
            speaker_id = speaker.speaker_id
        speaker = next(
            (item for item in speaker_list if item.speaker_id == speaker_id), None
        )
        if speaker is None:
            raise ValueError("unknown speaker_id")
        if lock_speaker and not speaker.locked:
            speaker_list = [
                replace(item, locked=True) if item.speaker_id == speaker.speaker_id else item
                for item in speaker_list
            ]
        found: set[str] = set()
        chapters: list[ChapterScript] = []
        for chapter in script.chapters:
            segments = []
            for segment in chapter.segments:
                if segment.segment_id in selected:
                    found.add(segment.segment_id)
                    segment = replace(
                        segment,
                        speaker_id=speaker.speaker_id,
                        speaker_source="manual",
                        status="confirmed",
                        candidate_speaker_id=None,
                        candidate_speaker_name=None,
                        candidate_confidence=None,
                        uncertainty_reason=None,
                    )
                segments.append(segment)
            chapters.append(replace(chapter, segments=segments))
        missing = selected - found
        if missing:
            raise ValueError(f"unknown segment_id: {min(missing)}")
        return (
            replace(script, chapters=chapters, revision=script.revision + 1),
            replace(
                speakers,
                speakers=speaker_list,
                revision=speakers.revision + 1,
            ),
        )

    @staticmethod
    def merge_speakers(
        script: ScriptDocument,
        speakers: SpeakersDocument,
        *,
        source_speaker_id: str,
        target_speaker_id: str,
    ) -> tuple[ScriptDocument, SpeakersDocument]:
        if source_speaker_id == target_speaker_id:
            raise ValueError("source and target speakers must differ")
        if source_speaker_id == "narrator":
            raise ValueError("narrator cannot be merged into another speaker")
        source = next(
            (item for item in speakers.speakers if item.speaker_id == source_speaker_id),
            None,
        )
        target = next(
            (item for item in speakers.speakers if item.speaker_id == target_speaker_id),
            None,
        )
        if source is None or target is None:
            raise ValueError("unknown source or target speaker")
        aliases = list(
            dict.fromkeys([*target.aliases, source.display_name, *source.aliases])
        )
        speaker_list = [
            replace(item, aliases=aliases)
            if item.speaker_id == target_speaker_id
            else item
            for item in speakers.speakers
            if item.speaker_id != source_speaker_id
        ]
        chapters = [
            replace(
                chapter,
                segments=[
                    replace(
                        segment,
                        speaker_id=target_speaker_id,
                        speaker_source="manual",
                        status="confirmed",
                    )
                    if segment.speaker_id == source_speaker_id
                    else segment
                    for segment in chapter.segments
                ],
            )
            for chapter in script.chapters
        ]
        return (
            replace(script, chapters=chapters, revision=script.revision + 1),
            replace(
                speakers,
                speakers=speaker_list,
                revision=speakers.revision + 1,
            ),
        )
