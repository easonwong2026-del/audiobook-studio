"""合成队列 + 断点续跑 + 段级缓存（B7：缓存键含合成参数内容哈希）"""
from __future__ import annotations

import logging
import os
import time
import uuid
import wave
from typing import Generator, Optional

from . import project_paths, script_loader, segment_cache
from repositories.project_repo import ProjectRepository

logger = logging.getLogger(__name__)


def _publish_segment(temp_path: str, final_path: str) -> None:
    """Validate a completed WAV and atomically publish it into the cache."""
    try:
        if not os.path.isfile(temp_path) or os.path.getsize(temp_path) <= 0:
            raise RuntimeError("合成结果为空")
        with wave.open(temp_path, "rb") as audio:
            if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                raise RuntimeError("合成结果不是有效 WAV")
        os.replace(temp_path, final_path)
    except Exception:
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise


def _seg_cache_key(seg, emotion: str = None, emo_alpha: float = None,
                   speech_rate: float = None,
                   speaker_fingerprint: str | None = None) -> str:
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
) -> Generator[str, None, None]:
    # NumPy / SciPy 随 TTS 适配层按需加载，不进入应用启动热路径。
    from . import tts_engine

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
    remaining = ProjectRepository.get_remaining(project_name)
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
    try:
        for ch in script.chapters:
            ch_label = str(ch.id)
            ch_unselected = selected_set is not None and ch_label not in selected_set
            for seg in ch.segments:
                if selected_segment_set is not None and str(seg.id) not in selected_segment_set:
                    continue
                # O5：未选中章节 -> 标 skipped 并跳过（不合成、不写 wav）
                if ch_unselected:
                    if seg.id in remaining_set:
                        status_writer.update(seg.id, "skipped")
                    continue
                if seg.id not in remaining_set:
                    continue

                speaker = per_segment_voice.get(str(seg.id)) or voice_bindings.get(seg.role)
                if not speaker and cast_active and getattr(seg, "role_id", None):
                    cast_binding = cast_role_bindings.get(str(seg.role_id))
                    if isinstance(cast_binding, dict):
                        speaker = cast_binding.get("project_voice_path")
                if not speaker:
                    yield f"[X] {seg.id} 角色'{seg.role}'未绑定音频"
                    status_writer.update(seg.id, "failed")
                    continue

                if not os.path.isabs(str(speaker)):
                    speaker = os.path.join(project_dir, str(speaker))
                if not os.path.isfile(speaker):
                    yield f"[X] {seg.id} 音频文件不存在"
                    status_writer.update(seg.id, "failed")
                    continue

                speaker_fingerprint = None
                if cast_active or str(seg.id) in per_segment_voice:
                    resolved_speaker = speaker
                    if resolved_speaker not in speaker_fingerprints:
                        speaker_fingerprints[resolved_speaker] = (
                            segment_cache.speaker_fingerprint_for_path(resolved_speaker)
                        )
                    speaker_fingerprint = speaker_fingerprints[resolved_speaker]

                seg_start = time.time()
                try:
                    # 2.3 O2：用有效合成参数（全局覆盖 + 段落默认）派生缓存键与调用参数，
                    # 保证「覆盖变化 → 文件名变化 → 重合成命中一致」（一致性根因修复）。
                    emotion_eff, emo_alpha_eff, speech_rate_eff = (
                        segment_cache.effective_params(seg, overrides)
                    )
                    # B7：缓存键 = 段标识 + 合成参数内容哈希。
                    seg_path = os.path.join(
                        segments_dir,
                        f"{_seg_cache_key(seg, emotion_eff, emo_alpha_eff, speech_rate_eff, speaker_fingerprint)}.wav",
                    )

                    if not os.path.isfile(seg_path):
                        yield f"[/] {seg.id} {seg.role} 合成中..."
                        if cb_seg_state:
                            cb_seg_state(seg.id, "running", 0.0)
                        from . import directed_synthesis

                        temp_path = os.path.join(
                            segments_dir,
                            f".{os.path.basename(seg_path)}.{uuid.uuid4().hex}.part.wav",
                        )
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
                        )
                        _publish_segment(temp_path, seg_path)
                        yield "[/] vram_clean"

                    elapsed = time.time() - seg_start
                    status_writer.update(seg.id, "done")
                    done += 1
                    if cb_seg_state:
                        cb_seg_state(seg.id, "done", 1.0)

                    voice_name = os.path.splitext(os.path.basename(speaker))[0][:20]
                    yield f"[+] {seg.id}|{seg.role}|{voice_name}|{elapsed:.1f}s"

                    if cb_audio:
                        cb_audio(seg.id, seg_path)

                except Exception as exc:
                    logger.exception("合成失败 %s", seg.id)
                    status_writer.update(seg.id, "failed")
                    if cb_seg_state:
                        cb_seg_state(seg.id, "error", 0.0)
                    yield f"[X] {seg.id} 失败: {str(exc)[:60]}"

                if cb_progress:
                    cb_progress(done / total)

            # Chapter boundary fsyncs the O(1) recovery journal.  The task
            # boundary consolidates project.json once, avoiding O(N²) rewrites.
            status_writer.checkpoint()
            yield f"[=] ch{ch.id}|{ch.title}"

        elapsed_total = time.time() - start_time
        yield f"[0] done|{total}|{elapsed_total:.0f}s"
    finally:
        status_writer.close()
