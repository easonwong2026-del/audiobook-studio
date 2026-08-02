"""AI-owned V4 script segmentation with exact source-coordinate validation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ai.providers.exceptions import ProviderOutputTruncatedError
from domain.v4 import (
    ChapterScript,
    CharacterBibleDocument,
    ScriptDirectorBatch,
    ScriptDocument,
    SemanticSegment,
)
from domain.v4.models import source_sha256 as source_digest
from repositories.ai_first_checkpoint_repository import (
    ScriptDirectorCheckpointRepository,
)
from services.ai_first_source import normalized_source, split_source_range


@dataclass(frozen=True)
class ScriptDirectorResult:
    script: ScriptDocument
    completed_chapters: int
    failed_chapters: int
    failed_chapter_ids: list[str]
    resumed: bool


class AIScriptDirectorService:
    """Turn raw source into the V4 semantic script after the bible exists."""

    def __init__(
        self,
        adapter: Any,
        checkpoint: ScriptDirectorCheckpointRepository,
        *,
        max_input_chars: int = 12000,
    ):
        self.adapter = adapter
        self.checkpoint = checkpoint
        self.max_input_chars = max_input_chars

    def direct(
        self,
        source_text: str,
        source_script: ScriptDocument,
        bible: CharacterBibleDocument,
        *,
        progress_callback=None,
        force_restart: bool = False,
    ) -> ScriptDirectorResult:
        source_sha = source_digest(source_text)
        fingerprint = self._fingerprint(source_sha, bible)
        state = None if force_restart else self.checkpoint.load(
            source_sha256=source_sha, input_fingerprint=fingerprint
        )
        resumed = state is not None
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

        bible_payload = bible.to_dict()
        speakers = {
            "narrator",
            *[
                item.speaker_id
                for item in bible.characters
                if item.speaker_id
            ],
        }
        chapter_batches: dict[str, list[ScriptDirectorBatch]] = {}
        failed: list[str] = []
        chapter_state = state.setdefault("chapters", {})
        for chapter in source_script.chapters:
            entry = chapter_state.get(chapter.chapter_id) or {}
            stored = entry.get("batches")
            if entry.get("status") == "completed" and isinstance(stored, list) and stored:
                try:
                    loaded_batches = [
                        ScriptDirectorBatch.from_dict(item) for item in stored
                    ]
                    for loaded in loaded_batches:
                        self._validate_batch(
                            loaded,
                            source_text,
                            loaded.source_start,
                            loaded.source_end,
                            speakers,
                            expected_chapter_id=chapter.chapter_id,
                        )
                    chapter_batches[chapter.chapter_id] = loaded_batches
                    continue
                except (TypeError, ValueError):
                    entry = {
                        "status": "invalidated",
                        "attempts": entry.get("attempts", 0),
                    }
                    chapter_state[chapter.chapter_id] = entry
                    self.checkpoint.save(state)
            entry.update({"status": "running", "attempts": int(entry.get("attempts", 0)) + 1})
            chapter_state[chapter.chapter_id] = entry
            self.checkpoint.save(state)
            self._report(
                progress_callback,
                f"正在分析章节剧本：{chapter.title or chapter.chapter_id}",
            )
            try:
                batches: list[ScriptDirectorBatch] = []
                context_before = ""
                for chunk_start, chunk_end in split_source_range(
                    source_text, chapter.start, chapter.end, self.max_input_chars
                ):
                    batch = self._analyze_batch_resilient(
                        source_sha=source_sha,
                        chapter=chapter,
                        source_text=source_text,
                        source_start=chunk_start,
                        source_end=chunk_end,
                        bible=bible_payload,
                        allowed_speaker_ids=speakers,
                        context_before=context_before,
                    )
                    self._validate_batch(
                        batch,
                        source_text,
                        chunk_start,
                        chunk_end,
                        speakers,
                        expected_chapter_id=chapter.chapter_id,
                    )
                    batches.append(batch)
                    context_before = source_text[max(chapter.start, chunk_end - 720):chunk_end]
                    entry["batches"] = [item.to_dict() for item in batches]
                    self.checkpoint.save(state)
                entry.update({
                    "status": "completed",
                    "batches": [item.to_dict() for item in batches],
                })
                chapter_state[chapter.chapter_id] = entry
                chapter_batches[chapter.chapter_id] = batches
                self.checkpoint.save(state)
            except Exception as exc:  # noqa: BLE001 - keep completed chapters
                entry.update({
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:500],
                })
                chapter_state[chapter.chapter_id] = entry
                failed.append(chapter.chapter_id)
                self.checkpoint.save(state)
                break

        completed = sum(
            item.get("status") == "completed" for item in chapter_state.values()
        )
        chapters: list[ChapterScript] = []
        sequence = 1
        for chapter in source_script.chapters:
            batches = chapter_batches.get(chapter.chapter_id)
            if not batches:
                chapters.append(chapter)
                sequence += len(chapter.segments)
                continue
            segments: list[SemanticSegment] = []
            for batch in batches:
                for item in batch.segments:
                    segments.append(
                        self._to_semantic_segment(
                            item, chapter.chapter_id, sequence
                        )
                    )
                    sequence += 1
            chapters.append(
                ChapterScript(
                    chapter_id=chapter.chapter_id,
                    title=chapter.title,
                    start=chapter.start,
                    end=chapter.end,
                    segments=segments,
                )
            )
        script = ScriptDocument(
            source_sha256=source_sha,
            chapters=chapters,
            revision=source_script.revision + (1 if completed else 0),
        )
        script.validate(source_text)
        state["status"] = "partial" if failed else "completed"
        self.checkpoint.save(state)
        return ScriptDirectorResult(
            script=script,
            completed_chapters=completed,
            failed_chapters=len(failed),
            failed_chapter_ids=failed,
            resumed=resumed,
        )

    def _analyze_batch(
        self,
        *,
        source_sha: str,
        chapter,
        source_text: str,
        source_start: int,
        source_end: int,
        bible: dict[str, Any],
        allowed_speaker_ids: set[str],
        context_before: str,
    ) -> ScriptDirectorBatch:
        method = getattr(self.adapter, "analyze_batch", None)
        if method is None:
            method = getattr(self.adapter, "direct_batch", None)
        if method is None:
            raise TypeError("AI script director adapter needs analyze_batch()")
        raw = method(
            source_sha256=source_sha,
            chapter_id=chapter.chapter_id,
            source_start=source_start,
            source_end=source_end,
            text=source_text[source_start:source_end],
            bible=bible,
            context_before=context_before,
        )
        if isinstance(raw, ScriptDirectorBatch):
            return raw
        if not isinstance(raw, dict):
            raise TypeError("AI script director response must be a JSON object")
        return ScriptDirectorBatch.from_dict(raw)

    def _analyze_batch_resilient(self, **kwargs) -> ScriptDirectorBatch:
        """Reuse V3's truncation principle with V4 absolute coordinates."""
        request_kwargs = {
            key: value for key, value in kwargs.items() if key != "split_depth"
        }
        try:
            return self._analyze_batch(**request_kwargs)
        except ProviderOutputTruncatedError:
            start = kwargs["source_start"]
            end = kwargs["source_end"]
            depth = int(kwargs.get("split_depth", 0))
            if depth >= 3 or end - start < 400:
                # A final retry lets transient transport truncation recover;
                # the caller will persist the failure if it remains invalid.
                return self._analyze_batch(**request_kwargs)
            split_at = self._safe_split_point(kwargs["source_text"], start, end)
            if split_at <= start or split_at >= end:
                return self._analyze_batch(**request_kwargs)
            children = []
            for child_start, child_end in ((start, split_at), (split_at, end)):
                child = dict(kwargs)
                child.update({
                    "source_start": child_start,
                    "source_end": child_end,
                    "context_before": kwargs.get("context_before", ""),
                    "split_depth": depth + 1,
                })
                children.append(self._analyze_batch_resilient(**child))
            return ScriptDirectorBatch(
                chapter_id=kwargs["chapter"].chapter_id,
                source_start=start,
                source_end=end,
                segments=[item for child in children for item in child.segments],
            )

    @staticmethod
    def _safe_split_point(source_text: str, start: int, end: int) -> int:
        midpoint = start + (end - start) // 2
        window = source_text[start:midpoint]
        candidates = [
            window.rfind(mark)
            for mark in ("\n\n", "。", "！", "？", "；", "\n", "”", "」", "』")
        ]
        boundary = max(candidates, default=-1)
        return start + boundary + 1 if boundary >= (midpoint - start) // 3 else midpoint

    @staticmethod
    def _validate_batch(
        batch: ScriptDirectorBatch,
        source_text: str,
        chunk_start: int,
        chunk_end: int,
        allowed_speaker_ids: set[str],
        expected_chapter_id: str | None = None,
    ) -> None:
        if expected_chapter_id is not None and batch.chapter_id != expected_chapter_id:
            raise ValueError("AI script director chapter_id does not match request")
        if batch.source_start != chunk_start or batch.source_end != chunk_end:
            raise ValueError("AI script director batch coordinates do not match request")
        cursor = chunk_start
        actual: list[str] = []
        for item in batch.segments:
            if item.source_start < chunk_start or item.source_end > chunk_end:
                raise ValueError("AI script director segment is outside its batch")
            if item.source_start < cursor:
                raise ValueError("AI script director segments overlap or are out of order")
            if normalized_source(source_text[cursor:item.source_start]):
                raise ValueError("AI script director omitted non-whitespace source")
            expected = source_text[item.source_start:item.source_end]
            if expected != item.text:
                raise ValueError("AI script director changed segment text")
            if item.speaker_id is not None and item.speaker_id not in allowed_speaker_ids:
                raise ValueError(f"AI script director returned unknown speaker_id: {item.speaker_id}")
            if item.segment_type == "narration" and item.speaker_id not in {None, "narrator"}:
                raise ValueError("narration segment must use narrator")
            actual.append(item.text)
            cursor = item.source_end
        if normalized_source(source_text[cursor:chunk_end]):
            raise ValueError("AI script director omitted trailing non-whitespace source")
        if normalized_source("".join(actual)) != normalized_source(
            source_text[chunk_start:chunk_end]
        ):
            raise ValueError("AI script director coverage does not match source")

    @staticmethod
    def _to_semantic_segment(
        item, chapter_id: str, sequence: int
    ) -> SemanticSegment:
        segment_type = item.segment_type
        kind = "narration" if segment_type in {"narration", "stage_direction"} else "dialogue"
        speaker_id = item.speaker_id
        if kind == "narration" and speaker_id is None:
            speaker_id = "narrator"
        status = "confirmed" if speaker_id else "unresolved"
        speaker_source = "ai" if speaker_id else "unresolved"
        return SemanticSegment(
            segment_id=AIScriptDirectorService._segment_id(
                chapter_id, item.source_start, item.source_end, sequence
            ),
            chapter_id=chapter_id,
            start=item.source_start,
            end=item.source_end,
            kind=kind,
            speaker_id=speaker_id,
            speaker_source=speaker_source,
            status=status,
            dialogue_type=segment_type,
            confidence=item.confidence,
            emotion=item.emotion,
            emotion_strength=item.emotion_strength,
            delivery=dict(item.delivery),
            pause_before=item.pause_before,
            pause_after=item.pause_after,
            pauses=list(item.pauses),
        )

    @staticmethod
    def _segment_id(chapter_id: str, start: int, end: int, sequence: int) -> str:
        digest = hashlib.sha256(f"{chapter_id}:{start}:{end}".encode()).hexdigest()[:12]
        return f"segment_{digest or sequence:0>12}"

    def _fingerprint(self, source_sha: str, bible: CharacterBibleDocument) -> str:
        payload = f"{source_sha}:{bible.revision}:{getattr(self.adapter, 'name', '')}:{getattr(self.adapter, 'model', '')}:{self.max_input_chars}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _report(callback, message: str) -> None:
        if callback is not None:
            callback(message)
