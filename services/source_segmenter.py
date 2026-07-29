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

_CHAPTER_RE = re.compile(
    r"(?m)^(?:第[零〇一二三四五六七八九十百千万两\d]+[章节回卷部篇]|"
    r"(?:chapter|part)\s+\d+)\b[^\n]*",
    re.IGNORECASE,
)
_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』", '"': '"'}
_SPEAKER_BEFORE_RE = re.compile(
    r"([\w\u3400-\u9fff·]{1,24}?)(?:说道|问道|答道|说|问|答|喊|叫|道)"
    r"[：:，,\s]*$"
)
_SPEAKER_AFTER_RE = re.compile(
    r"^\s*[，,。.!！?？]?\s*([\w\u3400-\u9fff·]{1,24}?)"
    r"(?:说道|问道|答道|说|问|答|喊|叫|道)"
)
_PRONOUNS = {"他", "她", "它", "他们", "她们", "它们", "我", "你", "我们", "你们"}


@dataclass(frozen=True)
class SegmentationResult:
    script: ScriptDocument
    speakers: SpeakersDocument


class SourceSegmenter:
    """Produce a lossless baseline; uncertain dialogue remains unresolved."""

    def segment(self, source_text: str) -> SegmentationResult:
        if not source_text.strip():
            raise ValueError("source text cannot be empty")
        chapters: list[ChapterScript] = []
        names: dict[str, Speaker] = {}
        sequence = 1
        for chapter_index, (start, end, title) in enumerate(
            self._chapter_ranges(source_text), start=1
        ):
            chapter_id = f"chapter_{chapter_index:04d}"
            segments: list[SemanticSegment] = []
            for item_start, item_end, kind, speaker_name in self._scan(
                source_text, start, end
            ):
                if not source_text[item_start:item_end].strip():
                    continue
                speaker_id: str | None
                if kind == "narration":
                    speaker_id = "narrator"
                    speaker_source = "rule"
                    status = "confirmed"
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
    def _chapter_ranges(source_text: str) -> list[tuple[int, int, str]]:
        matches = list(_CHAPTER_RE.finditer(source_text))
        if not matches:
            return [(0, len(source_text), "正文")]
        ranges: list[tuple[int, int, str]] = []
        if source_text[: matches[0].start()].strip():
            ranges.append((0, matches[0].start(), "前言"))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(source_text)
            ranges.append((match.start(), end, match.group(0).strip()))
        return ranges

    def _scan(
        self, text: str, start: int, end: int
    ) -> list[tuple[int, int, str, str | None]]:
        items: list[tuple[int, int, str, str | None]] = []
        cursor = start
        index = start
        while index < end:
            opener = text[index]
            if opener not in _QUOTE_PAIRS:
                index += 1
                continue
            closer = _QUOTE_PAIRS[opener]
            close = self._find_close(text, index + 1, end, opener, closer)
            if cursor < index:
                items.append((cursor, index, "narration", None))
            quote_end = close + 1 if close is not None else end
            name = self._speaker_before(text, start, index)
            if not name and close is not None:
                name = self._speaker_after(text, quote_end, end)
            items.append((index, quote_end, "dialogue", name))
            cursor = quote_end
            index = quote_end
        if cursor < end:
            items.append((cursor, end, "narration", None))
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
        return None if name in _PRONOUNS else name

    @staticmethod
    def _speaker_after(text: str, quote_end: int, chapter_end: int) -> str | None:
        context = text[quote_end:min(chapter_end, quote_end + 64)]
        match = _SPEAKER_AFTER_RE.search(context)
        name = match.group(1) if match else None
        return None if name in _PRONOUNS else name
