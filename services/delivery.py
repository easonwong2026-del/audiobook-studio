"""Deterministic inputs used to decide whether a delivery is still current.

The delivery manifest is a historical record.  A ``ready`` flag on its own
only says that one export completed at some point in the past; it does not
prove that the project still has the same script, active audio revisions, or
Voice Cast.  This module keeps the freshness contract framework-free so both
the export service and the workflow service can use exactly the same input
snapshot.

Only JSON-safe, project-relative values are included in the snapshot.  Paths
are never absolute and volatile project status fields are deliberately left
out: workflow derives those independently as blockers, while the snapshot
captures the content that an export actually consumes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import ntpath
import os
from typing import Any

from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository
from repositories.voice_cast_repo import VoiceCastRepository
from lib.tts_profile import public_profile, resolve_profile


SNAPSHOT_VERSION = 1


def _canonical_json(value: Any) -> str:
    """Serialize a JSON value deterministically for hashing.

    Project documents already originate from JSON, nevertheless ``default``
    is intentionally not used here: silently stringifying an unsupported
    value would make two different values appear equivalent.  ``allow_nan``
    is disabled so the helper never emits a non-standard JSON hash input.
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _sorted_json(value: Any) -> Any:
    """Return a detached JSON value with object keys in lexical order."""

    if isinstance(value, dict):
        return {
            str(key): _sorted_json(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_sorted_json(item) for item in value]
    return value


def _file_sha256(path: str) -> str:
    """Return a file digest, or an empty string when the file is unavailable."""

    if not path or not os.path.isfile(path):
        return ""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _project_relative(project_dir: str, relative_path: Any) -> str:
    """Normalize a revision path without allowing absolute path leakage."""

    raw = str(relative_path or "").replace("\\", "/")
    if not raw:
        return ""
    # Revision paths are persisted as project-relative paths.  For old data
    # that accidentally contains an absolute path, retain only the path's
    # relative representation when it is inside the project; otherwise leave
    # it blank rather than putting a machine-specific path in a hash.
    if os.path.isabs(raw) or ntpath.isabs(raw):
        # A Windows-style path cannot be resolved safely on a POSIX host.  It
        # is still better to omit it than to persist a machine-specific value.
        if ntpath.isabs(raw) and not os.path.isabs(raw):
            return ""
        try:
            candidate = os.path.realpath(raw)
            root = os.path.realpath(project_dir)
            common = os.path.commonpath((candidate, root))
            if common == root:
                raw = os.path.relpath(candidate, root).replace(os.sep, "/")
            else:
                return ""
        except (OSError, ValueError):
            return ""
    # Do not permit ``..`` to make the same file represent different paths.
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _absolute_project_path(project_dir: str, relative_path: str) -> str:
    if not relative_path:
        return ""
    path = os.path.join(project_dir, *relative_path.split("/"))
    try:
        root = os.path.realpath(project_dir)
        if os.path.commonpath((os.path.realpath(path), root)) != root:
            return ""
    except (OSError, ValueError):
        return ""
    return path


def _public_engine_snapshot(value: Any) -> dict[str, Any]:
    """Normalize revision provenance without persisting a local model path."""
    if not isinstance(value, dict) or not value:
        return {}
    try:
        return public_profile(resolve_profile(value))
    except (OSError, TypeError, ValueError, RuntimeError):
        return {}


def _engine_provenance(revisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the actual engine identities present in active revisions."""
    profiles: dict[str, dict[str, Any]] = {}
    for revision in revisions:
        profile = revision.get("engine_snapshot") if isinstance(revision, dict) else None
        identity = str(profile.get("cache_identity") or "") if isinstance(profile, dict) else ""
        if identity and isinstance(profile, dict):
            profiles.setdefault(identity, profile)
    ordered = [profiles[key] for key in sorted(profiles)]
    if len(ordered) == 1:
        return {"status": "uniform", "engine_snapshot": ordered[0], "engines": ordered}
    if len(ordered) > 1:
        return {"status": "mixed", "engine_snapshot": {}, "engines": ordered}
    return {"status": "unknown", "engine_snapshot": {}, "engines": []}


def _segment_order(script: dict[str, Any]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    ordinal = 0
    for chapter_index, chapter in enumerate(script.get("chapters", [])):
        if not isinstance(chapter, dict):
            continue
        chapter_id = str(
            chapter.get("chapter_code") or chapter.get("id") or chapter_index
        )
        for segment_index, segment in enumerate(chapter.get("segments", [])):
            if not isinstance(segment, dict):
                continue
            segment_id = str(segment.get("id") or "")
            # Keep the traversal order explicit.  The export pipeline consumes
            # segments in this same order, and an order-only edit must stale a
            # previous manifest even if all segment ids remain unchanged.
            ordered.append({
                "ordinal": ordinal,
                "chapter_index": chapter_index,
                "chapter_id": chapter_id,
                "segment_index": segment_index,
                "segment_id": segment_id,
            })
            ordinal += 1
    return ordered


def _active_revisions(
    project_name: str,
    project_dir: str,
    segment_order: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    state = QualityRepository.load(project_name)
    active_ids = state.get("active_revisions", {})
    revisions = state.get("revisions", {})
    result: list[dict[str, Any]] = []
    for item in segment_order:
        segment_id = str(item.get("segment_id") or "")
        revision_id = str(active_ids.get(segment_id) or "")
        revision = revisions.get(revision_id)
        if not isinstance(revision, dict):
            revision = {}
        relative_path = _project_relative(project_dir, revision.get("relative_path"))
        path = _absolute_project_path(project_dir, relative_path)
        metadata = revision.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        metadata_sha256 = str(metadata.get("sha256") or "")
        # A completed revision's metadata checksum is the immutable content
        # identity recorded by QA/repair.  Avoid rereading every long-book WAV
        # on each workflow poll; legacy records without a checksum fall back to
        # the file digest so they still receive a useful freshness identity.
        checksum = metadata_sha256 or _file_sha256(path)
        result.append({
            "ordinal": int(item.get("ordinal", 0) or 0),
            "segment_id": segment_id,
            "revision_id": revision_id,
            "audio_revision": int(revision.get("audio_revision", 0) or 0),
            "relative_path": relative_path,
            "sha256": checksum,
            "checksum": checksum,
            "cache_identity": str(revision.get("cache_identity") or ""),
            "voice_fingerprint": str(revision.get("voice_fingerprint") or ""),
            "engine_snapshot": _public_engine_snapshot(
                (revision.get("params") or {}).get("engine_snapshot")
                if isinstance(revision.get("params"), dict) else None
            ),
        })
    return result


def _voice_file_fingerprint(project_dir: str, relative_path: Any) -> str:
    relative = _project_relative(project_dir, relative_path)
    return _file_sha256(_absolute_project_path(project_dir, relative))


def _voice_cast_snapshot(
    project_name: str,
    project_dir: str,
    bindings: dict[str, Any],
) -> dict[str, Any]:
    """Return a stable identity/fingerprint for v2 Voice Cast or legacy data."""

    cast = VoiceCastRepository.load_cast(project_dir)
    roster = VoiceCastRepository.load_roster(project_dir)
    roster_document = copy.deepcopy(roster) if isinstance(roster, dict) else {}

    if isinstance(cast, dict):
        raw_roles = cast.get("roles")
        status = str(cast.get("status") or "draft")
        roles: dict[str, dict[str, Any]] = {}
        if isinstance(raw_roles, dict):
            role_items = raw_roles.items()
        elif isinstance(raw_roles, list):
            role_items = (
                (str(item.get("role_id") or ""), item)
                for item in raw_roles
                if isinstance(item, dict)
            )
        else:
            role_items = ()
        for role_id, raw in role_items:
            if not role_id or not isinstance(raw, dict):
                continue
            relative_path = _project_relative(
                project_dir, raw.get("project_voice_path")
            )
            declared_fingerprint = str(raw.get("voice_sha256") or "")
            file_fingerprint = (
                declared_fingerprint
                or _voice_file_fingerprint(project_dir, relative_path)
            )
            roles[str(role_id)] = {
                "name": str(raw.get("name") or ""),
                "voice_asset_id": str(raw.get("voice_asset_id") or ""),
                "voice_sha256": declared_fingerprint,
                "project_voice_path": relative_path,
                "file_sha256": file_fingerprint,
                "locked": bool(raw.get("locked", False)),
            }
        document = {
            "mode": "voice_cast",
            "version": str(cast.get("version") or "1.0"),
            "status": status,
            "roles": {key: roles[key] for key in sorted(roles)},
        }
    else:
        # Projects created before Character Roster / Voice Cast use the legacy
        # role_bindings map.  Keep this path deterministic and content-aware so
        # a legacy delivery also becomes stale after a rebind or invalidation.
        legacy = bindings.get("role_bindings") if isinstance(bindings, dict) else {}
        roles = {}
        if isinstance(legacy, dict):
            for role_id, raw in sorted(legacy.items(), key=lambda pair: str(pair[0])):
                if not isinstance(raw, dict):
                    continue
                relative_path = _project_relative(
                    project_dir, raw.get("project_voice_path")
                )
                roles[str(role_id)] = {
                    "voice_asset_id": str(raw.get("voice_asset_id") or ""),
                    "voice_sha256": str(raw.get("voice_sha256") or ""),
                    "project_voice_path": relative_path,
                    "file_sha256": (
                        str(raw.get("voice_sha256") or "")
                        or _voice_file_fingerprint(project_dir, relative_path)
                    ),
                }
        # Some early legacy projects only had the display-name keyed
        # ``bindings`` map.  Include its content identity as a fallback so a
        # rebind is still visible even when ``role_bindings`` was never
        # materialized.
        if not roles and isinstance(bindings, dict):
            raw_bindings = bindings.get("bindings")
            if isinstance(raw_bindings, dict):
                for role_name, raw_path in sorted(
                    raw_bindings.items(), key=lambda pair: str(pair[0])
                ):
                    relative_path = _project_relative(project_dir, raw_path)
                    roles[str(role_name)] = {
                        "voice_asset_id": "",
                        "voice_sha256": "",
                        "project_voice_path": relative_path,
                        "file_sha256": _voice_file_fingerprint(
                            project_dir, relative_path
                        ),
                    }
        document = {
            "mode": "legacy_manual",
            "version": "legacy",
            "status": "legacy",
            "roles": roles,
        }

    identity = _hash_json({"roster": roster_document, "cast": document})
    role_fingerprints = [
        {
            "role_id": role_id,
            "voice_asset_id": role.get("voice_asset_id", ""),
            "voice_sha256": role.get("voice_sha256", ""),
            "file_sha256": role.get("file_sha256", ""),
        }
        for role_id, role in sorted(document["roles"].items())
    ]
    fingerprint = _hash_json(role_fingerprints)
    return {
        "mode": document["mode"],
        "status": document["status"],
        "identity": identity,
        "fingerprint": fingerprint,
        "roles": document["roles"],
        "roster_identity": _hash_json(roster_document),
    }


def compute_delivery_input_hash(snapshot: dict[str, Any] | str) -> str:
    """Hash a snapshot without recursively hashing its stored hash field.

    Passing a project name is accepted as a convenience for callers that do
    not need the full snapshot object.
    """

    if isinstance(snapshot, str):
        return delivery_input_hash(snapshot)

    if not isinstance(snapshot, dict):
        raise TypeError("delivery snapshot 必须是对象")
    payload = copy.deepcopy(snapshot)
    payload.pop("delivery_input_hash", None)
    payload.pop("freshness_hash", None)
    return _hash_json(payload)


def compute_delivery_input_snapshot(project_name: str) -> dict[str, Any]:
    """Build the deterministic content identity consumed by formal delivery.

    The returned object is JSON serializable and includes its own
    ``delivery_input_hash``.  The hash is calculated from every other field,
    making it safe for callers to persist the object alongside a manifest or
    to pass only the hash when public API output should stay compact.
    """

    project = str(project_name or "").strip()
    if not project:
        raise ValueError("project_name 不能为空")
    _meta, script, bindings = ProjectRepository.load_project(project)
    project_dir = ProjectRepository.get_project_dir(project)
    # ``load_project`` canonicalizes collections; hashing that canonical
    # representation avoids formatting or dictionary insertion-order noise.
    structured_script_identity = _hash_json(script)
    segment_order = _segment_order(script)
    active_revisions = _active_revisions(project, project_dir, segment_order)
    voice_cast = _voice_cast_snapshot(project, project_dir, bindings)
    metadata = script.get("meta") if isinstance(script.get("meta"), dict) else {}
    engine_provenance = _engine_provenance(active_revisions)
    payload: dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "project": project,
        "structured_script_identity": structured_script_identity,
        "segment_order": segment_order,
        "active_revisions": active_revisions,
        "engine_provenance": engine_provenance,
        "voice_cast": voice_cast,
        "metadata": _sorted_json(metadata),
    }
    digest = compute_delivery_input_hash(payload)
    return {**payload, "delivery_input_hash": digest}


def delivery_input_hash(project_name: str) -> str:
    """Convenience API for callers that only need the current hash."""

    return str(compute_delivery_input_snapshot(project_name)["delivery_input_hash"])


# Descriptive aliases make the helper easy to discover without forcing either
# the export or workflow service to depend on a particular spelling.
build_delivery_input_snapshot = compute_delivery_input_snapshot
build_delivery_input_hash = compute_delivery_input_hash


__all__ = [
    "SNAPSHOT_VERSION",
    "build_delivery_input_hash",
    "build_delivery_input_snapshot",
    "compute_delivery_input_hash",
    "compute_delivery_input_snapshot",
    "delivery_input_hash",
]
