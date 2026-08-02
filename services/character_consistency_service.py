"""Deterministic second-pass checks for role and dialogue consistency."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any

from domain.v4 import ScriptDocument, SpeakersDocument
from domain.v4.character_extraction import CharacterCandidatesDocument
from services.speaker_normalization import normalize_speaker_name
from services.v4_analysis_config import DEFAULT_V4_ANALYSIS_CONFIG, V4AnalysisConfig


@dataclass(frozen=True)
class ConsistencyIssue:
    issue_id: str
    category: str
    action: str
    severity: str
    message: str
    segment_ids: list[str] = field(default_factory=list)
    speaker_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "category": self.category,
            "action": self.action,
            "severity": self.severity,
            "message": self.message,
            "segment_ids": list(self.segment_ids or []),
            "speaker_ids": list(self.speaker_ids or []),
        }


@dataclass(frozen=True)
class CharacterConsistencyResult:
    script: ScriptDocument
    issues: list[ConsistencyIssue]
    auto_fixed: list[ConsistencyIssue]
    ai_review: list[ConsistencyIssue]
    user_review: list[ConsistencyIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "character-consistency-v1",
            "issues": [item.to_dict() for item in self.issues],
            "summary": {
                "auto_fixed": len(self.auto_fixed),
                "ai_review": len(self.ai_review),
                "user_review": len(self.user_review),
            },
        }


class CharacterConsistencyService:
    def __init__(self, config: V4AnalysisConfig = DEFAULT_V4_ANALYSIS_CONFIG):
        self.config = config

    def check(
        self,
        source_text: str,
        script: ScriptDocument,
        speakers: SpeakersDocument,
        candidates: CharacterCandidatesDocument,
    ) -> CharacterConsistencyResult:
        script.validate(source_text)
        speakers.validate()
        candidates.validate()
        issues: list[ConsistencyIssue] = []
        updated_script = script

        # Quotations without a dialogue cue must never silently become a role
        # assignment.  This is safe enough to auto-correct because the segment
        # classifier already explicitly marked the text as quotation.
        chapters = []
        changed = False
        for chapter in script.chapters:
            segments = []
            for segment in chapter.segments:
                if (
                    segment.dialogue_type == "quotation"
                    and segment.speaker_id is not None
                    and segment.speaker_source != "manual"
                ):
                    changed = True
                    issue = ConsistencyIssue(
                        issue_id=f"quotation-{segment.segment_id}",
                        category="quotation_misattribution",
                        action="auto_fixed",
                        severity="warning",
                        message="引用/术语片段已取消人物归属，等待必要时人工确认。",
                        segment_ids=[segment.segment_id],
                        speaker_ids=[segment.speaker_id],
                    )
                    issues.append(issue)
                    segment = replace(
                        segment,
                        speaker_id=None,
                        speaker_source="unresolved",
                        status="unresolved",
                    )
                segments.append(segment)
            chapters.append(replace(chapter, segments=segments))
        if changed:
            updated_script = replace(script, chapters=chapters, revision=script.revision + 1)

        dialogue_by_chapter: dict[str, list[Any]] = {}
        for chapter in updated_script.chapters:
            dialogue_by_chapter[chapter.chapter_id] = [
                item
                for item in chapter.segments
                if item.kind == "dialogue" and item.dialogue_type != "quotation"
            ]

        issues.extend(self._dialogue_sequence_issues(source_text, dialogue_by_chapter))
        issues.extend(self._first_appearance_issues(dialogue_by_chapter))
        issues.extend(self._alias_issues(speakers, candidates))
        issues.extend(
            self._unresolved_spike_issues(dialogue_by_chapter)
        )

        auto_fixed = [item for item in issues if item.action == "auto_fixed"]
        ai_review = [item for item in issues if item.action == "ai_review"]
        user_review = [item for item in issues if item.action == "user_review"]
        return CharacterConsistencyResult(
            script=updated_script,
            issues=issues,
            auto_fixed=auto_fixed,
            ai_review=ai_review,
            user_review=user_review,
        )

    @staticmethod
    def _dialogue_sequence_issues(
        source_text: str, by_chapter: dict[str, list[Any]]
    ) -> list[ConsistencyIssue]:
        issues: list[ConsistencyIssue] = []
        for chapter_id, segments in by_chapter.items():
            for previous, current in zip(segments, segments[1:]):  # noqa: RUF007
                if previous.status != "confirmed" or current.status != "confirmed":
                    continue
                gap = source_text[previous.end:current.start]
                if gap.strip():
                    continue
                if previous.speaker_id == current.speaker_id and any(
                    mark in source_text[previous.start:previous.end]
                    for mark in ("？", "?")
                ):
                    issues.append(
                        ConsistencyIssue(
                            issue_id=f"self-dialogue-{previous.segment_id}-{current.segment_id}",
                            category="continuous_dialogue_self_answer",
                            action="ai_review",
                            severity="warning",
                            message=f"章节 {chapter_id} 连续问答被分配给同一角色，建议复核上下文。",
                            segment_ids=[previous.segment_id, current.segment_id],
                            speaker_ids=[previous.speaker_id],
                        )
                    )
            for first, second, third in zip(segments, segments[1:], segments[2:]):
                ids = [first.speaker_id, second.speaker_id, third.speaker_id]
                if (
                    all(item is not None for item in ids)
                    and len(set(ids)) == 3
                    and not source_text[first.end:second.start].strip()
                    and not source_text[second.end:third.start].strip()
                ):
                    issues.append(
                        ConsistencyIssue(
                            issue_id=f"third-speaker-{first.segment_id}-{third.segment_id}",
                            category="continuous_dialogue_third_speaker",
                            action="ai_review",
                            severity="info",
                            message=f"章节 {chapter_id} 的连续对话突然切换到第三个角色。",
                            segment_ids=[first.segment_id, second.segment_id, third.segment_id],
                            speaker_ids=[str(item) for item in ids],
                        )
                    )
        return issues

    @staticmethod
    def _first_appearance_issues(
        by_chapter: dict[str, list[Any]]
    ) -> list[ConsistencyIssue]:
        issues: list[ConsistencyIssue] = []
        first_seen: dict[str, Any] = {}
        for chapter_id, segments in by_chapter.items():
            for segment in segments:
                if segment.status != "confirmed" or not segment.speaker_id:
                    continue
                if segment.speaker_id in first_seen:
                    continue
                first_seen[segment.speaker_id] = segment
                if segment.speaker_source == "router":
                    issues.append(
                        ConsistencyIssue(
                            issue_id=f"first-appearance-{segment.segment_id}",
                            category="speaker_before_first_explicit_appearance",
                            action="user_review",
                            severity="info",
                            message=f"角色在章节 {chapter_id} 首次出现时由 AI 归属，缺少前置明确说话人证据。",
                            segment_ids=[segment.segment_id],
                            speaker_ids=[segment.speaker_id],
                        )
                    )
        return issues

    @staticmethod
    def _alias_issues(
        speakers: SpeakersDocument,
        candidates: CharacterCandidatesDocument,
    ) -> list[ConsistencyIssue]:
        owners: defaultdict[str, set[str]] = defaultdict(set)
        for speaker in speakers.speakers:
            for value in [speaker.display_name, *speaker.aliases]:
                key = normalize_speaker_name(value)
                if key:
                    owners[key].add(speaker.speaker_id)
        for candidate in candidates.candidates:
            if candidate.status != "candidate":
                continue
            for value in [candidate.display_name, *candidate.aliases]:
                key = normalize_speaker_name(value)
                if key:
                    owners[key].add(candidate.candidate_id)
        return [
            ConsistencyIssue(
                issue_id=f"alias-conflict-{alias}",
                category="alias_maps_to_multiple_characters",
                action="user_review",
                severity="error",
                message=f"别名「{alias}」同时指向多个角色或候选，未自动合并。",
                speaker_ids=sorted(ids),
            )
            for alias, ids in sorted(owners.items())
            if len(ids) > 1
        ]

    def _unresolved_spike_issues(
        self, by_chapter: dict[str, list[Any]]
    ) -> list[ConsistencyIssue]:
        issues: list[ConsistencyIssue] = []
        for chapter_id, segments in by_chapter.items():
            if not segments:
                continue
            unresolved = sum(item.status == "unresolved" for item in segments)
            ratio = unresolved / len(segments)
            if ratio >= self.config.consistency_unresolved_spike_ratio and unresolved >= 2:
                issues.append(
                    ConsistencyIssue(
                        issue_id=f"unresolved-spike-{chapter_id}",
                        category="chapter_unresolved_spike",
                        action="user_review",
                        severity="warning",
                        message=f"章节 {chapter_id} 有 {unresolved}/{len(segments)} 段对白未归属。",
                    )
                )
        return issues
