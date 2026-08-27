"""Thin MCP adapters for stable global voice assets."""
from __future__ import annotations

from typing import Any

from services import VoiceAssetError, VoiceAssetService


def _failure(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, VoiceAssetError):
        return {"success": False, "errors": [exc.as_issue()]}
    return {
        "success": False,
        "errors": [{"code": type(exc).__name__, "message": str(exc)}],
    }


def list_voice_assets(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    arguments = arguments or {}
    try:
        asset_id = str(arguments.get("voice_asset_id") or "").strip()
        if asset_id:
            return {"items": [VoiceAssetService.get_asset(asset_id)]}
        return {
            "items": VoiceAssetService.list_assets(
                arguments.get("search"), arguments.get("category")
            )
        }
    except Exception as exc:
        return _failure(exc)


def get_voice_asset(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        asset = VoiceAssetService.get_asset(arguments.get("voice_asset_id"))
        return {"success": True, **asset}
    except Exception as exc:
        return _failure(exc)


__all__ = ["get_voice_asset", "list_voice_assets"]
