"""Resumable end-to-end V4 character analysis orchestration.

The pipeline owns the user-facing order of operations.  Individual services
remain usable for the advanced workbench, but a newly imported project only
needs this one entry point.

PR #22（AI-first 假成功修复）：``_run_ai_first`` 重构为 attempt 编排层——
单次三阶段执行后做结果有效性检查（``AnalysisValidityChecker``），可疑结果
自动重试一次（``force_restart=True``，重新请求模型、不读刚写空 checkpoint），
仍异常则写 ``needs_attention`` + 稳定 ``reason_code`` + 用户可读 message；
completed 缓存复用前先校验，可疑历史缓存自动失效转入可恢复重分析。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai.v4_script_director import create_ai_first_adapters
from domain.v4 import CharacterBibleDocument, ScriptDocument, SpeakersDocument
from domain.v4.character_extraction import (
    CharacterCandidate,
    CharacterCandidatesDocument,
    CharacterEvidence,
    stable_candidate_id,
)
from repositories.ai_first_checkpoint_repository import (
    BookUnderstandingCheckpointRepository,
    ScriptDirectorCheckpointRepository,
    ScriptReviewCheckpointRepository,
)
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
from services.ai_first_source import split_source_range
from services.ai_script_director_service import AIScriptDirectorService
from services.ai_script_review_service import AIScriptReviewService
from services.ai_settings import AiSettingsService
from services.book_understanding_service import BookUnderstandingService
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
from services.source_segmenter import SourceSegmenter
from services.speaker_routing_service import RoutingResult, SpeakerRoutingService
from services.v4_analysis_config import (
    DEFAULT_V4_ANALYSIS_CONFIG,
    V4AnalysisConfig,
)
from services.v4_analysis_validity import (
    AnalysisRunStats,
    AnalysisValidityChecker,
    CountingAdapterProxy,
    DIALOGUE_COVERAGE_UNKNOWN_LABEL,
    PIPELINE_VERSION,
    REASON_MESSAGES,
    ReasonCode,
    ValidityReport,
    compute_input_fingerprint,
)
from services.v4_reanalysis_service import (
    migrate_voice_bindings,
    protect_manual_assignments,
    reconcile_speakers,
    remap_script_speakers,
    snapshot_reanalysis,
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
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _AiFirstAttempt:
    """单次 AI-first 三阶段执行的产物（attempt 编排层的原子单元）。"""

    script: ScriptDocument
    speakers: SpeakersDocument
    candidates: CharacterCandidatesDocument
    bible: CharacterBibleDocument
    summary: dict[str, Any]
    state: dict[str, Any]
    errors: list[str]
    stats: AnalysisRunStats | None = None
    report: ValidityReport | None = None


class V4ProjectAnalysisPipeline:
    """Run all AI stages while persisting a checkpoint after each boundary."""

    STAGES = (
        "book_understanding",
        "script_director",
        "script_review",
    )

    def __init__(
        self,
        project_path: str | Path,
        *,
        character_extraction_adapter: Any | None = None,
        character_consolidation_adapter: Any | None = None,
        speaker_routing_adapter: Any | None = None,
        book_understanding_adapter: Any | None = None,
        script_director_adapter: Any | None = None,
        script_review_adapter: Any | None = None,
        config: V4AnalysisConfig = DEFAULT_V4_ANALYSIS_CONFIG,
        ai_configured: bool = True,
        configuration_message: str = "",
    ):
        self.project_path = Path(project_path)
        self.character_extraction_adapter = character_extraction_adapter
        self.character_consolidation_adapter = character_consolidation_adapter
        self.speaker_routing_adapter = speaker_routing_adapter
        self.book_understanding_adapter = book_understanding_adapter
        self.script_director_adapter = script_director_adapter
        self.script_review_adapter = script_review_adapter
        self.config = config
        self.ai_configured = ai_configured
        self.configuration_message = configuration_message
        self.analysis_repository = V4AnalysisRepository(self.project_path)
        self.project_repository = ProjectV4Repository(self.project_path.parent)
        self.validity_checker = AnalysisValidityChecker(config)

    @property
    def _ai_first_enabled(self) -> bool:
        return (
            self.book_understanding_adapter is not None
            and self.script_director_adapter is not None
        )

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
            book_understanding, script_director, script_review = create_ai_first_adapters(
                provider, **common
            )
            return cls(
                project,
                book_understanding_adapter=book_understanding,
                script_director_adapter=script_director,
                script_review_adapter=script_review,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - do not lose imported project
            return cls(
                project,
                config=config,
                ai_configured=False,
                configuration_message=f"AI 尚未准备好：{str(exc)[:300]}",
            )

    def run(
        self,
        progress_callback: ProgressCallback | None = None,
        *,
        force_reanalysis: bool = False,
    ) -> V4AnalysisResult:
        from services.service_lifecycle import ServiceLifecycle

        if ServiceLifecycle.is_stopping():
            raise RuntimeError("Audiobook Studio 服务正在关闭，分析已保存为可恢复状态")
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

        if self._ai_first_enabled:
            return self._run_ai_first(
                source,
                script,
                speakers,
                candidates,
                candidates_repository,
                progress_callback=progress_callback,
                force_reanalysis=force_reanalysis,
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

    def _run_ai_first(
        self,
        source: str,
        old_script: ScriptDocument,
        old_speakers: SpeakersDocument,
        candidates: CharacterCandidatesDocument,
        candidates_repository: CharacterCandidatesRepository,
        *,
        progress_callback: ProgressCallback | None,
        force_reanalysis: bool,
    ) -> V4AnalysisResult:
        """Run the source → bible → director → reviewer chain.

        This path intentionally does not instantiate ``SourceSegmenter.segment``
        or any of the legacy candidate/routing services.  Their code remains
        available to the advanced compatibility workbench, but the default V4
        machine result comes only from the three AI-first stages below.

        PR #22：本次重构为 attempt 编排层——
        1. completed 缓存复用前先做有效性校验（P0-6）；
        2. 单次三阶段执行（``_run_ai_first_attempt``）；
        3. 可疑结果自动重试一次（``force_restart=True``）；
        4. 最终写 status/current_stage/reason_codes/message + stats/validity/attempts。
        """
        source_sha = old_script.source_sha256
        previous_state = self.analysis_repository.load(source_sha)
        continuing_ai_first = bool(
            previous_state
            and previous_state.get("analysis_mode") == "ai-first"
            and not force_reanalysis
        )
        cache_invalidated = False
        if (
            not force_reanalysis
            and previous_state
            and previous_state.get("analysis_mode") == "ai-first"
            and previous_state.get("status") == "completed"
            and (self.project_path / "runtime/character_bible.json").is_file()
        ):
            cached_report = self.validity_checker.check_cached_state(
                previous_state, source
            )
            if not cached_report.is_suspicious:
                return V4AnalysisResult(
                    "completed",
                    old_script,
                    old_speakers,
                    candidates,
                    dict(previous_state.get("summary") or {}),
                    list(previous_state.get("errors") or []),
                    str(previous_state.get("message") or "✅ 分析已完成"),
                    reason_codes=list(
                        (previous_state.get("validity") or {}).get("reason_codes") or []
                    ),
                )
            cache_invalidated = True
            self._report(
                progress_callback,
                "检测到历史可疑的空分析缓存，正在自动失效并重新分析",
            )

        provider = getattr(self.book_understanding_adapter, "name", "")
        model = getattr(self.book_understanding_adapter, "model", "")
        state = self.analysis_repository.start(
            source_sha,
            provider=provider,
            model=model,
            analysis_mode="ai-first",
        )
        attempts: list[dict[str, Any]] = []
        if cache_invalidated:
            state.setdefault("validity", {})["reason_codes"] = [
                ReasonCode.CACHE_INVALIDATED.value
            ]
            attempts.append(
                {
                    "status": "cache_invalidated",
                    "reason_codes": [ReasonCode.CACHE_INVALIDATED.value],
                    "summary": {},
                    "at": self._now_iso(),
                }
            )

        errors: list[str] = []
        baseline = SourceSegmenter().source_only(source).script
        baseline = replace(baseline, revision=max(1, old_script.revision))
        old_voices = self._read_json(self.project_path / "production/voices.json")
        adopting_legacy_machine_result = (
            self._contains_machine_result(old_script, old_speakers)
            and (not previous_state or previous_state.get("analysis_mode") != "ai-first")
        )
        if force_reanalysis or adopting_legacy_machine_result or cache_invalidated:
            try:
                snapshot_reanalysis(
                    self.project_path,
                    script_data=old_script.to_dict(),
                    speakers_data=old_speakers.to_dict(),
                    voices_data=old_voices,
                )
            except Exception as exc:  # noqa: BLE001 - snapshot failure is visible
                errors.append(f"重分析快照失败：{str(exc)[:400]}")

        empty_bible = CharacterBibleDocument(source_sha256=source_sha)
        initial_reconciliation = reconcile_speakers(
            empty_bible, old_speakers, old_script
        )
        baseline = remap_script_speakers(
            protect_manual_assignments(
                baseline,
                old_script,
                initial_reconciliation.preserved_speaker_ids,
                initial_reconciliation.speaker_id_map,
            ),
            initial_reconciliation.speaker_id_map,
        )
        speakers = initial_reconciliation.speakers
        if not continuing_ai_first:
            self._persist_documents_if_changed(
                source, old_script, old_speakers, baseline, speakers
            )
        script = baseline

        book_proxy = CountingAdapterProxy(self.book_understanding_adapter)
        director_proxy = CountingAdapterProxy(self.script_director_adapter)
        review_proxy = (
            CountingAdapterProxy(self.script_review_adapter)
            if self.script_review_adapter is not None
            else None
        )
        shards_total = self._compute_shards_total(source, script)
        started_at = self._now_iso()

        retries = 0
        final_attempt: _AiFirstAttempt | None = None
        final_report: ValidityReport | None = None
        while True:
            calls_before = self._proxy_call_count(book_proxy, director_proxy, review_proxy)
            attempt = self._run_ai_first_attempt(
                source,
                baseline,
                old_script,
                old_speakers,
                candidates_repository,
                state,
                book_adapter=book_proxy,
                director_adapter=director_proxy,
                review_adapter=review_proxy,
                force_restart=force_reanalysis or retries > 0,
                progress_callback=progress_callback,
            )
            attempt_calls = (
                self._proxy_call_count(book_proxy, director_proxy, review_proxy)
                - calls_before
            )
            attempt_stats = self._attempt_stats(
                attempt,
                attempt_calls=attempt_calls,
                shards_total=shards_total,
                retries=retries,
                started_at=started_at,
                finished_at=self._now_iso(),
            )
            report = self.validity_checker.check(
                source_text=source,
                script=attempt.script,
                speakers=attempt.speakers,
                candidates=attempt.candidates,
                bible_count=len(attempt.bible.characters),
                summary=attempt.summary,
                stats=attempt_stats,
                errors=attempt.errors,
            )
            attempt = replace(attempt, stats=attempt_stats, report=report)
            attempts.append(self._attempt_summary(attempt, attempt_calls))
            if not report.is_suspicious:
                final_attempt = attempt
                final_report = report
                break
            if not self.config.validity_retry_enabled or retries >= self.config.validity_retry_max:
                final_attempt = attempt
                final_report = report
                break
            retries += 1
            self._report(
                progress_callback,
                "检测到可疑的空分析结果，正在自动重试一次（重新请求模型）",
            )

        attempt = final_attempt
        report = final_report
        script = attempt.script
        speakers = attempt.speakers
        candidates = attempt.candidates
        summary = attempt.summary
        errors = attempt.errors
        status = "completed" if (not report.is_suspicious and not errors) else "needs_attention"
        summary["analysis_status"] = status
        reason_codes = [
            issue.code.value
            for issue in report.issues
            if issue.code != ReasonCode.OK
        ]
        if cache_invalidated and ReasonCode.CACHE_INVALIDATED.value not in reason_codes:
            reason_codes.insert(0, ReasonCode.CACHE_INVALIDATED.value)
        message = self._message(status, summary, errors, reason_codes=reason_codes)
        finished_at = self._now_iso()
        total_calls = self._proxy_call_count(book_proxy, director_proxy, review_proxy)
        state.update(
            {
                "source_sha256": script.source_sha256,
                "status": status,
                "current_stage": "completed" if status == "completed" else "needs_attention",
                "summary": summary,
                "errors": errors,
                "message": message,
                "analysis_mode": "ai-first",
                "provider": provider,
                "model": model,
                "pipeline_version": PIPELINE_VERSION,
                "input_fingerprint": compute_input_fingerprint(
                    source_sha, provider=provider, model=model, config=self.config
                ),
                "stats": {
                    "ai_requests": total_calls,
                    "chapters_total": len(script.chapters),
                    "chapters_completed": attempt_stats.chapters_completed,
                    "chapters_failed": attempt_stats.chapters_failed,
                    "shards_total": shards_total,
                    "retries": retries,
                    "failures": len(errors),
                    "started_at": started_at,
                    "finished_at": finished_at,
                },
                "validity": {
                    "checked": True,
                    "is_suspicious": report.is_suspicious,
                    "reason_codes": reason_codes,
                    "source_dialogue_signals": (
                        report.source_signals.to_dict()
                        if report.source_signals is not None
                        else {}
                    ),
                },
                "attempts": attempts,
            }
        )
        self._report(progress_callback, "正在保存分析结果")
        self.analysis_repository.save(state)
        self._report(
            progress_callback,
            "分析完成" if status == "completed" else "分析未完成，需要人工确认",
        )
        return V4AnalysisResult(
            status, script, speakers, candidates, summary, errors, message,
            reason_codes=reason_codes,
        )

    def _run_ai_first_attempt(
        self,
        source: str,
        baseline: ScriptDocument,
        old_script: ScriptDocument,
        old_speakers: SpeakersDocument,
        candidates_repository: CharacterCandidatesRepository,
        state: dict[str, Any],
        *,
        book_adapter: Any,
        director_adapter: Any,
        review_adapter: Any | None,
        force_restart: bool,
        progress_callback: ProgressCallback | None,
    ) -> _AiFirstAttempt:
        """单次执行 book_understanding → script_director → script_review。"""
        source_sha = baseline.source_sha256
        errors: list[str] = []
        empty_bible = CharacterBibleDocument(source_sha256=source_sha)

        self._report(progress_callback, "正在阅读全书")
        self._stage(state, "book_understanding", "running")
        bible = empty_bible
        try:
            understanding = BookUnderstandingService(
                book_adapter,
                BookUnderstandingCheckpointRepository(self.project_path),
                max_input_chars=self.config.ai_max_input_chars,
            ).understand(
                source,
                baseline,
                progress_callback=progress_callback,
                force_restart=force_restart,
            )
            bible = understanding.bible
            self._stage(
                state,
                "book_understanding",
                "completed" if understanding.failed_chapters == 0 else "partial",
                completed_chapters=understanding.completed_chapters,
                failed_chapters=understanding.failed_chapters,
                character_count=len(bible.characters),
            )
            if understanding.failed_chapters:
                errors.append(
                    f"全书阅读有 {understanding.failed_chapters} 个章节失败，可继续分析重试。"
                )
        except Exception as exc:  # noqa: BLE001 - preserve source-only project
            bible = empty_bible
            errors.append(f"全书人物理解失败：{str(exc)[:500]}")
            self._stage(state, "book_understanding", "failed", error=errors[-1])

        reconciliation = reconcile_speakers(bible, old_speakers, old_script)
        speakers = reconciliation.speakers
        self._report(progress_callback, "正在分析章节剧本")
        self._stage(state, "script_director", "running")
        script = baseline
        try:
            directed = AIScriptDirectorService(
                director_adapter,
                ScriptDirectorCheckpointRepository(self.project_path),
                max_input_chars=self.config.ai_max_input_chars,
            ).direct(
                source,
                baseline,
                bible,
                progress_callback=progress_callback,
                force_restart=force_restart,
            )
            script = remap_script_speakers(
                directed.script, reconciliation.speaker_id_map
            )
            script = protect_manual_assignments(
                script,
                old_script,
                reconciliation.preserved_speaker_ids,
                reconciliation.speaker_id_map,
            )
            if self._document_content(script) == self._document_content(old_script):
                script = replace(script, revision=old_script.revision)
            self._stage(
                state,
                "script_director",
                "completed" if directed.failed_chapters == 0 else "partial",
                completed_chapters=directed.completed_chapters,
                failed_chapters=directed.failed_chapters,
            )
            if directed.failed_chapters:
                errors.append(
                    f"剧本导演有 {directed.failed_chapters} 个章节失败，可继续分析重试。"
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"AI 剧本导演失败：{str(exc)[:500]}")
            self._stage(state, "script_director", "failed", error=errors[-1])

        self._persist_documents_if_changed(
            source, old_script, old_speakers, script, speakers
        )
        pending_voice_migrations = migrate_voice_bindings(
            self.project_path, old_speakers, speakers, reconciliation.speaker_id_map
        )
        projected_candidates = self._bible_candidates(bible)
        current_candidates = candidates_repository.load(source_sha)
        candidates = (
            replace(projected_candidates, revision=current_candidates.revision + 1)
            if projected_candidates.candidates != current_candidates.candidates
            else current_candidates
        )
        candidates_repository.save(candidates)

        self._report(progress_callback, "正在复查对白归属")
        self._stage(state, "script_review", "running")
        review_result = None
        if review_adapter is None:
            self._stage(state, "script_review", "skipped", patches=0)
        else:
            try:
                review_result = AIScriptReviewService(
                    review_adapter,
                    ScriptReviewCheckpointRepository(self.project_path),
                    min_confidence=self.config.routing_min_confidence,
                ).review(
                    source,
                    script,
                    speakers,
                    bible,
                    progress_callback=progress_callback,
                    force_restart=force_restart,
                )
                script = review_result.script
                self._stage(
                    state,
                    "script_review",
                    "completed" if not review_result.errors else "partial",
                    reviewed_chapters=review_result.reviewed_chapters,
                    auto_fixed=len(review_result.auto_fixed),
                    skipped_manual=len(review_result.skipped_manual),
                )
                errors.extend(
                    f"剧本复查：{item}" for item in review_result.errors
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"全书剧本复查失败：{str(exc)[:500]}")
                self._stage(state, "script_review", "failed", error=errors[-1])

        self._persist_documents_if_changed(
            source, old_script, old_speakers, script, speakers
        )
        summary = self._summary(
            script,
            speakers,
            candidates,
            None,
            None,
            filtered_noise=0,
            auto_confirmed_count=len(bible.characters),
            consolidation=None,
        )
        summary.update({
            "analysis_mode": "ai-first",
            "character_bible_count": len(bible.characters),
            "pending_voice_migrations": len(pending_voice_migrations),
            "review_auto_fixed": len(review_result.auto_fixed) if review_result else 0,
        })
        for character in bible.characters:
            speaker_id = reconciliation.speaker_id_map.get(
                character.speaker_id, character.speaker_id
            )
            card = summary.get("character_cards", {}).get(speaker_id)
            if card is not None:
                card["confidence"] = character.confidence
                card["importance"] = (
                    "主要角色" if character.importance == "major" else "次要角色"
                )
        return _AiFirstAttempt(
            script=script,
            speakers=speakers,
            candidates=candidates,
            bible=bible,
            summary=summary,
            state=state,
            errors=errors,
        )

    @staticmethod
    def _contains_machine_result(script: ScriptDocument, speakers: SpeakersDocument) -> bool:
        return any(
            item.speaker_type == "character" for item in speakers.speakers
        ) or any(
            segment.speaker_source in {"rule", "router", "ai"}
            for chapter in script.chapters
            for segment in chapter.segments
        )

    def _persist_documents_if_changed(
        self,
        source: str,
        old_script: ScriptDocument,
        old_speakers: SpeakersDocument,
        script: ScriptDocument,
        speakers: SpeakersDocument,
    ) -> None:
        current_script = self._load_script(source)
        current_speakers = self._load_speakers()
        script_changed = self._document_content(script) != self._document_content(
            current_script
        )
        speakers_changed = self._document_content(speakers) != self._document_content(
            current_speakers
        )
        if not script_changed and not speakers_changed:
            return
        if not script_changed:
            script = current_script
        elif script.revision <= current_script.revision:
            script = replace(script, revision=current_script.revision + 1)
        if not speakers_changed:
            speakers = current_speakers
        elif speakers.revision <= current_speakers.revision:
            speakers = replace(speakers, revision=current_speakers.revision + 1)
        self.project_repository.save_script_and_speakers(
            self.project_path, source, script, speakers
        )

    @staticmethod
    def _document_content(document: Any) -> dict[str, Any]:
        value = document.to_dict()
        value.pop("revision", None)
        return value

    @staticmethod
    def _bible_candidates(bible: CharacterBibleDocument) -> CharacterCandidatesDocument:
        values = []
        for character in bible.characters:
            evidence = [
                CharacterEvidence(item.chapter_id, item.text)
                for item in character.evidence
            ]
            candidate = CharacterCandidate(
                candidate_id=stable_candidate_id(character.canonical_name),
                display_name=character.canonical_name,
                aliases=list(character.aliases),
                confidence=character.confidence,
                evidence=evidence,
                source="ai",
                status="confirmed",
            )
            candidate.validate()
            values.append(candidate)
        return CharacterCandidatesDocument(
            source_sha256=bible.source_sha256,
            candidates=values,
        )

    @staticmethod
    def _read_json(path):
        import json

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

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
        stages = state.setdefault("stages", {})
        previous = stages.get(name) or {}
        now = self._now_iso()
        value: dict[str, Any] = {"status": status, **extra}
        if status == "running":
            value["started_at"] = previous.get("started_at") or now
            value["finished_at"] = ""
            value["duration_ms"] = 0
        else:
            started_at = previous.get("started_at") or now
            value["started_at"] = started_at
            value["finished_at"] = now
            value["duration_ms"] = self._duration_ms(started_at, now)
        stages[name] = value
        state["current_stage"] = name
        self.analysis_repository.save(state)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _duration_ms(started_at: str, finished_at: str) -> int:
        try:
            start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
            return max(0, int((finish - start).total_seconds() * 1000))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _report(callback: ProgressCallback | None, message: str) -> None:
        if callback is None:
            return
        try:
            callback(message)
        except TypeError:
            callback(str(message))

    def _compute_shards_total(self, source: str, script: ScriptDocument) -> int:
        """确定性分片数：按章节对 split_source_range 求和（DESIGN §6.6）。"""
        return sum(
            len(
                split_source_range(
                    source, chapter.start, chapter.end, self.config.ai_max_input_chars
                )
            )
            for chapter in script.chapters
        )

    @staticmethod
    def _proxy_call_count(
        book: CountingAdapterProxy,
        director: CountingAdapterProxy,
        review: CountingAdapterProxy | None,
    ) -> int:
        total = book.calls + director.calls
        if review is not None:
            total += review.calls
        return total

    @staticmethod
    def _attempt_stats(
        attempt: _AiFirstAttempt,
        *,
        attempt_calls: int,
        shards_total: int,
        retries: int,
        started_at: str,
        finished_at: str,
    ) -> AnalysisRunStats:
        stages = attempt.state.get("stages") or {}
        book = stages.get("book_understanding") or {}
        director = stages.get("script_director") or {}
        chapters_completed = int(book.get("completed_chapters", 0) or 0)
        chapters_failed = int(book.get("failed_chapters", 0) or 0) + int(
            director.get("failed_chapters", 0) or 0
        )
        return AnalysisRunStats(
            ai_requests=attempt_calls,
            chapters_total=len(attempt.script.chapters),
            chapters_completed=chapters_completed,
            chapters_failed=chapters_failed,
            shards_total=shards_total,
            retries=retries,
            failures=len(attempt.errors),
            started_at=started_at,
            finished_at=finished_at,
        )

    @staticmethod
    def _attempt_summary(attempt: _AiFirstAttempt, attempt_calls: int) -> dict[str, Any]:
        report = attempt.report
        if report is None:
            status = "partial" if attempt.errors else "completed"
            reason_codes: list[str] = []
        elif report.is_suspicious:
            status = "suspicious"
            reason_codes = [
                issue.code.value
                for issue in report.issues
                if issue.code != ReasonCode.OK
            ]
        else:
            status = "partial" if attempt.errors else "completed"
            reason_codes = [
                issue.code.value
                for issue in report.issues
                if issue.code != ReasonCode.OK
            ]
        return {
            "status": status,
            "reason_codes": reason_codes,
            "summary": {
                "identified_characters": attempt.summary.get("identified_characters", 0),
                "dialogue_total": attempt.summary.get("dialogue_total", 0),
                "dialogue_auto_routed": attempt.summary.get("dialogue_auto_routed", 0),
                "dialogue_unresolved": attempt.summary.get("dialogue_unresolved", 0),
                "character_bible_count": attempt.summary.get("character_bible_count", 0),
            },
            "ai_requests": attempt_calls,
            "at": V4ProjectAnalysisPipeline._now_iso(),
        }

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
        auto_routed = sum(
            item.speaker_source in {"router", "ai"} for item in dialogue
        )
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
            "dialogue_coverage": (auto_routed / len(dialogue)) if dialogue else None,
            "character_cards": character_cards,
            "consistency_auto_fixed": len(consistency.auto_fixed) if consistency else 0,
            "consistency_ai_review": len(consistency.ai_review) if consistency else 0,
            "consistency_user_review": len(consistency.user_review) if consistency else 0,
            "source_sha256": script.source_sha256,
        }

    @staticmethod
    def _message(
        status: str,
        summary: dict[str, Any],
        errors: list[str],
        reason_codes: list[str] | None = None,
    ) -> str:
        prefix = "✅ 分析完成" if status == "completed" else "⚠ 分析未完成，需要人工确认"
        coverage = summary.get("dialogue_coverage")
        coverage_text = (
            f"{coverage * 100:.0f}%"
            if coverage is not None
            else DIALOGUE_COVERAGE_UNKNOWN_LABEL
        )
        lines = [
            prefix,
            f"识别角色：{summary.get('identified_characters', 0)}",
            f"自动确认：{summary.get('auto_confirmed_characters', 0)}",
            f"需要检查：{summary.get('needs_review_characters', 0) + summary.get('dialogue_unresolved', 0)}",
            f"已过滤噪音：{summary.get('filtered_noise', 0)}",
            f"对白自动归属：{coverage_text}",
        ]
        for code_value in reason_codes or []:
            code = ReasonCode.from_value(code_value)
            if code is None or code == ReasonCode.OK:
                continue
            text = REASON_MESSAGES.get(code, "")
            if text:
                lines.append(text)
        lines.extend(f"- {item}" for item in errors)
        return "\n".join(lines)
