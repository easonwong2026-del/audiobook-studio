"""Export assembled v4 chapter audio without reading v3 script state."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

from lib.audio_format import concatenate_normalized, write_wav


class V4ExportService:
    @staticmethod
    def export(
        project_path: str | Path,
        *,
        output_format: str = "wav",
        bitrate: str = "192k",
        output_dir: str | Path | None = None,
    ) -> Path:
        project = Path(project_path)
        script = json.loads(
            (project / "script/script.json").read_text(encoding="utf-8")
        )
        chapters = [
            project / "audio/chapters" / f"{item['chapter_id']}.wav"
            for item in script["chapters"]
        ]
        missing = [item.name for item in chapters if not item.is_file()]
        if missing:
            raise RuntimeError(f"missing assembled chapters: {', '.join(missing)}")
        arrays = []
        rate = 22050
        for index, chapter in enumerate(chapters):
            data, rate, _ = concatenate_normalized([str(chapter)])
            if index:
                arrays.append(np.zeros(int(rate * 0.8), dtype=np.int16))
            arrays.append(data)
        combined = np.concatenate(arrays)
        destination = Path(output_dir) if output_dir else project / "output"
        destination.mkdir(parents=True, exist_ok=True)
        title = json.loads(
            (project / "project.json").read_text(encoding="utf-8")
        ).get("title") or project.name
        safe_title = "".join(
            item if item.isalnum() or item in "-_ " else "_" for item in title
        ).strip() or "audiobook"
        wav_path = destination / f"{safe_title}.wav"
        write_wav(str(wav_path), combined, rate)
        if output_format == "wav":
            return wav_path
        if output_format not in {"mp3", "m4b"}:
            raise ValueError("output format must be wav, mp3, or m4b")
        output = destination / f"{safe_title}.{output_format}"
        codec = "libmp3lame" if output_format == "mp3" else "aac"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(wav_path),
                    "-codec:a", codec, "-b:a", bitrate, str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"ffmpeg is required; intermediate WAV remains at {wav_path}"
            ) from exc
        if output.exists():
            wav_path.unlink()
        return output

    @staticmethod
    def copy_for_download(path: Path, allowed_root: str | Path) -> Path:
        root = Path(allowed_root)
        try:
            path.resolve().relative_to(root.resolve())
            return path
        except ValueError:
            target = root / "exports" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            return target

    @staticmethod
    def generate_subtitles(
        project_path: str | Path,
        formats: tuple[str, ...] = ("srt", "lrc"),
        output_dir: str | Path | None = None,
    ) -> list[Path]:
        """V4 项目字幕：按 runtime.db 已合成片段生成 srt / lrc。

        Returns:
            生成的字幕文件路径列表（无可用段落时返回空列表）。
        """
        import json as _json
        import sqlite3
        import wave

        from repositories.runtime_repository import RuntimeRepository

        project = Path(project_path)
        formats = [fmt.lower() for fmt in (formats or ())]
        if not formats:
            return []
        script = _json.loads(
            (project / "script/script.json").read_text(encoding="utf-8")
        )
        source = (project / "source/source.txt").read_text(encoding="utf-8")
        runtime = RuntimeRepository(project / "runtime/runtime.db")
        if not runtime.path.is_file():
            return []
        out_dir = Path(output_dir) if output_dir else project / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        title = _json.loads(
            (project / "project.json").read_text(encoding="utf-8")
        ).get("title") or project.name
        safe_title = "".join(
            item if item.isalnum() or item in "-_ " else "_" for item in title
        ).strip() or "audiobook"

        rows: list[tuple[int, int, int, str]] = []  # (index, start_ms, end_ms, text)
        cursor_ms = 0
        gap_ms = 800  # 与 V4ExportService.export 的 0.8s 静音一致
        index = 0
        for chapter in script.get("chapters", []):
            for segment in chapter.get("segments", []):
                segment_id = segment.get("id")
                try:
                    paths = runtime.resolved_audio_paths(segment_id)
                except KeyError:
                    continue
                audio_path = next(
                    (project / rel for rel in paths if (project / rel).is_file()),
                    None,
                )
                if audio_path is None:
                    continue
                duration_ms = _wav_duration_ms(audio_path)
                if duration_ms <= 0:
                    continue
                text = segment.get("text_override") or source[
                    int(segment.get("source_start") or 0) : int(
                        segment.get("source_end") or 0
                    )
                ]
                text = " ".join(text.split())
                if not text:
                    continue
                if cursor_ms:
                    cursor_ms += gap_ms
                rows.append((index, cursor_ms, cursor_ms + duration_ms, text))
                cursor_ms += duration_ms
                index += 1
        if not rows:
            return []

        def _srt() -> Path:
            lines = []
            for item_index, start_ms, end_ms, text in rows:
                lines.append(str(item_index + 1))
                lines.append(
                    f"{_fmt_srt(start_ms)} --> {_fmt_srt(end_ms)}"
                )
                lines.append(text)
                lines.append("")
            path = out_dir / f"{safe_title}.srt"
            path.write_text("\n".join(lines), encoding="utf-8")
            return path

        def _lrc() -> Path:
            lines = []
            for _item_index, start_ms, _end_ms, text in rows:
                lines.append(f"[{_fmt_lrc(start_ms)}]{text}")
            path = out_dir / f"{safe_title}.lrc"
            path.write_text("\n".join(lines), encoding="utf-8")
            return path

        produced: list[Path] = []
        if "srt" in formats:
            produced.append(_srt())
        if "lrc" in formats:
            produced.append(_lrc())
        return produced


def _wav_duration_ms(path: Path) -> int:
    import wave

    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
        return int(frames * 1000 / rate) if rate else 0
    except (wave.Error, OSError, EOFError, ValueError):
        return 0


def _fmt_srt(ms: int) -> str:
    hours, remainder = divmod(ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _fmt_lrc(ms: int) -> str:
    minutes, seconds = divmod(ms // 1000, 60)
    centis = (ms % 1000) // 10
    return f"{minutes:02d}:{seconds:02d}.{centis:02d}"
