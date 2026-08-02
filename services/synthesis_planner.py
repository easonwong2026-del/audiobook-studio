"""Compile semantic segments into safe, versioned TTS tasks."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass

from domain.v4 import ScriptDocument, SpeakersDocument
from domain.v4.production import (
    PerformanceOverrides,
    PlanDependencies,
    PlanPart,
    PlanTask,
    PronunciationRules,
    SynthesisPlan,
    TtsProfile,
    VoiceBindings,
)
from tts.text_measurement import TextMeasurer

_BREAKS = {
    "paragraph": ("\n\n",),
    "sentence": ("。", "！", "？", "!", "?", "……"),
    "semicolon": ("；", ";"),
    "comma": ("，", ","),
    "colon": ("：", ":"),
}


@dataclass(frozen=True)
class PlanningResult:
    plan: SynthesisPlan
    unresolved_segments: list[str]
    unbound_speakers: list[str]


class SynthesisPlanner:
    def __init__(self, measurer: TextMeasurer):
        self.measurer = measurer

    def plan(
        self,
        source_text: str,
        script: ScriptDocument,
        speakers: SpeakersDocument,
        voices: VoiceBindings,
        performance: PerformanceOverrides,
        pronunciation: PronunciationRules,
        profile: TtsProfile,
        *,
        previous_plan: SynthesisPlan | None = None,
    ) -> PlanningResult:
        script.validate(source_text)
        speakers.validate()
        voices.validate()
        performance.validate()
        pronunciation.validate()
        profile.validate()
        if profile.limits.metric != self.measurer.metric:
            raise ValueError("TTS profile metric does not match text measurer")
        tasks: list[PlanTask] = []
        unresolved: list[str] = []
        unbound: set[str] = set()
        for chapter in script.chapters:
            chapter_tasks: list[PlanTask] = []
            for segment in chapter.segments:
                if segment.status == "unresolved" or not segment.speaker_id:
                    unresolved.append(segment.segment_id)
                    continue
                binding = voices.bindings.get(segment.speaker_id)
                if binding is None:
                    unbound.add(segment.speaker_id)
                    continue
                actual_text = (
                    segment.text_override
                    if segment.text_override is not None
                    else source_text[segment.start:segment.end]
                )
                chunks = self._split(actual_text, profile)
                offset = 0
                for index, chunk in enumerate(chunks):
                    if segment.text_override is None:
                        chunk_start = segment.start + offset
                        chunk_end = chunk_start + len(chunk)
                    else:
                        chunk_start, chunk_end = segment.start, segment.end
                    task = self._task(
                        task_id=self._task_id(
                            chapter.chapter_id, [segment.segment_id], index
                        ),
                        chapter_id=chapter.chapter_id,
                        speaker_id=segment.speaker_id,
                        voice_id=binding.voice_id,
                        voice_fingerprint=binding.fingerprint,
                        segment_ids=[segment.segment_id],
                        parts=[
                            PlanPart(
                                segment_id=segment.segment_id,
                                source_start=chunk_start,
                                source_end=chunk_end,
                            )
                        ],
                        actual_text=chunk,
                        continuation=index > 0,
                        merge_allowed=segment.text_override is None,
                        performance=performance,
                        pronunciation=pronunciation,
                        profile=profile,
                    )
                    chapter_tasks.append(task)
                    offset += len(chunk)
            tasks.extend(self._merge(chapter_tasks, profile, source_text))
        # A source-only V4 project deliberately has no semantic speaker on its
        # pending interval. Keep that distinction in the script, but still
        # surface confirmed roles without voice bindings in the planning UI so
        # the user sees that production cannot start yet.
        if any(
            segment.status == "unresolved" and segment.dialogue_type == "unanalysed"
            for chapter in script.chapters
            for segment in chapter.segments
        ):
            unbound.update(
                speaker.speaker_id
                for speaker in speakers.speakers
                if speaker.status == "confirmed"
                and speaker.speaker_id not in voices.bindings
            )
        dependencies = PlanDependencies(
            source_sha256=script.source_sha256,
            script_revision=script.revision,
            voices_revision=voices.revision,
            performance_revision=performance.revision,
            pronunciation_revision=pronunciation.revision,
            tts_profile_revision=profile.revision,
        )
        revision = previous_plan.revision + 1 if previous_plan else 1
        return PlanningResult(
            plan=SynthesisPlan(
                revision=revision,
                dependencies=dependencies,
                tasks=tasks,
            ),
            unresolved_segments=unresolved,
            unbound_speakers=sorted(unbound),
        )

    def _split(self, text: str, profile: TtsProfile) -> list[str]:
        if self.measurer.measure(text) <= profile.limits.maximum:
            return [text]
        remaining = text
        chunks: list[str] = []
        while self.measurer.measure(remaining) > profile.limits.maximum:
            boundary = self._maximum_prefix(remaining, profile.limits.maximum)
            split_at = self._preferred_break(
                remaining, boundary, profile.split_priority
            )
            split_at = self._safe_boundary(remaining, split_at)
            if split_at <= 0:
                raise ValueError("unable to find a safe TTS split boundary")
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
        if remaining:
            chunks.append(remaining)
        return chunks

    def _maximum_prefix(self, text: str, maximum: int) -> int:
        low, high = 1, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.measurer.measure(text[:middle]) <= maximum:
                low = middle
            else:
                high = middle - 1
        return low

    @staticmethod
    def _preferred_break(text: str, boundary: int, priorities: list[str]) -> int:
        minimum = max(1, boundary // 3)
        for priority in priorities:
            if priority == "safe_character":
                return boundary
            candidates = _BREAKS.get(priority, ())
            positions = []
            for token in candidates:
                index = text.rfind(token, minimum, boundary + 1)
                if index >= 0:
                    positions.append(index + len(token))
            found = max(positions, default=0)
            if found > 0:
                return found
        return boundary

    @staticmethod
    def _safe_boundary(text: str, boundary: int) -> int:
        boundary = min(max(boundary, 1), len(text))
        while (
            boundary < len(text)
            and boundary > 1
            and unicodedata.combining(text[boundary])
        ):
            boundary -= 1
        if boundary < len(text):
            while (
                boundary > 1
                and text[boundary - 1].isascii()
                and text[boundary].isascii()
                and text[boundary - 1].isalnum()
                and text[boundary].isalnum()
            ):
                boundary -= 1
        return boundary

    @staticmethod
    def _task_id(chapter_id: str, segment_ids: list[str], part_index: int) -> str:
        digest = hashlib.sha256(
            f"{chapter_id}:{','.join(segment_ids)}:{part_index}".encode()
        ).hexdigest()[:16]
        return f"task_{digest}"

    def _task(
        self,
        *,
        task_id: str,
        chapter_id: str,
        speaker_id: str,
        voice_id: str,
        voice_fingerprint: str,
        segment_ids: list[str],
        parts: list[PlanPart],
        actual_text: str,
        continuation: bool,
        merge_allowed: bool,
        performance: PerformanceOverrides,
        pronunciation: PronunciationRules,
        profile: TtsProfile,
    ) -> PlanTask:
        performance_values = {
            key: performance.overrides.get(key)
            for key in segment_ids
            if key in performance.overrides
        }
        pronunciation_values = {
            "global": pronunciation.global_rules,
            "segments": {
                key: pronunciation.segments.get(key)
                for key in segment_ids
                if key in pronunciation.segments
            },
        }
        synthesis_settings = {
            "engine": profile.engine,
            "model_version": profile.model_version,
            "options": profile.options,
            "emotion": profile.emotion,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "voice": voice_fingerprint,
                    "actual_text": actual_text,
                    "pronunciation": pronunciation_values,
                    "performance": performance_values,
                    "synthesis": synthesis_settings,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        merge_group_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "voice": voice_fingerprint,
                    "performance": performance_values,
                    "synthesis": synthesis_settings,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return PlanTask(
            task_id=task_id,
            chapter_id=chapter_id,
            speaker_id=speaker_id,
            voice_id=voice_id,
            source_segments=segment_ids,
            parts=parts,
            actual_text=actual_text,
            text_length=self.measurer.measure(actual_text),
            continuation=continuation,
            pause_before_ms=0,
            pause_after_ms=120 if continuation else 600,
            crossfade_ms=20 if continuation else 0,
            input_fingerprint=fingerprint,
            merge_group_fingerprint=merge_group_fingerprint,
            merge_allowed=merge_allowed,
        )

    def _merge(
        self, tasks: list[PlanTask], profile: TtsProfile, source_text: str
    ) -> list[PlanTask]:
        if not profile.allow_merge:
            return tasks
        merged: list[PlanTask] = []
        for task in tasks:
            if not merged:
                merged.append(task)
                continue
            previous = merged[-1]
            gap = source_text[
                previous.parts[-1].source_end:task.parts[0].source_start
            ]
            combined_text = previous.actual_text + gap + task.actual_text
            can_merge = (
                not task.continuation
                and not previous.continuation
                and task.merge_allowed
                and previous.merge_allowed
                and task.chapter_id == previous.chapter_id
                and task.speaker_id == previous.speaker_id
                and task.voice_id == previous.voice_id
                and task.merge_group_fingerprint
                == previous.merge_group_fingerprint
                and previous.text_length < profile.limits.minimum
                and task.text_length < profile.limits.minimum
                and self.measurer.measure(combined_text)
                <= profile.limits.maximum
            )
            if not can_merge:
                merged.append(task)
                continue
            combined = combined_text
            combined_segments = [*previous.source_segments, *task.source_segments]
            merged[-1] = PlanTask(
                task_id=self._task_id(
                    previous.chapter_id, combined_segments, 0
                ),
                chapter_id=previous.chapter_id,
                speaker_id=previous.speaker_id,
                voice_id=previous.voice_id,
                source_segments=combined_segments,
                parts=[*previous.parts, *task.parts],
                actual_text=combined,
                text_length=self.measurer.measure(combined),
                continuation=False,
                pause_before_ms=previous.pause_before_ms,
                pause_after_ms=task.pause_after_ms,
                crossfade_ms=0,
                input_fingerprint=hashlib.sha256(
                    f"{previous.input_fingerprint}:{task.input_fingerprint}".encode()
                ).hexdigest(),
                merge_group_fingerprint=previous.merge_group_fingerprint,
                merge_allowed=True,
            )
        return merged
