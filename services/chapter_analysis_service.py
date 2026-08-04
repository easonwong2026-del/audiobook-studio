"""Default V4 one-chapter analysis workflow.

This service intentionally owns only the fast path.  The existing
``V4ProjectAnalysisPipeline`` remains available as the advanced full-book
AI-first workflow and is never consulted by this service.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from ai.chapter_analysis import create_chapter_analysis_adapter
from ai.providers.exceptions import (
    ProviderOutputInvalidJsonError,
    ProviderOutputTruncatedError,
)
from domain.v4 import (
    ChapterAnalysisResponse,
    ChapterScript,
    ScriptDocument,
    SemanticSegment,
    Speaker,
    SpeakersDocument,
    ValidationError,
)
from domain.v4.chapter_analysis import ChapterAnalysisRequest
from domain.v4.models import stable_speaker_id
from repositories.chapter_analysis_repository import ChapterAnalysisStateRepository
from repositories.production_repository import ProductionRepository
from repositories.project_v4_repository import ProjectV4Repository

from services.ai_settings import AiSettingsService
from services.chapter_analysis_validator import (
    ChapterAnalysisValidationError,
    ChapterAnalysisValidator,
    ValidatedChapterAnalysis,
)

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class ChapterAnalysisResult:
    status: str
    script: ScriptDocument
    speakers: SpeakersDocument
    summary: dict[str, Any]
    errors: list[str]
    message: str
    chapter_id: str = ""
    state: dict[str, Any] | None = None


class ChapterAnalysisService:
    """Analyze one already-selected chapter with at most one repair call."""

    _locks: ClassVar[dict[str, threading.Lock]] = {}
    _locks_guard = threading.Lock()

    def __init__(
        self,
        project_path: str | Path,
        adapter: Any | None = None,
        *,
        provider: str = "",
        model: str = "",
        configuration_message: str = "",
        project_repository: ProjectV4Repository | None = None,
        state_repository: ChapterAnalysisStateRepository | None = None,
        validator: ChapterAnalysisValidator | None = None,
    ):
        self.project_path = Path(project_path)
        self.adapter = adapter
        self.provider = provider
        self.model = model
        self.configuration_message = configuration_message
        self.project_repository = project_repository or ProjectV4Repository(
            self.project_path.parent
        )
        self.state_repository = state_repository or ChapterAnalysisStateRepository(
            self.project_path
        )
        self.validator = validator or ChapterAnalysisValidator()

    @classmethod
    def from_ai_settings(cls, project_path: str | Path) -> ChapterAnalysisService:
        project = Path(project_path)
        try:
            values = AiSettingsService.get_effective_provider_config()
        except Exception as exc:  # noqa: BLE001 - configuration is user input
            return cls(
                project,
                configuration_message=f"AI 配置读取失败：{str(exc)[:300]}",
            )
        provider = str(values.get("provider") or "local").strip().lower()
        api_key = str(values.get("api_key") or "").strip()
        if provider not in {"deepseek", "openai"} or not api_key:
            return cls(
                project,
                configuration_message=(
                    "AI 尚未配置。章节已保存；请配置 DeepSeek 或 OpenAI 后，"
                    "点击“快速分析当前章节”。"
                ),
            )
        try:
            adapter = create_chapter_analysis_adapter(
                provider,
                api_key=api_key,
                model=values.get("model") or "",
                base_url=values.get("base_url") or "",
                timeout=values.get("timeout", 180),
            )
            return cls(
                project,
                adapter,
                provider=adapter.name,
                model=adapter.model,
            )
        except Exception as exc:  # noqa: BLE001 - keep imported project usable
            return cls(
                project,
                configuration_message=f"AI 尚未准备好：{str(exc)[:300]}",
            )

    def analyze(
        self,
        *,
        chapter_id: str | None = None,
        force: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> ChapterAnalysisResult:
        from services.service_lifecycle import ServiceLifecycle

        if ServiceLifecycle.is_stopping():
            raise RuntimeError("Audiobook Studio 服务正在关闭，章节分析已停止")
        source = (self.project_path / "source/source.txt").read_text(encoding="utf-8")
        script = self._load_script(source)
        speakers = self._load_speakers()
        chapter = self._select_chapter(script, chapter_id)
        chapter_id = chapter.chapter_id
        lock = self._chapter_lock(
            f"{self.project_path.resolve()}::{chapter_id}"
        )
        if not lock.acquire(blocking=False):
            state = self.state_repository.load(chapter_id) or {
                "chapter_id": chapter_id,
                "status": "analyzing",
                "analysis_mode": "chapter-fast",
                "provider": self.provider,
                "model": self.model,
                "ai_requests": 0,
                "retries": 0,
                "message": "当前章节正在分析，请勿重复提交。",
            }
            return self._result(
                "analyzing",
                script,
                speakers,
                chapter_id,
                state.get("message", "当前章节正在分析，请勿重复提交。"),
                [],
                state,
            )

        try:
            state = self.state_repository.load(chapter_id)
            if (
                not force
                and state is not None
                and state.get("source_sha256") == script.source_sha256
                and state.get("status") in {"analyzed", "ready_for_synthesis", "completed"}
            ):
                return self._result(
                    state["status"],
                    script,
                    speakers,
                    chapter_id,
                    state.get("message", "当前章节已分析。"),
                    list(state.get("errors") or []),
                    state,
                )
            if self.adapter is None:
                state = self._save_state(
                    chapter_id,
                    source_sha256=script.source_sha256,
                    status="waiting_for_ai",
                    ai_requests=0,
                    retries=0,
                    message=self.configuration_message
                    or "AI 尚未配置，请配置后继续分析当前章节。",
                    errors=[self.configuration_message]
                    if self.configuration_message
                    else [],
                )
                return self._result(
                    "waiting_for_ai",
                    script,
                    speakers,
                    chapter_id,
                    state["message"],
                    list(state.get("errors") or []),
                    state,
                )

            chapter_text = source[chapter.start:chapter.end]
            known_characters = self._known_characters(speakers)
            request = ChapterAnalysisRequest(
                chapter_id=chapter_id,
                chapter_title=chapter.title or "当前章节",
                known_characters=known_characters,
                chapter_text=chapter_text,
            )
            request.validate()
            state = self._save_state(
                chapter_id,
                source_sha256=script.source_sha256,
                status="analyzing",
                ai_requests=0,
                retries=0,
                started_at=datetime.now(timezone.utc).isoformat(),
                message="正在分析当前章节（1/3）",
            )
            self._report(progress_callback, "正在分析当前章节（1/3）")
            raw_first: dict[str, Any] = {}
            errors: list[str] = []
            validated: ValidatedChapterAnalysis | None = None
            for attempt in range(2):
                if attempt == 1:
                    state = self._save_state(
                        chapter_id,
                        source_sha256=script.source_sha256,
                        status="analyzing",
                        ai_requests=1,
                        retries=1,
                        message="正在修复章节结果（重试 1/1）",
                        errors=errors,
                    )
                    self._report(progress_callback, "正在修复章节结果（重试 1/1）")
                try:
                    output = self.adapter.analyze_chapter(
                        chapter_id=request.chapter_id,
                        chapter_title=request.chapter_title,
                        known_characters=request.known_characters,
                        chapter_text=request.chapter_text,
                        previous_response=raw_first if attempt else None,
                        errors=errors if attempt else None,
                    )
                    raw = output.to_dict() if isinstance(output, ChapterAnalysisResponse) else output
                    raw_first = raw if isinstance(raw, dict) else {}
                    normalized, allowed = self._normalize_response(
                        raw, speakers, known_characters
                    )
                    self._report(progress_callback, "正在校验章节结果（2/3）")
                    validated = self.validator.validate(
                        normalized,
                        chapter_id=chapter_id,
                        source_text=chapter_text,
                        allowed_speaker_ids=allowed,
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - convert to visible state
                    errors = self._error_list(exc)
                    if attempt == 0 and self._repairable(exc):
                        continue
                    reason = (
                        "chapter_analysis_invalid"
                        if attempt == 1 and self._repairable(exc)
                        else self._reason_code(exc)
                    )
                    status = "needs_attention" if reason == "chapter_analysis_invalid" else "failed"
                    state = self._save_state(
                        chapter_id,
                        source_sha256=script.source_sha256,
                        status=status,
                        ai_requests=attempt + 1,
                        retries=attempt,
                        reason_code=reason,
                        message=self._failure_message(status, reason, errors),
                        errors=errors,
                    )
                    return self._result(
                        status,
                        script,
                        speakers,
                        chapter_id,
                        state["message"],
                        errors,
                        state,
                    )
            if validated is None:
                raise RuntimeError("章节分析没有得到可用结果")
            self._report(progress_callback, "正在保存章节结果（3/3）")
            updated_script, updated_speakers, summary = self._apply(
                source,
                script,
                speakers,
                chapter,
                validated,
            )
            self.project_repository.save_script_and_speakers(
                self.project_path,
                source,
                updated_script,
                updated_speakers,
            )
            production = ProductionRepository(self.project_path)
            voices, _performance, _pronunciation, _profile = production.load_inputs()
            selected = next(
                item for item in updated_script.chapters if item.chapter_id == chapter_id
            )
            unresolved = sum(item.status == "unresolved" for item in selected.segments)
            unbound = sorted(
                {
                    item.speaker_id
                    for item in selected.segments
                    if item.speaker_id and item.speaker_id not in voices.bindings
                }
            )
            summary.update(
                {
                    "unresolved_segments": unresolved,
                    "unbound_speakers": unbound,
                    "ai_requests": 2 if state.get("retries", 0) else 1,
                    "retries": state.get("retries", 0),
                    "analysis_mode": "chapter-fast",
                }
            )
            final_status = "ready_for_synthesis" if unresolved == 0 and not unbound else "analyzed"
            message = (
                f"✅ 当前章节分析完成：{len(selected.segments)} 个片段，"
                f"识别角色 {summary['identified_characters']}；"
                + ("可绑定音色后合成。" if final_status == "analyzed" else "已可生成本章合成计划。")
            )
            state = self._save_state(
                chapter_id,
                source_sha256=updated_script.source_sha256,
                status=final_status,
                provider=self.provider,
                model=self.model,
                ai_requests=2 if state.get("retries", 0) else 1,
                retries=state.get("retries", 0),
                completed_at=datetime.now(timezone.utc).isoformat(),
                message=message,
                errors=[],
                stats=summary,
            )
            return self._result(
                final_status,
                updated_script,
                updated_speakers,
                chapter_id,
                message,
                [],
                state,
                summary,
            )
        finally:
            lock.release()

    def _apply(
        self,
        source: str,
        script: ScriptDocument,
        speakers: SpeakersDocument,
        chapter: ChapterScript,
        validated: ValidatedChapterAnalysis,
    ) -> tuple[ScriptDocument, SpeakersDocument, dict[str, Any]]:
        updated_speakers = self._merge_speakers(speakers, validated.response)
        speaker_map = {item.speaker_id: item for item in updated_speakers.speakers}
        old_protected = [
            segment
            for segment in chapter.segments
            if segment.speaker_source == "manual"
            or (
                segment.speaker_id
                and speaker_map.get(segment.speaker_id) is not None
                and speaker_map[segment.speaker_id].locked
            )
        ]
        segments: list[SemanticSegment] = []
        for match in validated.segments:
            item = match.item
            kind = (
                "narration"
                if item.segment_type in {"narration", "stage_direction"}
                else "dialogue"
            )
            speaker_id = item.speaker_id
            speaker = speaker_map.get(speaker_id) if speaker_id else None
            if kind == "narration" and speaker_id is None:
                speaker_id = "narrator"
                speaker = speaker_map.get("narrator")
            if speaker is None and speaker_id is not None:
                speaker_id = None
            status = "confirmed" if speaker_id and speaker and speaker.status == "confirmed" else "unresolved"
            segment = SemanticSegment(
                segment_id=f"segment_{chapter.chapter_id}_{item.index:06d}",
                chapter_id=chapter.chapter_id,
                start=chapter.start + match.source_start,
                end=chapter.start + match.source_end,
                kind=kind,
                speaker_id=speaker_id if status == "confirmed" else None,
                speaker_source="ai" if status == "confirmed" else "unresolved",
                status=status,
                dialogue_type=item.segment_type,
                confidence=item.confidence,
                emotion=item.emotion,
            )
            segment = self._protect_manual(segment, old_protected)
            segments.append(segment)
        replacement = replace(chapter, segments=segments)
        updated_script = replace(
            script,
            chapters=[
                replacement if item.chapter_id == chapter.chapter_id else item
                for item in script.chapters
            ],
            revision=script.revision + 1,
        )
        updated_script.validate(source)
        updated_speakers.validate()
        summary = {
            "identified_characters": len(validated.response.character_updates),
            "segments": len(segments),
            "dialogue_segments": sum(item.kind == "dialogue" for item in segments),
            "coverage": 1.0,
        }
        return updated_script, updated_speakers, summary

    @staticmethod
    def _protect_manual(
        segment: SemanticSegment, protected: list[SemanticSegment]
    ) -> SemanticSegment:
        for old in protected:
            if old.start <= segment.start and old.end >= segment.end:
                return replace(
                    segment,
                    speaker_id=old.speaker_id,
                    speaker_source=old.speaker_source,
                    status=old.status,
                    kind=old.kind,
                    dialogue_type=old.dialogue_type,
                )
        return segment

    @staticmethod
    def _merge_speakers(
        speakers: SpeakersDocument, response: ChapterAnalysisResponse
    ) -> SpeakersDocument:
        current = {item.speaker_id: item for item in speakers.speakers}
        known_ids = set(current)
        by_name = {
            name: item.speaker_id
            for item in speakers.speakers
            for name in [item.display_name, *item.aliases]
        }
        for update in response.character_updates:
            target = update.character_id
            if not target or target not in current:
                target = by_name.get(update.canonical_name)
            if not target:
                target = stable_speaker_id(update.canonical_name)
            previous = current.get(target)
            if previous is not None:
                if previous.locked:
                    continue
                aliases = list(previous.aliases)
                for alias in [previous.display_name, *update.aliases]:
                    if alias and alias != update.canonical_name and alias not in aliases:
                        aliases.append(alias)
                current[target] = replace(
                    previous,
                    display_name=update.canonical_name,
                    aliases=aliases,
                    status="confirmed" if update.confidence >= 0.75 else previous.status,
                )
                by_name.update(
                    {name: target for name in [update.canonical_name, *update.aliases]}
                )
                continue
            status = "confirmed" if update.confidence >= 0.75 else "unresolved"
            current[target] = Speaker(
                speaker_id=target,
                display_name=update.canonical_name,
                aliases=list(update.aliases),
                status=status,
                speaker_type="character",
            )
            known_ids.add(target)
            by_name.update(
                {name: target for name in [update.canonical_name, *update.aliases]}
            )
        # Keep narrator first, then retain the user's existing ordering and
        # append newly introduced stable IDs.
        ordered = [current[item.speaker_id] for item in speakers.speakers]
        ordered.extend(
            current[item]
            for item in sorted(current)
            if item not in {speaker.speaker_id for speaker in speakers.speakers}
        )
        return replace(
            speakers,
            speakers=ordered,
            revision=speakers.revision + (ordered != speakers.speakers),
        )

    @staticmethod
    def _normalize_response(
        raw: dict[str, Any],
        speakers: SpeakersDocument,
        known_characters: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], set[str]]:
        response = ChapterAnalysisResponse.from_dict(raw)
        known = {item.speaker_id: item for item in speakers.speakers}
        by_name = {
            name: item.speaker_id
            for item in speakers.speakers
            for name in [item.display_name, *item.aliases]
        }
        new_mapping: dict[str, str] = {}
        updates = []
        for update in response.character_updates:
            target = update.character_id if update.character_id in known else None
            target = target or by_name.get(update.canonical_name)
            is_new = update.is_new
            if target is None:
                target = stable_speaker_id(update.canonical_name)
                is_new = True
            if update.character_id:
                new_mapping[update.character_id] = target
            new_mapping[f"new:{update.canonical_name}"] = target
            updates.append(replace(update, character_id=target, is_new=is_new))
        segments = []
        for item in response.segments:
            target = item.speaker_id
            if target in new_mapping:
                target = new_mapping[target]
            elif target is None and item.speaker_name:
                target = by_name.get(item.speaker_name) or new_mapping.get(
                    f"new:{item.speaker_name}"
                )
            segments.append(replace(item, speaker_id=target))
        normalized = replace(response, character_updates=updates, segments=segments)
        allowed = set(known) | {
            item.character_id
            for item in updates
            if item.character_id is not None
        }
        return normalized.to_dict(), allowed

    def _known_characters(self, speakers: SpeakersDocument) -> list[dict[str, Any]]:
        try:
            voices, _performance, _pronunciation, _profile = ProductionRepository(
                self.project_path
            ).load_inputs()
            bound = voices.bindings
        except (OSError, KeyError, ValueError):
            bound = {}
        return [
            {
                "character_id": item.speaker_id,
                "name": item.display_name,
                "aliases": list(item.aliases),
                "voice_bound": item.speaker_id in bound,
            }
            for item in speakers.speakers
        ]

    def _save_state(self, chapter_id: str, **values: Any) -> dict[str, Any]:
        state = self.state_repository.load(chapter_id) or {"chapter_id": chapter_id}
        state.update(
            {
                "analysis_mode": "chapter-fast",
                "provider": self.provider,
                "model": self.model,
                **values,
            }
        )
        return self.state_repository.save(state)

    def _result(
        self,
        status: str,
        script: ScriptDocument,
        speakers: SpeakersDocument,
        chapter_id: str,
        message: str,
        errors: list[str],
        state: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> ChapterAnalysisResult:
        return ChapterAnalysisResult(
            status=status,
            script=script,
            speakers=speakers,
            summary=summary or dict(state.get("stats") or {}),
            errors=errors,
            message=message,
            chapter_id=chapter_id,
            state=state,
        )

    def _load_script(self, source: str) -> ScriptDocument:
        import json

        data = json.loads((self.project_path / "script/script.json").read_text(encoding="utf-8"))
        return ScriptDocument.from_dict(data, source)

    def _load_speakers(self) -> SpeakersDocument:
        import json

        data = json.loads((self.project_path / "script/speakers.json").read_text(encoding="utf-8"))
        return SpeakersDocument.from_dict(data)

    @staticmethod
    def _select_chapter(script: ScriptDocument, chapter_id: str | None) -> ChapterScript:
        if chapter_id:
            for chapter in script.chapters:
                if chapter.chapter_id == chapter_id:
                    return chapter
            raise ValueError(f"chapter not found: {chapter_id}")
        if not script.chapters:
            raise ValueError("project has no chapters")
        return next(
            (
                item
                for item in script.chapters
                if any(segment.status == "unresolved" for segment in item.segments)
            ),
            script.chapters[0],
        )

    @classmethod
    def _chapter_lock(cls, chapter_id: str) -> threading.Lock:
        with cls._locks_guard:
            return cls._locks.setdefault(chapter_id, threading.Lock())

    @staticmethod
    def _repairable(exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                ChapterAnalysisValidationError,
                ValidationError,
                ProviderOutputInvalidJsonError,
                ProviderOutputTruncatedError,
            ),
        )

    @staticmethod
    def _reason_code(exc: Exception) -> str:
        message = str(exc).lower()
        if "timeout" in message or "timed out" in message or "超时" in message:
            return "chapter_analysis_timeout"
        return "chapter_analysis_provider_error"

    @staticmethod
    def _error_list(exc: Exception) -> list[str]:
        if isinstance(exc, ChapterAnalysisValidationError):
            return [item[:300] for item in exc.errors[:8]]
        return [str(exc)[:300] or exc.__class__.__name__]

    @staticmethod
    def _failure_message(status: str, reason: str, errors: list[str]) -> str:
        if reason == "chapter_analysis_invalid":
            return "⚠ 当前章节分析结果连续两次未通过本地校验，请检查原文后重试。"
        if reason == "chapter_analysis_timeout":
            return "⚠ 当前章节分析超时，未写入不完整剧本；请检查网络或超时设置后重试。"
        return f"⚠ 当前章节分析失败：{errors[0] if errors else status}"

    @staticmethod
    def _report(callback: ProgressCallback | None, message: str) -> None:
        if callback is not None:
            callback(message)
