"""Project-local persistence for Character Roster and Voice Cast JSON files.

The repository deliberately knows nothing about role matching or audio
assets.  It only provides atomic reads/writes for the two Phase-2 documents so
the service layer remains the single place that enforces lifecycle rules.

Since v3 the two documents live under ``99_系统数据/配置/``; v2/v1 projects
keep them at the project root.  All reads/writes resolve through
``lib.project_paths``.
"""
from __future__ import annotations

import json
import os
from typing import Any

from lib import project_paths
from ._atomic import atomic_write


class VoiceCastRepository:
    """Atomic storage boundary for ``character_roster.json`` and ``voice_cast.json``."""

    ROSTER_FILE = "character_roster.json"
    CAST_FILE = "voice_cast.json"

    _FILE_KEY = {
        ROSTER_FILE: "character_roster",
        CAST_FILE: "voice_cast",
    }

    @staticmethod
    def _path(project_dir: str, filename: str) -> str:
        key = VoiceCastRepository._FILE_KEY.get(filename, filename)
        return project_paths.project_file(project_dir, key)

    @staticmethod
    def _load(project_dir: str, filename: str) -> dict[str, Any] | None:
        path = VoiceCastRepository._path(project_dir, filename)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as file:
                value = json.load(file)
            return value if isinstance(value, dict) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _save(project_dir: str, filename: str, value: dict[str, Any]) -> None:
        path = VoiceCastRepository._path(project_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write(path, value)

    @classmethod
    def load_roster(cls, project_dir: str) -> dict[str, Any] | None:
        return cls._load(project_dir, cls.ROSTER_FILE)

    @classmethod
    def save_roster(cls, project_dir: str, value: dict[str, Any]) -> None:
        cls._save(project_dir, cls.ROSTER_FILE, value)

    @classmethod
    def load_cast(cls, project_dir: str) -> dict[str, Any] | None:
        return cls._load(project_dir, cls.CAST_FILE)

    @classmethod
    def save_cast(cls, project_dir: str, value: dict[str, Any]) -> None:
        cls._save(project_dir, cls.CAST_FILE, value)


CharacterRosterRepository = VoiceCastRepository


__all__ = ["CharacterRosterRepository", "VoiceCastRepository"]
