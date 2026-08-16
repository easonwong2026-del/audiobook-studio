"""导出元数据：MP3 ID3 标签 + M4B 章节（mutagen，纯 Python）。

书名 / 作者 / 封面来源优先取 structured_script.json 的 meta 字段，
缺失时用默认占位并打 warning（具体逻辑在 audio_pipeline._write_tags）。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .procutil import run_no_window

logger = logging.getLogger(__name__)


def write_mp3_tags(path: str, title: str, author: str,
                    album: str = "", cover_path: str | None = None) -> str:
    """用 mutagen 写 ID3v2 标题 / 作者 / 专辑，可选嵌入封面。

    Args:
        path: mp3 文件路径。
        title: 书名（TIT2）。
        author: 作者 / 演播（TPE1）。
        album: 专辑名（TALB），可空。
        cover_path: 封面图路径（png/jpg），可空；不存在则跳过封面。

    Returns:
        写回后的 path。
    """
    from mutagen.mp3 import MP3
    from mutagen.id3 import TIT2, TPE1, TALB, APIC

    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()

    # 清除旧值，避免重复写入叠加。
    audio.tags.delall("TIT2")
    audio.tags.delall("TPE1")
    audio.tags.delall("TALB")
    audio.tags.delall("APIC")

    audio.tags.add(TIT2(encoding=3, text=title))
    audio.tags.add(TPE1(encoding=3, text=author))
    if album:
        audio.tags.add(TALB(encoding=3, text=album))

    if cover_path and os.path.isfile(cover_path):
        with open(cover_path, "rb") as fh:
            img = fh.read()
        ext = os.path.splitext(cover_path)[1].lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=img))
    elif cover_path:
        logger.warning("封面文件不存在，跳过封面嵌入：%s", cover_path)

    audio.save(path)
    return path


def write_m4b_chapters(path: str, title: str, author: str,
                       album: str = "", chapters: list | None = None) -> str:
    """用 mutagen 写 M4B 文字标签 + 章节（播放器可章节跳转）。

    步骤：
      1) 文字标签（©nam/©ART/©alb）用 mutagen 写（可靠）；
      2) 章节跳转用 ffmpeg 章节元数据文件（-map_chapters 1）重封装，
         因为 mutagen 对纯音频 mp4 的章节写入常静默失败。无 ffmpeg
         时退回 mutagen 章节（best-effort）。

    Args:
        path: m4b 文件路径。
        title: 书名。
        author: 作者 / 演播。
        album: 专辑名，可空。
        chapters: 章节列表，元素为 (start_ms: int, title: str)。

    Returns:
        写回后的 path。
    """
    from mutagen.mp4 import MP4

    # 1) 文字标签（mutagen 直接写 ©nam/©ART/©alb，ffmpeg 重封装时保留）
    audio = MP4(path)
    audio["\xa9nam"] = title
    audio["\xa9ART"] = author
    if album:
        audio["\xa9alb"] = album
    audio.save(path)

    if not chapters:
        return path

    # 2) 章节：优先 ffmpeg 章节元数据文件（最可靠）
    ff = shutil.which("ffmpeg")
    if ff:
        meta_txt = _build_ffmpeg_chapter_file(chapters)
        tmp_meta = path + ".chaps.txt"
        tmp_out = path + ".chaps.mp4"
        try:
            with open(tmp_meta, "w", encoding="utf-8") as fh:
                fh.write(meta_txt)
            run_no_window(
                [ff, "-y", "-i", path, "-i", tmp_meta,
                 "-map", "0", "-map_chapters", "1", "-c", "copy", tmp_out],
                check=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            if os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 0:
                os.replace(tmp_out, path)
            else:
                logger.warning("ffmpeg 章节写入产物无效，保留原文件（仅文字标签）")
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            logger.warning("ffmpeg 章节写入失败，仅保留文字标签：%s", exc)
        finally:
            for f in (tmp_meta, tmp_out):
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError as exc:
                        logger.debug("清理临时章节文件失败: %s", exc)
    else:
        # 无 ffmpeg：退回 mutagen 章节（纯音频 mp4 可能不被播放器识别）
        logger.warning("环境无 ffmpeg，尝试 mutagen 写章节（可能不被播放器识别）")
        try:
            audio = MP4(path)
            audio.chapters = [(int(s), str(t)) for s, t in chapters]
            audio.save(path)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("mutagen 写章节失败：%s", exc)
    return path


def _build_ffmpeg_chapter_file(chapters: list) -> str:
    """生成 ffmpeg 章节元数据文件内容（FFMETADATA 格式，TIMEBASE=1/1000）。"""
    lines = [";FFMETADATA1"]
    n = len(chapters)
    for i, (start_ms, title) in enumerate(chapters):
        end_ms = chapters[i + 1][0] if i + 1 < n else int(start_ms) + 5000
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={int(start_ms)}")
        lines.append(f"END={int(end_ms)}")
        lines.append(f"title={title}")
    return "\n".join(lines) + "\n"
