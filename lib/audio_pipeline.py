"""音频后处理：WAV 拼接（scipy/numpy，无需 ffmpeg）+ 后处理链编排。

导出后处理链（export_book 内）：
    拼接 WAV → [D3 人声均衡(默认 off)] → [D1 响度 LUFS-16]
    → ffmpeg 转码 → [D2 写标签(ID3 / M4B 章节)]
不破坏 B8 已做的段间 / 章间静音。
"""
from __future__ import annotations

import gc
import logging
import os
import subprocess
from typing import Any

import numpy as np
from scipy.io import wavfile

from . import segment_cache
from . import audio_format as af
from .exceptions import ExportError

logger = logging.getLogger(__name__)

# 2.3/2.4：统一静音间隔常量（段间 / 章首，首章除外），与 generate_subtitles 共用
# 单一真相源，保证字幕时间戳与导出拼接的静音规则一致。
SEG_SILENCE_SEC = 0.3
CH_SILENCE_SEC = 0.8


def export_book(project_dir: str, format: str = "mp3", bitrate: str = "192k",
                output_dir: str = "", enable_eq: bool = False,
                target_lufs: float = -16.0) -> str:
    """拼接段落并导出成品（wav / mp3 / m4b）。

    后处理顺序：拼接 → 均衡(D3, 默认关闭) → 响度(D1, LUFS-16)
    → ffmpeg 转码 → 标签(D2)。B8 的段/章静音保持不变。

    Args:
        project_dir: 项目目录（含 structured_script.json 与 segments/）。
        format: 导出格式，wav / mp3 / m4b，默认 mp3。
        bitrate: mp3/m4b 比特率，默认 192k。
        output_dir: 输出目录，留空则用项目内 output/。
        enable_eq: 是否启用 D3 人声均衡，默认 False（零回归）。
        target_lufs: D1 目标响度，默认 -16.0。

    Returns:
        导出文件路径（wav 直接返回；mp3 / m4b 经 ffmpeg 转码后返回）。

    Raises:
        ExportError: mp3 / m4b 且 ffmpeg 缺失（FileNotFoundError）或转码失败
            （CalledProcessError）时。错误信息含中间 WAV 绝对路径、ffmpeg 安装
            链接（https://ffmpeg.org/download.html）与「可改用 WAV 格式」建议。
        RuntimeError: 存在未合成段落（缺 wav）时。
    """
    import json

    script_path = os.path.join(project_dir, "structured_script.json")
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    segments_dir = os.path.join(project_dir, "segments")
    out_dir = output_dir if output_dir else os.path.join(project_dir, "output")
    os.makedirs(out_dir, exist_ok=True)

    title = script.get("meta", {}).get("title", "audiobook")
    # 收集 (章索引, 数据)，保持顺序，便于在章首插入更长静音
    loaded: list = []
    missing_ids: list = []
    canonical_rate = None

    for ch_idx, ch in enumerate(script.get("chapters", [])):
        for seg in ch.get("segments", []):
            fp = _find_segment(
                segments_dir, seg["id"], seg["text"], seg["role"],
                seg.get("emotion", "neutral"),
                seg.get("emo_alpha", 1.0),
                seg.get("speech_rate", 1.0),
                seg.get("pinyin_hints"),
            )
            if fp:
                if canonical_rate is None:
                    r0, _ = wavfile.read(fp)
                    canonical_rate = int(r0)
                na = af.load_and_normalize_wav(
                    fp, target_rate=canonical_rate, target_channels=1,
                    target_dtype=np.int16,
                )
                loaded.append((ch_idx, na.data))
            else:
                missing_ids.append(seg["id"])

    # 任意段落缺失都直接报错，避免导出残缺成品（BUG B）
    if missing_ids:
        raise RuntimeError(f"以下段落未找到音频文件，无法导出: {missing_ids}")

    if loaded:
        logger.info(f"Export: {len(loaded)} found")
    else:
        # list files for debug
        existing = os.listdir(segments_dir)[:10] if os.path.isdir(segments_dir) else []
        raise RuntimeError(f"未找到任何已合成段落。segments/ 目录下文件: {existing}")

    # 统一 dtype 到 int16，避免拼接时 dtype 不一致报错（已在 load_and_normalize_wav 完成）
    rate = canonical_rate

    # 静音间隔：段间 SEG_SILENCE_SEC、章首（首章除外）CH_SILENCE_SEC（统一常量）
    seg_silence = np.zeros(int(rate * SEG_SILENCE_SEC), dtype=np.int16)
    ch_silence = np.zeros(int(rate * CH_SILENCE_SEC), dtype=np.int16)

    # 拼接并记录章节起点（采样点），供 m4b 章节标签推算（B8 静音仍生效）
    chapter_markers: list = []   # (chapter_index, start_sample)
    parts: list = []
    prev_ch = None
    cursor = 0
    for ch_idx, data in loaded:
        if prev_ch is None:
            # 首章起点为 0
            chapter_markers.append((ch_idx, cursor))
            parts.append(data)
            cursor += len(data)
        elif ch_idx != prev_ch:
            # 新章开头（非首章）：先插入较长静音，再记录章节起点（静音之后）
            chapter_markers.append((ch_idx, cursor + len(ch_silence)))
            parts.append(ch_silence)
            cursor += len(ch_silence)
            parts.append(data)
            cursor += len(data)
        else:
            # 段间插入短静音
            parts.append(seg_silence)
            cursor += len(seg_silence)
            parts.append(data)
            cursor += len(data)
        prev_ch = ch_idx

    combined = np.concatenate(parts)
    wav_path = os.path.join(out_dir, f"{title}.wav")
    wavfile.write(wav_path, rate, combined)

    # 2.4 M-2：拼接写盘后释放中间 numpy 数组，缓解长篇小说拼接峰值内存
    del loaded, parts, combined
    gc.collect()

    # ── 后处理（在拼接 WAV 上做，ffmpeg 转码之前）──
    from . import postprocess

    # D3 人声均衡（默认关闭，零回归）
    postprocess.apply_eq(wav_path, enable=enable_eq)
    # D1 响度归一（LUFS-16，保证多角色 / 多批次音量统一）
    postprocess.normalize_loudness(wav_path, target_lufs=target_lufs)

    # MP3/M4B 需要 ffmpeg 转码 + 写标签
    if format in ("mp3", "m4b"):
        ext = "mp3" if format == "mp3" else "m4b"
        out_path = os.path.join(out_dir, f"{title}.{ext}")
        codec = "libmp3lame" if format == "mp3" else "aac"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", wav_path, "-b:a", bitrate, "-codec:a", codec, out_path],
                check=True, capture_output=True, text=True
            )
            # D2 写标签（best-effort：标签失败不破坏音频导出）
            _write_tags(format, out_path, script, project_dir, chapter_markers, rate, logger)
            os.remove(wav_path)  # cleanup intermediate wav
        except FileNotFoundError as e:
            # R2：ffmpeg 未安装 / 未加入 PATH —— 显式报错，不再静默回退 WAV
            raise ExportError(
                "❌ 导出失败：未检测到 ffmpeg（系统未安装或未加入 PATH）。\n"
                f"已生成中间 WAV：{wav_path}\n"
                "请安装 ffmpeg：https://ffmpeg.org/download.html\n"
                "或改用 WAV 格式导出（无需 ffmpeg 转码）。"
            ) from e
        except subprocess.CalledProcessError as e:
            # R2：ffmpeg 转码失败 —— 显式报错，不再静默回退 WAV
            raise ExportError(
                f"❌ 导出失败：ffmpeg 转码失败（退出码 {e.returncode}）。\n"
                f"已生成中间 WAV：{wav_path}\n"
                "请检查 ffmpeg 安装：https://ffmpeg.org/download.html\n"
                "或改用 WAV 格式导出（无需 ffmpeg 转码）。"
            ) from e
        return out_path

    return wav_path


def _write_tags(format: str, out_path: str, script: dict, project_dir: str,
                chapter_markers: list, rate: int, logger) -> None:
    """根据 script / project 配置写 ID3 / M4B 章节标签。

    书名/作者/封面来源：structured_script.json 的 meta 字段；
    缺失则使用默认占位并打 warning。封面缺失时跳过封面但必写文字标签。
    """
    from . import metadata

    meta = script.get("meta", {})
    title = meta.get("title", "audiobook")
    author = meta.get("author") or meta.get("narrator") or ""
    album = meta.get("album") or title

    # 封面来源：structured_script meta.cover_path 或 project 配置；缺失则跳过封面
    cover_path = meta.get("cover_path")
    if cover_path:
        if not os.path.isabs(cover_path):
            cover_path = os.path.join(project_dir, cover_path)
        if not os.path.isfile(cover_path):
            logger.warning("封面图不存在，跳过封面嵌入：%s", cover_path)
            cover_path = None
    else:
        cover_path = None

    try:
        if format == "mp3":
            if not author:
                logger.warning("作者信息缺失，使用默认占位 'Unknown Author'")
                author = "Unknown Author"
            metadata.write_mp3_tags(out_path, title, author, album=album, cover_path=cover_path)
        elif format == "m4b":
            chapters = _build_chapters(script, chapter_markers, rate)
            if not author:
                logger.warning("作者信息缺失，使用默认占位 'Unknown Author'")
                author = "Unknown Author"
            metadata.write_m4b_chapters(out_path, title, author, album=album, chapters=chapters)
    except Exception as exc:  # pylint: disable=broad-except
        # 标签是元数据，写入失败不应破坏音频导出
        logger.warning("写入标签失败（已跳过，不影响音频导出）：%s", exc)


def _build_chapters(script: dict, chapter_markers: list, rate: int) -> list:
    """根据拼接时的章节起点（采样点）推算 (start_ms, title) 列表。"""
    title_by_id: dict = {}
    for ch in script.get("chapters", []):
        cid = ch["id"]
        title_by_id[cid] = ch.get("title", f"第{cid}章")
    chapters = []
    for cid, start_sample in chapter_markers:
        if cid not in title_by_id:
            continue
        start_ms = int(start_sample / rate * 1000)
        chapters.append((start_ms, title_by_id[cid]))
    return chapters


def _find_segment(segments_dir: str, seg_id: str, text: str, role: str, emotion: str,
                  emo_alpha: float = 1.0, speech_rate: float = 1.0,
                  pinyin_hints: Any = None) -> str | None:
    """查找某段已合成 wav：参数感知缓存键优先，旧版裸文件回退（B7 兼容）。

    委托给 ``segment_cache.find_segment_wav``，保持导出链路在 B7 文件名
    变更后仍完整：既命中新写入的 ``{seg_id}_{hash}.wav``，也兼容历史裸文件。
    """
    return segment_cache.find_segment_wav(
        segments_dir, seg_id, text, role, emotion, emo_alpha, speech_rate, pinyin_hints
    )


def generate_subtitles(project_dir: str, formats=("srt", "lrc"), output_dir: str = "") -> list:
    """生成字幕文件（srt / lrc），时间戳复用导出拼接的静音规则（SEG/CH_SILENCE_SEC）。

    逐段用 ``_find_segment`` 找到已合成 wav，读取时长，按统一静音间隔累计
    ``start_ms`` / ``end_ms``；返回生成的文件路径列表。某段缺失音频则跳过该段
    （不阻断其它段；缺段会在 ``export_book`` 单独报错，这里仅生成已存在段落的字幕）。

    Args:
        project_dir: 项目目录（含 ``structured_script.json`` 与 ``segments/``）。
        formats: 要生成的字幕格式集合，可含 ``"srt"`` / ``"lrc"``。
        output_dir: 输出目录，留空用项目内 ``output/``。

    Returns:
        生成的字幕文件路径列表（按请求格式，无可用段时返回空列表）。
    """
    import json

    formats = [f.lower() for f in (formats or ())]
    if not formats:
        return []

    script_path = os.path.join(project_dir, "structured_script.json")
    if not os.path.isfile(script_path):
        return []
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    segments_dir = os.path.join(project_dir, "segments")
    out_dir = output_dir if output_dir else os.path.join(project_dir, "output")
    os.makedirs(out_dir, exist_ok=True)

    title = script.get("meta", {}).get("title", "audiobook")
    rate = None
    rows: list = []  # (index, start_ms, end_ms, text)
    cursor_ms = 0
    prev_ch = None
    seg_index = 0

    for ch_idx, ch in enumerate(script.get("chapters", [])):
        for seg in ch.get("segments", []):
            fp = _find_segment(
                segments_dir, seg["id"], seg["text"], seg["role"],
                seg.get("emotion", "neutral"),
                seg.get("emo_alpha", 1.0),
                seg.get("speech_rate", 1.0),
                seg.get("pinyin_hints"),
            )
            if not fp:
                # 缺段则跳过（导出会单独报错），字幕不阻断
                continue
            r, data = wavfile.read(fp)
            if rate is None:
                rate = r
            dur_ms = int(len(data) / r * 1000) if r else 0
            # 段前静音间隔：首段 0；新章首（非首章）CH_SILENCE_SEC；其余 SEG_SILENCE_SEC
            if prev_ch is None:
                gap_ms = 0
            elif ch_idx != prev_ch:
                gap_ms = int(CH_SILENCE_SEC * 1000)
            else:
                gap_ms = int(SEG_SILENCE_SEC * 1000)
            start_ms = cursor_ms + gap_ms
            end_ms = start_ms + dur_ms
            seg_index += 1
            rows.append((seg_index, start_ms, end_ms, seg.get("text", "")))
            cursor_ms = end_ms
            prev_ch = ch_idx

    if not rows:
        return []

    written: list = []
    if "srt" in formats:
        p = os.path.join(out_dir, f"{title}.srt")
        _write_srt(p, rows)
        written.append(p)
    if "lrc" in formats:
        p = os.path.join(out_dir, f"{title}.lrc")
        _write_lrc(p, rows)
        written.append(p)
    return written


def _fmt_srt_time(ms: int) -> str:
    """将毫秒格式化为 srt 时间戳 ``HH:MM:SS,mmm``。"""
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    mm = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{mm:03d}"


def _write_srt(path: str, rows: list) -> None:
    """写入 srt 字幕：序号 / 时间轴 / 文本，空行分隔。"""
    lines: list = []
    for idx, start_ms, end_ms, text in rows:
        lines.append(str(idx))
        lines.append(f"{_fmt_srt_time(start_ms)} --> {_fmt_srt_time(end_ms)}")
        lines.append(text)
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _fmt_lrc_time(ms: int) -> str:
    """将毫秒格式化为 lrc 时间戳 ``[MM:SS.xx]``（点分隔百分秒）。"""
    m = ms // 60000
    s = (ms % 60000) / 1000.0
    return f"{m:02d}:{s:05.2f}"


def _write_lrc(path: str, rows: list) -> None:
    """写入 lrc 字幕：每行 ``[MM:SS.xx]文本``。"""
    lines = [f"[{_fmt_lrc_time(start_ms)}]{text}" for _, start_ms, _, text in rows]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def concat_for_preview(project_dir: str, chapter_id, output_path: str) -> str | None:
    """O13：合并试听——把单章多段 wav 拼成一条时间轴（不触 export_book）。

    复用 ``_find_segment``（含 B7 参数感知缓存键）定位每段 wav，段间插入
    ``SEG_SILENCE_SEC`` 静音（单章内部用段间静音，不用章首长静音），按与
    ``export_book`` 同构的 dtype→int16 归一后拼接写盘。失败段跳过（与
    ``generate_subtitles`` 同策略），全缺返回 None。

    Args:
        project_dir: 项目目录（含 ``structured_script.json`` 与 ``segments/``）。
        chapter_id: 章节 id（与剧本 ``ch.id`` 比对，自动归一为字符串）。
        output_path: 输出合并 wav 路径（由调用方负责落在 ``config.get_preview_dir()``）。

    Returns:
        输出文件路径（文件存在时）；无可用段时返回 None。
    """
    import json

    script_path = os.path.join(project_dir, "structured_script.json")
    if not os.path.isfile(script_path):
        return None
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    # 定位目标章节
    target = None
    for ch in script.get("chapters", []):
        if str(ch.get("id")) == str(chapter_id):
            target = ch
            break
    if target is None:
        return None

    segments_dir = os.path.join(project_dir, "segments")
    loaded: list = []  # int16 单声道数组（已统一规格）
    canonical_rate = None
    for seg in target.get("segments", []):
        fp = _find_segment(
            segments_dir, seg["id"], seg["text"], seg["role"],
            seg.get("emotion", "neutral"),
            seg.get("emo_alpha", 1.0),
            seg.get("speech_rate", 1.0),
            seg.get("pinyin_hints"),
        )
        if not fp:
            # 失败段跳过
            continue
        try:
            if canonical_rate is None:
                r0, _ = wavfile.read(fp)
                canonical_rate = int(r0)
            na = af.load_and_normalize_wav(
                fp, target_rate=canonical_rate, target_channels=1,
                target_dtype=np.int16,
            )
        except Exception:  # pylint: disable=broad-except
            continue
        if na.data.size == 0:
            continue
        loaded.append(na.data)

    # 全缺返回 None
    if not loaded:
        return None

    rate = canonical_rate
    # 段间静音（单章内部用段间短静音）
    seg_silence = np.zeros(int(rate * SEG_SILENCE_SEC), dtype=np.int16)
    parts: list = []
    for i, data in enumerate(loaded):
        if i > 0:
            parts.append(seg_silence)
        parts.append(data)
    combined = np.concatenate(parts)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    wavfile.write(output_path, rate, combined)
    return output_path if os.path.isfile(output_path) else None


def export_supplement(paths: list, out_path: str, format: str = "mp3", bitrate: str = "192k",
                      target_lufs: float = -16.0, insert_silence_sec: float = SEG_SILENCE_SEC,
                      title: str | None = None, artist: str | None = None,
                      album: str | None = None, cover_path: str | None = None) -> str:
    """把多段独立 wav 拼接为一条音频并导出（不依赖整本 script）。

    与 ``export_book`` 同构的后处理链：拼接多段独立 wav → 段间静音
    （``insert_silence_sec``，默认 ``SEG_SILENCE_SEC``）→ dtype→int16 归一
    → ``normalize_loudness``（LUFS）→ ffmpeg 转码（mp3 / m4b）→ best-effort 写标签。
    wav 格式无需 ffmpeg，直接返回拼接后的 wav。

    用于「角色单独补录 / 补合成导出」：补录产物是独立片段，不进整本拼接，
    因此导出链路与整本 ``export_book`` 解耦，避免触碰 ``structured_script.json``
    与 ``segments/``。

    Args:
        paths: 已合成的补录 wav 路径列表（按播放顺序）。
        out_path: 最终输出路径（含期望扩展名，如 ``...mp3``）；wav 直接写此路径，
            mp3 / m4b 会先写中间 wav 再转码。
        format: 导出格式，wav / mp3 / m4b，默认 mp3。
        bitrate: mp3 / m4b 比特率，默认 192k。
        target_lufs: 目标响度，默认 -16.0。
        insert_silence_sec: 段间静音秒数，默认 ``SEG_SILENCE_SEC``。
        title / artist / album / cover_path: 标签元数据（best-effort 写入，缺失则用默认值）。

    Returns:
        最终文件路径（wav 直接返回；mp3 / m4b 转码后返回）。

    Raises:
        ExportError: mp3 / m4b 且 ffmpeg 缺失（FileNotFoundError）或转码失败
            （CalledProcessError）时。错误信息含中间 WAV 绝对路径、ffmpeg 安装
            链接与「可改用 WAV 格式」建议。
        RuntimeError: paths 为空或任一片段缺失 / 不存在时。
    """
    if not paths:
        raise RuntimeError("导出失败：未提供任何音频片段（paths 为空）")

    loaded: list = []  # int16 单声道数组（已统一规格）
    canonical_rate = None
    for p in paths:
        if not p or not os.path.isfile(p):
            raise RuntimeError(f"导出失败：片段音频缺失或不存在: {p}")
        try:
            if canonical_rate is None:
                r0, _ = wavfile.read(p)
                canonical_rate = int(r0)
            na = af.load_and_normalize_wav(
                p, target_rate=canonical_rate, target_channels=1,
                target_dtype=np.int16,
            )
        except Exception as exc:  # pylint: disable=broad-except
            raise RuntimeError(f"导出失败：片段音频读取/归一化失败: {p}（{exc}）") from exc
        loaded.append(na.data)

    if not loaded:
        raise RuntimeError("导出失败：未加载到任何有效片段音频")
    rate = canonical_rate

    # 段间静音
    silence = (np.zeros(int(rate * max(insert_silence_sec, 0.0)), dtype=np.int16)
               if insert_silence_sec and insert_silence_sec > 0 else None)
    parts: list = []
    for i, data in enumerate(loaded):
        if i > 0 and silence is not None:
            parts.append(silence)
        parts.append(data)
    combined = np.concatenate(parts)

    # wav 直接写 out_path；mp3/m4b 先写中间 wav 再转码
    if format in ("mp3", "m4b"):
        wav_path = os.path.splitext(out_path)[0] + ".wav"
    else:
        wav_path = out_path
    out_dir = os.path.dirname(os.path.abspath(wav_path))
    os.makedirs(out_dir, exist_ok=True)
    wavfile.write(wav_path, rate, combined)

    # 2.4 M-2：拼接写盘后释放中间 numpy 数组
    del loaded, parts, combined
    gc.collect()

    from . import postprocess
    postprocess.normalize_loudness(wav_path, target_lufs=target_lufs)

    if format in ("mp3", "m4b"):
        ext = "mp3" if format == "mp3" else "m4b"
        out_path_real = os.path.splitext(out_path)[0] + "." + ext
        codec = "libmp3lame" if format == "mp3" else "aac"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", wav_path, "-b:a", bitrate, "-codec:a", codec, out_path_real],
                check=True, capture_output=True, text=True
            )
            # best-effort 写标签：失败不破坏音频导出
            _write_supplement_tags(format, out_path_real, title, artist, album, cover_path, logger)
            if os.path.isfile(wav_path):
                os.remove(wav_path)  # 清理中间 wav
        except FileNotFoundError as e:
            raise ExportError(
                "❌ 导出失败：未检测到 ffmpeg（系统未安装或未加入 PATH）。\n"
                f"已生成中间 WAV：{wav_path}\n"
                "请安装 ffmpeg：https://ffmpeg.org/download.html\n"
                "或改用 WAV 格式导出（无需 ffmpeg 转码）。"
            ) from e
        except subprocess.CalledProcessError as e:
            raise ExportError(
                f"❌ 导出失败：ffmpeg 转码失败（退出码 {e.returncode}）。\n"
                f"已生成中间 WAV：{wav_path}\n"
                "请检查 ffmpeg 安装：https://ffmpeg.org/download.html\n"
                "或改用 WAV 格式导出（无需 ffmpeg 转码）。"
            ) from e
        return out_path_real

    return wav_path


def _write_supplement_tags(format: str, out_path: str, title: str | None,
                            artist: str | None, album: str | None,
                            cover_path: str | None, logger) -> None:
    """为补录产物写文字标签（best-effort；失败不影响音频导出）。

    mp3 写 ID3（标题 / 作者 / 专辑，封面可空）；m4b 仅写文字标签
    （标题 / 作者 / 专辑），**不写章节**（补录是独立片段，无章节概念）。
    """
    from . import metadata

    title = title or "audiobook supplement"
    author = artist or "Unknown Author"
    album = album or title
    try:
        if format == "mp3":
            metadata.write_mp3_tags(out_path, title, author, album=album, cover_path=cover_path)
        elif format == "m4b":
            # 补录产物仅写文字标签，不写章节
            metadata.write_m4b_chapters(out_path, title, author, album=album, chapters=None)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("补录标签写入失败（已跳过，不影响音频导出）：%s", exc)
