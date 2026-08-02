"""Content-addressed audio cache backed by runtime.db."""
from __future__ import annotations

import hashlib
import sqlite3
import wave
from pathlib import Path


class AudioCacheRepository:
    def __init__(self, database_path: str | Path, project_path: str | Path):
        self.database_path = Path(database_path)
        self.project = Path(project_path)

    def lookup(self, cache_key: str) -> Path | None:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT file_path, file_sha256 FROM cache_entries
                 WHERE cache_key = ? AND valid = 1
                """,
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            path = self.project / row[0]
            if not path.is_file() or self._sha256(path) != row[1]:
                connection.execute(
                    "UPDATE cache_entries SET valid = 0 WHERE cache_key = ?",
                    (cache_key,),
                )
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE cache_entries SET last_used_at = CURRENT_TIMESTAMP
                 WHERE cache_key = ?
                """,
                (cache_key,),
            )
            connection.commit()
            return path

    def put(self, cache_key: str, audio_path: str | Path) -> Path:
        path = Path(audio_path)
        relative = path.resolve().relative_to(self.project.resolve()).as_posix()
        sha256 = self._sha256(path)
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            channels = handle.getnchannels()
            duration = handle.getnframes() / float(rate)
        size = path.stat().st_size
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO cache_entries(
                    cache_key, output_path, content_sha256, file_path, file_sha256,
                    duration, sample_rate, channels, size_bytes, valid,
                    created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(cache_key) DO UPDATE SET
                    output_path = excluded.output_path,
                    content_sha256 = excluded.content_sha256,
                    file_path = excluded.file_path,
                    file_sha256 = excluded.file_sha256,
                    duration = excluded.duration,
                    sample_rate = excluded.sample_rate,
                    channels = excluded.channels,
                    size_bytes = excluded.size_bytes,
                    valid = 1,
                    last_used_at = CURRENT_TIMESTAMP
                """,
                (
                    cache_key,
                    relative,
                    sha256,
                    relative,
                    sha256,
                    duration,
                    rate,
                    channels,
                    size,
                ),
            )
            connection.commit()
        return path

    def invalidate(self, cache_key: str) -> bool:
        """使指定缓存条目失效（仅标记，不删物理文件），返回是否有条目被标记。

        用于「重新生成指定 segment」：把对应任务的缓存作废，使其下次必重新合成，
        且不影响其他缓存条目。
        """
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE cache_entries SET valid = 0, last_used_at = CURRENT_TIMESTAMP
                 WHERE cache_key = ? AND valid = 1
                """,
                (cache_key,),
            )
            connection.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
