"""合成队列 + 断点续跑 + 段级缓存（B7：缓存键含合成参数内容哈希）"""
from __future__ import annotations

import logging
import os
import time
import uuid
import wave
from typing import Generator, Optional

from . import project_paths, script_loader, segment_cache
from .failures import (
    DEFAULT_RECOVERY_BUDGET,
    PHASE_ATOMIC_PUBLISH,
    PHASE_DIRECTED_SYNTHESIS,
    PHASE_STATUS_PERSIST,
    PHASE_UNKNOWN,
    PHASE_WAV_VALIDATE,
    RecoveryBudget,
    RecoveryHooks,
    SynthesisFailure,
    is_confirmed_engine_recovery,
    sanitize_message,
    traceback_origin,
)
from repositories.project_repo import ProjectRepository

logger = logging.getLogger(__name__)


def _trace_call(trace, method: str, *args, **kwargs):
    """Invoke an optional diagnostic hook without changing queue behavior."""
    if trace is None:
        return None
    try:
        return getattr(trace, method)(*args, **kwargs)
    except Exception:  # noqa: BLE001  # diagnostics must not escape production
        logger.debug("performance trace %s failed", method, exc_info=True)
        return None


class _PhaseFailure(Exception):
    """Internal phase-tagged failure raised by lib layers below the engine."""

    def __init__(self, phase: str, original: BaseException) -> None:
        super().__init__(str(original))
        self.phase = phase
        self.original = original


def _publish_segment(
    temp_path: str,
    final_path: str,
    *,
    performance_trace=None,
    segment_id: str | None = None,
    chapter_id: str | None = None,
) -> None:
    """Validate a completed WAV and atomically publish it into the cache.

    Failure phases are distinguished: an empty/invalid WAV is a
    ``wav_validate`` failure; ``os.replace`` failures are ``atomic_publish``.
    OSError(errno=22) raised here is a file-system/publish problem and must
    never be classified as an engine-runtime failure.
    """
    validate_started = time.perf_counter()
    try:
        if not os.path.isfile(temp_path) or os.path.getsize(temp_path) <= 0:
            raise RuntimeError("合成结果为空")
        with wave.open(temp_path, "rb") as audio:
            if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                raise RuntimeError("合成结果不是有效 WAV")
    except Exception as exc:
        _trace_call(
            performance_trace,
            "record_failure",
            PHASE_WAV_VALIDATE,
            segment_id=segment_id,
            chapter_id=chapter_id,
            error=exc,
        )
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise _PhaseFailure(PHASE_WAV_VALIDATE, exc) from exc
    finally:
        _trace_call(
            performance_trace,
            "add_timing",
            "wav_validate",
            time.perf_counter() - validate_started,
            scope="segment",
            segment_id=segment_id,
            chapter_id=chapter_id,
        )
    publish_started = time.perf_counter()
    publish_succeeded = False
    publish_error: BaseException | None = None
    try:
        os.replace(temp_path, final_path)
        publish_succeeded = True
    except Exception as exc:
        publish_error = exc
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise _PhaseFailure(PHASE_ATOMIC_PUBLISH, exc) from exc
    finally:
        _trace_call(
            performance_trace,
            "record_publish",
            time.perf_counter() - publish_started,
            segment_id=segment_id,
            chapter_id=chapter_id,
            success=publish_succeeded,
            error=publish_error,
        )


def _status_update(
    status_writer,
    segment_id: str,
    status: str,
    *,
    performance_trace=None,
    chapter_id: str | None = None,
) -> None:
    started = time.perf_counter()
    try:
        status_writer.update(segment_id, status)
    except Exception as exc:
        _trace_call(
            performance_trace,
            "record_status",
            time.perf_counter() - started,
            status=status,
            segment_id=segment_id,
            chapter_id=chapter_id,
            success=False,
            error=exc,
        )
        raise
    _trace_call(
        performance_trace,
        "record_status",
        time.perf_counter() - started,
        status=status,
        segment_id=segment_id,
        chapter_id=chapter_id,
    )


def _classify_failure(
    seg,
    chapter_id: str,
    exc: BaseException,
) -> SynthesisFailure:
    """Build a structured failure with the precise phase."""
    from . import tts_engine

    if isinstance(exc, tts_engine.EngineRuntimeFailure):
        return SynthesisFailure.from_exception(
            segment_id=getattr(seg, "id", ""),
            chapter_id=chapter_id,
            phase=exc.phase,
            exc=exc,
            recoverable=exc.recoverable,
            engine_related=True,
            code=exc.code,
        )
    if isinstance(exc, _PhaseFailure):
        return SynthesisFailure.from_exception(
            segment_id=getattr(seg, "id", ""),
            chapter_id=chapter_id,
            phase=exc.phase,
            exc=exc.original,
        )
    return SynthesisFailure.from_exception(
        segment_id=getattr(seg, "id", ""),
        chapter_id=chapter_id,
        phase=PHASE_UNKNOWN,
        exc=exc,
    )


def _deliver_failure(
    hooks: RecoveryHooks,
    failure: SynthesisFailure,
) -> None:
    if hooks.on_failure is not None:
        try:
            hooks.on_failure(failure)
        except Exception:  # pylint: disable=broad-except
            logger.exception("结构化失败事件回调异常")


def _record_systemic(
    fingerprint_segments: dict[str, set[str]],
    failure: SynthesisFailure,
    limits: RecoveryBudget,
) -> bool:
    """Track distinct segments per fingerprint; True when the systemic
    threshold is reached."""
    if not failure.fingerprint:
        return False
    seen = fingerprint_segments.setdefault(failure.fingerprint, set())
    if failure.segment_id not in seen:
        seen.add(failure.segment_id)
    return len(seen) >= max(int(limits.systemic_failure_threshold), 1)


def _recovery_event(
    hooks: RecoveryHooks,
    event: dict,
) -> None:
    if hooks.on_recovery is not None:
        try:
            hooks.on_recovery(event)
        except Exception:  # pylint: disable=broad-except
            logger.exception("恢复事件回调异常")


def _seg_cache_key(seg, emotion: str = None, emo_alpha: float = None,
                   speech_rate: float = None,
                   speaker_fingerprint: str | None = None,
                   engine_identity: str | None = None) -> str:
    """段缓存键（B7）：段标识 + 合成参数内容哈希，参数一变即生成新文件名。

    直接委派 ``lib.segment_cache.segment_cache_key``（单一公式来源），确保合成侧
    （queue 批量链路）与导出侧（audio_pipeline / app.py）使用同一缓存键，导出不丢段；
    ``pinyin_hints`` 的 ``{}`` 与 ``None`` 归一化等价也在该处统一处理。

    2.3 O2：新增 ``emotion`` / ``emo_alpha`` / ``speech_rate`` 可选覆盖参数。
    留空（None）时回退到段落自身默认值，保证未覆盖时行为与现有一致。

    Args:
        seg: 段落对象，至少含 ``id`` / ``emotion`` 属性，可选 ``emo_alpha`` /
            ``speech_rate`` / ``pinyin_hints``。
        emotion: 有效情感（覆盖）；None=用段落自身。
        emo_alpha: 有效情绪强度（覆盖）；None=用段落自身。
        speech_rate: 有效语速（覆盖）；None=用段落自身。

    Returns:
        ``{seg_id}_{md5前8位}`` 形式、不含扩展名的缓存键。
    """
    if emotion is None:
        emotion = getattr(seg, "emotion", "neutral")
    if emo_alpha is None:
        emo_alpha = getattr(seg, "emo_alpha", 1.0)
    if speech_rate is None:
        speech_rate = getattr(seg, "speech_rate", 1.0)
    return segment_cache.segment_cache_key(
        seg.id,
        emotion,
        emo_alpha,
        speech_rate,
        getattr(seg, "pinyin_hints", None),
        segment_cache.director_metadata_for(seg),
        speaker_fingerprint,
        engine_identity,
    )


def synthesize_project(
    project_name: str,
    voice_bindings: dict,
    cb_audio=None,
    cb_progress=None,
    num_beams: int = 2,
    emotion: str = None,
    emo_alpha: float = None,
    speech_rate: float = None,
    cb_seg_state=None,
    selected_chapters: Optional[list] = None,
    selected_segment_ids: Optional[list] = None,
    voice_overrides: Optional[dict[str, str]] = None,
    cb_failure=None,
    recovery: Optional[RecoveryHooks] = None,
    budget: Optional[RecoveryBudget] = None,
    performance_trace=None,
    engine_identity: str | None = None,
) -> Generator[str, None, None]:
    # NumPy / SciPy 随 TTS 适配层按需加载，不进入应用启动热路径。
    from . import tts_engine
    if not engine_identity:
        try:
            engine_identity = tts_engine.get_engine_profile().get("cache_identity")
        except (AttributeError, TypeError):
            engine_identity = None

    project_dir = ProjectRepository.get_project_dir(project_name)
    # P2 提速：open_project 已把剧本读入内存（dict），直接用 from_dict 构造 Script，
    # 避免对同一个 structured_script.json 做第二次磁盘读取。
    _, script_data, bindings_document = ProjectRepository.load_project(project_name)
    script = script_loader.from_dict(script_data)

    segments_dir = project_paths.project_dir(project_dir, "segments", create=True)
    os.makedirs(segments_dir, exist_ok=True)
    cast_active = os.path.isfile(os.path.join(project_dir, "voice_cast.json"))
    speaker_fingerprints: dict[str, str | None] = {}
    cast_role_bindings = (
        bindings_document.get("role_bindings", {})
        if isinstance(bindings_document, dict) else {}
    )

    selected_segment_set = (
        {str(segment_id) for segment_id in selected_segment_ids}
        if selected_segment_ids else None
    )
    per_segment_voice = {
        str(segment_id): str(path)
        for segment_id, path in (voice_overrides or {}).items()
        if str(segment_id).strip() and str(path).strip()
    }
    remaining = ProjectRepository.get_remaining(
        project_name, engine_identity=engine_identity
    )
    if selected_segment_set is not None:
        remaining = [segment_id for segment_id in remaining if segment_id in selected_segment_set]
    if not remaining:
        yield "[0] all_done 所有段落已完成"
        return
    remaining_set = set(remaining)

    total = script_loader.count_segments(script)
    if selected_segment_set is not None:
        total = sum(
            1 for chapter in script.chapters
            for segment in chapter.segments
            if str(segment.id) in selected_segment_set
        )
    elif selected_chapters:
        selected_chapter_set = {str(chapter_id) for chapter_id in selected_chapters}
        total = sum(
            len(chapter.segments)
            for chapter in script.chapters
            if str(chapter.id) in selected_chapter_set
        )
    done = total - len(remaining)
    start_time = time.time()

    # O5：选中章节集合（None/空 = 全选）；未选中章的段将被标 skipped 并跳过。
    selected_set = None
    if selected_chapters:
        selected_set = {str(c) for c in selected_chapters}

    # 2.3 O2：把上层透传的全局覆盖归一化为 overrides dict；
    # 循环内每段经 effective_params 求有效参数，保证缓存键随覆盖变化一致。
    overrides = {
        "emotion": emotion,  # None = 按剧本每段自身值
        "override": (emo_alpha is not None or speech_rate is not None),
        "emo_alpha": emo_alpha if emo_alpha is not None else 1.0,
        "speech_rate": speech_rate if speech_rate is not None else 1.0,
    }

    status_writer = ProjectRepository.segment_status_batch(project_name, flush_every=0)
    hooks = recovery if isinstance(recovery, RecoveryHooks) else RecoveryHooks()
    limits = budget if isinstance(budget, RecoveryBudget) else DEFAULT_RECOVERY_BUDGET
    fingerprint_segments: dict[str, set[str]] = {}
    # This counter intentionally lives outside the segment loop.  The
    # engine_recycle_limit is a budget for the whole production task, not a
    # fresh allowance for every segment.
    engine_recycles_used = 0
    active_chapter_trace = None
    active_segment_trace = None
    try:
        for ch in script.chapters:
            ch_label = str(ch.id)
            chapter_trace = _trace_call(
                performance_trace,
                "start_chapter",
                ch_label,
            )
            active_chapter_trace = chapter_trace
            ch_unselected = selected_set is not None and ch_label not in selected_set
            for seg in ch.segments:
                if selected_segment_set is not None and str(seg.id) not in selected_segment_set:
                    continue
                # O5：未选中章节 -> 标 skipped 并跳过（不合成、不写 wav）
                if ch_unselected:
                    if seg.id in remaining_set:
                        _status_update(
                            status_writer,
                            seg.id,
                            "skipped",
                            performance_trace=performance_trace,
                            chapter_id=ch_label,
                        )
                    continue
                if seg.id not in remaining_set:
                    continue

                segment_trace = _trace_call(
                    performance_trace,
                    "start_segment",
                    str(seg.id),
                    chapter_id=ch_label,
                )
                active_segment_trace = segment_trace
                speaker_started = time.perf_counter()

                speaker = per_segment_voice.get(str(seg.id)) or voice_bindings.get(seg.role)
                if not speaker and cast_active and getattr(seg, "role_id", None):
                    cast_binding = cast_role_bindings.get(str(seg.role_id))
                    if isinstance(cast_binding, dict):
                        speaker = cast_binding.get("project_voice_path")
                if not speaker:
                    _deliver_failure(hooks, SynthesisFailure.from_exception(
                        segment_id=str(seg.id),
                        chapter_id=str(ch.id),
                        phase=PHASE_DIRECTED_SYNTHESIS,
                        exc=ValueError("角色未绑定音频"),
                        code="VOICE_BINDING_MISSING",
                    ))
                    yield f"[X] {seg.id} 角色'{seg.role}'未绑定音频"
                    _status_update(
                        status_writer,
                        seg.id,
                        "failed",
                        performance_trace=performance_trace,
                        chapter_id=ch_label,
                    )
                    _trace_call(segment_trace, "close")
                    continue

                if not os.path.isabs(str(speaker)):
                    speaker = os.path.join(project_dir, str(speaker))
                if not os.path.isfile(speaker):
                    _deliver_failure(hooks, SynthesisFailure.from_exception(
                        segment_id=str(seg.id),
                        chapter_id=str(ch.id),
                        phase=PHASE_DIRECTED_SYNTHESIS,
                        exc=FileNotFoundError("参考音频文件不存在"),
                        code="SPEAKER_AUDIO_MISSING",
                    ))
                    yield f"[X] {seg.id} 音频文件不存在"
                    _status_update(
                        status_writer,
                        seg.id,
                        "failed",
                        performance_trace=performance_trace,
                        chapter_id=ch_label,
                    )
                    _trace_call(segment_trace, "close")
                    continue

                _trace_call(
                    performance_trace,
                    "add_timing",
                    "speaker_resolution",
                    time.perf_counter() - speaker_started,
                    scope="segment",
                    chapter_id=ch_label,
                    segment_id=str(seg.id),
                )

                speaker_fingerprint = None
                if cast_active or str(seg.id) in per_segment_voice:
                    resolved_speaker = speaker
                    if resolved_speaker not in speaker_fingerprints:
                        fingerprint_started = time.perf_counter()
                        speaker_fingerprints[resolved_speaker] = (
                            segment_cache.speaker_fingerprint_for_path(resolved_speaker)
                        )
                        _trace_call(
                            performance_trace,
                            "add_timing",
                            "speaker_fingerprint",
                            time.perf_counter() - fingerprint_started,
                            scope="segment",
                            chapter_id=ch_label,
                            segment_id=str(seg.id),
                        )
                    speaker_fingerprint = speaker_fingerprints[resolved_speaker]

                seg_start = time.time()
                # 2.3 O2：用有效合成参数（全局覆盖 + 段落默认）派生缓存键与调用参数，
                # 保证「覆盖变化 → 文件名变化 → 重合成命中一致」（一致性根因修复）。
                effective_started = time.perf_counter()
                emotion_eff, emo_alpha_eff, speech_rate_eff = segment_cache.effective_params(
                    seg, overrides
                )
                _trace_call(
                    performance_trace,
                    "add_timing",
                    "effective_params",
                    time.perf_counter() - effective_started,
                    scope="segment",
                    chapter_id=ch_label,
                    segment_id=str(seg.id),
                )
                # B7：缓存键 = 段标识 + 合成参数内容哈希。
                seg_path = os.path.join(
                    segments_dir,
                    f"{_seg_cache_key(seg, emotion_eff, emo_alpha_eff, speech_rate_eff, speaker_fingerprint, engine_identity)}.wav",
                )

                cache_started = time.perf_counter()
                cache_hit = os.path.isfile(seg_path)
                _trace_call(
                    performance_trace,
                    "record_cache",
                    str(seg.id),
                    hit=cache_hit,
                    lookup_elapsed=time.perf_counter() - cache_started,
                    chapter_id=ch_label,
                )
                if cache_hit:
                    # Cache hit: mark done without invoking the engine.
                    try:
                        _status_update(
                            status_writer,
                            seg.id,
                            "done",
                            performance_trace=performance_trace,
                            chapter_id=ch_label,
                        )
                    except Exception as exc:
                        _deliver_failure(hooks, SynthesisFailure.from_exception(
                            segment_id=str(seg.id),
                            chapter_id=str(ch.id),
                            phase=PHASE_STATUS_PERSIST,
                            exc=exc,
                        ))
                    done += 1
                    if cb_seg_state:
                        cb_seg_state(seg.id, "done", 1.0)
                    elapsed = time.time() - seg_start
                    voice_name = os.path.splitext(os.path.basename(speaker))[0][:20]
                    yield f"[+] {seg.id}|{seg.role}|{voice_name}|{elapsed:.1f}s"
                    if cb_audio:
                        cb_audio(seg.id, seg_path)
                    if cb_progress:
                        cb_progress(done / total)
                    _trace_call(segment_trace, "close")
                    continue

                # ---- bounded engine-recycle self-healing ----
                segment_recovery_attempt = 0
                stop_run = False
                stop_reason = "recovery_budget_exhausted"
                final_failure: Optional[SynthesisFailure] = None
                try:
                    temp_path = os.path.join(
                        segments_dir,
                        f".{os.path.basename(seg_path)}.{uuid.uuid4().hex}.part.wav",
                    )
                    yield f"[/] {seg.id} {seg.role} 合成中..."
                    if cb_seg_state:
                        cb_seg_state(seg.id, "running", 0.0)
                    from . import directed_synthesis

                    try:
                        directed_synthesis.synthesize(
                            segment=seg,
                            speaker_audio=speaker,
                            emotion=emotion_eff,
                            emo_alpha=emo_alpha_eff,
                            speech_rate=speech_rate_eff,
                            pinyin_hints=getattr(seg, "pinyin_hints", None),
                            output_path=temp_path,
                            num_beams=num_beams,
                            engine=tts_engine,
                            trace=performance_trace,
                            trace_chapter_id=ch_label,
                        )
                    except tts_engine.EngineRuntimeFailure:
                        raise
                    except Exception as exc:
                        raise _PhaseFailure(PHASE_DIRECTED_SYNTHESIS, exc) from exc
                    _publish_segment(
                        temp_path,
                        seg_path,
                        performance_trace=performance_trace,
                        segment_id=str(seg.id),
                        chapter_id=ch_label,
                    )
                    yield "[/] vram_clean"

                    elapsed = time.time() - seg_start
                    try:
                        _status_update(
                            status_writer,
                            seg.id,
                            "done",
                            performance_trace=performance_trace,
                            chapter_id=ch_label,
                        )
                    except Exception as exc:
                        raise _PhaseFailure(PHASE_STATUS_PERSIST, exc) from exc
                    done += 1
                    if cb_seg_state:
                        cb_seg_state(seg.id, "done", 1.0)
                    voice_name = os.path.splitext(os.path.basename(speaker))[0][:20]
                    yield f"[+] {seg.id}|{seg.role}|{voice_name}|{elapsed:.1f}s"
                    if cb_audio:
                        cb_audio(seg.id, seg_path)
                except Exception as exc:
                    try:
                        if os.path.isfile(temp_path):
                            os.remove(temp_path)
                    except OSError:
                        pass
                    failure = _classify_failure(seg, str(ch.id), exc)
                    _deliver_failure(hooks, failure)
                    logger.exception("合成失败 %s", seg.id)
                    systemic = _record_systemic(fingerprint_segments, failure, limits)
                    while failure is not None:
                        confirmed_recovery = (
                            hooks.enabled and is_confirmed_engine_recovery(failure)
                        )
                        if systemic and hooks.enabled and not confirmed_recovery:
                            # A repeated non-engine (or otherwise unapproved)
                            # fingerprint is a systemic failure storm.  Stop
                            # pulling new segments, but never recycle TTS for
                            # this path.
                            stop_run = True
                            stop_reason = "systemic_fingerprint"
                            final_failure = failure
                            break
                        if not confirmed_recovery:
                            final_failure = failure
                            break
                        if engine_recycles_used >= max(
                            int(limits.engine_recycle_limit), 0
                        ):
                            stop_run = True
                            stop_reason = "recovery_budget_exhausted"
                            final_failure = failure
                            break
                        engine_recycles_used += 1
                        segment_recovery_attempt += 1
                        _recovery_event(hooks, {
                            "event": "recovering",
                            "segment_id": str(seg.id),
                            "chapter_id": str(ch.id),
                            "attempt": engine_recycles_used,
                            "segment_attempt": segment_recovery_attempt,
                            "max_attempts": int(limits.engine_recycle_limit),
                            "reason_code": failure.code or "TTS_ENGINE_RUNTIME_FAILURE",
                            "fingerprint": failure.fingerprint,
                            "exception_type": failure.exception_type,
                            "errno": failure.errno,
                            "phase": failure.phase,
                            "message": failure.message,
                            "traceback_origin": failure.traceback_origin,
                            "code": failure.code,
                        })
                        if hooks.enabled and hooks.shutdown_requested and hooks.shutdown_requested():
                            _trace_call(performance_trace, "record_boundary", "recovery_shutdown")
                            yield "[re] shutdown"
                            return
                        if hooks.enabled and hooks.cancel_requested and hooks.cancel_requested():
                            _trace_call(
                                performance_trace,
                                "record_boundary",
                                "recovery_cancel",
                            )
                            yield "[re] cancelled"
                            return
                        if hooks.enabled:
                            hooks.pause_gate()
                        if hooks.enabled and hooks.shutdown_requested and hooks.shutdown_requested():
                            _trace_call(performance_trace, "record_boundary", "recovery_shutdown")
                            yield "[re] shutdown"
                            return
                        if hooks.enabled and hooks.cancel_requested and hooks.cancel_requested():
                            _trace_call(
                                performance_trace,
                                "record_boundary",
                                "recovery_cancel",
                            )
                            yield "[re] cancelled"
                            return
                        try:
                            generation = hooks.recycle()
                        except Exception as recycle_exc:
                            logger.exception("引擎回收失败 %s", seg.id)
                            recycle_original = getattr(
                                recycle_exc, "original_exception", None
                            )
                            if not isinstance(recycle_original, BaseException):
                                recycle_original = recycle_exc.__cause__
                            if not isinstance(recycle_original, BaseException):
                                recycle_original = recycle_exc
                            _recovery_event(hooks, {
                                "event": "recycle_failed",
                                "segment_id": str(seg.id),
                                "chapter_id": str(ch.id),
                                "attempt": engine_recycles_used,
                                "max_attempts": int(limits.engine_recycle_limit),
                                "reason_code": "TTS_ENGINE_RECYCLE_FAILED",
                                "fingerprint": failure.fingerprint,
                                "exception_type": failure.exception_type,
                                "errno": failure.errno,
                                "phase": failure.phase,
                                "message": failure.message,
                                "traceback_origin": failure.traceback_origin,
                                "code": "TTS_ENGINE_RECYCLE_FAILED",
                                "recycle_exception_type": type(recycle_original).__name__,
                                "recycle_errno": getattr(
                                    recycle_original, "errno", None
                                ),
                                "recycle_message": sanitize_message(recycle_original),
                                "recycle_traceback_origin": traceback_origin(
                                    recycle_original
                                ),
                            })
                            stop_run = True
                            stop_reason = "engine_recycle_failed"
                            final_failure = failure
                            break
                        _recovery_event(hooks, {
                            "event": "recycle_done",
                            "segment_id": str(seg.id),
                            "chapter_id": str(ch.id),
                            "engine_generation": generation,
                            "attempt": engine_recycles_used,
                            "segment_attempt": segment_recovery_attempt,
                            "recycles_used": engine_recycles_used,
                        })
                        # Retry the SAME segment after a real engine recycle.
                        retried_ok = False
                        for _retry in range(max(int(limits.segment_retry_limit), 1)):
                            if hooks.enabled and hooks.shutdown_requested and hooks.shutdown_requested():
                                _trace_call(performance_trace, "record_boundary", "recovery_shutdown")
                                yield "[re] shutdown"
                                return
                            if hooks.enabled and hooks.cancel_requested and hooks.cancel_requested():
                                _trace_call(
                                    performance_trace,
                                    "record_boundary",
                                    "recovery_cancel",
                                )
                                yield "[re] cancelled"
                                return
                            if hooks.enabled:
                                hooks.pause_gate()
                            if hooks.enabled and hooks.shutdown_requested and hooks.shutdown_requested():
                                _trace_call(performance_trace, "record_boundary", "recovery_shutdown")
                                yield "[re] shutdown"
                                return
                            if hooks.enabled and hooks.cancel_requested and hooks.cancel_requested():
                                _trace_call(
                                    performance_trace,
                                    "record_boundary",
                                    "recovery_cancel",
                                )
                                yield "[re] cancelled"
                                return
                            retry_temp = os.path.join(
                                segments_dir,
                                f".{os.path.basename(seg_path)}.{uuid.uuid4().hex}.part.wav",
                            )
                            try:
                                yield f"[/] {seg.id} {seg.role} 重试合成中..."
                                if cb_seg_state:
                                    cb_seg_state(seg.id, "running", 0.0)
                                directed_synthesis.synthesize(
                                    segment=seg,
                                    speaker_audio=speaker,
                                    emotion=emotion_eff,
                                    emo_alpha=emo_alpha_eff,
                                    speech_rate=speech_rate_eff,
                                    pinyin_hints=getattr(seg, "pinyin_hints", None),
                                    output_path=retry_temp,
                                    num_beams=num_beams,
                                    engine=tts_engine,
                                    trace=performance_trace,
                                    trace_chapter_id=ch_label,
                                )
                                _publish_segment(
                                    retry_temp,
                                    seg_path,
                                    performance_trace=performance_trace,
                                    segment_id=str(seg.id),
                                    chapter_id=ch_label,
                                )
                                _status_update(
                                    status_writer,
                                    seg.id,
                                    "done",
                                    performance_trace=performance_trace,
                                    chapter_id=ch_label,
                                )
                                done += 1
                                if cb_seg_state:
                                    cb_seg_state(seg.id, "done", 1.0)
                                voice_name = os.path.splitext(
                                    os.path.basename(speaker)
                                )[0][:20]
                                yield (
                                    f"[+] {seg.id}|{seg.role}|{voice_name}|"
                                    f"{time.time() - seg_start:.1f}s"
                                )
                                if cb_audio:
                                    cb_audio(seg.id, seg_path)
                                retried_ok = True
                                break
                            except Exception as retry_exc:
                                try:
                                    if os.path.isfile(retry_temp):
                                        os.remove(retry_temp)
                                except OSError:
                                    pass
                                failure = _classify_failure(seg, str(ch.id), retry_exc)
                                _deliver_failure(hooks, failure)
                                systemic = _record_systemic(
                                    fingerprint_segments, failure, limits
                                )
                                if systemic and hooks.enabled and not is_confirmed_engine_recovery(failure):
                                    stop_run = True
                                    stop_reason = "systemic_fingerprint"
                                    retried_ok = False
                                    break
                                if not is_confirmed_engine_recovery(failure):
                                    # Recovery no longer applies (phase changed
                                    # or failure became non-recoverable).
                                    retried_ok = False
                                    break
                        if retried_ok:
                            _recovery_event(hooks, {
                                "event": "recovered",
                                "segment_id": str(seg.id),
                                "chapter_id": str(ch.id),
                                "attempt": engine_recycles_used,
                                "segment_attempt": segment_recovery_attempt,
                                "engine_generation": generation,
                                "recycles_used": engine_recycles_used,
                            })
                            final_failure = None
                            break
                        # Retry failed: consume the next engine recycle.
                        continue

                if final_failure is not None:
                    try:
                        _status_update(
                            status_writer,
                            seg.id,
                            "failed",
                            performance_trace=performance_trace,
                            chapter_id=ch_label,
                        )
                    except Exception as persist_exc:
                        _deliver_failure(hooks, SynthesisFailure.from_exception(
                            segment_id=str(seg.id),
                            chapter_id=str(ch.id),
                            phase=PHASE_STATUS_PERSIST,
                            exc=persist_exc,
                        ))
                    if cb_seg_state:
                        cb_seg_state(seg.id, "error", 0.0)
                    yield f"[X] {seg.id} 失败: {final_failure.message[:80]}"
                    if hooks.enabled and stop_run:
                        _recovery_event(hooks, {
                            "event": "exhausted",
                            "segment_id": str(seg.id),
                            "chapter_id": str(ch.id),
                            "attempt": engine_recycles_used,
                            "max_attempts": int(limits.engine_recycle_limit),
                            "reason_code": (
                                "TTS_ENGINE_RECYCLE_FAILED"
                                if stop_reason == "engine_recycle_failed"
                                else (
                                    "SYSTEMIC_FAILURE_THRESHOLD"
                                    if stop_reason == "systemic_fingerprint"
                                    else final_failure.code
                                    or "TTS_ENGINE_RUNTIME_FAILURE"
                                )
                            ),
                            "fingerprint": final_failure.fingerprint,
                            "exception_type": final_failure.exception_type,
                            "errno": final_failure.errno,
                            "phase": final_failure.phase,
                            "message": final_failure.message,
                            "traceback_origin": final_failure.traceback_origin,
                            "code": final_failure.code,
                            "recycles_used": engine_recycles_used,
                            "reason": stop_reason,
                        })
                        # Engine-related budget exhaustion stops the run.
                        yield f"[re] stop|{seg.id}|{final_failure.code or 'recovery_exhausted'}"
                        _trace_call(segment_trace, "close")
                        return

                _trace_call(segment_trace, "close")
                active_segment_trace = None
                if cb_progress:
                    cb_progress(done / total)

            # Chapter boundary fsyncs the O(1) recovery journal.  The task
            # boundary consolidates project.json once, avoiding O(N²) rewrites.
            status_writer.checkpoint()
            _trace_call(chapter_trace, "close")
            active_chapter_trace = None
            _trace_call(performance_trace, "checkpoint")
            yield f"[=] ch{ch.id}|{ch.title}"

        elapsed_total = time.time() - start_time
        yield f"[0] done|{total}|{elapsed_total:.0f}s"
    finally:
        _trace_call(active_segment_trace, "close")
        _trace_call(active_chapter_trace, "close")
        status_writer.close()
