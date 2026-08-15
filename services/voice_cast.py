"""Character Roster and Voice Cast lifecycle services.

This module is intentionally independent of Gradio.  The UI and MCP adapters
call the same methods, while the existing ``voice_bindings.json`` remains the
runtime bridge consumed by the current synthesis queue.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from typing import Any

from lib import project_paths, script_loader, segment_cache
from repositories.project_repo import ProjectRepository
from repositories.voice_cast_repo import VoiceCastRepository

from .runtime_engine import read_runtime_engine_status
from .voice_assets import VoiceAssetError, VoiceAssetService


class VoiceCastError(ValueError):
    """A domain error with a machine-readable code and optional issue list."""

    def __init__(self, code: str, message: str, *, errors: list[dict[str, Any]] | None = None, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
        self.errors = list(errors or [])

    def as_issue(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **self.details}

    def as_payload(self) -> dict[str, Any]:
        errors = self.errors or [self.as_issue()]
        return {"success": False, "valid": False, "ready": False, "errors": errors}


_ROLE_FIELDS = (
    "name", "aliases", "type", "importance", "gender", "age_stage", "description",
)
_DEFAULT_ROLE_FIELDS = {
    "aliases": [],
    "type": "character",
    "importance": "supporting",
    "gender": "unknown",
    "age_stage": "unknown",
    "description": "",
}


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _project_dir(project_name: str) -> str:
    name = str(project_name or "").strip()
    if not name:
        raise VoiceCastError("PROJECT_NAME_REQUIRED", "project_name 不能为空")
    path = ProjectRepository.get_project_dir(name)
    if not os.path.isdir(path):
        raise VoiceCastError("PROJECT_NOT_FOUND", "项目不存在", project_name=name)
    return path


def _guard_mutation(project_name: str, operation: str) -> None:
    """Keep cast/roster writes from racing an active production attempt."""
    from .project import ensure_project_mutation_allowed

    ensure_project_mutation_allowed(project_name, operation)


def _role_records(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize roster roles from either the public list or mapping form."""
    if isinstance(value, dict) and "roles" in value:
        value = value.get("roles")
    records: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, raw in value.items():
            item = dict(raw) if isinstance(raw, dict) else {}
            item.setdefault("role_id", str(key))
            records.append(item)
    elif isinstance(value, list):
        for index, raw in enumerate(value):
            if not isinstance(raw, dict):
                parse_errors.append(_issue("INVALID_ROLE", f"roles[{index}] 必须是对象", path=f"roles[{index}]"))
                continue
            records.append(dict(raw))
    elif value is not None:
        parse_errors.append(_issue("INVALID_ROLES", "roles 必须是对象或数组", path="roles"))
    return records, parse_errors


def _normalize_role(record: dict[str, Any]) -> dict[str, Any]:
    result = {key: record.get(key, default) for key, default in _DEFAULT_ROLE_FIELDS.items()}
    result["role_id"] = str(record.get("role_id") or "").strip()
    result["name"] = str(record.get("name") or "").strip()
    aliases = record.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [aliases]
    result["aliases"] = [str(alias).strip() for alias in aliases] if isinstance(aliases, list) else []
    for key in ("type", "importance", "gender", "age_stage", "description"):
        result[key] = str(record.get(key, result[key]) or "").strip()
    return result


def _roster_map(document: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(document, dict):
        return {}
    records, _ = _role_records(document)
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        normalized = _normalize_role(record)
        if normalized["role_id"]:
            result[normalized["role_id"]] = normalized
    return result


def _roster_document(project_name: str, roles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": "1.0",
        "project_name": str(project_name),
        "roles": roles,
    }


def _cast_records(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(value, dict) and "roles" in value:
        value = value.get("roles")
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, raw in value.items():
            item = dict(raw) if isinstance(raw, dict) else {}
            item.setdefault("role_id", str(key))
            records.append(item)
    elif isinstance(value, list):
        for index, raw in enumerate(value):
            if not isinstance(raw, dict):
                errors.append(_issue("INVALID_CAST_ROLE", f"roles[{index}] 必须是对象", path=f"roles[{index}]"))
                continue
            records.append(dict(raw))
    elif value is not None:
        errors.append(_issue("INVALID_CAST_ROLES", "roles 必须是对象或数组", path="roles"))
    return records, errors


def _normalize_cast(project_name: str, value: Any, roster: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if isinstance(value, dict) and "roles" in value:
        status = str(value.get("status") or "draft")
        raw_roles = value.get("roles")
        raw_document = value
    else:
        status = "draft"
        raw_roles = value
        raw_document = None
    records, _ = _cast_records(raw_roles)
    roles: dict[str, dict[str, Any]] = {}
    for raw in records:
        role_id = str(raw.get("role_id") or "").strip()
        if not role_id:
            continue
        role = {
            "name": str(raw.get("name") or roster.get(role_id, {}).get("name") or role_id),
            "voice_asset_id": str(raw.get("voice_asset_id") or "").strip() or None,
            "locked": bool(raw.get("locked", False)),
        }
        for key in ("voice_sha256", "project_voice_path"):
            if raw.get(key):
                role[key] = str(raw[key])
        roles[role_id] = role
    if status not in {"draft", "locked"}:
        status = "draft"
    # Lifecycle revision fields (人工确认门): preserved across re-normalization
    # so every create/modify/rebind can be tracked without external state.
    def _revision(value: Any, default: int = 0) -> int:
        try:
            return max(int(value or default), 0)
        except (TypeError, ValueError):
            return default

    cast_revision = _revision(
        raw_document.get("cast_revision") if raw_document is not None else None
    )
    confirmed_raw = raw_document.get("confirmed_revision") if raw_document is not None else None
    confirmed_revision = _revision(confirmed_raw, 0) if confirmed_raw not in (None, "") else None
    changed_raw = raw_document.get("changed_roles") if raw_document is not None else None
    changed_roles = (
        [str(item) for item in changed_raw]
        if isinstance(changed_raw, list) else []
    )
    return {
        "version": "1.0",
        "project_name": str(project_name),
        "status": status,
        "roles": roles,
        "cast_revision": cast_revision,
        "confirmed_revision": confirmed_revision,
        "changed_roles": changed_roles,
    }


def _cast_document(project_name: str, roles: dict[str, dict[str, Any]], status: str = "draft") -> dict[str, Any]:
    return {
        "version": "1.0",
        "project_name": str(project_name),
        "status": status if status in {"draft", "locked"} else "draft",
        "roles": roles,
        "cast_revision": 0,
        "confirmed_revision": None,
        "changed_roles": [],
    }


def _mark_cast_changed(document: dict[str, Any], role_ids: list[str] | None = None) -> None:
    """Bump cast_revision and invalidate the human confirmation.

    Called by every create/modify/rebind Voice Cast mutation.  Only an
    explicit ``confirm_voice_cast`` re-sets ``confirmed_revision``.
    """
    try:
        document["cast_revision"] = int(document.get("cast_revision") or 0) + 1
    except (TypeError, ValueError):
        document["cast_revision"] = 1
    document["confirmed_revision"] = None
    changed = document.get("changed_roles")
    if not isinstance(changed, list):
        changed = []
    for role_id in role_ids or []:
        role_id = str(role_id)
        if role_id and role_id not in changed:
            changed.append(role_id)
    document["changed_roles"] = changed


def _relative_project_voice_path(filename: str) -> str:
    # Keep the public project contract stable even when v2 stores the actual
    # file under 04_角色与声音 or exposes voices/ as a junction/symlink.
    return os.path.join("voices", filename).replace(os.sep, "/")


def _safe_component(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z一-龥_-]+", "_", str(value or "").strip())
    return result.strip("._") or "role"


def _snapshot_record(project_name: str, role_id: str, voice_asset_id: str) -> dict[str, Any] | None:
    """Resolve a project snapshot when the global library is unavailable."""
    project_dir = _project_dir(project_name)
    bindings = ProjectRepository.load_bindings(project_dir)
    role_binding = (bindings.get("role_bindings", {}) if isinstance(bindings, dict) else {}).get(role_id)
    if not isinstance(role_binding, dict) or role_binding.get("voice_asset_id") != voice_asset_id:
        return None
    project_path = str(role_binding.get("project_voice_path") or "")
    if not project_path:
        return None
    path = project_path if os.path.isabs(project_path) else os.path.join(project_dir, project_path)
    if not os.path.isfile(path):
        return None
    digest = segment_cache.speaker_fingerprint_for_path(path)
    expected = str(role_binding.get("voice_sha256") or "")
    if not digest or (expected and digest != expected):
        return None
    return {
        "voice_asset_id": voice_asset_id,
        "name": os.path.splitext(os.path.basename(path))[0],
        "category": "未分类",
        "tags": [],
        "file_name": os.path.basename(path),
        "sha256": digest,
        "size_bytes": os.path.getsize(path),
        "_path": path,
        "_snapshot": True,
    }


def _asset_for_role(project_name: str, role_id: str, binding: dict[str, Any]) -> dict[str, Any]:
    asset_id = str(binding.get("voice_asset_id") or "").strip()
    if not asset_id:
        raise VoiceCastError("VOICE_ASSET_ID_REQUIRED", "角色尚未绑定音色资产", role_id=role_id)
    try:
        record = VoiceAssetService.get_record(asset_id)
    except VoiceAssetError as exc:
        record = _snapshot_record(project_name, role_id, asset_id)
        if record is None:
            raise VoiceCastError(exc.code, str(exc), role_id=role_id, voice_asset_id=asset_id) from exc
    return record


def _project_voice_snapshot_path(
    project_dir: str,
    role_id: str,
    binding: dict[str, Any],
    bindings_document: dict[str, Any] | None = None,
) -> str:
    """Return the project-local voice snapshot path for one cast binding.

    Voice Cast records created by older builds may not contain
    ``project_voice_path`` even though the runtime bridge does.  Read that
    bridge as a compatibility fallback, but never fall back to the global
    library here: formal production must use a project-owned snapshot.
    """
    candidate = str(binding.get("project_voice_path") or "").strip()
    if not candidate and isinstance(bindings_document, dict):
        role_bindings = bindings_document.get("role_bindings", {})
        runtime_binding = role_bindings.get(role_id) if isinstance(role_bindings, dict) else None
        if isinstance(runtime_binding, dict):
            candidate = str(runtime_binding.get("project_voice_path") or "").strip()
    if not candidate:
        return ""
    return candidate if os.path.isabs(candidate) else os.path.join(project_dir, candidate)


def _read_roster(project_name: str) -> dict[str, Any] | None:
    return VoiceCastRepository.load_roster(_project_dir(project_name))


def _read_cast(project_name: str) -> dict[str, Any] | None:
    return VoiceCastRepository.load_cast(_project_dir(project_name))


def _raise_validation(result: dict[str, Any], fallback_code: str = "VALIDATION_FAILED") -> None:
    errors = list(result.get("errors") or [])
    if errors:
        first = errors[0]
        raise VoiceCastError(
            str(first.get("code") or fallback_code),
            str(first.get("message") or "校验失败"),
            errors=errors,
        )


class VoiceCastResolver:
    """Shared Character Roster / Voice Cast resolver and lifecycle service."""

    @classmethod
    def validate_character_roster(cls, project_name: str, roster: Any = None, roles: Any = None) -> dict[str, Any]:
        _project_dir(project_name)
        supplied = roster if roster is not None else roles
        if supplied is None:
            supplied = _read_roster(project_name)
        records, errors = _role_records(supplied)
        normalized: dict[str, dict[str, Any]] = {}
        role_id_seen: dict[str, int] = {}
        canonical_seen: dict[str, str] = {}
        aliases_seen: dict[str, str] = {}
        for index, raw in enumerate(records):
            role = _normalize_role(raw)
            role_id = role["role_id"]
            name = role["name"]
            path = f"roles[{index}]"
            if not role_id:
                errors.append(_issue("ROLE_ID_REQUIRED", "role_id 不能为空", path=f"{path}.role_id"))
            elif role_id in role_id_seen:
                errors.append(_issue("DUPLICATE_ROLE_ID", "role_id 重复", role_id=role_id, path=f"{path}.role_id"))
            else:
                role_id_seen[role_id] = index
            if not name:
                errors.append(_issue("ROLE_NAME_REQUIRED", "角色 canonical name 不能为空", path=f"{path}.name"))
            elif name in canonical_seen:
                errors.append(_issue(
                    "DUPLICATE_CANONICAL_NAME", "角色 canonical name 重复", name=name,
                    roles=[canonical_seen[name], role_id], path=f"{path}.name",
                ))
            else:
                canonical_seen[name] = role_id
            local_aliases: set[str] = set()
            for alias in role["aliases"]:
                if not alias:
                    errors.append(_issue("EMPTY_ALIAS", "alias 不能为空", role_id=role_id, path=f"{path}.aliases"))
                    continue
                if alias in local_aliases:
                    errors.append(_issue("DUPLICATE_ALIAS", "同一角色 alias 重复", role_id=role_id, alias=alias))
                local_aliases.add(alias)
                if alias in aliases_seen and aliases_seen[alias] != role_id:
                    errors.append(_issue(
                        "ALIAS_CONFLICT", "角色别名存在冲突", role_id=role_id, alias=alias,
                        conflicts_with=aliases_seen[alias], roles=[aliases_seen[alias], role_id],
                    ))
                else:
                    aliases_seen[alias] = role_id
            if role_id:
                normalized[role_id] = role

        for alias, role_id in aliases_seen.items():
            canonical_role = canonical_seen.get(alias)
            if canonical_role and canonical_role != role_id:
                errors.append(_issue(
                    "ALIAS_CANONICAL_CONFLICT", "alias 与另一角色 canonical name 冲突",
                    alias=alias, role_id=role_id, conflicts_with=canonical_role,
                ))
            elif canonical_role == role_id:
                errors.append(_issue(
                    "ALIAS_CANONICAL_CONFLICT", "alias 不能与本角色 canonical name 相同",
                    alias=alias, role_id=role_id,
                ))

        if not records and not errors:
            errors.append(_issue("ROSTER_EMPTY", "Character Roster 至少需要一个角色", path="roles"))

        return {
            "valid": not errors,
            "ready": not errors,
            "errors": errors,
            "warnings": [],
            "summary": {"roles": len(normalized), "errors": len(errors)},
            "roles": normalized,
        }

    @classmethod
    def get_character_roster(cls, project_name: str) -> dict[str, Any]:
        _project_dir(project_name)
        document = _read_roster(project_name)
        if not document:
            return {
                "exists": False,
                "version": "1.0",
                "project_name": project_name,
                "roles": {},
            }
        result = cls.validate_character_roster(project_name, document)
        return {
            "exists": True,
            "version": "1.0",
            "project_name": project_name,
            "roles": result["roles"],
            "valid": result["valid"],
            "errors": result["errors"],
        }

    @classmethod
    def set_character_roster(cls, project_name: str, roster: Any = None, roles: Any = None) -> dict[str, Any]:
        _guard_mutation(project_name, "set_character_roster")
        project_dir = _project_dir(project_name)
        if VoiceCastRepository.load_roster(project_dir) is not None or os.path.isfile(
            os.path.join(project_dir, VoiceCastRepository.ROSTER_FILE)
        ):
            raise VoiceCastError("ROSTER_EXISTS", "Character Roster 已存在，请使用 add 或 update")
        supplied = roster if roster is not None else roles
        result = cls.validate_character_roster(project_name, supplied)
        _raise_validation(result)
        document = _roster_document(project_name, result["roles"])
        VoiceCastRepository.save_roster(project_dir, document)
        return {"success": True, "created": True, **document}

    @classmethod
    def add_character_roles(cls, project_name: str, roles: Any = None, roster: Any = None) -> dict[str, Any]:
        _guard_mutation(project_name, "add_character_roles")
        project_dir = _project_dir(project_name)
        current_document = _read_roster(project_name)
        current = _roster_map(current_document)
        supplied = roles if roles is not None else roster
        new_records, parse_errors = _role_records(supplied)
        candidate = list(current.values())
        errors = list(parse_errors)
        for record in new_records:
            role = _normalize_role(record)
            if role["role_id"] in current:
                errors.append(_issue("ROLE_ALREADY_EXISTS", "role_id 已存在，请使用 update_character_role", role_id=role["role_id"]))
            candidate.append(role)
        validation = cls.validate_character_roster(project_name, candidate)
        validation["errors"] = errors + validation["errors"]
        validation["valid"] = not validation["errors"]
        _raise_validation(validation)
        merged = _roster_document(project_name, validation["roles"])
        VoiceCastRepository.save_roster(project_dir, merged)
        return {"success": True, "added": [item["role_id"] for item in new_records], **merged}

    @classmethod
    def update_character_role(cls, project_name: str, role_id: Any, updates: dict[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(role_id, dict):
            payload = role_id
            role_id = payload.get("role_id")
            updates = payload.get("updates") if isinstance(payload.get("updates"), dict) else payload
        _guard_mutation(project_name, "update_character_role")
        project_dir = _project_dir(project_name)
        roster = _roster_map(_read_roster(project_name))
        target = str(role_id or "").strip()
        if target not in roster:
            raise VoiceCastError("ROLE_NOT_FOUND", "指定 role_id 不存在", role_id=target)
        changes = dict(updates or {})
        if "role_id" in changes and str(changes["role_id"]).strip() != target:
            raise VoiceCastError("ROLE_ID_IMMUTABLE", "role_id 是稳定主键，不能修改", role_id=target)
        changes.pop("role_id", None)
        previous_role = dict(roster[target])
        merged = dict(previous_role)
        for key in _ROLE_FIELDS:
            if key in changes:
                merged[key] = changes[key]
        merged["role_id"] = target
        candidate = list(roster.values())
        candidate = [merged if item["role_id"] == target else item for item in candidate]
        validation = cls.validate_character_roster(project_name, candidate)
        _raise_validation(validation)
        document = _roster_document(project_name, validation["roles"])
        VoiceCastRepository.save_roster(project_dir, document)
        # Keep the runtime bridge usable when an explicit roster update changes
        # a canonical name or alias for an already-cast role.
        cast_document = _read_cast(project_name)
        if isinstance(cast_document, dict):
            cast = _normalize_cast(project_name, cast_document, validation["roles"])
            if target in cast["roles"]:
                cast["roles"][target]["name"] = validation["roles"][target]["name"]
                _mark_cast_changed(cast, [target])
                VoiceCastRepository.save_cast(project_dir, cast)
                cls.apply_cast(project_name, cast, [target])
                runtime_bindings = ProjectRepository.load_bindings(project_dir)
                inner = runtime_bindings.get("bindings", {}) if isinstance(runtime_bindings, dict) else {}
                new_names = {
                    validation["roles"][target]["name"],
                    *validation["roles"][target].get("aliases", []),
                }
                for old_name in {
                    previous_role.get("name"), *previous_role.get("aliases", [])
                } - new_names:
                    inner.pop(old_name, None)
                if isinstance(runtime_bindings, dict):
                    runtime_bindings["bindings"] = inner
                    ProjectRepository.save_bindings(project_dir, runtime_bindings)
        return {"success": True, "updated": target, **document}

    @classmethod
    def resolve_role(
        cls,
        project_name: str,
        segment: dict[str, Any] | None = None,
        *,
        role_id: str | None = None,
        role_name: str | None = None,
        roster: dict[str, Any] | None = None,
        allow_legacy: bool = True,
    ) -> dict[str, Any]:
        document = roster if roster is not None else _read_roster(project_name)
        roles = _roster_map(document)
        segment = segment if isinstance(segment, dict) else {}
        requested_id = str(role_id if role_id is not None else segment.get("role_id") or "").strip()
        requested_name = str(
            role_name if role_name is not None else segment.get("role") or segment.get("speaker") or ""
        ).strip()
        if requested_id:
            if requested_id in roles:
                return {"role_id": requested_id, "name": roles[requested_id]["name"], "role": roles[requested_id], "matched_by": "role_id"}
            raise VoiceCastError("ROLE_NOT_IN_ROSTER", "role_id 不在 Character Roster 中", role_id=requested_id, name=requested_name)
        if requested_name:
            canonical_matches = [rid for rid, role in roles.items() if role["name"] == requested_name]
            if canonical_matches:
                rid = canonical_matches[0]
                return {"role_id": rid, "name": roles[rid]["name"], "role": roles[rid], "matched_by": "canonical_name"}
            alias_matches = [rid for rid, role in roles.items() if requested_name in role.get("aliases", [])]
            if len(alias_matches) > 1:
                raise VoiceCastError(
                    "ALIAS_CONFLICT", "角色别名存在冲突", alias=requested_name,
                    roles=alias_matches,
                )
            if alias_matches:
                rid = alias_matches[0]
                return {"role_id": rid, "name": roles[rid]["name"], "role": roles[rid], "matched_by": "alias"}
        if allow_legacy and not roles and requested_name:
            return {"role_id": None, "name": requested_name, "role": None, "matched_by": "legacy_name", "legacy": True}
        raise VoiceCastError("ROLE_NOT_IN_ROSTER", "角色不在 Character Roster 中", name=requested_name, role_id=requested_id or None)

    @classmethod
    def resolve_segment_speaker_fingerprint(
        cls,
        project_name: str,
        segment: dict[str, Any] | None = None,
        *,
        role_id: str | None = None,
        role_name: str | None = None,
    ) -> str | None:
        """Return the content fingerprint of the voice bound to a segment's role.

        Voice Cast projects resolve ``segment.role_id`` (falling back to
        ``segment.role`` / ``segment.speaker`` canonical names) to the active
        ``role_bindings`` entry, then compute the SHA-256 content fingerprint
        of its ``project_voice_path``.  Legacy (non-Voice-Cast) projects return
        ``None`` so their speaker-agnostic caches keep their historical keys.

        Used by plan/retry accounting so a done segment produced under a Voice
        Cast binding is matched with its real speaker-aware cache key instead
        of being reported as remaining / retryable.
        """
        project_dir = _project_dir(project_name)
        if not os.path.isfile(os.path.join(project_dir, "voice_cast.json")):
            return None
        segment = segment if isinstance(segment, dict) else {}
        requested_id = str(
            role_id if role_id is not None else segment.get("role_id") or ""
        ).strip()
        requested_name = str(
            role_name if role_name is not None
            else segment.get("role") or segment.get("speaker") or ""
        ).strip()
        bindings = ProjectRepository.load_bindings(project_dir)
        role_bindings = bindings.get("role_bindings", {}) if isinstance(bindings, dict) else {}
        binding = role_bindings.get(requested_id)
        if not isinstance(binding, dict) and requested_name:
            binding = next(
                (item for item in role_bindings.values()
                 if isinstance(item, dict) and item.get("role_name") == requested_name),
                None,
            )
        if not isinstance(binding, dict):
            return None
        path = str(binding.get("project_voice_path") or "")
        if not path:
            return None
        if not os.path.isabs(path):
            path = os.path.join(project_dir, path)
        return segment_cache.speaker_fingerprint_for_path(path)

    @classmethod
    def _cast_validation(cls, project_name: str, cast: Any = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any] | None]:
        roster_document = _read_roster(project_name)
        roster = _roster_map(roster_document)
        supplied = cast if cast is not None else _read_cast(project_name)
        document = _normalize_cast(project_name, supplied or {}, roster)
        return document, roster, roster_document

    @classmethod
    def validate_voice_cast(cls, project_name: str, cast: Any = None, roles: Any = None) -> dict[str, Any]:
        _project_dir(project_name)
        supplied = cast if cast is not None else roles
        document, roster, _ = cls._cast_validation(project_name, supplied)
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        raw_cast = supplied if supplied is not None else _read_cast(project_name)
        raw_records, parse_errors = _cast_records(
            raw_cast.get("roles") if isinstance(raw_cast, dict) and "roles" in raw_cast else raw_cast
        )
        errors.extend(parse_errors)
        seen_cast_roles: set[str] = set()
        for raw in raw_records:
            rid = str(raw.get("role_id") or "").strip()
            if rid and rid in seen_cast_roles:
                errors.append(_issue("DUPLICATE_CAST_ROLE", "Voice Cast 中 role_id 重复", role_id=rid))
            if rid:
                seen_cast_roles.add(rid)
        if not roster:
            errors.append(_issue("ROSTER_NOT_FOUND", "请先设置 Character Roster"))
        current = _read_cast(project_name)
        current_roles = _normalize_cast(project_name, current or {}, roster).get("roles", {}) if current else {}
        for role_id, binding in document["roles"].items():
            if role_id not in roster:
                errors.append(_issue("ROLE_NOT_IN_ROSTER", "Voice Cast 中的 role_id 不存在", role_id=role_id))
                continue
            asset_id = binding.get("voice_asset_id")
            if not asset_id:
                continue
            try:
                asset = _asset_for_role(project_name, role_id, binding)
            except VoiceCastError as exc:
                errors.append(exc.as_issue())
                continue
            expected_sha = str(binding.get("voice_sha256") or "")
            if expected_sha and expected_sha != asset["sha256"]:
                errors.append(_issue(
                    "VOICE_ASSET_SHA256_MISMATCH", "音色资产内容哈希与演员表记录不一致",
                    role_id=role_id, voice_asset_id=asset_id,
                ))
            if asset.get("_snapshot"):
                warnings.append(_issue(
                    "VOICE_ASSET_SOURCE_MISSING_SNAPSHOT_USED",
                    "全局音色源不可用，使用项目声音快照",
                    role_id=role_id, voice_asset_id=asset_id,
                ))
            old = current_roles.get(role_id)
            if isinstance(old, dict) and old.get("locked") and old.get("voice_asset_id") != asset_id:
                errors.append(_issue(
                    "CAST_ROLE_LOCKED", "角色演员已锁定，不能普通换声",
                    role_id=role_id, voice_asset_id=asset_id,
                ))
        for role_id, old in current_roles.items():
            if role_id in document["roles"] and isinstance(old, dict) and old.get("locked"):
                new_id = document["roles"][role_id].get("voice_asset_id")
                if new_id != old.get("voice_asset_id"):
                    errors.append(_issue("CAST_ROLE_LOCKED", "角色演员已锁定，不能普通换声", role_id=role_id))
        unbound = [role_id for role_id in roster if not document["roles"].get(role_id, {}).get("voice_asset_id")]
        bound = len(roster) - len(unbound)
        valid = not errors
        return {
            "valid": valid,
            "ready": valid and not unbound,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "roles": len(roster),
                "bound": bound,
                "unbound": len(unbound),
                "errors": len(errors),
            },
            "unbound_roles": unbound,
            "cast": document,
        }

    @classmethod
    def get_voice_cast(cls, project_name: str) -> dict[str, Any]:
        _project_dir(project_name)
        roster = _roster_map(_read_roster(project_name))
        cast = _read_cast(project_name)
        if not cast:
            return {
                "exists": False, "version": "1.0", "project_name": project_name,
                "status": "draft", "roles": {},
            }
        document = _normalize_cast(project_name, cast, roster)
        validation = cls.validate_voice_cast(project_name, document)
        cast_revision = int(document.get("cast_revision") or 0)
        confirmed_raw = document.get("confirmed_revision")
        confirmed_revision = int(confirmed_raw) if confirmed_raw is not None else None
        confirmed = confirmed_revision is not None and confirmed_revision == cast_revision
        return {
            "exists": True,
            **document,
            "valid": validation["valid"],
            "ready": validation["ready"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "summary": validation["summary"],
            "confirmation_required": not confirmed,
            "confirmed": confirmed,
            "next_actions": (
                ["plan_production", "start_production"]
                if confirmed else
                ["get_voice_cast", "confirm_voice_cast"]
            ),
        }

    @classmethod
    def set_voice_cast(cls, project_name: str, cast: Any = None, roles: Any = None) -> dict[str, Any]:
        _guard_mutation(project_name, "set_voice_cast")
        project_dir = _project_dir(project_name)
        if os.path.isfile(os.path.join(project_dir, VoiceCastRepository.CAST_FILE)):
            raise VoiceCastError("CAST_EXISTS", "Voice Cast 已存在，请使用 bind_cast_role 或 update 流程")
        supplied = cast if cast is not None else roles
        validation = cls.validate_voice_cast(project_name, supplied)
        _raise_validation(validation)
        document = validation["cast"]
        document["status"] = "draft"
        _mark_cast_changed(document, list(document["roles"]))
        old_runtime_bindings = ProjectRepository.load_bindings(project_dir)
        segments_to_invalidate: list[str] = []
        # A project that already has formal output for a role starts that cast
        # entry locked, even if the incoming draft omitted ``locked``.
        for role_id, binding in document["roles"].items():
            if binding.get("voice_asset_id"):
                affected, _ = cls._matching_done_segments(project_name, role_id)
                if affected:
                    binding["locked"] = True
                    old_runtime = (old_runtime_bindings.get("role_bindings", {})
                                   if isinstance(old_runtime_bindings, dict) else {}).get(role_id, {})
                    old_path = str(old_runtime.get("project_voice_path") or "") if isinstance(old_runtime, dict) else ""
                    if old_path and not os.path.isabs(old_path):
                        old_path = os.path.join(project_dir, old_path)
                    old_fingerprint = segment_cache.speaker_fingerprint_for_path(old_path)
                    if old_fingerprint != binding.get("voice_sha256"):
                        segments_to_invalidate.extend(affected)
        VoiceCastRepository.save_cast(project_dir, document)
        applied = cls.apply_cast(project_name, document)
        invalidated = ProjectRepository.invalidate_done_segments(
            project_name, sorted(set(segments_to_invalidate))
        )
        return {"success": True, "segments_invalidated": invalidated, **applied}

    @classmethod
    def apply_cast(cls, project_name: str, cast: Any = None, role_ids: list[str] | None = None) -> dict[str, Any]:
        _guard_mutation(project_name, "apply_voice_cast")
        project_dir = _project_dir(project_name)
        roster = _roster_map(_read_roster(project_name))
        document = _normalize_cast(project_name, cast if cast is not None else _read_cast(project_name) or {}, roster)
        selected = {str(item) for item in role_ids} if role_ids else set(document["roles"])
        bindings = ProjectRepository.load_bindings(project_dir)
        if not isinstance(bindings, dict):
            bindings = {}
        bindings.setdefault("bindings", {})
        bindings.setdefault("role_bindings", {})
        bindings.setdefault("role_categories", {})
        applied_roles: list[str] = []
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        voice_dir = project_paths.project_dir(project_dir, "voices", create=True)
        compatibility_voice_dir = os.path.join(project_dir, "voices")
        for role_id, binding in document["roles"].items():
            if role_id not in selected or not binding.get("voice_asset_id") or role_id not in roster:
                continue
            asset = _asset_for_role(project_name, role_id, binding)
            filename = f"{_safe_component(role_id)}_{asset['sha256'][:8]}{os.path.splitext(asset['file_name'])[1].lower() or '.wav'}"
            destination = os.path.join(voice_dir, filename)
            source = asset["_path"]
            if os.path.abspath(source) != os.path.abspath(destination):
                if not os.path.isfile(destination) or segment_cache.speaker_fingerprint_for_path(destination) != asset["sha256"]:
                    shutil.copy2(source, destination)
            # Windows may have a real compatibility directory instead of a
            # junction.  Keep the documented project/voices path usable too.
            logical_destination = os.path.join(compatibility_voice_dir, filename)
            if os.path.realpath(logical_destination) != os.path.realpath(destination):
                os.makedirs(compatibility_voice_dir, exist_ok=True)
                if not os.path.isfile(logical_destination) or segment_cache.speaker_fingerprint_for_path(logical_destination) != asset["sha256"]:
                    shutil.copy2(source, logical_destination)
            project_relative = _relative_project_voice_path(filename)
            binding.update({
                "name": roster[role_id]["name"],
                "voice_asset_id": asset["voice_asset_id"],
                "voice_sha256": asset["sha256"],
                "project_voice_path": project_relative,
            })
            runtime_names = [roster[role_id]["name"], *roster[role_id].get("aliases", [])]
            for runtime_name in dict.fromkeys(runtime_names):
                bindings["bindings"][runtime_name] = os.path.abspath(destination)
                bindings["role_categories"][runtime_name] = asset.get("category") or "未分类"
            bindings["role_bindings"][role_id] = {
                "role_name": roster[role_id]["name"],
                "voice_asset_id": asset["voice_asset_id"],
                "voice_sha256": asset["sha256"],
                "project_voice_path": project_relative,
                "locked": bool(binding.get("locked", False)),
                "bound_at": now,
            }
            applied_roles.append(role_id)
        bindings["bound_at"] = now
        ProjectRepository.save_bindings(project_dir, bindings)
        try:
            shutil.copy2(
                os.path.join(project_dir, "voice_bindings.json"),
                os.path.join(voice_dir, "voice_bindings.json"),
            )
        except OSError:
            pass
        document["roles"] = {key: value for key, value in document["roles"].items()}
        VoiceCastRepository.save_cast(project_dir, document)
        return {"success": True, "cast": document, "applied_roles": applied_roles}

    @classmethod
    def _matching_done_segments(cls, project_name: str, role_id: str) -> tuple[list[str], str | None]:
        project_dir = _project_dir(project_name)
        meta, script, _ = ProjectRepository.load_project(project_name)
        roster = _read_roster(project_name)
        matched: list[str] = []
        old_path: str | None = None
        old_bindings = ProjectRepository.load_bindings(project_dir)
        role_binding = old_bindings.get("role_bindings", {}).get(role_id) if isinstance(old_bindings, dict) else None
        if isinstance(role_binding, dict):
            candidate = str(role_binding.get("project_voice_path") or "")
            old_path = candidate if os.path.isabs(candidate) else os.path.join(project_dir, candidate)
        _, chapters = script_loader.resolve_collections(script)
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            for segment in chapter.get("segments", []):
                if not isinstance(segment, dict):
                    continue
                try:
                    resolved = cls.resolve_role(project_name, segment, roster=roster, allow_legacy=not bool(roster))
                except VoiceCastError:
                    continue
                if resolved.get("role_id") == role_id and meta.segments_status.get(str(segment.get("id"))) == "done":
                    matched.append(str(segment.get("id")))
        return matched, old_path

    @classmethod
    def bind_cast_role(
        cls,
        project_name: str,
        role_id: Any = None,
        voice_asset_id: str | None = None,
        force_rebind: bool = False,
    ) -> dict[str, Any]:
        if isinstance(role_id, dict):
            payload = role_id
            role_id = payload.get("role_id")
            voice_asset_id = payload.get("voice_asset_id")
            force_rebind = bool(payload.get("force_rebind", force_rebind))
        _guard_mutation(project_name, "bind_cast_role")
        project_dir = _project_dir(project_name)
        rid = str(role_id or "").strip()
        roster = _roster_map(_read_roster(project_name))
        if rid not in roster:
            raise VoiceCastError("ROLE_NOT_IN_ROSTER", "指定 role_id 不存在", role_id=rid)
        aid = str(voice_asset_id or "").strip()
        if not aid:
            raise VoiceCastError("VOICE_ASSET_ID_REQUIRED", "voice_asset_id 不能为空", role_id=rid)
        cast = _normalize_cast(project_name, _read_cast(project_name) or {}, roster)
        old = cast["roles"].get(rid, {})
        old_id = old.get("voice_asset_id")
        if old.get("locked") and old_id != aid and not force_rebind:
            raise VoiceCastError("CAST_ROLE_LOCKED", "角色演员已锁定，普通修改被拒绝", role_id=rid)
        # Validate the selected asset before touching project state.
        try:
            asset = VoiceAssetService.get_record(aid)
        except Exception as exc:
            if isinstance(exc, VoiceAssetError):
                raise VoiceCastError(exc.code, str(exc), role_id=rid, voice_asset_id=aid) from exc
            raise
        affected_ids: list[str] = []
        old_path: str | None = None
        if old_id != aid:
            affected_ids, old_path = cls._matching_done_segments(project_name, rid)
        if affected_ids and old_id != aid and not force_rebind:
            raise VoiceCastError(
                "CAST_ROLE_LOCKED",
                "角色已经产生正式音频，换声必须显式 force_rebind",
                role_id=rid,
                segments=len(affected_ids),
            )
        role_binding = {
            "name": roster[rid]["name"],
            "voice_asset_id": aid,
            "voice_sha256": asset["sha256"],
            "locked": bool(old.get("locked")) if old_id == aid else bool(old.get("locked") or affected_ids),
        }
        cast["roles"][rid] = role_binding
        if old_id != aid and cast.get("status") == "locked" and not force_rebind:
            cast["status"] = "draft"
        _mark_cast_changed(cast, [rid])
        VoiceCastRepository.save_cast(project_dir, cast)
        applied = cls.apply_cast(project_name, cast, [rid])
        invalidated = 0
        if force_rebind and old_id != aid:
            invalidated = ProjectRepository.invalidate_done_segments(project_name, affected_ids)
            # The singleton runtime owns the embedding cache.  Rebinding writes
            # a content-hashed project voice path, so the next job naturally
            # uses a new cache key without importing TTS in this client process.
        return {
            "success": True,
            "role_id": rid,
            "voice_asset_id": aid,
            "segments_invalidated": invalidated,
            "affected_segment_count": len(affected_ids),
            "cast": applied["cast"],
        }

    @classmethod
    def finalize_voice_cast(cls, project_name: str) -> dict[str, Any]:
        """Lock the cast for production without recording a user confirmation.

        Legacy semantic: marks every bound role ``locked`` so a later bind
        requires ``force_rebind``.  This is *not* the human confirmation gate;
        use ``confirm_voice_cast`` for the explicit user-confirmation flow.
        """
        _guard_mutation(project_name, "finalize_voice_cast")
        project_dir = _project_dir(project_name)
        validation = cls.validate_voice_cast(project_name)
        if not validation["ready"]:
            errors = list(validation.get("errors") or [])
            for role_id in validation.get("unbound_roles", []):
                errors.append(_issue("ROLE_UNBOUND", "角色尚未绑定声音", role_id=role_id))
            raise VoiceCastError("CAST_NOT_READY", "Voice Cast 尚未完成，不能锁定", errors=errors)
        document = validation["cast"]
        document["status"] = "locked"
        for binding in document["roles"].values():
            if binding.get("voice_asset_id"):
                binding["locked"] = True
        _mark_cast_changed(document, [])
        VoiceCastRepository.save_cast(project_dir, document)
        applied = cls.apply_cast(project_name, document)
        return {"success": True, "status": "locked", "cast": applied["cast"]}

    @classmethod
    def confirm_voice_cast(cls, project_name: str) -> dict[str, Any]:
        """Human confirmation gate: record that a user explicitly confirmed.

        Sets ``confirmed_revision = cast_revision`` (current binding state) and
        locks every bound role.  Any later create/modify/rebind bumps
        ``cast_revision`` and clears ``confirmed_revision``, so a stale
        confirmation can never authorize production with changed voices.
        Returns machine-readable state including role_bindings and
        ``next_actions`` for the confirmation payload.
        """
        _guard_mutation(project_name, "confirm_voice_cast")
        project_dir = _project_dir(project_name)
        validation = cls.validate_voice_cast(project_name)
        if not validation["ready"]:
            errors = list(validation.get("errors") or [])
            for role_id in validation.get("unbound_roles", []):
                errors.append(_issue("ROLE_UNBOUND", "角色尚未绑定声音", role_id=role_id))
            raise VoiceCastError("CAST_NOT_READY", "Voice Cast 尚未完成，不能确认", errors=errors)
        document = validation["cast"]
        document["status"] = "locked"
        for binding in document["roles"].values():
            if binding.get("voice_asset_id"):
                binding["locked"] = True
        # A confirmation only captures the exact revision it validates; the
        # cast_revision is not bumped by confirming.
        document["confirmed_revision"] = int(document.get("cast_revision") or 0)
        document["changed_roles"] = []
        VoiceCastRepository.save_cast(project_dir, document)
        applied = cls.apply_cast(project_name, document)
        role_bindings = [
            {
                "role_id": role_id,
                "name": str(role.get("name") or ""),
                "voice_asset_id": str(role.get("voice_asset_id") or ""),
                "voice_sha256": str(role.get("voice_sha256") or ""),
                "locked": bool(role.get("locked", False)),
            }
            for role_id, role in applied["cast"].get("roles", {}).items()
            if isinstance(role, dict)
        ]
        return {
            "success": True,
            "status": "locked",
            "confirmed": True,
            "cast_revision": int(applied["cast"].get("cast_revision") or 0),
            "confirmed_revision": int(applied["cast"].get("confirmed_revision") or 0),
            "role_bindings": role_bindings,
            "changed_roles": list(applied["cast"].get("changed_roles") or []),
            "next_actions": ["plan_production", "start_production"],
            "cast": applied["cast"],
        }

    @classmethod
    def get_confirmation_state(cls, project_name: str) -> dict[str, Any]:
        """Return the confirmation gate state for one project (no mutation)."""
        _project_dir(project_name)
        roster = _roster_map(_read_roster(project_name))
        cast_document = _read_cast(project_name)
        if cast_document is None:
            return {
                "mode": "legacy_manual",
                "confirmed": True,
                "confirmation_required": False,
                "cast_revision": 0,
                "confirmed_revision": None,
                "role_bindings": [],
                "changed_roles": [],
            }
        cast = _normalize_cast(project_name, cast_document, roster)
        cast_revision = int(cast.get("cast_revision") or 0)
        confirmed_raw = cast.get("confirmed_revision")
        confirmed_revision = int(confirmed_raw) if confirmed_raw is not None else None
        confirmed = confirmed_revision is not None and confirmed_revision == cast_revision
        role_bindings = [
            {
                "role_id": role_id,
                "name": str(role.get("name") or ""),
                "voice_asset_id": str(role.get("voice_asset_id") or ""),
                "locked": bool(role.get("locked", False)),
            }
            for role_id, role in sorted(cast.get("roles", {}).items())
            if isinstance(role, dict)
        ]
        return {
            "mode": "voice_cast",
            "confirmed": confirmed,
            "confirmation_required": not confirmed,
            "cast_revision": cast_revision,
            "confirmed_revision": confirmed_revision,
            "role_bindings": role_bindings,
            "changed_roles": list(cast.get("changed_roles") or []),
            "next_actions": (
                ["plan_production", "start_production"]
                if confirmed else
                ["get_voice_cast", "confirm_voice_cast"]
            ),
        }

    @classmethod
    def get_unbound_roles(cls, project_name: str) -> list[dict[str, Any]]:
        roster = _roster_map(_read_roster(project_name))
        cast = _normalize_cast(project_name, _read_cast(project_name) or {}, roster)
        return [
            {"role_id": role_id, **role}
            for role_id, role in roster.items()
            if not cast["roles"].get(role_id, {}).get("voice_asset_id")
        ]

    @classmethod
    def get_voice_binding_status(cls, project_name: str) -> dict[str, Any]:
        project_dir = _project_dir(project_name)
        roster_document = _read_roster(project_name)
        if not roster_document:
            meta, script, bindings = ProjectRepository.load_project(project_name)
            voices, _ = script_loader.resolve_collections(script)
            inner = bindings.get("bindings", {}) if isinstance(bindings, dict) else {}
            def _legacy_bound_path(role: str) -> str | None:
                value = inner.get(role)
                if not value:
                    return None
                path = str(value)
                return path if os.path.isabs(path) else os.path.join(project_dir, path)
            items = [
                {
                    "role_id": None,
                    "name": role,
                    "bound": bool(_legacy_bound_path(role) and os.path.isfile(_legacy_bound_path(role))),
                    "voice_asset_id": None,
                    "locked": False,
                }
                for role in voices
            ]
            bound = sum(item["bound"] for item in items)
            cast_ready = bound == len(items)
            runtime_status = read_runtime_engine_status()
            runtime_state = runtime_status["runtime_state"]
            engine_state = runtime_status["engine_state"]
            return {
                "project_name": project_name,
                "mode": "legacy_manual",
                "roles_total": len(items),
                "bound": bound,
                "unbound": len(items) - bound,
                "new_roles": 0,
                "cast_locked": False,
                "locked": 0,
                "locked_roles": [],
                "cast_ready": cast_ready,
                "production_ready": cast_ready,
                "runtime_status": runtime_state,
                "engine_state": engine_state,
                "engine_ready": engine_state == "ready",
                # Eligibility to START synthesis: an unknown/uninitialized
                # engine is fine (the runtime preflights on task claim);
                # only a declared engine error blocks starting.
                "synthesis_ready": (
                    cast_ready
                    and runtime_state != "error"
                    and engine_state != "error"
                ),
                "roles": items,
            }
        roster = _roster_map(roster_document)
        cast = _normalize_cast(project_name, _read_cast(project_name) or {}, roster)
        validation = cls.validate_voice_cast(project_name, cast)
        items: list[dict[str, Any]] = []
        for role_id, role in roster.items():
            binding = cast["roles"].get(role_id, {})
            asset_id = binding.get("voice_asset_id")
            bound = False
            if asset_id:
                try:
                    _asset_for_role(project_name, role_id, binding)
                    bound = True
                except VoiceCastError:
                    bound = False
            items.append({
                "role_id": role_id,
                "name": role["name"],
                "bound": bound,
                "voice_asset_id": asset_id,
                "locked": bool(binding.get("locked", False)),
            })
        bound = sum(item["bound"] for item in items)
        new_role_details: dict[str, dict[str, Any]] = {}
        try:
            _, script, _ = ProjectRepository.load_project(project_name)
            _, chapters = script_loader.resolve_collections(script)
            for chapter in chapters:
                if not isinstance(chapter, dict):
                    continue
                for segment in chapter.get("segments", []):
                    if not isinstance(segment, dict):
                        continue
                    try:
                        cls.resolve_role(project_name, segment, roster=roster_document, allow_legacy=False)
                    except VoiceCastError as exc:
                        if exc.code != "ALIAS_CONFLICT":
                            name = str(segment.get("role") or segment.get("speaker") or "").strip()
                            if name:
                                new_role_details.setdefault(name, {
                                    "suggested_role_id": cls._suggested_role_id(name, segment.get("role_id")),
                                    "name": name,
                                    "first_seen_chapter": str(chapter.get("id") or ""),
                                    "first_seen_segment": str(segment.get("id") or ""),
                                })
        except Exception:
            # Status remains useful even if an in-progress script is malformed.
            new_role_details = {}
        cast_ready = bool(
            validation["ready"] and bound == len(items) and not new_role_details
        )
        runtime_status = read_runtime_engine_status()
        runtime_state = runtime_status["runtime_state"]
        engine_state = runtime_status["engine_state"]
        locked_roles = [item for item in items if item.get("locked")]
        cast_revision = int(cast.get("cast_revision") or 0)
        confirmed_raw = cast.get("confirmed_revision")
        confirmed_revision = int(confirmed_raw) if confirmed_raw is not None else None
        confirmed = confirmed_revision is not None and confirmed_revision == cast_revision
        return {
            "project_name": project_name,
            "mode": "voice_cast",
            "roles_total": len(items),
            "bound": bound,
            "unbound": len(items) - bound,
            "locked": len(locked_roles),
            "locked_roles": [
                {
                    "role_id": item.get("role_id"),
                    "name": item.get("name"),
                }
                for item in locked_roles
            ],
            "new_roles": len(new_role_details),
            "new_role_details": list(new_role_details.values()),
            "cast_locked": cast.get("status") == "locked",
            "cast_ready": cast_ready,
            "production_ready": cast_ready,
            "confirmation_required": not confirmed,
            "confirmed": confirmed,
            "cast_revision": cast_revision,
            "confirmed_revision": confirmed_revision,
            "runtime_status": runtime_state,
            "engine_state": engine_state,
            "engine_ready": engine_state == "ready",
            "synthesis_ready": (
                cast_ready
                and runtime_state != "error"
                and engine_state != "error"
            ),
            "roles": items,
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        }

    @classmethod
    def check_production_scope(
        cls,
        project_name: str,
        segment_ids: list[str] | tuple[str, ...] | set[str] | None,
    ) -> dict[str, Any]:
        """Check only the roles used by an exact production segment scope.

        This is intentionally independent from ``cast.status``.  A draft
        Voice Cast means that the whole-book cast has not been finalized; it
        does not mean that a selected subset is unusable.  Callers must pass
        the already-normalized segment IDs so a one-segment scope can never
        accidentally widen to the containing chapter.
        """
        project_dir = _project_dir(project_name)
        requested_ids: list[str] = []
        seen: set[str] = set()
        for value in segment_ids or []:
            segment_id = str(value or "").strip()
            if segment_id and segment_id not in seen:
                requested_ids.append(segment_id)
                seen.add(segment_id)

        _meta, script, bindings_document = ProjectRepository.load_project(project_name)
        _voices, chapters = script_loader.resolve_collections(script)
        segments: dict[str, dict[str, Any]] = {}
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            for segment in chapter.get("segments", []):
                if isinstance(segment, dict):
                    segment_id = str(segment.get("id") or "").strip()
                    if segment_id:
                        segments[segment_id] = segment

        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        selected = [segments[item] for item in requested_ids if item in segments]
        for segment_id in requested_ids:
            if segment_id not in segments:
                errors.append(_issue(
                    "SEGMENT_NOT_FOUND",
                    f"段落不存在: {segment_id}",
                    segment_id=segment_id,
                ))

        roster_document = _read_roster(project_name)
        roster = _roster_map(roster_document)
        cast = _normalize_cast(project_name, _read_cast(project_name) or {}, roster)
        required_roles: list[dict[str, Any]] = []
        required_by_id: dict[str, dict[str, Any]] = {}
        unbound_roles: list[str] = []
        missing_roles: list[dict[str, Any]] = []

        def _add_required(role_id: str | None, name: str, **fields: Any) -> dict[str, Any]:
            key = str(role_id or name or "").strip()
            if key in required_by_id:
                return required_by_id[key]
            item = {
                "role_id": str(role_id).strip() if role_id else None,
                "name": str(name or role_id or "").strip(),
                "bound": False,
                "locked": False,
                **fields,
            }
            required_by_id[key] = item
            required_roles.append(item)
            return item

        if not roster_document:
            # Legacy/manual projects keep their existing name -> absolute path
            # bridge.  Only names used by the selected segments participate in
            # readiness; unrelated future roles are deliberately ignored.
            runtime_bindings = (
                bindings_document.get("bindings", {})
                if isinstance(bindings_document, dict) else {}
            )
            runtime_bindings = runtime_bindings if isinstance(runtime_bindings, dict) else {}
            for segment in selected:
                name = str(segment.get("role") or segment.get("speaker") or "").strip()
                role = _add_required(None, name)
                path_value = runtime_bindings.get(name)
                path = str(path_value or "").strip()
                if path and not os.path.isabs(path):
                    path = os.path.join(project_dir, path)
                role["bound"] = bool(path and os.path.isfile(path))
                if not role["bound"] and name and name not in unbound_roles:
                    unbound_roles.append(name)
                    errors.append(_issue(
                        "ROLE_UNBOUND",
                        f"角色尚未绑定声音: {name}",
                        role=name,
                    ))
            return {
                "ready": not errors,
                "mode": "legacy_manual",
                "cast_status": "draft",
                "cast_locked": False,
                "segment_count": len(requested_ids),
                "selected_segment_ids": requested_ids,
                "required_roles": required_roles,
                "required_role_count": len(required_roles),
                "bound_role_count": sum(1 for item in required_roles if item["bound"]),
                "unbound_roles": unbound_roles,
                "missing_roles": missing_roles,
                "errors": errors,
                "warnings": warnings,
            }

        role_bindings = (
            bindings_document.get("role_bindings", {})
            if isinstance(bindings_document, dict) else {}
        )
        role_bindings = role_bindings if isinstance(role_bindings, dict) else {}
        for segment in selected:
            name = str(segment.get("role") or segment.get("speaker") or "").strip()
            requested_role_id = str(segment.get("role_id") or "").strip() or None
            try:
                resolved = cls.resolve_role(
                    project_name,
                    segment,
                    roster=roster_document,
                    allow_legacy=False,
                )
            except VoiceCastError as exc:
                role = _add_required(requested_role_id, name)
                missing = {
                    "role_id": requested_role_id,
                    "name": name,
                }
                if missing not in missing_roles:
                    missing_roles.append(missing)
                if not any(
                    issue.get("code") == exc.code
                    and issue.get("role_id") == requested_role_id
                    and issue.get("name") == name
                    for issue in errors
                ):
                    errors.append(exc.as_issue())
                role["error"] = exc.code
                continue

            role_id = str(resolved.get("role_id") or "").strip()
            role_name = str(resolved.get("name") or name).strip()
            binding = cast["roles"].get(role_id, {})
            binding = binding if isinstance(binding, dict) else {}
            asset_id = str(binding.get("voice_asset_id") or "").strip()
            role = _add_required(
                role_id,
                role_name,
                voice_asset_id=asset_id or None,
                locked=bool(binding.get("locked", False)),
            )
            role["locked"] = bool(binding.get("locked", False))
            if not asset_id:
                if role_id not in unbound_roles:
                    unbound_roles.append(role_id)
                errors.append(_issue(
                    "ROLE_UNBOUND",
                    f"角色尚未绑定声音: {role_name}",
                    role_id=role_id,
                    name=role_name,
                ))
                continue

            try:
                asset = _asset_for_role(project_name, role_id, binding)
            except VoiceCastError as exc:
                errors.append(exc.as_issue())
                if role_id not in unbound_roles:
                    unbound_roles.append(role_id)
                continue

            expected_sha = str(binding.get("voice_sha256") or asset.get("sha256") or "")
            actual_sha = str(asset.get("sha256") or "")
            if expected_sha and actual_sha and expected_sha != actual_sha:
                errors.append(_issue(
                    "VOICE_ASSET_SHA256_MISMATCH",
                    "音色资产内容哈希与演员表记录不一致",
                    role_id=role_id,
                    voice_asset_id=asset_id,
                ))
                continue

            snapshot_path = _project_voice_snapshot_path(
                project_dir,
                role_id,
                binding,
                bindings_document,
            )
            snapshot_sha = segment_cache.speaker_fingerprint_for_path(snapshot_path)
            if not snapshot_sha or (actual_sha and snapshot_sha != actual_sha):
                errors.append(_issue(
                    "VOICE_SNAPSHOT_MISSING",
                    f"角色声音快照不可用: {role_name}",
                    role_id=role_id,
                    voice_asset_id=asset_id,
                    fix_hint="重新应用当前 Voice Cast 绑定，生成项目声音快照。",
                ))
                continue
            role["bound"] = True

        return {
            "ready": not errors,
            "mode": "voice_cast",
            "cast_status": cast.get("status", "draft"),
            "cast_locked": cast.get("status") == "locked",
            "segment_count": len(requested_ids),
            "selected_segment_ids": requested_ids,
            "required_roles": required_roles,
            "required_role_count": len(required_roles),
            "bound_role_count": sum(1 for item in required_roles if item["bound"]),
            "unbound_roles": unbound_roles,
            "missing_roles": missing_roles,
            "errors": errors,
            "warnings": warnings,
        }

    @classmethod
    def lock_production_scope(
        cls,
        project_name: str,
        segment_ids: list[str] | tuple[str, ...] | set[str] | None,
    ) -> dict[str, Any]:
        """Lock only roles used by a claimed formal production task.

        This method is called by the runtime after a task has become active.
        It intentionally does not call ``_guard_mutation``: the active task is
        the ownership guard, and normal cast mutations are rejected while it
        exists.  It performs a fresh readiness check before writing, so a
        stale plan can never lock or use a changed binding.
        """
        check = cls.check_production_scope(project_name, segment_ids)
        if not check["ready"]:
            first = next(
                (item for item in check.get("errors", []) if isinstance(item, dict)),
                {},
            )
            raise VoiceCastError(
                "PRODUCTION_SCOPE_NOT_READY",
                str(first.get("message") or "所选生产范围尚未就绪"),
                errors=list(check.get("errors") or []),
                segment_ids=list(check.get("selected_segment_ids") or []),
            )
        if check.get("mode") != "voice_cast":
            return {
                "locked_role_ids": [],
                "required_roles": check.get("required_roles", []),
                "cast_status": check.get("cast_status", "draft"),
            }

        project_dir = _project_dir(project_name)
        roster = _roster_map(_read_roster(project_name))
        cast = _normalize_cast(project_name, _read_cast(project_name) or {}, roster)
        locked_role_ids: list[str] = []
        for role in check.get("required_roles", []):
            role_id = str(role.get("role_id") or "").strip() if isinstance(role, dict) else ""
            if not role_id or role_id not in cast["roles"]:
                continue
            binding = cast["roles"][role_id]
            if not binding.get("locked"):
                binding["locked"] = True
                locked_role_ids.append(role_id)
        if locked_role_ids:
            VoiceCastRepository.save_cast(project_dir, cast)
            runtime_bindings = ProjectRepository.load_bindings(project_dir)
            if not isinstance(runtime_bindings, dict):
                runtime_bindings = {}
            role_bindings = runtime_bindings.setdefault("role_bindings", {})
            if not isinstance(role_bindings, dict):
                role_bindings = {}
                runtime_bindings["role_bindings"] = role_bindings
            for role_id in locked_role_ids:
                current = role_bindings.get(role_id)
                if isinstance(current, dict):
                    current["locked"] = True
            ProjectRepository.save_bindings(project_dir, runtime_bindings)
        return {
            "locked_role_ids": locked_role_ids,
            "required_roles": check.get("required_roles", []),
            "cast_status": cast.get("status", "draft"),
        }

    @staticmethod
    def _suggested_role_id(name: str, explicit: str | None = None) -> str:
        if explicit and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", explicit):
            return explicit
        # Small deterministic transliteration table for the common Chinese
        # names used in the workflow examples; unknown text falls back to a
        # stable hash rather than a fuzzy guess.
        pinyin = {
            "丁": "ding", "仪": "yi", "叶": "ye", "文": "wen", "洁": "jie",
            "汪": "wang", "淼": "miao", "常": "chang", "伟": "wei", "司": "si",
            "马": "ma", "王": "wang", "小": "xiao", "雨": "yu", "旁": "narrator",
            "白": "narrator", "叙": "narrator", "述": "narrator", "者": "narrator",
        }
        chunks: list[str] = []
        for char in str(name or "").strip():
            if char.isascii() and (char.isalnum() or char in "_-"):
                chunks.append(char.lower())
            elif char in pinyin:
                chunks.append(pinyin[char])
        slug = "_".join(chunk for chunk in chunks if chunk)
        slug = re.sub(r"_+", "_", slug).strip("_")
        if slug and not slug.startswith("role_"):
            return f"role_{slug}"
        digest = hashlib.sha1(str(name or "").strip().encode("utf-8")).hexdigest()[:12]
        return f"role_{digest}"

    @classmethod
    def check_chapter_roles(cls, project_name: str, chapters: Any) -> dict[str, Any]:
        _project_dir(project_name)
        if isinstance(chapters, dict):
            chapters = chapters.get("chapters", [])
        chapters = chapters if isinstance(chapters, list) else []
        roster_document = _read_roster(project_name)
        roster = _roster_map(roster_document)
        cast = _normalize_cast(project_name, _read_cast(project_name) or {}, roster)
        known_ids: list[str] = []
        new_roles: dict[str, dict[str, Any]] = {}
        unbound_ids: set[str] = set()
        errors: list[dict[str, Any]] = []
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            chapter_id = str(chapter.get("chapter_code") or chapter.get("id") or "")
            for segment in chapter.get("segments", []):
                if not isinstance(segment, dict):
                    continue
                name = str(segment.get("role") or segment.get("speaker") or "").strip()
                try:
                    resolved = cls.resolve_role(
                        project_name, segment, roster=roster_document,
                        allow_legacy=not bool(roster_document),
                    )
                except VoiceCastError as exc:
                    if exc.code == "ALIAS_CONFLICT":
                        errors.append(exc.as_issue())
                        continue
                    key = str(segment.get("role_id") or name)
                    if key not in new_roles:
                        new_roles[key] = {
                            "suggested_role_id": cls._suggested_role_id(name, segment.get("role_id")),
                            "name": name,
                            "first_seen_chapter": chapter_id,
                            "first_seen_segment": str(segment.get("id") or ""),
                        }
                    continue
                role_id = resolved.get("role_id")
                if not role_id:
                    # Legacy/manual projects have no stable roster.  They are
                    # intentionally left to the existing role-name binding UI.
                    continue
                if role_id not in known_ids:
                    known_ids.append(role_id)
                if not cast["roles"].get(role_id, {}).get("voice_asset_id"):
                    unbound_ids.add(role_id)
        if not roster_document:
            status = cls.get_voice_binding_status(project_name)
            return {
                "known_roles": len(known_ids),
                "known_role_ids": known_ids,
                "new_roles": list(new_roles.values()),
                "unbound_roles": [],
                "synthesis_ready": status["synthesis_ready"],
                "legacy_manual": True,
                "errors": errors,
            }
        return {
            "known_roles": len(known_ids),
            "known_role_ids": known_ids,
            "new_roles": list(new_roles.values()),
            "unbound_roles": sorted(unbound_ids),
            "unbound_role_details": [
                {"role_id": rid, "name": roster[rid]["name"]}
                for rid in sorted(unbound_ids)
                if rid in roster
            ],
            "synthesis_ready": not new_roles and not unbound_ids and not errors,
            "errors": errors,
        }


VoiceCastService = VoiceCastResolver

# Functional aliases keep the service convenient for small integrations while
# the class remains the shared state-free implementation used by UI and MCP.
resolve_role = VoiceCastResolver.resolve_role
apply_cast = VoiceCastResolver.apply_cast
validate_cast = VoiceCastResolver.validate_voice_cast
get_unbound_roles = VoiceCastResolver.get_unbound_roles


__all__ = [
    "VoiceCastError", "VoiceCastResolver", "VoiceCastService",
    "apply_cast", "get_unbound_roles", "resolve_role", "validate_cast",
]
