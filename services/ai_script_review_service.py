"""AI reviewer stage that applies only validated, non-manual corrections."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from domain.v4 import (
    CharacterBibleDocument,
    ScriptDocument,
    ScriptReviewResponse,
    SpeakersDocument,
)
from domain.v4.models import source_sha256 as source_digest
from repositories.ai_first_checkpoint_repository import ScriptReviewCheckpointRepository
from repositories.v4_atomic import atomic_write_json


@dataclass(frozen=True)
class ScriptReviewResult:
    script: ScriptDocument
    reviewed_chapters: int
    auto_fixed: list[str]
    skipped_manual: list[str]
    errors: list[str]


class AIScriptReviewService:
    """Ask AI to audit the whole generated script, then apply safe patches."""

    def __init__(
        self,
        adapter: Any | None,
        checkpoint: ScriptReviewCheckpointRepository,
        *,
        min_confidence: float = 0.75,
    ):
        self.adapter = adapter
        self.checkpoint = checkpoint
        self.min_confidence = min_confidence

    def review(
        self,
        source_text: str,
        script: ScriptDocument,
        speakers: SpeakersDocument,
        bible: CharacterBibleDocument,
        *,
        progress_callback=None,
        force_restart: bool = False,
    ) -> ScriptReviewResult:
        if self.adapter is None:
            return ScriptReviewResult(script, 0, [], [], [])
        source_sha = source_digest(source_text)
        fingerprint = self._fingerprint(source_sha, script, bible)
        state = None if force_restart else self.checkpoint.load(
            source_sha256=source_sha, input_fingerprint=fingerprint
        )
        if state is None:
            state = {
                "source_sha256": source_sha,
                "input_fingerprint": fingerprint,
                "provider": getattr(self.adapter, "name", ""),
                "model": getattr(self.adapter, "model", ""),
                "status": "running",
                "chapters": {},
            }
            self.checkpoint.save(state)

        chapter_state = state.setdefault("chapters", {})
        patches_by_chapter: dict[str, ScriptReviewResponse] = {}
        errors: list[str] = []
        reviewed = 0
        for chapter in script.chapters:
            entry = chapter_state.get(chapter.chapter_id) or {}
            if entry.get("status") == "completed" and isinstance(entry.get("response"), dict):
                try:
                    patches_by_chapter[chapter.chapter_id] = (
                        ScriptReviewResponse.from_dict(entry["response"])
                    )
                    self._validate_response(
                        patches_by_chapter[chapter.chapter_id],
                        chapter.chapter_id,
                        script,
                        {item.speaker_id for item in speakers.speakers},
                    )
                    reviewed += 1
                    continue
                except (TypeError, ValueError):
                    pass
            self._report(
                progress_callback,
                f"正在复查对白归属：{chapter.title or chapter.chapter_id}",
            )
            entry.update({"status": "running", "attempts": int(entry.get("attempts", 0)) + 1})
            chapter_state[chapter.chapter_id] = entry
            self.checkpoint.save(state)
            try:
                response = self._review_chapter(
                    source_sha=source_sha,
                    source_text=source_text,
                    chapter=chapter,
                    script=script,
                    speakers=speakers,
                    bible=bible,
                )
                allowed_ids = {item.speaker_id for item in speakers.speakers}
                self._validate_response(response, chapter.chapter_id, script, allowed_ids)
                entry.update({"status": "completed", "response": response.to_dict()})
                chapter_state[chapter.chapter_id] = entry
                patches_by_chapter[chapter.chapter_id] = response
                reviewed += 1
                self.checkpoint.save(state)
            except Exception as exc:  # noqa: BLE001 - keep previous script usable
                message = f"{chapter.chapter_id}: {str(exc)[:400]}"
                errors.append(message)
                entry.update({
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:500],
                })
                chapter_state[chapter.chapter_id] = entry
                self.checkpoint.save(state)

        updated, auto_fixed, skipped = self._apply(
            source_text,
            script,
            speakers,
            patches_by_chapter,
            self.min_confidence,
        )
        if updated != script:
            updated.validate(source_text)
        state["status"] = "partial" if errors else "completed"
        self.checkpoint.save(state)
        atomic_write_json(
            self.checkpoint.path.parent.parent / "script_review.json",
            {
                "schema_version": "v4-script-review-v1",
                "source_sha256": source_sha,
                "reviewed_chapters": reviewed,
                "auto_fixed": auto_fixed,
                "skipped_manual": skipped,
                "errors": errors,
            },
        )
        return ScriptReviewResult(updated, reviewed, auto_fixed, skipped, errors)

    def _review_chapter(
        self, *, source_sha: str, source_text: str, chapter, script, speakers, bible
    ) -> ScriptReviewResponse:
        method = getattr(self.adapter, "review_chapter", None)
        if method is None:
            method = getattr(self.adapter, "review", None)
        if method is None:
            raise TypeError("AI script reviewer adapter needs review_chapter()")
        chapter_payload = chapter.to_dict()
        raw = method(
            source_sha256=source_sha,
            chapter_id=chapter.chapter_id,
            source_text=source_text[chapter.start:chapter.end],
            script=chapter_payload,
            bible=bible.to_dict(),
            allowed_speakers=[
                {
                    "speaker_id": item.speaker_id,
                    "name": item.display_name,
                    "aliases": item.aliases,
                    "locked": item.locked,
                }
                for item in speakers.speakers
            ],
        )
        if isinstance(raw, ScriptReviewResponse):
            return raw
        return ScriptReviewResponse.from_dict(raw)

    @staticmethod
    def _validate_response(
        response: ScriptReviewResponse,
        chapter_id: str,
        script: ScriptDocument,
        allowed_ids: set[str],
    ) -> None:
        chapter_ids = {
            item.segment_id
            for chapter in script.chapters
            if chapter.chapter_id == chapter_id
            for item in chapter.segments
        }
        for patch in response.patches:
            if patch.segment_id not in chapter_ids:
                raise ValueError(f"review returned unknown segment_id: {patch.segment_id}")
            if patch.speaker_id is not None and patch.speaker_id not in allowed_ids:
                raise ValueError(f"review returned unknown speaker_id: {patch.speaker_id}")
            if patch.action == "reassign" and patch.speaker_id is None:
                raise ValueError("reassign patch needs a speaker_id")
            if patch.action == "unresolve" and patch.speaker_id is not None:
                raise ValueError("unresolve patch cannot include a speaker_id")

    @staticmethod
    def _apply(source_text, script, speakers, patches_by_chapter, min_confidence):
        locked = {item.speaker_id for item in speakers.speakers if item.locked}
        auto_fixed: list[str] = []
        skipped: list[str] = []
        chapters = []
        changed = False
        for chapter in script.chapters:
            patch_map = {
                item.segment_id: item
                for item in patches_by_chapter.get(
                    chapter.chapter_id, ScriptReviewResponse([])
                ).patches
            }
            segments = []
            for segment in chapter.segments:
                patch = patch_map.get(segment.segment_id)
                if patch is None:
                    segments.append(segment)
                    continue
                if segment.speaker_source == "manual" or segment.speaker_id in locked:
                    skipped.append(segment.segment_id)
                    segments.append(segment)
                    continue
                if patch.confidence < min_confidence:
                    segments.append(segment)
                    continue
                if patch.action == "unresolve":
                    updated = replace(
                        segment,
                        speaker_id=None,
                        speaker_source="unresolved",
                        status="unresolved",
                    )
                else:
                    speaker_id = segment.speaker_id
                    if patch.action == "reassign":
                        speaker_id = patch.speaker_id
                    segment_type = patch.segment_type or segment.dialogue_type
                    kind = (
                        "narration"
                        if segment_type in {"narration", "stage_direction"}
                        else "dialogue"
                    )
                    if kind == "narration":
                        speaker_id = "narrator"
                    updated = replace(
                        segment,
                        kind=kind,
                        dialogue_type=segment_type,
                        speaker_id=speaker_id,
                        speaker_source="ai" if speaker_id else "unresolved",
                        status="confirmed" if speaker_id else "unresolved",
                        confidence=patch.confidence,
                    )
                if updated != segment:
                    changed = True
                    auto_fixed.append(segment.segment_id)
                segments.append(updated)
            chapters.append(replace(chapter, segments=segments))
        if not changed:
            return script, auto_fixed, skipped
        return replace(script, chapters=chapters, revision=script.revision + 1), auto_fixed, skipped

    def _fingerprint(self, source_sha: str, script: ScriptDocument, bible: CharacterBibleDocument) -> str:
        value = f"{source_sha}:{script.revision}:{bible.revision}:{getattr(self.adapter, 'name', '')}:{getattr(self.adapter, 'model', '')}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _report(callback, message: str) -> None:
        if callback is not None:
            callback(message)
