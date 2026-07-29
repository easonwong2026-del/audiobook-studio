"""UI-neutral handlers for a future isolated v4 speaker review page."""
from __future__ import annotations

from domain.v4 import ScriptDocument, SpeakersDocument
from services.speaker_review_service import SpeakerReviewService


def unresolved_review_rows(
    source_text: str,
    script_data: dict,
) -> list[list[str]]:
    script = ScriptDocument.from_dict(script_data, source_text)
    return [
        [row["segment_id"], row["chapter_id"], row["text"]]
        for row in SpeakerReviewService.unresolved_rows(source_text, script)
    ]


def assign_review_rows(
    source_text: str,
    script_data: dict,
    speakers_data: dict,
    segment_ids: list[str],
    speaker_id: str,
) -> tuple[dict, dict]:
    script = ScriptDocument.from_dict(script_data, source_text)
    speakers = SpeakersDocument.from_dict(speakers_data)
    updated_script, updated_speakers = SpeakerReviewService.assign(
        script,
        speakers,
        segment_ids=segment_ids,
        speaker_id=speaker_id,
    )
    updated_script.validate(source_text)
    updated_speakers.validate()
    return updated_script.to_dict(), updated_speakers.to_dict()
