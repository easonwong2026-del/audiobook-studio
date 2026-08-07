"""Stable, path-free voice asset discovery for MCP and Voice Cast."""
from __future__ import annotations

import hashlib
import os
from typing import Any

from lib import config
from lib import voice_lib


AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"})


class VoiceAssetError(ValueError):
    """Structured domain error raised by the voice asset service."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_issue(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **self.details}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _category(filename: str) -> str:
    try:
        return str(voice_lib._category_of(filename))
    except AttributeError:
        stem = os.path.basename(filename).split("_", 1)[0]
        return stem or "未分类"


class VoiceAssetService:
    """Scan the global voice library and expose stable metadata only.

    ``voice_asset_id`` is ``voice_`` plus the first 12 hexadecimal characters
    of the file's full SHA-256.  It is deterministic across restarts and a
    content change necessarily produces a new ID.  Two byte-identical files
    intentionally represent the same reusable asset.
    """

    @staticmethod
    def _scan_records() -> list[dict[str, Any]]:
        root = config.get_voice_library()
        if not root or not os.path.isdir(root):
            return []
        records: list[dict[str, Any]] = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return []
        for filename in names:
            path = os.path.join(root, filename)
            if not os.path.isfile(path) or os.path.splitext(filename)[1].lower() not in AUDIO_EXTENSIONS:
                continue
            try:
                size_bytes = os.path.getsize(path)
                digest = _sha256(path)
            except OSError:
                # A file can disappear while the library is being scanned.
                continue
            stem = os.path.splitext(filename)[0]
            category = _category(filename)
            records.append({
                "voice_asset_id": f"voice_{digest[:12]}",
                "name": stem,
                "category": category,
                "tags": [],
                "file_name": filename,
                "sha256": digest,
                "size_bytes": size_bytes,
                "_path": os.path.abspath(path),
            })
        return records

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record[key]
            for key in (
                "voice_asset_id", "name", "category", "tags", "file_name",
                "sha256", "size_bytes",
            )
        }

    @classmethod
    def list_assets(cls, search: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        query = str(search or "").strip().casefold()
        requested_category = str(category or "").strip()
        result: list[dict[str, Any]] = []
        for record in cls._scan_records():
            if requested_category and record["category"] != requested_category:
                continue
            if query:
                haystack = " ".join(
                    [record["name"], record["file_name"], record["category"], *record["tags"]]
                ).casefold()
                if query not in haystack:
                    continue
            result.append(cls._public(record))
        return result

    @classmethod
    def list_voice_assets(cls, search: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        """Named alias used by the MCP adapter and external callers."""
        return cls.list_assets(search, category)

    @classmethod
    def get_record(cls, voice_asset_id: str) -> dict[str, Any]:
        asset_id = str(voice_asset_id or "").strip()
        if not asset_id:
            raise VoiceAssetError("VOICE_ASSET_ID_REQUIRED", "voice_asset_id 不能为空")
        matches = [record for record in cls._scan_records() if record["voice_asset_id"] == asset_id]
        if not matches:
            raise VoiceAssetError(
                "VOICE_ASSET_NOT_FOUND",
                "指定音色资产不存在",
                voice_asset_id=asset_id,
            )
        # Identical files share an ID; choose the first deterministic filename
        # while exposing all duplicates only through list_voice_assets.
        return matches[0]

    @classmethod
    def get_asset(cls, voice_asset_id: str) -> dict[str, Any]:
        return cls._public(cls.get_record(voice_asset_id))

    @classmethod
    def get_voice_asset(cls, voice_asset_id: str) -> dict[str, Any]:
        return cls.get_asset(voice_asset_id)

    @classmethod
    def resolve_path(cls, voice_asset_id: str) -> str:
        record = cls.get_record(voice_asset_id)
        path = record["_path"]
        if not os.path.isfile(path):
            raise VoiceAssetError(
                "VOICE_ASSET_AUDIO_MISSING",
                "音色资产文件不存在",
                voice_asset_id=voice_asset_id,
                file_name=record["file_name"],
            )
        return path

    @classmethod
    def asset_id_for_path(cls, path: str) -> str:
        """Return the same ID rule for a validated audio path."""
        try:
            digest = _sha256(path)
        except (OSError, TypeError) as exc:
            raise VoiceAssetError("VOICE_ASSET_AUDIO_MISSING", "音频文件无法读取", path=str(path)) from exc
        return f"voice_{digest[:12]}"


def list_voice_assets(search: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
    return VoiceAssetService.list_assets(search, category)


def get_voice_asset(voice_asset_id: str) -> dict[str, Any]:
    return VoiceAssetService.get_asset(voice_asset_id)


__all__ = [
    "AUDIO_EXTENSIONS",
    "VoiceAssetError",
    "VoiceAssetService",
    "get_voice_asset",
    "list_voice_assets",
]
