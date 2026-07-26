"""合成队列 + 断点续跑 + 段级缓存（B7：缓存键含合成参数内容哈希）"""
from __future__ import annotations
import logging
import os
import time
from typing import Generator, Optional

from . import script_loader
from . import project_manager as pm
from . import segment_cache

logger = logging.getLogger(__name__)


def _seg_cache_key(seg, emotion: str = None, emo_alpha: float = None,
                   speech_rate: float = None) -> str:
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
) -> Generator[str, None, None]:
    # NumPy / SciPy 随 TTS 适配层按需加载，不进入应用启动热路径。
    from . import tts_engine

    project_dir = pm.get_project_dir(project_name)
    # P2 提速：open_project 已把剧本读入内存（dict），直接用 from_dict 构造 Script，
    # 避免对同一个 structured_script.json 做第二次磁盘读取。
    _, script_data, _ = pm.open_project(project_name)
    script = script_loader.from_dict(script_data)

    segments_dir = os.path.join(project_dir, "segments")
    os.makedirs(segments_dir, exist_ok=True)

    remaining = pm.get_remaining(project_name)
    if not remaining:
        yield "[0] all_done 所有段落已完成"
        return

    total = script_loader.count_segments(script)
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

    for ch in script.chapters:
        ch_label = str(ch.id)
        ch_unselected = selected_set is not None and ch_label not in selected_set
        for seg in ch.segments:
            # O5：未选中章节 -> 标 skipped 并跳过（不合成、不写 wav）
            if ch_unselected:
                if seg.id in remaining:
                    pm.update_segment_status(project_name, seg.id, "skipped")
                continue
            if seg.id not in remaining:
                continue

            speaker = voice_bindings.get(seg.role)
            if not speaker:
                yield f"[X] {seg.id} 角色'{seg.role}'未绑定音频"
                pm.update_segment_status(project_name, seg.id, "failed")
                continue

            if not os.path.isfile(speaker):
                yield f"[X] {seg.id} 音频文件不存在"
                pm.update_segment_status(project_name, seg.id, "failed")
                continue

            seg_start = time.time()
            try:
                # 2.3 O2：用有效合成参数（全局覆盖 + 段落默认）派生缓存键与调用参数，
                # 保证「覆盖变化 → 文件名变化 → 重合成命中一致」（一致性根因修复）。
                emotion_eff, emo_alpha_eff, speech_rate_eff = segment_cache.effective_params(seg, overrides)
                # B7：缓存键 = 段标识 + 合成参数内容哈希（_seg_cache_key）。
                #     参数(emotion/emo_alpha/speech_rate/pinyin_hints) 任一变化 →
                #     文件名变化 → 旧文件不再被命中 → 触发重新合成。
                seg_path = os.path.join(
                    segments_dir,
                    f"{_seg_cache_key(seg, emotion_eff, emo_alpha_eff, speech_rate_eff)}.wav",
                )

                if not os.path.isfile(seg_path):
                    yield f"[/] {seg.id} {seg.role} 合成中..."
                    # O3/O12：段「运行」点回调（内存态 running，不写 meta）
                    if cb_seg_state:
                        cb_seg_state(seg.id, "running", 0.0)
                    tts_engine.synthesize_segment(
                        text=seg.text,
                        speaker_audio=speaker,
                        emotion=emotion_eff,
                        emo_alpha=emo_alpha_eff,
                        speech_rate=speech_rate_eff,
                        pinyin_hints=getattr(seg, 'pinyin_hints', None),
                        output_path=seg_path,
                        num_beams=num_beams,
                    )
                    yield "[/] vram_clean"

                elapsed = time.time() - seg_start
                pm.update_segment_status(project_name, seg.id, "done")
                done += 1
                # O3/O12：段「完成」点回调（内存态 done，不写 meta）
                if cb_seg_state:
                    cb_seg_state(seg.id, "done", 1.0)

                # ✅ seg_id|role|音色名|耗时
                voice_name = os.path.splitext(os.path.basename(speaker))[0][:20]
                yield f"[+] {seg.id}|{seg.role}|{voice_name}|{elapsed:.1f}s"

                if cb_audio:
                    cb_audio(seg.id, seg_path)

            except Exception as e:
                logger.exception(f"合成失败 {seg.id}")
                pm.update_segment_status(project_name, seg.id, "failed")
                # O3/O12：段「失败」点回调（内存态 error，不写 meta）
                if cb_seg_state:
                    cb_seg_state(seg.id, "error", 0.0)
                yield f"[X] {seg.id} 失败: {str(e)[:60]}"

            if cb_progress:
                cb_progress(done / total)

        yield f"[=] ch{ch.id}|{ch.title}"

    elapsed_total = time.time() - start_time
    yield f"[0] done|{total}|{elapsed_total:.0f}s"
