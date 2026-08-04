"""Local, cursor-based validation for chapter-fast AI responses."""
from __future__ import annotations

import re
from dataclasses import dataclass

from domain.v4.chapter_analysis import (
    ChapterAnalysisResponse,
    ChapterAnalysisSegment,
)
from domain.v4.models import ValidationError


class ChapterAnalysisValidationError(ValidationError):
    """The response cannot be safely mapped back to the immutable source."""

    def __init__(self, *errors: str):
        self.errors = [str(error) for error in errors if str(error).strip()]
        super().__init__("；".join(self.errors) or "章节分析输出校验失败")


@dataclass(frozen=True)
class MatchedChapterSegment:
    item: ChapterAnalysisSegment
    source_start: int
    source_end: int
    source_text: str


@dataclass(frozen=True)
class ValidatedChapterAnalysis:
    response: ChapterAnalysisResponse
    segments: list[MatchedChapterSegment]


class ChapterAnalysisValidator:
    """Validate structure, speaker IDs, order, and lossless source coverage."""

    def validate(
        self,
        response: ChapterAnalysisResponse | dict,
        *,
        chapter_id: str,
        source_text: str,
        allowed_speaker_ids: set[str] | frozenset[str],
    ) -> ValidatedChapterAnalysis:
        try:
            parsed = (
                response
                if isinstance(response, ChapterAnalysisResponse)
                else ChapterAnalysisResponse.from_dict(response)
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise ChapterAnalysisValidationError(str(exc)) from exc

        errors: list[str] = []
        if parsed.chapter_id != chapter_id:
            errors.append(
                f"chapter_id 不匹配：期望 {chapter_id}，得到 {parsed.chapter_id}"
            )
        if not source_text.strip():
            errors.append("当前章节原文为空")
        matches: list[MatchedChapterSegment] = []
        cursor = 0
        allowed = set(allowed_speaker_ids) | {"narrator"}
        for expected_index, item in enumerate(parsed.segments):
            if item.index != expected_index:
                errors.append(
                    f"segment index 必须连续有序：期望 {expected_index}，得到 {item.index}"
                )
            if item.speaker_id is not None and item.speaker_id not in allowed:
                errors.append(f"未知 speaker_id：{item.speaker_id}")
            if item.segment_type in {"narration", "stage_direction"} and item.speaker_id not in {
                None,
                "narrator",
            }:
                errors.append(f"{item.segment_type} 不允许绑定角色：{item.speaker_id}")
            found = self._match_from_cursor(source_text, cursor, item.text)
            if found is None:
                errors.append(
                    f"segment {expected_index} 无法从 source cursor={cursor} 连续匹配，可能遗漏或改写原文"
                )
                continue
            start, end = found
            if source_text[cursor:start].strip():
                errors.append(
                    f"segment {expected_index} 前存在未覆盖原文：{source_text[cursor:start][:40]!r}"
                )
            matches.append(
                MatchedChapterSegment(
                    item=item,
                    source_start=start,
                    source_end=end,
                    source_text=source_text[start:end],
                )
            )
            cursor = end
        if source_text[cursor:].strip():
            errors.append(
                f"segments 末尾遗漏原文：{source_text[cursor:][:40]!r}"
            )
        if self._looks_like_dialogue(source_text) and not any(
            item.item.segment_type in {"dialogue", "inner_monologue", "quotation"}
            for item in matches
        ):
            errors.append("原文存在明显对白/引用，但输出没有对白类 segment")
        if errors:
            raise ChapterAnalysisValidationError(*errors)
        return ValidatedChapterAnalysis(parsed, matches)

    @staticmethod
    def _match_from_cursor(
        source_text: str, cursor: int, candidate: str
    ) -> tuple[int, int] | None:
        if not candidate or cursor < 0 or cursor > len(source_text):
            return None
        if source_text.startswith(candidate, cursor):
            return cursor, cursor + len(candidate)
        # Permit whitespace normalization only.  Punctuation, characters, and
        # their order must still be identical; a rewritten sentence cannot pass.
        source_tail = source_text[cursor:]
        source_compact = re.sub(r"\s+", "", source_tail)
        candidate_compact = re.sub(r"\s+", "", candidate)
        if not candidate_compact or not source_compact.startswith(candidate_compact):
            return None
        consumed = 0
        compact_index = 0
        while consumed < len(source_tail) and compact_index < len(candidate_compact):
            char = source_tail[consumed]
            consumed += 1
            if char.isspace():
                continue
            if char != candidate_compact[compact_index]:
                return None
            compact_index += 1
        return cursor, cursor + consumed

    @staticmethod
    def _looks_like_dialogue(source_text: str) -> bool:
        pairs = (("“", "”"), ("「", "」"), ("『", "』"), ('"', '"'))
        quoted = any(source_text.count(left) >= 1 and source_text.count(right) >= 1 for left, right in pairs)
        cue = bool(re.search(r"(?:说道|问道|答道|回答|喊道|叫道|说：|问：)", source_text))
        return quoted and cue
