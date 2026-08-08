"""Thin MCP adapters for Character Roster and Voice Cast services."""
from __future__ import annotations

from typing import Any, Callable

from services import VoiceCastError, VoiceCastResolver
from services.voice_assets import VoiceAssetError


def _failure(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, VoiceCastError):
        return exc.as_payload()
    if isinstance(exc, VoiceAssetError):
        return {"success": False, "errors": [exc.as_issue()]}
    return {
        "success": False,
        "errors": [{"code": type(exc).__name__, "message": str(exc)}],
    }


def _call(function: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        return _failure(exc)


def _project(arguments: dict[str, Any]) -> str:
    return str(arguments.get("project_name") or "").strip()


def set_character_roster(arguments: dict[str, Any]) -> dict[str, Any]:
    roles = arguments.get("roles", arguments.get("roster"))
    return _call(VoiceCastResolver.set_character_roster, _project(arguments), roles)


def get_character_roster(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(VoiceCastResolver.get_character_roster, _project(arguments))


def add_character_roles(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(VoiceCastResolver.add_character_roles, _project(arguments), arguments.get("roles"))


def update_character_role(arguments: dict[str, Any]) -> dict[str, Any]:
    updates = arguments.get("updates")
    if not isinstance(updates, dict):
        updates = {
            key: value for key, value in arguments.items()
            if key not in {"project_name", "role_id"}
        }
    return _call(
        VoiceCastResolver.update_character_role,
        _project(arguments), arguments.get("role_id"), updates,
    )


def validate_character_roster(arguments: dict[str, Any]) -> dict[str, Any]:
    roles = arguments.get("roles", arguments.get("roster"))
    return _call(VoiceCastResolver.validate_character_roster, _project(arguments), roles)


def set_voice_cast(arguments: dict[str, Any]) -> dict[str, Any]:
    roles = arguments.get("roles", arguments.get("cast"))
    return _call(VoiceCastResolver.set_voice_cast, _project(arguments), roles)


def get_voice_cast(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(VoiceCastResolver.get_voice_cast, _project(arguments))


def bind_cast_role(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(
        VoiceCastResolver.bind_cast_role,
        _project(arguments),
        arguments.get("role_id"),
        arguments.get("voice_asset_id"),
        bool(arguments.get("force_rebind", False)),
    )


def validate_voice_cast(arguments: dict[str, Any]) -> dict[str, Any]:
    cast = arguments.get("roles", arguments.get("cast"))
    return _call(VoiceCastResolver.validate_voice_cast, _project(arguments), cast)


def finalize_voice_cast(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(VoiceCastResolver.finalize_voice_cast, _project(arguments))


def get_voice_binding_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(VoiceCastResolver.get_voice_binding_status, _project(arguments))


def check_chapter_roles(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(
        VoiceCastResolver.check_chapter_roles,
        _project(arguments), arguments.get("chapters", []),
    )


__all__ = [
    "add_character_roles", "bind_cast_role", "check_chapter_roles",
    "finalize_voice_cast", "get_character_roster", "get_voice_binding_status",
    "get_voice_cast", "set_character_roster", "set_voice_cast",
    "update_character_role", "validate_character_roster", "validate_voice_cast",
]
