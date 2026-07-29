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
