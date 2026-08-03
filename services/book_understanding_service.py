"""Sequential full-book AI reading with durable character memory."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any

from domain.v4 import CharacterBibleDocument
from domain.v4.models import source_sha256 as source_digest
from domain.v4.models import stable_speaker_id
from repositories.ai_first_checkpoint_repository import (
    BookUnderstandingCheckpointRepository,
)
from repositories.v4_atomic import atomic_write_json
from services.ai_first_source import split_source_range


@dataclass(frozen=True)
class BookUnderstandingResult:
    bible: CharacterBibleDocument
    completed_chapters: int
    failed_chapters: int
    failed_chapter_ids: list[str]
    resumed: bool


class BookUnderstandingService:
    """Make AI's rolling book memory the only machine character source."""

    def __init__(
        self,
        adapter: Any,
        checkpoint: BookUnderstandingCheckpointRepository,
        *,
        max_input_chars: int = 12000,
    ):
        self.adapter = adapter
        self.checkpoint = checkpoint
        self.max_input_chars = max_input_chars

    def understand(
        self,
        source_text: str,
        script,
        *,
        progress_callback=None,
        force_restart: bool = False,
    ) -> BookUnderstandingResult:
        source_sha = source_digest(source_text)
        fingerprint = self._fingerprint(source_sha)
        state = None if force_restart else self.checkpoint.load(
            source_sha256=source_sha, input_fingerprint=fingerprint
        )
        resumed = state is not None
        memory = None
        if state is not None:
            try:
                memory = self._load_memory(state.get("memory"), source_sha)
                memory = self._validate_evidence(memory, source_text, script.chapters)
            except (TypeError, ValueError):
                # A partial write or stale semantic memory must never be
                # treated as a completed chapter. Rebuild from the source.
                state = None
                resumed = False
        if state is None:
            memory = CharacterBibleDocument(source_sha256=source_sha)
            state = {
                "source_sha256": source_sha,
                "input_fingerprint": fingerprint,
                "provider": getattr(self.adapter, "name", ""),
                "model": getattr(self.adapter, "model", ""),
                "status": "running",
                "current_chapter_id": "",
                "chapters": {},
                "memory": memory.to_dict(),
                "finalized": False,
            }
            self.checkpoint.save(state)
        if memory is None:
            memory = self._load_memory(state.get("memory"), source_sha)
        failed: list[str] = []
        chapters_state = state.setdefault("chapters", {})

        for chapter in script.chapters:
            entry = chapters_state.get(chapter.chapter_id) or {}
            if entry.get("status") == "completed":
                # The persisted memory already includes this chapter.  Do not
                # call the model again when the app is reopened.
                continue
            state["current_chapter_id"] = chapter.chapter_id
            entry.update({"status": "running", "attempts": int(entry.get("attempts", 0)) + 1})
            chapters_state[chapter.chapter_id] = entry
            self.checkpoint.save({**state, "memory": memory.to_dict()})
            self._report(
                progress_callback,
                f"正在阅读全书：{chapter.title or chapter.chapter_id}",
            )
            try:
                chapter_memory = memory
                chunks = split_source_range(
                    source_text, chapter.start, chapter.end, self.max_input_chars
                )
                for chunk_start, chunk_end in chunks:
                    response = self._read_chunk(
                        source_sha=source_sha,
                        chapter=chapter,
                        source_text=source_text,
                        source_start=chunk_start,
                        source_end=chunk_end,
                        memory=chapter_memory,
                        chapters=script.chapters,
                    )
                    chapter_memory = self._merge(memory, response, source_sha)
                    memory = chapter_memory
                    state["memory"] = memory.to_dict()
                    self.checkpoint.save(state)
                entry.update({
                    "status": "completed",
                    "source_start": chapter.start,
                    "source_end": chapter.end,
                })
                chapters_state[chapter.chapter_id] = entry
                state["memory"] = memory.to_dict()
                self.checkpoint.save(state)
            except Exception as exc:  # noqa: BLE001 - stage is resumable
                entry.update({
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:500],
                })
                chapters_state[chapter.chapter_id] = entry
                failed.append(chapter.chapter_id)
                self.checkpoint.save({**state, "memory": memory.to_dict()})
                # 一章失败不应中断整本书：保留 failed 状态，继续处理剩余章节；
                # 下次运行（断点续传）会对 failed 章节重试。
                continue

        completed = sum(
            item.get("status") == "completed" for item in chapters_state.values()
        )
        if not failed and completed == len(script.chapters) and not state.get("finalized"):
            self._report(progress_callback, "正在建立人物关系")
            final = self._finalize(
                source_sha,
                memory,
                source_text=source_text,
                chapters=script.chapters,
            )
            memory = self._merge(memory, final, source_sha)
            state["memory"] = memory.to_dict()
            state["finalized"] = True
            state["status"] = "completed"
            self.checkpoint.save(state)
            self._save_public_bible(memory)
        else:
            state["status"] = "partial" if failed else "running"
            self.checkpoint.save({**state, "memory": memory.to_dict()})
        return BookUnderstandingResult(
            bible=memory,
            completed_chapters=completed,
            failed_chapters=len(failed),
            failed_chapter_ids=failed,
            resumed=resumed,
        )

    def _read_chunk(
        self,
        *,
        source_sha: str,
        chapter,
        source_text: str,
        source_start: int,
        source_end: int,
        memory: CharacterBibleDocument,
        chapters,
    ) -> CharacterBibleDocument:
        method = getattr(self.adapter, "read_chapter", None)
        if method is None:
            method = getattr(self.adapter, "understand_chapter", None)
        if method is None:
            raise TypeError("AI book-understanding adapter needs read_chapter()")
        raw = method(
            source_sha256=source_sha,
            chapter_id=chapter.chapter_id,
            chapter_title=chapter.title,
            source_start=source_start,
            source_end=source_end,
            text=source_text[source_start:source_end],
            memory=memory.to_dict(),
        )
        return self._coerce(
            raw,
            source_sha,
            chapter.chapter_id,
            source_text=source_text,
            chapters=chapters,
        )

    def _finalize(
        self,
        source_sha: str,
        memory: CharacterBibleDocument,
        *,
        source_text: str,
        chapters,
    ) -> CharacterBibleDocument:
        method = getattr(self.adapter, "finalize", None)
        if method is None:
            method = getattr(self.adapter, "finalize_book", None)
        if method is None:
            return memory
        raw = method(source_sha256=source_sha, memory=memory.to_dict())
        return self._coerce(
            raw,
            source_sha,
            "final",
            source_text=source_text,
            chapters=chapters,
        )

    @staticmethod
    def _coerce(
        raw: Any,
        source_sha: str,
        chapter_id: str,
        *,
        source_text: str | None = None,
        chapters=None,
    ) -> CharacterBibleDocument:
        if isinstance(raw, CharacterBibleDocument):
            value = raw
        elif isinstance(raw, dict):
            payload = dict(raw)
            if isinstance(payload.get("memory"), dict):
                payload = dict(payload["memory"])
            payload.setdefault("schema_version", "character-bible-chapter-v1")
            payload.setdefault("source_sha256", source_sha)
            payload.setdefault("uncertain_entities", [])
            payload.setdefault("revision", 1)
            value = CharacterBibleDocument.from_dict(payload)
        else:
            raise TypeError("AI book-understanding response must be a JSON object")
        if value.source_sha256 != source_sha:
            raise ValueError(f"人物圣经 source SHA 与项目不匹配（{chapter_id}）")
        if source_text is not None:
            value = BookUnderstandingService._validate_evidence(
                value, source_text, chapters or []
            )
        return BookUnderstandingService._with_stable_ids(value)

    @staticmethod
    def _validate_evidence(
        bible: CharacterBibleDocument, source_text: str, chapters
    ) -> CharacterBibleDocument:
        """校验并修正证据坐标，返回修正后的 bible（语义不变：证据必须真实存在）。

        当证据文本与原文坐标不一致（AI 常给出轻微偏移的坐标）时，不直接判死：
        先在对应章节范围内查找证据文本（原文精确查找 → 空白规范化查找），
        找到则修正 ``source_start`` / ``source_end`` 并继续；找不到才抛错
        （真实性保护：证据必须真实存在于原文，只是允许坐标偏移）。
        """
        chapter_ranges = {
            item.chapter_id: (item.start, item.end) for item in chapters
        }
        characters: list = []
        for character in bible.characters:
            evidence = list(character.evidence)
            for index, item in enumerate(evidence):
                if item.source_start is None or item.source_end is None:
                    if item.text not in source_text:
                        raise ValueError(
                            f"人物证据不在原文中：{character.canonical_name}"
                        )
                    continue
                start, end = item.source_start, item.source_end
                if not 0 <= start < end <= len(source_text):
                    raise ValueError("人物证据坐标超出原文范围")
                chapter_range = chapter_ranges.get(item.chapter_id)
                if chapter_range and not (
                    chapter_range[0] <= start < end <= chapter_range[1]
                ):
                    raise ValueError("人物证据坐标不属于对应章节")
                if source_text[start:end] == item.text:
                    continue
                corrected = BookUnderstandingService._relocate_evidence(
                    item, source_text, chapter_range
                )
                if corrected is None:
                    raise ValueError("人物证据文本与原文不一致")
                evidence[index] = corrected
            characters.append(
                replace(character, evidence=evidence)
                if evidence != character.evidence
                else character
            )
        if characters == bible.characters:
            return bible
        return replace(bible, characters=characters)

    @staticmethod
    def _relocate_evidence(item, source_text: str, chapter_range) -> Any | None:
        """在章节范围内重新定位证据文本；找不到返回 None。

        策略 1：原文精确查找（``str.find``）；
        策略 2：空白规范化后查找（``re.sub(r"\\s+", "", text)``），再把规范化
        偏移映射回原始坐标——容忍 AI 对空白/标点边界的轻微偏差。
        """
        if chapter_range is None:
            chapter_start, chapter_end = 0, len(source_text)
        else:
            chapter_start, chapter_end = chapter_range
        raw_index = source_text.find(item.text, chapter_start, chapter_end)
        if raw_index != -1:
            return replace(
                item,
                source_start=raw_index,
                source_end=raw_index + len(item.text),
            )
        normalized = re.sub(r"\s+", "", item.text)
        if not normalized:
            return None
        chapter_slice = source_text[chapter_start:chapter_end]
        normalized_source = re.sub(r"\s+", "", chapter_slice)
        normalized_index = normalized_source.find(normalized)
        if normalized_index == -1:
            return None
        real_start = BookUnderstandingService._map_normalized_index(
            chapter_slice, normalized_index
        )
        real_end = BookUnderstandingService._map_normalized_index(
            chapter_slice, normalized_index + len(normalized)
        )
        if real_start is None or real_end is None or real_end <= real_start:
            return None
        return replace(
            item,
            source_start=chapter_start + real_start,
            source_end=chapter_start + real_end,
        )

    @staticmethod
    def _map_normalized_index(text: str, normalized_index: int) -> int | None:
        """把空白规范化后的偏移映射回原始文本偏移（跳过空白字符）。"""
        seen = 0
        for offset, char in enumerate(text):
            if char.isspace():
                continue
            if seen == normalized_index:
                return offset
            seen += 1
        if seen == normalized_index:
            return len(text)
        return None

    @staticmethod
    def _with_stable_ids(value: CharacterBibleDocument) -> CharacterBibleDocument:
        characters = [
            replace(
                item,
                speaker_id=item.speaker_id or stable_speaker_id(item.canonical_name),
            )
            for item in value.characters
        ]
        return replace(value, characters=characters)

    @staticmethod
    def _merge(
        current: CharacterBibleDocument,
        incoming: CharacterBibleDocument,
        source_sha: str,
    ) -> CharacterBibleDocument:
        by_id = {item.character_id: item for item in current.characters}
        name_to_id = {
            name: item.character_id
            for item in current.characters
            for name in [item.canonical_name, *item.aliases]
        }
        for incoming_item in incoming.characters:
            candidate = incoming_item
            existing_id = candidate.character_id if candidate.character_id in by_id else None
            if existing_id is None:
                for name in [candidate.canonical_name, *candidate.aliases]:
                    if name in name_to_id:
                        existing_id = name_to_id[name]
                        break
            if existing_id is None:
                by_id[candidate.character_id] = candidate
                for name in [candidate.canonical_name, *candidate.aliases]:
                    name_to_id[name] = candidate.character_id
                continue
            previous = by_id[existing_id]
            aliases = list(
                dict.fromkeys(
                    [
                        *previous.aliases,
                        previous.canonical_name,
                        *candidate.aliases,
                    ]
                )
            )
            aliases = [item for item in aliases if item != candidate.canonical_name]
            evidence = list(previous.evidence)
            seen_evidence = {
                (item.chapter_id, item.text, item.source_start, item.source_end)
                for item in evidence
            }
            for item in candidate.evidence:
                key = (item.chapter_id, item.text, item.source_start, item.source_end)
                if key not in seen_evidence:
                    evidence.append(item)
                    seen_evidence.add(key)
            relationships = list(previous.relationships)
            relationship_keys = {
                (item.character_id, item.relation) for item in relationships
            }
            for item in candidate.relationships:
                if (item.character_id, item.relation) not in relationship_keys:
                    relationships.append(item)
                    relationship_keys.add((item.character_id, item.relation))
            by_id[existing_id] = replace(
                candidate,
                character_id=existing_id,
                speaker_id=previous.speaker_id or candidate.speaker_id,
                aliases=aliases,
                evidence=evidence,
                relationships=relationships,
                confidence=max(previous.confidence, candidate.confidence),
            )
            for name in [candidate.canonical_name, *candidate.aliases, *aliases]:
                name_to_id[name] = existing_id
        uncertain = list(current.uncertain_entities)
        seen_uncertain = {repr(item) for item in uncertain}
        for item in incoming.uncertain_entities:
            if repr(item) not in seen_uncertain:
                uncertain.append(item)
                seen_uncertain.add(repr(item))
        result = CharacterBibleDocument(
            source_sha256=source_sha,
            characters=list(by_id.values()),
            uncertain_entities=uncertain,
            revision=max(current.revision, incoming.revision) + 1,
            schema_version="character-bible-final-v1",
        )
        result.validate()
        return result

    @staticmethod
    def _load_memory(raw: Any, source_sha: str) -> CharacterBibleDocument:
        if not isinstance(raw, dict):
            return CharacterBibleDocument(source_sha256=source_sha)
        try:
            return BookUnderstandingService._with_stable_ids(
                CharacterBibleDocument.from_dict(raw)
            )
        except (TypeError, ValueError):
            return CharacterBibleDocument(source_sha256=source_sha)

    def _save_public_bible(self, bible: CharacterBibleDocument) -> None:
        # The public copy contains only validated semantic memory and evidence;
        # the original source remains source/source.txt.
        atomic_write_json(
            self.checkpoint.path.parent.parent / "character_bible.json",
            bible.to_dict(),
        )

    def _fingerprint(self, source_sha: str) -> str:
        value = f"{source_sha}:{getattr(self.adapter, 'name', '')}:{getattr(self.adapter, 'model', '')}:{self.max_input_chars}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _report(callback, message: str) -> None:
        if callback is not None:
            callback(message)
