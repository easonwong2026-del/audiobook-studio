"""Resumable end-to-end V4 character analysis orchestration.

The pipeline owns the user-facing order of operations.  Individual services
remain usable for the advanced workbench, but a newly imported project only
needs this one entry point.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai.character_consolidation import create_character_consolidation_adapter
from ai.character_extraction import create_character_extraction_adapter
from ai.speaker_routing import create_speaker_routing_adapter
from domain.v4 import ScriptDocument, SpeakersDocument
from domain.v4.character_extraction import CharacterCandidatesDocument
from repositories.character_candidates_repository import CharacterCandidatesRepository
from repositories.character_consolidation_checkpoint_repository import (
    CharacterConsolidationCheckpointRepository,
)
from repositories.character_extraction_checkpoint_repository import (
    CharacterExtractionCheckpointRepository,
)
from repositories.project_v4_repository import ProjectV4Repository
from repositories.routing_checkpoint_repository import RoutingCheckpointRepository
from repositories.v4_analysis_repository import V4AnalysisRepository
from repositories.v4_atomic import atomic_write_json
from services.ai_settings import AiSettingsService
from services.character_consistency_service import (
    CharacterConsistencyResult,
    CharacterConsistencyService,
)
from services.character_consolidation_service import (
    CharacterConsolidationResult,
    CharacterConsolidationService,
)
from services.character_extraction_service import (
    CharacterExtractionResult,
    CharacterExtractionService,
    merge_character_candidates,
)
from services.speaker_routing_service import RoutingResult, SpeakerRoutingService
from services.v4_analysis_config import (
    DEFAULT_V4_ANALYSIS_CONFIG,
    V4AnalysisConfig,
)

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class V4AnalysisResult:
    status: str
    script: ScriptDocument
    speakers: SpeakersDocument
    candidates: CharacterCandidatesDocument
    summary: dict[str, Any]
    errors: list[str]
    message: str


class V4ProjectAnalysisPipeline:
    """Run all AI stages while persisting a checkpoint after each boundary."""

    STAGES = (
        "character_extraction",
        "character_consolidation",
        "auto_confirmation",
        "speaker_routing",
        "consistency_check",
    )

    def __init__(
        self,
        project_path: str | Path,
        *,
        character_extraction_adapter: Any | None = None,
        character_consolidation_adapter: Any | None = None,
        speaker_routing_adapter: Any | None = None,
        config: V4AnalysisConfig = DEFAULT_V4_ANALYSIS_CONFIG,
        ai_configured: bool = True,
        configuration_message: str = "",
    ):
        self.project_path = Path(project_path)
        self.character_extraction_adapter = character_extraction_adapter
        self.character_consolidation_adapter = character_consolidation_adapter
        self.speaker_routing_adapter = speaker_routing_adapter
        self.config = config
        self.ai_configured = ai_configured
        self.configuration_message = configuration_message
        self.analysis_repository = V4AnalysisRepository(self.project_path)
        self.project_repository = ProjectV4Repository(self.project_path.parent)

    @classmethod
    def from_ai_settings(
        cls,
        project_path: str | Path,
        *,
        config: V4AnalysisConfig = DEFAULT_V4_ANALYSIS_CONFIG,
    ) -> V4ProjectAnalysisPipeline:
        project = Path(project_path)
        try:
            values = AiSettingsService.get_effective_provider_config()
        except Exception as exc:  # noqa: BLE001 - configuration is user input
            return cls(
                project,
                config=config,
                ai_configured=False,
                configuration_message=f"AI 配置读取失败：{str(exc)[:300]}",
            )
        provider = str(values.get("provider") or "local").strip().lower()
        api_key = str(values.get("api_key") or "").strip()
        if provider not in {"deepseek", "openai"} or not api_key:
            return cls(
                project,
                config=config,
                ai_configured=False,
                configuration_message=(
                    "AI 尚未配置。项目已创建，但角色自动分析未执行。\n"
                    "请配置 DeepSeek 或 OpenAI 后点击“继续分析”。"
                ),
            )
        common = {
            "api_key": api_key,
            "model": values.get("model") or "",
            "base_url": values.get("base_url") or "",
            "timeout": values.get("timeout", 180),
        }
        try:
            return cls(
                project,
                character_extraction_adapter=create_character_extraction_adapter(
                    provider, **common
                ),
                character_consolidation_adapter=create_character_consolidation_adapter(
                    provider, **common
                ),
                speaker_routing_adapter=create_speaker_routing_adapter(
                    provider, **common
                ),
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - do not lose imported project
            return cls(
                project,
                config=config,
                ai_configured=False,
                configuration_message=f"AI 尚未准备好：{str(exc)[:300]}",
            )

    def run(self, progress_callback: ProgressCallback | None = None) -> V4AnalysisResult:
        source = (self.project_path / "source/source.txt").read_text(encoding="utf-8")
        script = self._load_script(source)
        speakers = self._load_speakers()
        candidates_repository = CharacterCandidatesRepository(self.project_path)
        candidates = candidates_repository.load(script.source_sha256)
        if not self.ai_configured:
            summary = self._summary(
                script, speakers, candidates, None, None, filtered_noise=0,
                auto_confirmed_count=0, consolidation=None,
            )
            summary["analysis_status"] = "waiting_for_ai"
            state = {
                "source_sha256": script.source_sha256,
                "status": "waiting_for_ai",
                "current_stage": "awaiting_ai",
                "provider": "",
                "stages": {},
                "summary": summary,
                "errors": [self.configuration_message],
                "message": self.configuration_message,
            }
            self.analysis_repository.save(state)
            self._report(progress_callback, self.configuration_message)
            return V4AnalysisResult(
                "waiting_for_ai", script, speakers, candidates, summary,
                [self.configuration_message], self.configuration_message,
            )

        state = self.analysis_repository.start(
            script.source_sha256,
            provider=getattr(self.character_extraction_adapter, "name", ""),
        )
        errors: list[str] = []
        extraction: CharacterExtractionResult | None = None
        consolidation: CharacterConsolidationResult | None = None
        routing: RoutingResult | None = None
        consistency: CharacterConsistencyResult | None = None

        self._report(progress_callback, "正在提取角色")
        self._stage(state, "character_extraction", "running")
        if self.character_extraction_adapter is None:
            self._stage(state, "character_extraction", "skipped")
        else:
            try:
                extraction = CharacterExtractionService(
                    self.character_extraction_adapter,
                    CharacterExtractionCheckpointRepository(
                        self.project_path / "runtime/character_extraction"
                    ),
                    candidates_repository,
                ).extract(source, script)
                candidates = extraction.candidates
                if extraction.failed_chapters:
                    errors.append(
                        f"角色提取有 {extraction.failed_chapters} 个章节失败，可继续分析重试。"
                    )
                self._stage(
                    state,
                    "character_extraction",
                    "completed" if extraction.failed_chapters == 0 else "partial",
                    completed_chapters=extraction.completed_chapters,
                    failed_chapters=extraction.failed_chapters,
                    filtered_noise=extraction.filtered_noise_count,
                )
            except Exception as exc:  # noqa: BLE001 - preserve imported project
                errors.append(f"角色提取失败：{str(exc)[:500]}")
                self._stage(state, "character_extraction", "failed", error=errors[-1])

        self._report(progress_callback, "正在统一角色和别名")
        self._stage(state, "character_consolidation", "running")
        if not candidates.candidates:
            self._stage(state, "character_consolidation", "completed", groups=0)
        else:
            try:
                consolidation = CharacterConsolidationService(
                    self.character_consolidation_adapter,
                    CharacterConsolidationCheckpointRepository(
                        self.project_path / "runtime/character_consolidation"
                    ),
                    auto_confirm_threshold=self.config.auto_confirm_threshold,
                ).consolidate(
                    script.source_sha256, candidates, speakers
                )
                candidates = consolidation.candidates
                if consolidation.speakers != speakers:
                    self.project_repository.save_script_and_speakers(
                        self.project_path, source, script, consolidation.speakers
                    )
                    speakers = consolidation.speakers
                if candidates != candidates_repository.load(script.source_sha256):
                    candidates_repository.save(candidates)
                self._stage(
                    state,
                    "character_consolidation",
                    "completed",
                    groups=len(consolidation.response.characters),
                    unresolved_groups=len(consolidation.response.unresolved_groups),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"全书角色统一失败：{str(exc)[:500]}")
                self._stage(state, "character_consolidation", "failed", error=errors[-1])

        self._report(progress_callback, "正在自动确认高可信角色")
        self._stage(state, "auto_confirmation", "running")
        if consolidation is None:
            self._stage(state, "auto_confirmation", "skipped", confirmed=0)
        else:
            self._stage(
                state,
                "auto_confirmation",
                "completed",
                confirmed=len(consolidation.auto_confirmed),
            )

        self._report(progress_callback, "正在分析对白归属")
        self._stage(state, "speaker_routing", "running")
        if self.speaker_routing_adapter is None:
            self._stage(state, "speaker_routing", "skipped")
        else:
            try:
                routing = SpeakerRoutingService(
                    self.speaker_routing_adapter,
                    RoutingCheckpointRepository(self.project_path / "runtime/runtime.db"),
                    batch_size=self.config.routing_batch_size,
                    context_radius=self.config.routing_context_radius,
                    scene_gap=self.config.routing_scene_gap,
                    previous_speaker_limit=self.config.routing_previous_speaker_limit,
                    min_confidence=self.config.routing_min_confidence,
                ).route(source, script, speakers)
                if routing.script != script or routing.speakers != speakers:
                    self.project_repository.save_script_and_speakers(
                        self.project_path, source, routing.script, routing.speakers
                    )
                    script, speakers = routing.script, routing.speakers
                if routing.candidates:
                    current = candidates_repository.load(script.source_sha256)
                    merged = merge_character_candidates(
                        current.candidates, routing.candidates
                    )
                    if merged != current.candidates:
                        candidates = CharacterCandidatesDocument(
                            source_sha256=script.source_sha256,
                            candidates=merged,
                            revision=current.revision + 1,
                        )
                        candidates_repository.save(candidates)
                else:
                    candidates = candidates_repository.load(script.source_sha256)
                if routing.failed_batches:
                    errors.append(
                        f"对白归属有 {routing.failed_batches} 个批次失败，可继续分析重试。"
                    )
                self._stage(
                    state,
                    "speaker_routing",
                    "completed" if routing.failed_batches == 0 else "partial",
                    completed_batches=routing.completed_batches,
                    failed_batches=routing.failed_batches,
                    unresolved_segments=routing.unresolved_segments,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"对白归属失败：{str(exc)[:500]}")
                self._stage(state, "speaker_routing", "failed", error=errors[-1])

        self._report(progress_callback, "正在检查分析结果")
        self._stage(state, "consistency_check", "running")
        try:
            consistency = CharacterConsistencyService(self.config).check(
                source, script, speakers, candidates
            )
            if consistency.script != script:
                self.project_repository.save_script_and_speakers(
                    self.project_path, source, consistency.script, speakers
                )
                script = consistency.script
            atomic_write_json(
                self.project_path / "runtime/character_consistency.json",
                {
                    "schema_version": "character-consistency-v1",
                    "source_sha256": script.source_sha256,
                    "script_revision": script.revision,
                    **consistency.to_dict(),
                },
            )
            self._stage(
                state,
                "consistency_check",
                "completed",
                auto_fixed=len(consistency.auto_fixed),
                ai_review=len(consistency.ai_review),
                user_review=len(consistency.user_review),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"一致性检查失败：{str(exc)[:500]}")
            self._stage(state, "consistency_check", "failed", error=errors[-1])

        summary = self._summary(
            script,
            speakers,
            candidates,
            routing,
            consistency,
            filtered_noise=(extraction.filtered_noise_count if extraction else 0),
            auto_confirmed_count=(len(consolidation.auto_confirmed) if consolidation else 0),
            consolidation=consolidation,
        )
        summary["analysis_status"] = "partial" if errors else "completed"
        status = "partial" if errors else "completed"
        message = self._message(status, summary, errors)
        state.update(
            {
                "source_sha256": script.source_sha256,
                "status": status,
                "current_stage": "completed" if not errors else "needs_attention",
                "summary": summary,
                "errors": errors,
                "message": message,
            }
        )
        self.analysis_repository.save(state)
        self._report(progress_callback, "分析完成" if not errors else "分析完成（有阶段需要继续）")
        return V4AnalysisResult(
            status, script, speakers, candidates, summary, errors, message
        )

    def _load_script(self, source: str) -> ScriptDocument:
        import json

        return ScriptDocument.from_dict(
            json.loads((self.project_path / "script/script.json").read_text(encoding="utf-8")),
            source,
        )

    def _load_speakers(self) -> SpeakersDocument:
        import json

        return SpeakersDocument.from_dict(
            json.loads(
                (self.project_path / "script/speakers.json").read_text(encoding="utf-8")
            )
        )

    def _stage(self, state: dict[str, Any], name: str, status: str, **extra: Any) -> None:
        value = {"status": status, **extra}
        state.setdefault("stages", {})[name] = value
        state["current_stage"] = name
        self.analysis_repository.save(state)

    @staticmethod
    def _report(callback: ProgressCallback | None, message: str) -> None:
        if callback is None:
            return
        try:
            callback(message)
        except TypeError:
            callback(str(message))

    @staticmethod
    def _summary(
        script: ScriptDocument,
        speakers: SpeakersDocument,
        candidates: CharacterCandidatesDocument,
        routing: RoutingResult | None,
        consistency: CharacterConsistencyResult | None,
        *,
        filtered_noise: int = 0,
        auto_confirmed_count: int = 0,
        consolidation: CharacterConsolidationResult | None = None,
    ) -> dict[str, Any]:
        dialogue = [
            segment
            for chapter in script.chapters
            for segment in chapter.segments
            if segment.kind == "dialogue" and segment.dialogue_type != "quotation"
        ]
        auto_routed = sum(item.speaker_source == "router" for item in dialogue)
        unresolved = sum(item.status == "unresolved" for item in dialogue)
        groups = consolidation.response.characters if consolidation else []
        character_cards: dict[str, dict[str, Any]] = {}
        for speaker in speakers.speakers:
            if speaker.speaker_type != "character":
                continue
            group = next(
                (
                    item
                    for item in groups
                    if speaker.display_name in [item.canonical_name, *item.aliases]
                ),
                None,
            )
            character_cards[speaker.speaker_id] = {
                "importance": (
                    "主要角色"
                    if (group and group.importance == "major")
                    or sum(
                        segment.speaker_id == speaker.speaker_id
                        for chapter in script.chapters
                        for segment in chapter.segments
                        if segment.kind == "dialogue"
                    ) >= 3
                    else "次要角色"
                ),
                "dialogue_count": sum(
                    segment.speaker_id == speaker.speaker_id
                    for chapter in script.chapters
                    for segment in chapter.segments
                    if segment.kind == "dialogue"
                ),
                "confidence": group.confidence if group else 1.0,
            }
        return {
            "identified_characters": sum(
                item.speaker_type == "character" and item.status == "confirmed"
                for item in speakers.speakers
            ),
            "auto_confirmed_characters": int(auto_confirmed_count),
            "needs_review_characters": sum(
                item.status == "candidate" for item in candidates.candidates
            ),
            "filtered_noise": int(filtered_noise),
            "dialogue_total": len(dialogue),
            "dialogue_auto_routed": auto_routed,
            "dialogue_unresolved": unresolved,
            "dialogue_coverage": (auto_routed / len(dialogue)) if dialogue else 1.0,
            "character_cards": character_cards,
            "consistency_auto_fixed": len(consistency.auto_fixed) if consistency else 0,
            "consistency_ai_review": len(consistency.ai_review) if consistency else 0,
            "consistency_user_review": len(consistency.user_review) if consistency else 0,
            "source_sha256": script.source_sha256,
        }

    @staticmethod
    def _message(status: str, summary: dict[str, Any], errors: list[str]) -> str:
        prefix = "✅ 分析完成" if status == "completed" else "⚠ 分析完成，但有阶段需要继续"
        lines = [
            prefix,
            f"识别角色：{summary.get('identified_characters', 0)}",
            f"自动确认：{summary.get('auto_confirmed_characters', 0)}",
            f"需要检查：{summary.get('needs_review_characters', 0) + summary.get('dialogue_unresolved', 0)}",
            f"已过滤噪音：{summary.get('filtered_noise', 0)}",
            f"对白自动归属：{summary.get('dialogue_coverage', 1.0) * 100:.0f}%",
        ]
        lines.extend(f"- {item}" for item in errors)
        return "\n".join(lines)
