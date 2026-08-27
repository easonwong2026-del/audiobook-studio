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


def _records(value: Any, *, bindings: bool = False) -> list[dict[str, Any]]:
    if isinstance(value, dict) and "roles" in value:
        value = value.get("roles")
    if isinstance(value, dict):
        result = []
        for key, raw in value.items():
            if bindings and isinstance(raw, str):
                item = {"voice_asset_id": raw}
            else:
                item = dict(raw) if isinstance(raw, dict) else {}
            item.setdefault("role_id", str(key))
            result.append(item)
        return result
    if isinstance(value, list):
        if not all(isinstance(item, dict) for item in value):
            raise ValueError("roles/bindings 中每项必须是对象")
        return [dict(item) for item in value]
    if value is None:
        return []
    raise ValueError("roles/bindings 必须是对象或数组")


def _role_id(record: dict[str, Any]) -> str:
    role_id = str(record.get("role_id") or "").strip()
    if not role_id:
        raise ValueError("每个角色必须包含 role_id")
    return role_id


def _configure_roster(project: str, value: Any) -> list[str]:
    records = _records(value)
    current = VoiceCastResolver.get_character_roster(project)
    current_roles = current.get("roles", {}) if current.get("exists") else {}
    additions = [record for record in records if _role_id(record) not in current_roles]
    changed: list[str] = [_role_id(record) for record in additions]
    if not current.get("exists"):
        VoiceCastResolver.set_character_roster(project, records)
        return changed
    if additions:
        VoiceCastResolver.add_character_roles(project, additions)
    role_fields = {
        "name", "aliases", "type", "importance", "gender", "age_stage", "description",
    }
    for record in records:
        role_id = _role_id(record)
        if role_id not in current_roles:
            continue
        previous = current_roles[role_id]
        updates = {
            key: record[key]
            for key in role_fields
            if key in record and record[key] != previous.get(key)
        }
        if updates:
            VoiceCastResolver.update_character_role(project, role_id, updates)
            changed.append(role_id)
    return changed


def _configure_bindings(project: str, value: Any, force_rebind: bool) -> list[str]:
    records = _records(value, bindings=True)
    cast = VoiceCastResolver.get_voice_cast(project)
    changed: list[str] = []
    if not cast.get("exists"):
        payload: dict[str, dict[str, Any]] = {}
        for record in records:
            role_id = _role_id(record)
            asset_id = str(record.get("voice_asset_id") or "").strip()
            if not asset_id:
                raise ValueError(f"角色 {role_id} 必须包含 voice_asset_id")
            payload[role_id] = {"voice_asset_id": asset_id}
            changed.append(role_id)
        VoiceCastResolver.set_voice_cast(project, payload)
        return changed
    for record in records:
        role_id = _role_id(record)
        asset_id = str(record.get("voice_asset_id") or "").strip()
        if not asset_id:
            raise ValueError(f"角色 {role_id} 必须包含 voice_asset_id")
        previous = cast.get("roles", {}).get(role_id, {})
        if str(previous.get("voice_asset_id") or "").strip() == asset_id:
            continue
        VoiceCastResolver.bind_cast_role(project, role_id, asset_id, force_rebind)
        changed.append(role_id)
    return changed


def _aggregate_voice_cast(project: str) -> dict[str, Any]:
    cast = VoiceCastResolver.get_voice_cast(project)
    roster = VoiceCastResolver.get_character_roster(project)
    binding_status = VoiceCastResolver.get_voice_binding_status(project)
    confirmation = VoiceCastResolver.get_confirmation_state(project)
    validation = {
        "valid": bool(cast.get("valid", False)),
        "ready": bool(cast.get("ready", False)),
        "errors": list(cast.get("errors") or []),
        "warnings": list(cast.get("warnings") or []),
        "summary": dict(cast.get("summary") or {}),
    }
    runtime = {
        key: binding_status.get(key)
        for key in (
            "runtime_status", "engine_state", "engine_ready", "synthesis_ready",
        )
    }
    lock_state = {
        "status": str(cast.get("status") or "draft"),
        "cast_locked": bool(binding_status.get("cast_locked", False)),
        "locked": int(binding_status.get("locked") or 0),
        "locked_roles": list(binding_status.get("locked_roles") or []),
    }
    return {
        **cast,
        "roster": roster,
        "bindings": dict(cast.get("roles") or {}),
        "validation": validation,
        "readiness": {
            "cast_ready": bool(binding_status.get("cast_ready", cast.get("ready", False))),
            "production_ready": bool(binding_status.get("production_ready", False)),
            "engine_ready": bool(binding_status.get("engine_ready", False)),
            "synthesis_ready": bool(binding_status.get("synthesis_ready", False)),
        },
        "lock_state": lock_state,
        "confirmation": confirmation,
        "binding_status": binding_status,
        "runtime": runtime,
        "runtime_status": runtime["runtime_status"],
        "engine_state": runtime["engine_state"],
        "engine_ready": runtime["engine_ready"],
        "synthesis_ready": runtime["synthesis_ready"],
    }


def configure_voice_cast(arguments: dict[str, Any]) -> dict[str, Any]:
    """Apply roster/binding changes and return the unconfirmed aggregate state."""
    project = _project(arguments)
    try:
        roles = arguments.get("roles")
        if roles is None:
            roles = arguments.get("roster")
        bindings = arguments.get("bindings")
        if bindings is None:
            bindings = arguments.get("voice_bindings")
        if bindings is None:
            bindings = arguments.get("voice_cast", arguments.get("cast"))
        if bindings is None and roles is not None:
            embedded = [
                record for record in _records(roles)
                if str(record.get("voice_asset_id") or "").strip()
            ]
            if embedded:
                bindings = embedded
        changed: list[str] = []
        if roles is not None:
            changed.extend(_configure_roster(project, roles))
        if bindings is not None:
            changed.extend(
                _configure_bindings(
                    project, bindings, bool(arguments.get("force_rebind", False))
                )
            )
        state = get_voice_cast({"project_name": project})
        if state.get("success") is False:
            return state
        return {
            "success": True,
            "configured": roles is not None or bindings is not None,
            "changed_roles": list(dict.fromkeys(changed)),
            **state,
        }
    except Exception as exc:
        return _failure(exc)


def get_voice_cast(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(_aggregate_voice_cast, _project(arguments))


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


def confirm_voice_cast(arguments: dict[str, Any]) -> dict[str, Any]:
    """Record an explicit human confirmation of the current Voice Cast.

    Only call this after the user has explicitly confirmed the
    role -> voice mapping.  Sets confirmed_revision = cast_revision and locks
    the cast; any later bind/rebind invalidates the confirmation.
    """
    return _call(VoiceCastResolver.confirm_voice_cast, _project(arguments))


def get_voice_cast_confirmation(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the confirmation gate state without mutating the cast."""
    return _call(VoiceCastResolver.get_confirmation_state, _project(arguments))


def get_voice_binding_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(VoiceCastResolver.get_voice_binding_status, _project(arguments))


def check_chapter_roles(arguments: dict[str, Any]) -> dict[str, Any]:
    return _call(
        VoiceCastResolver.check_chapter_roles,
        _project(arguments), arguments.get("chapters", []),
    )


__all__ = [
    "add_character_roles", "bind_cast_role", "check_chapter_roles",
    "configure_voice_cast", "confirm_voice_cast", "finalize_voice_cast",
    "get_character_roster",
    "get_voice_binding_status", "get_voice_cast", "get_voice_cast_confirmation",
    "set_character_roster", "set_voice_cast",
    "update_character_role", "validate_character_roster", "validate_voice_cast",
]
