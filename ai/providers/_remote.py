"""Shared batching, protocol validation, and transport for remote JSON providers."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional

from ai.prompts.script_director_v3 import (
    BREATHS,
    EMOTIONS,
    PAUSE_TYPES,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)

from .base import ScriptAnalysisProvider
from .exceptions import (
    ProviderOutputInvalidJsonError,
    ProviderOutputTruncatedError,
)

Transport = Callable[[str, Dict[str, str], Dict[str, Any], float], Dict[str, Any]]
ProgressCallback = Callable[[str], None]

_CHAPTER_HEADING_RE = re.compile(
    r"(?mi)^\s*(?:第[零一二三四五六七八九十百千万两\d]+[章节回卷部篇].*|"
    r"chapter\s+\d+.*)\s*$"
)
_EXPLANATION_PREFIX_RE = re.compile(
    r"^\s*(?:以下是|这是|根据(?:原文|要求)|analysis|here\s+is)",
    re.IGNORECASE,
)


@dataclass
class SourceChunk:
    """One source chapter part sent under the versioned batch protocol."""

    batch_id: str
    chapter_key: str
    chapter_title: str
    part_index: int
    part_total: int
    text: str
    batch_index: int = 1
    batch_total: int = 1


def _default_transport(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"AI Provider 请求失败（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 AI Provider：{exc.reason}") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AI Provider 返回了非 JSON HTTP 响应") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AI Provider HTTP 响应顶层不是 JSON 对象")
    return result


def _looks_truncated_json(cleaned: str, exc: json.JSONDecodeError) -> bool:
    message = exc.msg.lower()
    if "unterminated" in message:
        return True
    tail_distance = max(0, len(cleaned) - exc.pos)
    return tail_distance <= 4 and cleaned[-1:] not in {"}", "]"}


def parse_json_content(content: str) -> Dict[str, Any]:
    """Parse one complete provider object without attempting synthetic repair."""
    if not isinstance(content, str) or not content.strip():
        raise ProviderOutputTruncatedError("模型响应为空")
    cleaned = content.strip()
    fenced = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$",
        cleaned,
        re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        cleaned = fenced.group(1)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        message = f"模型输出不是合法 JSON（第 {exc.lineno} 行，第 {exc.colno} 列）"
        if _looks_truncated_json(cleaned, exc):
            raise ProviderOutputTruncatedError(message) from exc
        raise ProviderOutputInvalidJsonError(message) from exc
    if not isinstance(result, dict):
        raise ProviderOutputInvalidJsonError("模型输出顶层必须是 JSON 对象")
    return result


class RemoteJsonDirectorProvider(ScriptAnalysisProvider):
    """Remote provider with bounded chapter batching and local validation."""

    api_key_env = ""
    model_env = ""
    base_url_env = ""
    default_model = ""
    default_base_url = ""
    max_split_depth = 3
    max_leaf_attempts = 2

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 180.0,
        max_input_chars: Optional[int] = None,
        transport: Optional[Transport] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        self.api_key = (api_key or os.getenv(self.api_key_env, "")).strip()
        self.model = (
            model
            or (os.getenv(self.model_env, "") if self.model_env else "")
            or self.default_model
        ).strip()
        self.base_url = (
            base_url
            or (os.getenv(self.base_url_env, "") if self.base_url_env else "")
            or self.default_base_url
        ).rstrip("/")
        self.timeout = timeout
        configured_limit = max_input_chars
        if configured_limit is None:
            try:
                configured_limit = int(
                    os.getenv("AUDIOBOOK_STUDIO_AI_MAX_INPUT_CHARS", "12000")
                )
            except ValueError:
                configured_limit = 12000
        self.max_input_chars = max(200, int(configured_limit))
        self._transport = transport or _default_transport
        self.progress_callback = progress_callback
        self._validated_batch_ids: set[str] = set()
        self._request_count = 0

    def _require_config(self) -> None:
        if not self.api_key:
            raise ValueError(
                f"{self.name} Provider 未配置密钥；请设置环境变量 {self.api_key_env}"
            )
        if not self.model:
            raise ValueError(f"{self.name} Provider 未配置模型")

    def extract_characters(self, text: str) -> List[str]:
        raw = self.analyze_script(text)
        seen: List[str] = []
        for chapter in raw.get("chapters", []):
            if not isinstance(chapter, dict):
                continue
            for segment in chapter.get("segments", []):
                if not isinstance(segment, dict):
                    continue
                speaker = str(segment.get("speaker") or segment.get("role") or "").strip()
                if speaker and speaker not in seen:
                    seen.append(speaker)
        return seen

    def generate_segments(
        self,
        text: str,
        characters: List[str],
    ) -> List[Dict[str, Any]]:
        raw = self.analyze_script(text)
        return [
            segment
            for chapter in raw.get("chapters", [])
            if isinstance(chapter, dict)
            for segment in chapter.get("segments", [])
            if isinstance(segment, dict)
        ]

    def analyze_script(
        self,
        text: str,
        *,
        title: str = "",
        author: str = "",
    ) -> Dict[str, Any]:
        self._require_config()
        chunks = self._source_chunks(text)
        total = len(chunks)
        chunks = [
            replace(chunk, batch_index=index, batch_total=total)
            for index, chunk in enumerate(chunks, 1)
        ]
        self._validated_batch_ids = set()
        self._request_count = 0

        chapter_order: list[str] = []
        chapters: dict[str, Dict[str, Any]] = {}
        for chunk in chunks:
            self._notify(
                f"4/6 AI 分析：第 {chunk.batch_index}/{chunk.batch_total} 批"
                f"（{chunk.chapter_title}，分片 {chunk.part_index}/{chunk.part_total}）"
            )
            segments = self.analyze_chunk_with_retry(
                chunk,
                title=title,
                author=author,
            )
            if chunk.chapter_key not in chapters:
                chapter_order.append(chunk.chapter_key)
                chapters[chunk.chapter_key] = {
                    "id": chunk.chapter_key,
                    "title": chunk.chapter_title,
                    "segments": [],
                }
            chapters[chunk.chapter_key]["segments"].extend(segments)

        return {
            "provider": self.name,
            "meta": {
                "provider_model": self.model,
                "analysis_batches": total,
                "analysis_requests": self._request_count,
                "batch_schema_version": SCHEMA_VERSION,
            },
            "chapters": [chapters[key] for key in chapter_order],
        }

    def analyze_chunk_with_retry(
        self,
        chunk: SourceChunk,
        *,
        title: str = "",
        author: str = "",
        split_depth: int = 0,
    ) -> list[dict]:
        """Analyze only this chunk; split boundedly when output is truncated."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_leaf_attempts + 1):
            try:
                self._request_count += 1
                result = self._request_json(
                    SYSTEM_PROMPT,
                    build_user_prompt(chunk=chunk, title=title, author=author),
                    task="legacy_script_director",
                    reasoning=False,
                )
                return self._validate_batch(result, chunk)
            except ProviderOutputTruncatedError as exc:
                last_error = exc
                children = (
                    self._split_retry_chunk(chunk, split_depth)
                    if split_depth < self.max_split_depth
                    else []
                )
                if children:
                    self._notify(
                        f"4/6 AI 分析重试：{chunk.chapter_title} "
                        f"{chunk.batch_index}/{chunk.batch_total} 批输出被截断，"
                        f"自动拆分（深度 {split_depth + 1}/{self.max_split_depth}）"
                    )
                    merged: list[dict] = []
                    for child in children:
                        merged.extend(
                            self.analyze_chunk_with_retry(
                                child,
                                title=title,
                                author=author,
                                split_depth=split_depth + 1,
                            )
                        )
                    return merged
                if attempt < self.max_leaf_attempts:
                    self._notify(
                        f"4/6 AI 分析重试：最小分片第 {attempt + 1}/"
                        f"{self.max_leaf_attempts} 次"
                    )
                    continue
            except ProviderOutputInvalidJsonError:
                raise

        reason = str(last_error or "模型输出无效")
        raise ProviderOutputTruncatedError(
            "AI 分析失败\n"
            f"来源章节：{chunk.chapter_title}\n"
            f"批次：{chunk.batch_index}/{chunk.batch_total}\n"
            f"分片：{chunk.part_index}/{chunk.part_total}\n"
            f"原因：模型输出达到长度限制，自动拆分后仍失败（{reason}）"
        ) from last_error

    def _validate_batch(
        self,
        result: Dict[str, Any],
        chunk: SourceChunk,
    ) -> list[dict]:
        if result.get("schema_version") != SCHEMA_VERSION:
            raise ProviderOutputInvalidJsonError(
                f"AI 批次协议版本无效，应为 {SCHEMA_VERSION}"
            )
        if result.get("batch_id") != chunk.batch_id:
            raise ProviderOutputInvalidJsonError("AI 响应 batch_id 与请求不匹配")
        if result.get("source_chapter_id") != chunk.chapter_key:
            raise ProviderOutputInvalidJsonError("AI 响应来源章节与请求不匹配")
        if result.get("source_chapter_title") != chunk.chapter_title:
            raise ProviderOutputInvalidJsonError("AI 响应来源章节标题与请求不匹配")
        if chunk.batch_id in self._validated_batch_ids:
            raise ProviderOutputInvalidJsonError("检测到重复 AI 批次，已停止合并")

        segments = result.get("segments")
        if not isinstance(segments, list) or not segments:
            raise ProviderOutputInvalidJsonError("AI 响应 segments 必须是非空列表")
        validated: list[dict] = []
        for index, raw in enumerate(segments, 1):
            if not isinstance(raw, dict):
                raise ProviderOutputInvalidJsonError(
                    f"AI 响应第 {index} 个 segment 不是对象"
                )
            segment = dict(raw)
            text = str(segment.get("text") or "").strip()
            speaker = str(segment.get("speaker") or "").strip()
            if not text or not speaker:
                raise ProviderOutputInvalidJsonError(
                    f"AI 响应第 {index} 个 segment 的 text/speaker 不能为空"
                )
            if _EXPLANATION_PREFIX_RE.match(text):
                raise ProviderOutputInvalidJsonError("AI 响应疑似包含解释性前缀")
            emotion = str(segment.get("emotion") or "")
            if emotion not in EMOTIONS:
                raise ProviderOutputInvalidJsonError(
                    f"AI 响应第 {index} 个 segment 的 emotion 非法"
                )
            self._number_in_range(
                segment.get("emotion_strength"), 0, 1, "emotion_strength"
            )
            delivery = segment.get("delivery")
            if not isinstance(delivery, dict):
                raise ProviderOutputInvalidJsonError("AI 响应 delivery 必须是对象")
            self._number_in_range(delivery.get("speed"), 0.85, 1.15, "speed")
            self._number_in_range(delivery.get("pitch"), -12, 12, "pitch")
            self._number_in_range(delivery.get("intensity"), 0, 1, "intensity")
            if delivery.get("breath") not in BREATHS:
                raise ProviderOutputInvalidJsonError("AI 响应 breath 枚举值非法")
            self._number_in_range(
                segment.get("pause_before", 0), 0, 3000, "pause_before"
            )
            self._number_in_range(
                segment.get("pause_after", 600), 0, 3000, "pause_after"
            )
            pauses = segment.get("pauses", [])
            if not isinstance(pauses, list):
                raise ProviderOutputInvalidJsonError("AI 响应 pauses 必须是列表")
            for pause in pauses:
                if not isinstance(pause, dict) or pause.get("type") not in PAUSE_TYPES:
                    raise ProviderOutputInvalidJsonError("AI 响应 pause 格式或类型非法")
                self._number_in_range(
                    pause.get("position"), 0, len(text), "pause.position"
                )
                self._number_in_range(
                    pause.get("duration"), 100, 3000, "pause.duration"
                )
            validated.append(segment)

        self._validate_source_coverage(chunk.text, validated)
        self._validated_batch_ids.add(chunk.batch_id)
        return validated

    @staticmethod
    def _number_in_range(value: Any, low: float, high: float, field: str) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ProviderOutputInvalidJsonError(f"AI 响应 {field} 不是数值") from None
        if not low <= number <= high:
            raise ProviderOutputInvalidJsonError(
                f"AI 响应 {field} 超出允许范围 {low}-{high}"
            )

    @staticmethod
    def _validate_source_coverage(source: str, segments: list[dict]) -> None:
        normalize = lambda value: re.sub(r"\s+", "", str(value or ""))
        expected = normalize(source)
        actual = normalize("".join(str(item.get("text") or "") for item in segments))
        if len(expected) < 40:
            return
        length_ratio = len(actual) / max(1, len(expected))
        similarity = SequenceMatcher(None, expected, actual, autojunk=False).ratio()
        if not 0.65 <= length_ratio <= 1.35 or similarity < 0.65:
            raise ProviderOutputInvalidJsonError(
                "AI 响应与当前输入批次覆盖差异过大，疑似丢失、改写或补写原文"
            )

    def _source_chunks(self, text: str) -> List[SourceChunk]:
        """Split by chapter, paragraph, then Chinese sentence boundaries."""
        clean = str(text or "").strip()
        matches = list(_CHAPTER_HEADING_RE.finditer(clean))
        sections: list[tuple[str, str]] = []
        if matches:
            if matches[0].start() > 0:
                preface = clean[:matches[0].start()].strip()
                if preface:
                    sections.append(("前言", preface))
            for index, match in enumerate(matches):
                end = (
                    matches[index + 1].start()
                    if index + 1 < len(matches)
                    else len(clean)
                )
                title = match.group(0).strip()
                content = clean[match.end():end].strip()
                sections.append((title, content or title))
        else:
            sections = [("全文", clean)]

        chunks: list[SourceChunk] = []
        for chapter_index, (chapter_title, section) in enumerate(sections, 1):
            chapter_key = f"chapter-{chapter_index:03d}"
            parts = self._split_oversized(section)
            for part_index, part in enumerate(parts, 1):
                chunks.append(
                    SourceChunk(
                        batch_id=f"{chapter_key}-part-{part_index:02d}",
                        chapter_key=chapter_key,
                        chapter_title=chapter_title,
                        part_index=part_index,
                        part_total=len(parts),
                        text=part,
                    )
                )
        return chunks

    def _split_oversized(self, text: str) -> List[str]:
        if len(text) <= self.max_input_chars:
            return [text]
        paragraphs = [
            part.strip()
            for part in re.split(r"\n\s*\n", text)
            if part.strip()
        ]
        pieces: list[str] = []
        current = ""
        for paragraph in paragraphs or [text]:
            paragraph_parts = self._split_long_paragraph(paragraph)
            for part in paragraph_parts:
                candidate = f"{current}\n\n{part}".strip() if current else part
                if current and len(candidate) > self.max_input_chars:
                    pieces.append(current)
                    current = part
                else:
                    current = candidate
        if current:
            pieces.append(current)
        return pieces

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        if len(paragraph) <= self.max_input_chars:
            return [paragraph]
        sentences = [
            piece.strip()
            for piece in re.findall(r".*?[。！？；](?:[”’」』】）)]*)|.+$", paragraph)
            if piece.strip()
        ]
        pieces: list[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > self.max_input_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(self._hard_split_at_safe_edge(sentence))
                continue
            candidate = current + sentence
            if current and len(candidate) > self.max_input_chars:
                pieces.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            pieces.append(current)
        return pieces

    def _hard_split_at_safe_edge(self, text: str) -> list[str]:
        pieces: list[str] = []
        remaining = text
        while len(remaining) > self.max_input_chars:
            window = remaining[: self.max_input_chars]
            boundary = max(
                window.rfind(mark)
                for mark in ("。", "！", "？", "；", "”", "’", "」", "』", "）", ")")
            )
            end = boundary + 1 if boundary >= self.max_input_chars // 2 else len(window)
            pieces.append(remaining[:end].strip())
            remaining = remaining[end:].strip()
        if remaining:
            pieces.append(remaining)
        return pieces

    def _split_retry_chunk(
        self,
        chunk: SourceChunk,
        split_depth: int,
    ) -> list[SourceChunk]:
        text = chunk.text.strip()
        if len(text) < 2:
            return []
        midpoint = len(text) // 2
        candidates = [
            match.end()
            for match in re.finditer(r"\n\s*\n|[。！？；][”’」』】）)]*", text)
            if 0 < match.end() < len(text)
        ]
        if candidates:
            split_at = min(candidates, key=lambda value: abs(value - midpoint))
        else:
            split_at = midpoint
        left = text[:split_at].strip()
        right = text[split_at:].strip()
        if not left or not right:
            return []

        total = chunk.part_total * 2
        base_index = (chunk.part_index - 1) * 2
        depth = split_depth + 1
        return [
            replace(
                chunk,
                batch_id=f"{chunk.batch_id}-split-{depth}-{index}",
                part_index=base_index + index,
                part_total=total,
                text=part,
            )
            for index, part in enumerate((left, right), 1)
        ]

    def _notify(self, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(message)

    def _request_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        raise NotImplementedError
