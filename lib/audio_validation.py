"""Small shared checks for WAV files used as formal segment audio."""
from __future__ import annotations

import os
import wave


def is_valid_wav_file(path: str | os.PathLike[str] | None) -> bool:
    """Return whether ``path`` is a non-empty, readable WAV with frames."""
    if not path:
        return False
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
        with wave.open(os.fspath(path), "rb") as audio:
            return audio.getnframes() > 0 and audio.getframerate() > 0
    except (EOFError, OSError, ValueError, wave.Error):
        return False
