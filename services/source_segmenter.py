"""Deterministic baseline segmentation over immutable source coordinates."""
from __future__ import annotations

import re
from dataclasses import dataclass

from domain.v4 import (
    ChapterScript,
    ScriptDocument,
    SemanticSegment,
    Speaker,
    SpeakersDocument,
)
from domain.v4.models import source_sha256, stable_speaker_id
from services.speaker_normalization import (
    is_likely_character_name,
    normalize_speaker_name,
)

_CHAPTER_RE = re.compile(
    r"(?m)^(?:第[零〇一二三四五六七八九十百千万两\d]+[章节回卷部篇]|"
    r"(?:chapter|part)\s+\d+)\b[^\n]*",
    re.IGNORECASE,
)
_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』", '"': '"'}
_SPEAKER_BEFORE_RE = re.compile(
    r"([\w\u3400-\u9fff·]{1,8}?)(?:说道|问道|答道|回答|说|问|答|喊|(?<![名作做])叫|道)"
    r"[：:，,\s]*$"
)
_SPEAKER_AFTER_RE = re.compile(
    r"^\s*[，,。.!！?？]?\s*([\w\u3400-\u9fff·]{1,8}?)"
    r"(?:说道|问道|答道|回答|说|问|答|喊|(?<![名作做])叫|道)(?=[：:，,。]|$)"
)
_PRONOUNS = {"他", "她", "它", "他们", "她们", "它们", "我", "你", "我们", "你们"}
_DIALOGUE_CUES = (
    "说道", "问道", "答道", "回答", "说", "问", "答", "喊", "叫", "道",
    "传来", "提示", "声音",
)

# 题名页行的判定上限：书名 / 作者等短行（无句末标点、无引号）视为题名页
_TITLE_LINE_MAX = 24


@dataclass(frozen=True)
class SegmentationResult:
    script: ScriptDocument
    speakers: SpeakersDocument


class SourceSegmenter:
    """Produce a lossless baseline; uncertain dialogue remains unresolved."""

    def source_only_chapter(
        self,
        source_text: str,
        *,
        chapter_id: str = "chapter_0001",
        title: str = "当前章节",
    ) -> SegmentationResult:
        """Build one lossless chapter without detecting or splitting chapters.

        The fast V4 path treats an upload or paste as the complete current
        chapter.  Keeping this separate from ``source_only`` preserves the
        legacy/full-book chapter detector for advanced and compatibility
        workflows.
        """
        if not source_text.strip():
            raise ValueError("source text cannot be empty")
        script = ScriptDocument(
            source_sha256=source_sha256(source_text),
            chapters=[
                ChapterScript(
                    chapter_id=chapter_id,
                    title=title.strip() or "当前章节",
                    start=0,
                    end=len(source_text),
                    segments=[
                        SemanticSegment(
                            segment_id=f"segment_{chapter_id}_pending",
                            chapter_id=chapter_id,
                            start=0,
                            end=len(source_text),
                            kind="narration",
                            speaker_id=None,
                            speaker_source="unresolved",
                            status="unresolved",
                            dialogue_type="unanalysed",
                        )
                    ],
                )
            ],
        )
        speakers = SpeakersDocument(
            speakers=[
                Speaker(
                    speaker_id="narrator",
                    display_name="旁白",
                    status="confirmed",
                    speaker_type="narrator",
                    locked=True,
                )
            ]
        )
        script.validate(source_text)
        speakers.validate()
        return SegmentationResult(script=script, speakers=speakers)

    def source_only(self, source_text: str) -> SegmentationResult:
        """Build the V4 pre-AI document without making semantic decisions.

        This is deliberately separate from :meth:`segment`.  The latter is a
        compatibility helper for the old offline/rule tests; the V4 creation
        path must use this method so no name, quote or speech attribution can
        enter the formal speaker table before an AI director has read the
        source.  Each chapter is represented by one lossless, unresolved
        interval.  The interval is a transport placeholder, not narration.
        """
        if not source_text.strip():
            raise ValueError("source text cannot be empty")
        chapters: list[ChapterScript] = []
        chapter_ranges, _skip_ranges = self._chapter_ranges(source_text)
        sequence = 1
        for chapter_index, (start, end, title) in enumerate(
            chapter_ranges, start=1
        ):
            chapter_id = f"chapter_{chapter_index:04d}"
            chapters.append(
                ChapterScript(
                    chapter_id=chapter_id,
                    title=title,
                    start=start,
                    end=end,
                    segments=[
                        SemanticSegment(
                            segment_id=f"segment_{sequence:06d}",
                            chapter_id=chapter_id,
                            start=start,
                            end=end,
                            kind="narration",
                            speaker_id=None,
                            speaker_source="unresolved",
                            status="unresolved",
                            dialogue_type="unanalysed",
                        )
                    ],
                )
            )
            sequence += 1
        script = ScriptDocument(
            source_sha256=source_sha256(source_text), chapters=chapters
        )
        speakers = SpeakersDocument(
            speakers=[
                Speaker(
                    speaker_id="narrator",
                    display_name="旁白",
                    status="confirmed",
                    speaker_type="narrator",
                    locked=True,
                )
            ]
        )
        script.validate(source_text)
        speakers.validate()
        return SegmentationResult(script=script, speakers=speakers)

    def segment(self, source_text: str) -> SegmentationResult:
        if not source_text.strip():
            raise ValueError("source text cannot be empty")
        chapters: list[ChapterScript] = []
        names: dict[str, Speaker] = {}
        sequence = 1
        chapter_ranges, skip_ranges = self._chapter_ranges(source_text)
        for chapter_index, (start, end, title) in enumerate(
            chapter_ranges, start=1
        ):
            chapter_id = f"chapter_{chapter_index:04d}"
            segments: list[SemanticSegment] = []
            for item_start, item_end, kind, speaker_name, dialogue_type in self._scan(
                source_text, start, end, skip_ranges
            ):
                if not source_text[item_start:item_end].strip():
                    continue
                speaker_id: str | None
                if kind == "narration":
                    speaker_id = "narrator"
                    speaker_source = "rule"
                    status = "confirmed"
                    dialogue_type = "narration"
                elif speaker_name:
                    speaker_id = stable_speaker_id(speaker_name)
                    speaker_source = "rule"
                    status = "confirmed"
                    names.setdefault(
                        speaker_id,
                        Speaker(
                            speaker_id=speaker_id,
                            display_name=speaker_name,
                            status="confirmed",
                            speaker_type="character",
                        ),
                    )
                else:
                    speaker_id = None
                    speaker_source = "unresolved"
                    status = "unresolved"
                segments.append(
                    SemanticSegment(
                        segment_id=f"segment_{sequence:06d}",
                        chapter_id=chapter_id,
                        start=item_start,
                        end=item_end,
                        kind=kind,
                        speaker_id=speaker_id,
                        speaker_source=speaker_source,
                        status=status,
                        dialogue_type=dialogue_type,
                    )
                )
                sequence += 1
            chapters.append(
                ChapterScript(
                    chapter_id=chapter_id,
                    title=title,
                    start=start,
                    end=end,
                    segments=segments,
                )
            )
        script = ScriptDocument(source_sha256=source_sha256(source_text), chapters=chapters)
        speakers = SpeakersDocument(
            speakers=[
                Speaker(
                    speaker_id="narrator",
                    display_name="旁白",
                    status="confirmed",
                    speaker_type="narrator",
                    locked=True,
                ),
                *sorted(names.values(), key=lambda item: item.speaker_id),
            ]
        )
        script.validate(source_text)
        speakers.validate()
        return SegmentationResult(script=script, speakers=speakers)

    @staticmethod
    def _chapter_ranges(
        source_text: str,
    ) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
        """返回 (章节范围, 题名页行区间)。

        开头的纯题名页（书名 / 作者等短行）不单独成章：并入第一章的范围，
        其文本成为第一章开头的旁白段（保持 lossless），避免「三章正文 +
        一个前言伪章节」的错觉章节数。
        """
        matches = list(_CHAPTER_RE.finditer(source_text))
        if not matches:
            return [(0, len(source_text), "正文")], []
        ranges: list[tuple[int, int, str]] = []
        skip: list[tuple[int, int]] = []
        first = matches[0]
        prefix = source_text[: first.start()]
        is_title_page = bool(prefix.strip()) and bool(
            SourceSegmenter._front_title_ranges(prefix)
        )
        if is_title_page:
            # 纯题名页：第一章范围前移到 0，题名文本并入第一章旁白
            end = matches[1].start() if len(matches) > 1 else len(source_text)
            ranges.append((0, end, first.group(0).strip()))
        elif prefix.strip():
            ranges.append((0, first.start(), "序章"))
        for index, match in enumerate(matches):
            if is_title_page and index == 0:
                continue  # 已并入上一段
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(source_text)
            )
            ranges.append((match.start(), end, match.group(0).strip()))
        return ranges, skip

    @staticmethod
    def _front_title_ranges(prefix: str) -> list[tuple[int, int]]:
        """判定前置内容是否为纯题名页；是则返回其行区间（相对全文起点）。

        纯题名页要求：每一行都较短（<=24 字符）、不含句末标点、不含引号。
        """
        ranges: list[tuple[int, int]] = []
        cursor = 0
        for line in prefix.splitlines(keepends=True):
            stripped = line.strip()
            if not stripped:
                cursor += len(line)
                continue
            if len(stripped) > _TITLE_LINE_MAX or any(
                ch in stripped for ch in "。！？；：”“「」『』"
            ):
                return []
            ranges.append((cursor, cursor + len(line)))
            cursor += len(line)
        return ranges

    def _scan(
        self,
        text: str,
        start: int,
        end: int,
        skip_ranges: list[tuple[int, int]] | tuple[tuple[int, int], ...] = (),
    ) -> list[tuple[int, int, str, str | None, str]]:
        items: list[tuple[int, int, str, str | None, str]] = []
        cursor = start
        index = start
        skip = _clip_ranges(skip_ranges, start, end)
        while index < end:
            opener = text[index]
            if opener not in _QUOTE_PAIRS:
                index += 1
                continue
            closer = _QUOTE_PAIRS[opener]
            close = self._find_close(text, index + 1, end, opener, closer)
            if cursor < index:
                items.extend(
                    (na, nb, "narration", None, "narration")
                    for na, nb in _narration_ranges(cursor, index, skip)
                )
            quote_end = close + 1 if close is not None else end
            name = self._speaker_before(text, start, index)
            if name:
                name = normalize_speaker_name(name) or None
            if not name and close is not None:
                name = self._speaker_after(text, quote_end, end)
                name = normalize_speaker_name(name) or None
            dialogue_type = (
                "dialogue"
                if name
                else (
                    "suspected_dialogue"
                    if self._has_dialogue_cue(text, start, index, quote_end, end)
                    else "quotation"
                )
            )
            items.append((index, quote_end, "dialogue", name, dialogue_type))
            cursor = quote_end
            index = quote_end
        if cursor < end:
            items.extend(
                (na, nb, "narration", None, "narration")
                for na, nb in _narration_ranges(cursor, end, skip)
            )
        return items

    @staticmethod
    def _find_close(
        text: str, start: int, end: int, opener: str, closer: str
    ) -> int | None:
        if opener != closer:
            found = text.find(closer, start, end)
            return found if found >= 0 else None
        index = start
        while index < end:
            if text[index] == '"' and (index == 0 or text[index - 1] != "\\"):
                return index
            index += 1
        return None

    @staticmethod
    def _speaker_before(text: str, chapter_start: int, quote_start: int) -> str | None:
        context = text[max(chapter_start, quote_start - 64):quote_start]
        match = _SPEAKER_BEFORE_RE.search(context)
        name = match.group(1) if match else None
        if name in _PRONOUNS:
            return None
        cleaned = normalize_speaker_name(name) if name else ""
        return cleaned if cleaned and is_likely_character_name(cleaned) else None

    @staticmethod
    def _speaker_after(text: str, quote_end: int, chapter_end: int) -> str | None:
        context = text[quote_end:min(chapter_end, quote_end + 64)]
        match = _SPEAKER_AFTER_RE.search(context)
        name = match.group(1) if match else None
        if name in _PRONOUNS:
            return None
        cleaned = normalize_speaker_name(name) if name else ""
        return cleaned if cleaned and is_likely_character_name(cleaned) else None

    @staticmethod
    def _has_dialogue_cue(
        text: str,
        chapter_start: int,
        quote_start: int,
        quote_end: int,
        chapter_end: int,
    ) -> bool:
        before = text[max(chapter_start, quote_start - 32):quote_start]
        after = text[quote_end:min(chapter_end, quote_end + 32)]
        return any(cue in before or cue in after for cue in _DIALOGUE_CUES)


def _clip_ranges(
    ranges: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    """把跳过的区间裁剪到 [start, end) 内。"""
    clipped: list[tuple[int, int]] = []
    for item_start, item_end in ranges:
        a = max(item_start, start)
        b = min(item_end, end)
        if a < b:
            clipped.append((a, b))
    return clipped


def _narration_ranges(
    a: int, b: int, skip: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """返回 (a, b) 内去掉 skip 区间后的连续子区间列表（保持坐标无损）。"""
    result: list[tuple[int, int]] = []
    cursor = a
    for item_start, item_end in skip:
        if item_end <= cursor or item_start >= b:
            continue
        if item_start > cursor:
            result.append((cursor, min(item_start, b)))
        cursor = max(cursor, item_end)
    if cursor < b:
        result.append((cursor, b))
    return result
