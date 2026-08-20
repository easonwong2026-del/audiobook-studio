"""Read-only Chapter → Book merge planning.

This module is deliberately a planning boundary, not a merge boundary.  It
reads the persisted project documents and the existing storage layout, then
returns a deterministic description of what a future merge would need to
resolve.  There is intentionally no ``execute_merge`` (or equivalent) API in
this module.

The planner does not use ``ProjectRepository.load_project`` or the high-level
quality/task services for its read path.  Those APIs are correct for normal
application work, but some of them repair checkpoints or initialise durable
stores as part of a read.  A dry run must not do that.  Raw JSON reads and a
read-only SQLite connection keep the C.1 contract explicit.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote

from lib import chapter_identity, project_paths, script_loader, segment_cache
from repositories.project_repo import ProjectRepository
from repositories.project_storage_repo import ProjectStorageRepository

PLANNER_SCHEMA_VERSION = "chapter-merge-plan-v1"

PLANNING_ALLOWED = "PLANNING_ALLOWED"
PLANNING_BLOCKED = "PLANNING_BLOCKED"

READY = "READY"
READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
BLOCKED = "BLOCKED"

NO_COLLISION = "NO_COLLISION"
COLLISION_REMAP_REQUIRED = "COLLISION_REMAP_REQUIRED"
UNRESOLVABLE_COLLISION = "UNRESOLVABLE_COLLISION"

COMPLETE_AUDIO = "COMPLETE_AUDIO"
PARTIAL_AUDIO = "PARTIAL_AUDIO"
NO_AUDIO = "NO_AUDIO"

INFO = "INFO"
WARNING = "WARNING"
ERROR = "ERROR"

_ACTIVE_TASK_STATES = frozenset(
    {"pending", "running", "pausing", "paused", "recovering", "cancelling"}
)
_TERMINAL_REPAIR_STATES = frozenset(
    {"done", "completed", "failed", "cancelled", "error", "interrupted"}
)
_PERSISTED_FILE_KEYS = (
    "project_meta",
    "structured_script",
    "voice_bindings",
    "character_roster",
    "voice_cast",
    "quality_state",
    "task_db",
    "segment_status_journal",
)
_AUDIO_DIRECTORY_KEYS = (
    "segments",
    "chapter_audio",
    "merged_audio",
    "supplement_audio",
)


def _canonical(value: Any) -> str:
    """Encode a JSON-safe value without timestamps or process-local values."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bytes_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_read_json(path: str) -> tuple[Any, str | None]:
    if not os.path.isfile(path):
        return None, "missing"
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file), None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _relative(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except (TypeError, ValueError):
        return os.path.basename(path)


def _file_fingerprint(path: str, root: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": _relative(root, path),
        "exists": False,
    }
    try:
        stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return result
    result.update(
        {
            "exists": True,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": _bytes_digest(path),
        }
    )
    return result


def _directory_fingerprints(root: str, directory: str) -> list[dict[str, Any]]:
    """Fingerprint files below a logical project directory without creating it."""
    if not os.path.isdir(directory) or os.path.islink(directory):
        return []
    result: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(
            item for item in dirs if not os.path.islink(os.path.join(current, item))
        )
        for filename in sorted(files):
            path = os.path.join(current, filename)
            if os.path.islink(path):
                continue
            result.append(_file_fingerprint(path, root))
    return result


def _json_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _task_database_rows(path: str) -> tuple[tuple[dict[str, Any], ...], str | None]:
    """Read the task database through SQLite's ``mode=ro`` URI only."""
    if not os.path.isfile(path):
        return (), None
    connection: sqlite3.Connection | None = None
    try:
        uri = f"file:{quote(os.path.abspath(path))}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='production_tasks'"
        ).fetchone()
        if table is None:
            return (), "production_tasks table is missing"
        rows: list[dict[str, Any]] = []
        for row in connection.execute(
            "SELECT task_id, task_type, project, status, scope_json, "
            "options_json, created_at, updated_at FROM production_tasks "
            "ORDER BY task_id"
        ):
            rows.append(
                {
                    "task_id": str(row["task_id"] or ""),
                    "task_type": str(row["task_type"] or ""),
                    "project": str(row["project"] or ""),
                    "status": str(row["status"] or ""),
                    "scope": _decode_json_object(row["scope_json"]),
                    "options": _decode_json_object(row["options_json"]),
                    "created_at": str(row["created_at"] or ""),
                    "updated_at": str(row["updated_at"] or ""),
                }
            )
        return tuple(rows), None
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return (), f"{type(exc).__name__}: {exc}"
    finally:
        if connection is not None:
            connection.close()


def _legacy_task_rows(project_name: str) -> tuple[dict[str, Any], ...]:
    """Inspect legacy JSON task records without calling its creating accessor."""
    try:
        from lib import config

        # ``get_preview_dir`` and ``get_data_dir`` both create directories;
        # the private path-only helper is intentional for this read-only path.
        directory = os.path.join(config._data_dir_path(), "preview", "task_records")
    except (OSError, TypeError, ValueError):
        return ()
    if not os.path.isdir(directory):
        return ()
    result: list[dict[str, Any]] = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".json"):
            continue
        value, error = _safe_read_json(os.path.join(directory, filename))
        if error or not isinstance(value, dict):
            continue
        if str(value.get("project") or "") != str(project_name):
            continue
        result.append(
            {
                "task_id": str(value.get("task_id") or filename[:-5]),
                "task_type": str(value.get("task_type") or ""),
                "project": str(value.get("project") or ""),
                "status": str(value.get("status") or ""),
                "scope": _json_dict(value.get("scope")),
                "options": _json_dict(value.get("options")),
                "created_at": str(value.get("created_at") or ""),
                "updated_at": str(value.get("updated_at") or ""),
            }
        )
    return tuple(result)


def _decode_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return _json_dict(decoded)


def _read_status_journal(path: str) -> tuple[tuple[dict[str, Any], ...], str | None]:
    if not os.path.isfile(path):
        return (), None
    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    events.append(value)
                else:
                    return (), f"line {line_number} is not an object"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return (), f"{type(exc).__name__}: {exc}"
    return tuple(events), None


@dataclass
class _ProjectState:
    name: str
    path: str
    meta: dict[str, Any] = field(default_factory=dict)
    script: dict[str, Any] = field(default_factory=dict)
    bindings: dict[str, Any] = field(default_factory=dict)
    roster: dict[str, Any] = field(default_factory=dict)
    cast: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    status_events: tuple[dict[str, Any], ...] = ()
    task_rows: tuple[dict[str, Any], ...] = ()
    paths: dict[str, str] = field(default_factory=dict)
    read_errors: tuple[str, ...] = ()
    task_error: str | None = None
    status_error: str | None = None

    @property
    def project_id(self) -> str:
        return str(self.meta.get("project_id") or "").strip()

    @property
    def project_kind(self) -> str:
        value = str(self.meta.get("project_kind") or "book").strip().lower()
        return "chapter" if value == "chapter" else "book"

    @property
    def parent_project_id(self) -> str:
        return str(self.meta.get("parent_project_id") or "").strip()

    def effective_statuses(self) -> dict[str, str]:
        statuses = {
            str(key): str(value)
            for key, value in _json_dict(self.meta.get("segments_status")).items()
        }
        for event in self.status_events:
            segment_id = str(event.get("segment_id") or "").strip()
            status = str(event.get("status") or "").strip()
            if segment_id and status:
                statuses[segment_id] = status
        return statuses


def _load_project_state(name: str) -> _ProjectState:
    path = ProjectRepository.get_project_dir(name)
    state = _ProjectState(name=str(name), path=path)
    errors: list[str] = []
    for key in _PERSISTED_FILE_KEYS:
        try:
            state.paths[key] = project_paths.project_file(path, key)
        except (KeyError, OSError, ValueError) as exc:
            errors.append(f"{key}: {type(exc).__name__}: {exc}")

    if not os.path.isdir(path):
        state.read_errors = ("project directory is missing",)
        return state

    values: dict[str, Any] = {}
    for key in (
        "project_meta",
        "structured_script",
        "voice_bindings",
        "character_roster",
        "voice_cast",
        "quality_state",
    ):
        file_path = state.paths.get(key, "")
        value, error = _safe_read_json(file_path)
        if error and error != "missing":
            errors.append(f"{key}: {error}")
        values[key] = value

    state.meta = _json_dict(values.get("project_meta"))
    raw_script = values.get("structured_script")
    state.script = (
        script_loader.canonicalize_collections(raw_script)
        if isinstance(raw_script, dict)
        else {}
    )
    state.bindings = _json_dict(values.get("voice_bindings"))
    state.roster = _json_dict(values.get("character_roster"))
    state.cast = _json_dict(values.get("voice_cast"))
    state.quality = _json_dict(values.get("quality_state"))

    status_events, status_error = _read_status_journal(
        state.paths.get("segment_status_journal", "")
    )
    state.status_events = status_events
    state.status_error = status_error
    if status_error:
        errors.append(f"segment_status_journal: {status_error}")

    task_rows, task_error = _task_database_rows(state.paths.get("task_db", ""))
    legacy_rows = _legacy_task_rows(state.name)
    seen_task_ids = {str(item.get("task_id") or "") for item in task_rows}
    state.task_rows = task_rows + tuple(
        item for item in legacy_rows if str(item.get("task_id") or "") not in seen_task_ids
    )
    state.task_error = task_error
    if task_error:
        errors.append(f"task_db: {task_error}")
    state.read_errors = tuple(errors)
    return state


def _script_chapters(script: Mapping[str, Any]) -> list[dict[str, Any]]:
    _voices, chapters = script_loader.resolve_collections(dict(script))
    return [item for item in chapters if isinstance(item, dict)]


def _script_validation(state: _ProjectState) -> list[dict[str, Any]]:
    if not state.script:
        return [{"code": "SCRIPT_MISSING", "message": "structured_script.json 缺失或不可读取"}]
    try:
        issues = script_loader.validate_script_issues(state.script)
    except (TypeError, ValueError, AttributeError) as exc:
        return [{"code": "SCRIPT_INVALID", "message": str(exc)}]
    return [dict(item) for item in issues if isinstance(item, dict)]


def _chapter_order(chapter: Mapping[str, Any], index: int) -> int:
    for key in (chapter_identity.CHAPTER_NUMBER_KEY, "chapter_order", "order"):
        value = chapter.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return index + 1


def _segment_content_identity(segment: Mapping[str, Any]) -> str:
    relevant = {
        "id": str(segment.get("id") or ""),
        "role": str(segment.get("role") or segment.get("speaker") or ""),
        "role_id": str(segment.get("role_id") or ""),
        "text": str(segment.get("text") or ""),
        "emotion": segment.get("emotion", "neutral"),
        "emo_alpha": segment.get("emo_alpha", 1.0),
        "speech_rate": segment.get("speech_rate", 1.0),
        "pinyin_hints": segment.get("pinyin_hints") or None,
        "delivery": segment.get("delivery") or {},
        "pause_before": segment.get("pause_before", 0),
        "pause_after": segment.get("pause_after", 0),
        "pauses": segment.get("pauses") or [],
    }
    return _digest(relevant)


def _segment_text_identity(segment: Mapping[str, Any]) -> str:
    return hashlib.sha256(str(segment.get("text") or "").encode("utf-8")).hexdigest()


def _audio_file_matches(name: str, segment_id: str) -> bool:
    stem, extension = os.path.splitext(name)
    return extension.lower() == ".wav" and (
        stem == segment_id or stem.startswith(f"{segment_id}_")
    )


def _segment_audio(
    state: _ProjectState,
    segment: Mapping[str, Any],
    all_segment_ids: set[str],
) -> dict[str, Any]:
    segment_id = str(segment.get("id") or "").strip()
    segments_dir = project_paths.project_dir(state.path, "segments")
    candidates: list[str] = []
    try:
        if os.path.isdir(segments_dir):
            candidates = sorted(
                name
                for name in os.listdir(segments_dir)
                if _audio_file_matches(name, segment_id)
            )
    except OSError:
        candidates = []

    active_revision_id = _json_dict(state.quality.get("active_revisions")).get(segment_id)
    active_revision = _json_dict(state.quality.get("revisions")).get(
        str(active_revision_id or "")
    )
    revision_path = ""
    revision_exists = False
    if isinstance(active_revision, dict):
        relative_path = str(active_revision.get("relative_path") or "").strip()
        if relative_path:
            try:
                absolute = project_paths.resolve_relative(state.path, relative_path)
                revision_path = _relative(state.path, absolute)
                revision_exists = os.path.isfile(absolute)
            except (OSError, ValueError):
                revision_path = relative_path

    # ``has_segment_wav`` is the existing repository rule for cache variants.
    # The explicit candidate list above is retained for reporting and hashing.
    try:
        repository_present = segment_cache.has_segment_wav(segments_dir, segment_id)
    except (OSError, TypeError, ValueError):
        repository_present = bool(candidates)
    present = bool(repository_present or revision_exists)
    expected_relative = _relative(
        state.path, os.path.join(segments_dir, f"{segment_id}.wav")
    )
    if revision_path:
        expected_relative = revision_path
    files = [
        _file_fingerprint(os.path.join(segments_dir, name), state.path)
        for name in candidates
    ]
    if revision_path and revision_path not in {item["path"] for item in files}:
        revision_absolute = os.path.join(state.path, revision_path)
        files.append(_file_fingerprint(revision_absolute, state.path))
    return {
        "segment_id": segment_id,
        "expected_relative_path": expected_relative,
        "expected_path_pattern": f"{_relative(state.path, segments_dir)}/{segment_id}_*.wav",
        "present": present,
        "files": files,
        "file_count": len(files),
        "active_revision_id": str(active_revision_id or ""),
        "repository_lookup": "segment_cache.has_segment_wav",
        "segment_id_known": segment_id in all_segment_ids,
    }


def _segment_records(state: _ProjectState) -> list[dict[str, Any]]:
    chapters = _script_chapters(state.script)
    statuses = state.effective_statuses()
    ids = {
        str(segment.get("id") or "").strip()
        for chapter in chapters
        for segment in _json_list(chapter.get("segments"))
        if isinstance(segment, dict) and str(segment.get("id") or "").strip()
    }
    records: list[dict[str, Any]] = []
    for chapter_index, chapter in enumerate(chapters):
        chapter_id = str(chapter.get("id") or "").strip()
        for segment_index, segment in enumerate(_json_list(chapter.get("segments"))):
            if not isinstance(segment, dict):
                continue
            segment_id = str(segment.get("id") or "").strip()
            records.append(
                {
                    "segment_id": segment_id,
                    "order": len(records),
                    "chapter_id": chapter_id,
                    "chapter_index": chapter_index,
                    "segment_index": segment_index,
                    "role": str(segment.get("role") or segment.get("speaker") or ""),
                    "role_id": str(segment.get("role_id") or "").strip(),
                    "text_sha256": _segment_text_identity(segment),
                    "content_identity": _segment_content_identity(segment),
                    "status": statuses.get(segment_id, "pending"),
                    "audio": _segment_audio(state, segment, ids),
                }
            )
    return records


def _unexpected_audio(state: _ProjectState, segment_ids: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in _AUDIO_DIRECTORY_KEYS:
        try:
            directory = project_paths.project_dir(state.path, key)
        except (KeyError, OSError, ValueError):
            continue
        if not os.path.isdir(directory) or os.path.islink(directory):
            continue
        for item in _directory_fingerprints(state.path, directory):
            name = os.path.basename(str(item.get("path") or ""))
            if not name.lower().endswith(".wav"):
                continue
            if any(_audio_file_matches(name, segment_id) for segment_id in segment_ids):
                continue
            result.append(item)
    return result


def _project_file_fingerprints(state: _ProjectState) -> dict[str, dict[str, Any]]:
    return {
        key: _file_fingerprint(path, state.path)
        for key, path in sorted(state.paths.items())
        if key in _PERSISTED_FILE_KEYS
    }


def _state_fingerprint(state: _ProjectState, voice_assets: list[dict[str, Any]]) -> str:
    audio: dict[str, list[dict[str, Any]]] = {}
    for key in _AUDIO_DIRECTORY_KEYS:
        try:
            directory = project_paths.project_dir(state.path, key)
        except (KeyError, OSError, ValueError):
            audio[key] = []
            continue
        audio[key] = _directory_fingerprints(state.path, directory)
    return _digest(
        {
            "files": _project_file_fingerprints(state),
            "task_rows": list(state.task_rows),
            "audio": audio,
            "voice_assets": sorted(voice_assets, key=lambda item: _canonical(item)),
            "project_identity": {
                "project_name": state.name,
                "project_id": state.project_id,
                "project_kind": state.project_kind,
                "parent_project_id": state.parent_project_id,
            },
        }
    )


def _public_project_reference(state: _ProjectState) -> ProjectReference:
    script_meta = _json_dict(state.script.get("meta"))
    chapters = _script_chapters(state.script)
    first_chapter = chapters[0] if chapters else {}
    return ProjectReference(
        project_name=state.name,
        project_id=state.project_id,
        project_kind=state.project_kind,
        parent_project_id=state.parent_project_id,
        title=str(script_meta.get("title") or state.name),
        chapter_title=(
            str(state.meta.get("chapter_title") or first_chapter.get("title") or "").strip()
            or None
        ),
        chapter_order=_positive_int(state.meta.get("chapter_order")),
    )


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _role_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and "roles" in value:
        value = value.get("roles")
    if isinstance(value, dict):
        result = []
        for key, raw in value.items():
            item = dict(raw) if isinstance(raw, dict) else {}
            item.setdefault("role_id", str(key))
            result.append(item)
        return result
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _resolve_voice_path(state: _ProjectState, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw
    try:
        return project_paths.resolve_relative(state.path, raw)
    except ValueError:
        return os.path.join(state.path, raw)


def _voice_identity(state: _ProjectState, record: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    asset_id = str(record.get("voice_asset_id") or "").strip()
    if asset_id:
        return f"asset:{asset_id}", {"voice_asset_id": asset_id}
    sha = str(record.get("voice_sha256") or record.get("sha256") or "").strip()
    if sha:
        return f"sha256:{sha}", {"voice_sha256": sha}
    path_value = record.get("project_voice_path") or record.get("path") or record.get("voice_path")
    path = _resolve_voice_path(state, path_value)
    if path and os.path.isfile(path):
        try:
            digest = _bytes_digest(path)
        except OSError:
            digest = ""
        if digest:
            return f"sha256:{digest}", {"path": _relative(state.path, path), "sha256": digest}
    if path:
        return f"path:{_relative(state.path, path)}", {"path": _relative(state.path, path)}
    return "", {}


def _voice_bindings(state: _ProjectState, script: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize stable cast identities and legacy display-name bindings."""
    roles: dict[str, dict[str, Any]] = {}
    voices, chapters = script_loader.resolve_collections(dict(script))

    def ensure(key: str, display_name: str = "") -> dict[str, Any]:
        item = roles.setdefault(
            key,
            {"role_key": key, "display_name": display_name or key, "identity": "", "details": {}},
        )
        if display_name and item.get("display_name") == key:
            item["display_name"] = display_name
        return item

    roster_records = _role_records(state.roster)
    roster_by_name: dict[str, str] = {}
    for raw in roster_records:
        role_id = str(raw.get("role_id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if role_id:
            ensure(role_id, name or role_id)
            if name:
                roster_by_name[name] = role_id

    for role_name in (voices or {}):
        name = str(role_name)
        ensure(roster_by_name.get(name, name), name)
    for chapter in chapters:
        for segment in _json_list(chapter.get("segments")):
            if not isinstance(segment, dict):
                continue
            role_id = str(segment.get("role_id") or "").strip()
            role_name = str(segment.get("role") or segment.get("speaker") or "").strip()
            ensure(role_id or roster_by_name.get(role_name, role_name), role_name)

    cast_records = _role_records(state.cast)
    for raw in cast_records:
        key = str(raw.get("role_id") or raw.get("name") or "").strip()
        if not key:
            continue
        item = ensure(key, str(raw.get("name") or key))
        identity, details = _voice_identity(state, raw)
        if identity:
            item["identity"] = identity
            item["details"] = details

    raw_role_bindings = _json_dict(state.bindings.get("role_bindings"))
    for key, raw in raw_role_bindings.items():
        item = ensure(str(key))
        raw_record = raw if isinstance(raw, dict) else {"path": raw}
        identity, details = _voice_identity(state, raw_record)
        if identity and not item.get("identity"):
            item["identity"] = identity
            item["details"] = details

    legacy_bindings = _json_dict(state.bindings.get("bindings"))
    for name, raw in legacy_bindings.items():
        key = roster_by_name.get(str(name), str(name))
        item = ensure(key, str(name))
        identity, details = _voice_identity(state, {"path": raw})
        if identity and not item.get("identity"):
            item["identity"] = identity
            item["details"] = details

    return {key: roles[key] for key in sorted(roles)}


def _quality_inventory(state: _ProjectState, segment_ids: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    quality = state.quality
    revisions = _json_dict(quality.get("revisions"))
    active = _json_dict(quality.get("active_revisions"))
    technical = _json_dict(quality.get("technical_qa"))
    human = _json_dict(quality.get("human_reviews"))
    revision_segments = {
        str(record.get("segment_id") or "").strip()
        for record in revisions.values()
        if isinstance(record, dict) and str(record.get("segment_id") or "").strip()
    }
    orphan_revisions = sorted(revision_segments - segment_ids)
    qa_revision_ids = set(technical) | set(human)
    qa_segments: set[str] = set()
    orphan_qa: list[str] = []
    for revision_id in sorted(qa_revision_ids):
        record = revisions.get(revision_id)
        segment_id = str(record.get("segment_id") or "").strip() if isinstance(record, dict) else ""
        if not segment_id or segment_id not in segment_ids:
            orphan_qa.append(revision_id)
        else:
            qa_segments.add(segment_id)
    qa = {
        "technical_record_count": len(technical),
        "human_review_count": len(human),
        "record_count": len(qa_revision_ids),
        "segment_coverage": sorted(qa_segments),
        "orphan_revision_ids": orphan_qa,
        "transfer_policy": "EXCLUDED_FROM_EXECUTION_PLAN",
        "remap_required": False,
    }
    revision = {
        "record_count": len(revisions),
        "active_revision_count": len(active),
        "segment_ids": sorted(revision_segments),
        "orphan_segment_ids": orphan_revisions,
        "transfer_policy": "NOT_TRANSFERRED_IN_C1",
        "remap_required": False,
    }
    return qa, revision


def _task_inventory(state: _ProjectState, project_name: str) -> dict[str, Any]:
    rows = [row for row in state.task_rows if str(row.get("project") or "") == project_name]
    active = [row for row in rows if str(row.get("status") or "") in _ACTIVE_TASK_STATES]
    provenance = []
    for row in rows:
        options = _json_dict(row.get("options"))
        snapshot = options.get("engine_snapshot")
        if isinstance(snapshot, dict) and snapshot:
            provenance.append(
                {
                    "task_id": str(row.get("task_id") or ""),
                    "task_type": str(row.get("task_type") or ""),
                    "status": str(row.get("status") or ""),
                    "cache_identity": str(snapshot.get("cache_identity") or ""),
                }
            )
    return {
        "record_count": len(rows),
        "active_count": len(active),
        "active_tasks": sorted(active, key=lambda item: str(item.get("task_id") or "")),
        "active_task_policy": "NOT_TRANSFERABLE",
        "provenance_records": sorted(provenance, key=lambda item: _canonical(item)),
        "database_read_error": state.task_error or "",
    }


def _repair_inventory(state: _ProjectState, task_inventory: Mapping[str, Any]) -> dict[str, Any]:
    records = _json_dict(state.quality.get("repair_history"))
    active = []
    for identifier, raw in records.items():
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "").lower()
        if status in _TERMINAL_REPAIR_STATES:
            continue
        active.append(
            {
                "repair_id": str(identifier),
                "task_id": str(raw.get("task_id") or ""),
                "status": status,
            }
        )
    return {
        "record_count": len(records),
        "active_count": len(active),
        "active_repairs": sorted(active, key=lambda item: item["repair_id"]),
        "policy": "NOT_TRANSFERABLE",
    }


def _integrity_snapshot(name: str) -> dict[str, Any]:
    try:
        report = ProjectStorageRepository.check_project_integrity(name)
    except (OSError, TypeError, ValueError, KeyError, RuntimeError) as exc:
        return {
            "status": "FAIL",
            "ok": False,
            "issue_count": 1,
            "issues": [{"code": "INTEGRITY_SCAN_ERROR", "severity": ERROR, "message": str(exc)}],
        }
    issues = [
        {
            "code": str(item.get("code") or ""),
            "severity": str(item.get("severity") or ERROR).upper(),
            "segment_id": str(item.get("segment_id") or ""),
            "message": str(item.get("message") or ""),
        }
        for item in _json_list(report.get("issues"))
        if isinstance(item, dict)
    ]
    issues.sort(key=lambda item: (item["code"], item["segment_id"], item["message"]))
    return {
        "status": "PASS" if bool(report.get("ok")) else "FAIL",
        "ok": bool(report.get("ok")),
        "issue_count": len(issues),
        "issues": issues,
    }


def _public_conflict_details(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _public_conflict_details(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_public_conflict_details(item) for item in value]
    return value


@dataclass(frozen=True)
class ProjectReference:
    project_name: str
    project_id: str
    project_kind: str
    parent_project_id: str = ""
    title: str = ""
    chapter_title: str | None = None
    chapter_order: int | None = None


@dataclass(frozen=True)
class MergeConflict:
    code: str
    severity: str
    domain: str
    message: str
    blocking: bool = False
    source_ref: str = ""
    target_ref: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "domain": self.domain,
            "message": self.message,
            "blocking": self.blocking,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "details": _public_conflict_details(self.details),
        }


@dataclass(frozen=True)
class MergeValidationResult:
    valid: bool
    planning_allowed: bool
    source_project_id: str = ""
    target_project_id: str = ""
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SegmentRemapPlan:
    policy: str
    preserve_source_ids: bool
    collisions: tuple[str, ...] = ()
    proposed_mapping: Mapping[str, str] = field(default_factory=dict)
    remap_required: bool = False
    reason: str = ""


@dataclass(frozen=True)
class MergeSourceInventory:
    total_segments: int
    ordered_segments: tuple[Mapping[str, Any], ...] = ()
    segment_ids: tuple[str, ...] = ()
    chapter_count: int = 0
    audio: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MergeTargetInventory:
    total_segments: int
    ordered_segments: tuple[Mapping[str, Any], ...] = ()
    segment_ids: tuple[str, ...] = ()
    chapter_count: int = 0
    audio: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VoiceCompatibilityPlan:
    roles: tuple[Mapping[str, Any], ...] = ()
    compatible_count: int = 0
    source_only_count: int = 0
    target_only_count: int = 0
    conflict_count: int = 0
    policy: str = "NO_SILENT_BINDING_SELECTION"


@dataclass(frozen=True)
class MergePlan:
    """Machine-readable, deterministic C.1 plan.

    ``MergePlan`` intentionally exposes mapping-like helpers because existing
    service consumers in this repository commonly consume JSON-shaped service
    results, while domain callers can use the typed attributes directly.
    """

    schema_version: str
    source_project: ProjectReference | None
    target_project: ProjectReference | None
    validation: MergeValidationResult
    source_inventory: MergeSourceInventory
    target_inventory: MergeTargetInventory
    segment_remap: SegmentRemapPlan
    placement: Mapping[str, Any]
    audio_policy: Mapping[str, Any]
    voice_compatibility: VoiceCompatibilityPlan
    qa_inventory: Mapping[str, Any]
    revision_inventory: Mapping[str, Any]
    task_state: Mapping[str, Any]
    provenance: Mapping[str, Any]
    export_policy: Mapping[str, Any]
    backup_policy: Mapping[str, Any]
    integrity: Mapping[str, Any]
    conflicts: tuple[MergeConflict, ...]
    planning_status: str
    execution_eligibility: str
    plan_token: str
    fingerprint: str
    token_scope: Mapping[str, Any]

    @property
    def source(self) -> ProjectReference | None:
        return self.source_project

    @property
    def target(self) -> ProjectReference | None:
        return self.target_project

    @property
    def token(self) -> str:
        return self.plan_token

    @property
    def eligibility(self) -> str:
        return self.execution_eligibility

    @property
    def planning_allowed(self) -> bool:
        return self.validation.planning_allowed

    def as_dict(self) -> dict[str, Any]:
        payload = _public_conflict_details(asdict(self))
        payload["source"] = payload.get("source_project")
        payload["target"] = payload.get("target_project")
        payload["token"] = self.plan_token
        payload["eligibility"] = self.execution_eligibility
        payload["planning_allowed"] = self.validation.planning_allowed
        return payload

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)


def _empty_source_inventory() -> MergeSourceInventory:
    return MergeSourceInventory(total_segments=0)


def _empty_target_inventory() -> MergeTargetInventory:
    return MergeTargetInventory(total_segments=0)


def _resolve_project_ref(
    reference: Any,
    states: Mapping[str, _ProjectState],
) -> tuple[_ProjectState | None, bool, str]:
    if isinstance(reference, Mapping):
        requested_name = str(
            reference.get("project_name")
            or reference.get("name")
            or reference.get("id")
            or reference.get("project_id")
            or ""
        ).strip()
    else:
        requested_name = str(reference or "").strip()
    if not requested_name:
        return None, False, ""
    if requested_name in states:
        return states[requested_name], False, requested_name
    matches = [state for state in states.values() if state.project_id == requested_name]
    if len(matches) == 1:
        return matches[0], False, requested_name
    if len(matches) > 1:
        return None, True, requested_name
    return None, False, requested_name


def _catalog_states() -> dict[str, _ProjectState]:
    try:
        names = ProjectRepository.scan_projects()
    except (OSError, TypeError, ValueError):
        names = []
    return {name: _load_project_state(name) for name in sorted(set(names))}


def _identity_index(states: Mapping[str, _ProjectState]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for state in states.values():
        if state.project_id:
            index.setdefault(state.project_id, []).append(state.name)
    return {key: sorted(value) for key, value in sorted(index.items())}


def _add_conflict(
    conflicts: list[MergeConflict],
    code: str,
    severity: str,
    domain: str,
    message: str,
    *,
    blocking: bool = False,
    source_ref: str = "",
    target_ref: str = "",
    details: Mapping[str, Any] | None = None,
) -> None:
    conflicts.append(
        MergeConflict(
            code=code,
            severity=severity,
            domain=domain,
            message=message,
            blocking=blocking,
            source_ref=source_ref,
            target_ref=target_ref,
            details=dict(details or {}),
        )
    )


def _placement(
    source: _ProjectState,
    target: _ProjectState,
    conflicts: list[MergeConflict],
) -> dict[str, Any]:
    source_chapters = _script_chapters(source.script)
    target_chapters = _script_chapters(target.script)
    source_order = _positive_int(source.meta.get("chapter_order"))
    if source_order is None and source_chapters:
        source_order = _positive_int(source_chapters[0].get(chapter_identity.CHAPTER_NUMBER_KEY))
    source_title = str(
        source.meta.get("chapter_title")
        or (source_chapters[0].get("title") if source_chapters else "")
        or source.name
    ).strip()
    if not isinstance(target.script, dict) or not isinstance(target.script.get("chapters"), list):
        _add_conflict(
            conflicts,
            "TARGET_SCRIPT_STRUCTURE_UNSAFE",
            ERROR,
            "placement",
            "目标 Book 的实际剧本结构无法安全表示章节插入位置",
            blocking=True,
            source_ref=source.name,
            target_ref=target.name,
        )
        return {
            "status": "BLOCKED",
            "mode": "UNRESOLVABLE",
            "reason": "target script chapters is not a list",
            "source_title": source_title,
            "source_order": source_order,
        }

    ordered = [
        {
            "index": index,
            "chapter_id": str(chapter.get("id") or ""),
            "title": str(chapter.get("title") or ""),
            "order": _chapter_order(chapter, index),
        }
        for index, chapter in enumerate(target_chapters)
    ]
    if source_order is None:
        return {
            "status": "READY_WITH_WARNINGS",
            "mode": "APPEND_AT_END",
            "target_index": len(ordered),
            "source_title": source_title,
            "source_order": None,
            "basis": "target structured_script chapter array; catalog order absent",
        }

    after = [item for item in ordered if item["order"] <= source_order]
    before = [item for item in ordered if item["order"] > source_order]
    if before:
        next_item = min(before, key=lambda item: (item["order"], item["index"]))
        previous = max(after, key=lambda item: (item["order"], item["index"])) if after else None
        return {
            "status": "READY",
            "mode": "INSERT_BEFORE",
            "target_index": next_item["index"],
            "after": previous,
            "before": next_item,
            "source_title": source_title,
            "source_order": source_order,
            "basis": "source project chapter_order compared with actual target script order",
        }
    return {
        "status": "READY",
        "mode": "APPEND_AT_END",
        "target_index": len(ordered),
        "after": max(ordered, key=lambda item: (item["order"], item["index"])) if ordered else None,
        "source_title": source_title,
        "source_order": source_order,
        "basis": "source project chapter_order compared with actual target script order",
    }


def _audio_inventory(
    source: _ProjectState,
    target: _ProjectState,
    source_records: list[dict[str, Any]],
    target_records: list[dict[str, Any]],
    conflicts: list[MergeConflict],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_present = [record for record in source_records if record["audio"]["present"]]
    source_missing = [record["segment_id"] for record in source_records if not record["audio"]["present"]]
    if not source_records or not source_present:
        coverage = NO_AUDIO
        severity = ERROR
        blocking = True
    elif len(source_present) < len(source_records):
        coverage = PARTIAL_AUDIO
        severity = WARNING
        blocking = False
    else:
        coverage = COMPLETE_AUDIO
        severity = INFO
        blocking = False
    if coverage != COMPLETE_AUDIO:
        _add_conflict(
            conflicts,
            "SOURCE_AUDIO_MISSING",
            severity,
            "audio",
            (
                "来源章节没有可解析的分段音频"
                if coverage == NO_AUDIO
                else "来源章节只有部分分段音频可解析"
            ),
            blocking=blocking,
            source_ref=source.name,
            target_ref=target.name,
            details={"coverage": coverage, "missing_segment_ids": sorted(source_missing)},
        )

    source_ids = {record["segment_id"] for record in source_records if record["segment_id"]}
    target_ids = {record["segment_id"] for record in target_records if record["segment_id"]}
    target_by_id = {record["segment_id"]: record for record in target_records}
    target_conflicts: list[dict[str, Any]] = []
    for record in source_records:
        segment_id = record["segment_id"]
        if not segment_id:
            continue
        target_audio = target_by_id.get(segment_id, {}).get("audio", {})
        if target_audio.get("present") or segment_id in target_ids:
            target_conflicts.append(
                {
                    "segment_id": segment_id,
                    "target_segment_exists": segment_id in target_ids,
                    "target_audio_files": target_audio.get("files", []),
                }
            )
    if target_conflicts and not (source_ids & target_ids):
        _add_conflict(
            conflicts,
            "TARGET_AUDIO_PATH_CONFLICT",
            ERROR,
            "audio",
            "目标分段音频目录已有与来源 ID 相同的文件，无法安全保留来源文件名",
            blocking=True,
            source_ref=source.name,
            target_ref=target.name,
            details={"conflicts": target_conflicts},
        )

    source_unexpected = _unexpected_audio(source, source_ids)
    target_unexpected = _unexpected_audio(target, target_ids)
    source_audio = {
        "expected_count": len(source_records),
        "present_count": len(source_present),
        "missing_count": len(source_missing),
        "missing_segment_ids": sorted(source_missing),
        "coverage": coverage,
        "files": [item for record in source_records for item in record["audio"]["files"]],
        "unexpected_files": source_unexpected,
    }
    target_audio = {
        "existing_segment_count": len(target_records),
        "files": [item for record in target_records for item in record["audio"]["files"]],
        "unexpected_files": target_unexpected,
        "destination_conflicts": target_conflicts,
        "proposed_destination_unique": not bool(target_conflicts),
    }
    return source_audio, target_audio


def _voice_compatibility(
    source: _ProjectState,
    target: _ProjectState,
    conflicts: list[MergeConflict],
) -> tuple[VoiceCompatibilityPlan, list[dict[str, Any]]]:
    source_roles = _voice_bindings(source, source.script)
    target_roles = _voice_bindings(target, target.script)
    role_rows: list[dict[str, Any]] = []
    for role_key in sorted(set(source_roles) | set(target_roles)):
        source_item = source_roles.get(role_key)
        target_item = target_roles.get(role_key)
        if source_item is None:
            status = "TARGET_ONLY"
        elif target_item is None:
            status = "SOURCE_ONLY"
        elif source_item.get("identity") and source_item.get("identity") == target_item.get("identity"):
            status = "COMPATIBLE"
        else:
            status = "CONFLICT"
        row = {
            "role_key": role_key,
            "display_name": str((source_item or target_item or {}).get("display_name") or role_key),
            "status": status,
            "source_voice": source_item.get("identity", "") if source_item else "",
            "target_voice": target_item.get("identity", "") if target_item else "",
            "source_details": source_item.get("details", {}) if source_item else {},
            "target_details": target_item.get("details", {}) if target_item else {},
        }
        role_rows.append(row)
        if status == "SOURCE_ONLY":
            _add_conflict(
                conflicts,
                "SOURCE_ONLY_ROLE",
                ERROR,
                "voice_cast",
                f"目标 Book 缺少来源角色绑定：{row['display_name']}",
                blocking=True,
                source_ref=source.name,
                target_ref=target.name,
                details=row,
            )
        elif status == "CONFLICT":
            _add_conflict(
                conflicts,
                "VOICE_BINDING_CONFLICT",
                ERROR,
                "voice_cast",
                f"来源与目标的角色音色不一致：{row['display_name']}",
                blocking=True,
                source_ref=source.name,
                target_ref=target.name,
                details=row,
            )
        elif status == "TARGET_ONLY":
            _add_conflict(
                conflicts,
                "TARGET_ONLY_ROLE",
                WARNING,
                "voice_cast",
                f"目标 Book 存在来源未使用的角色：{row['display_name']}",
                source_ref=source.name,
                target_ref=target.name,
                details=row,
            )

    compatible = sum(row["status"] == "COMPATIBLE" for row in role_rows)
    source_only = sum(row["status"] == "SOURCE_ONLY" for row in role_rows)
    target_only = sum(row["status"] == "TARGET_ONLY" for row in role_rows)
    conflicts_count = sum(row["status"] == "CONFLICT" for row in role_rows)
    plan = VoiceCompatibilityPlan(
        roles=tuple(role_rows),
        compatible_count=compatible,
        source_only_count=source_only,
        target_only_count=target_only,
        conflict_count=conflicts_count,
    )
    assets = []
    for state, role_map in ((source, source_roles), (target, target_roles)):
        for row in role_map.values():
            details = _json_dict(row.get("details"))
            path = details.get("path")
            if path:
                assets.append(_file_fingerprint(os.path.join(state.path, path), state.path))
    return plan, assets


class ChapterMergePlanner:
    """The C.1 read-only planner service."""

    @staticmethod
    def list_source_chapters() -> list[ProjectReference]:
        states = _catalog_states()
        duplicate_ids = {
            project_id
            for project_id, names in _identity_index(states).items()
            if len(names) > 1
        }
        result = []
        for state in states.values():
            if state.project_kind != "chapter" or not state.project_id:
                continue
            if state.project_id in duplicate_ids:
                continue
            if _script_validation(state):
                continue
            result.append(_public_project_reference(state))
        return sorted(result, key=lambda item: (item.chapter_order is None, item.chapter_order or 0, item.project_name))

    @staticmethod
    def list_target_books(source_chapter: Any = None) -> list[ProjectReference]:
        states = _catalog_states()
        duplicate_ids = {
            project_id
            for project_id, names in _identity_index(states).items()
            if len(names) > 1
        }
        source_state, _ambiguous, _requested = _resolve_project_ref(source_chapter, states)
        result = []
        for state in states.values():
            if state.project_kind != "book" or not state.project_id:
                continue
            if state.project_id in duplicate_ids:
                continue
            if state.parent_project_id:
                continue
            if source_state is not None and state.name == source_state.name:
                continue
            if _script_validation(state):
                continue
            result.append(_public_project_reference(state))
        return sorted(result, key=lambda item: item.project_name)

    @classmethod
    def plan_merge(
        cls,
        source_chapter: Any,
        target_book: Any,
        *,
        allow_unrelated_target: bool = False,
        session: Any = None,
        opened_projects: Iterable[str] | None = None,
    ) -> MergePlan:
        states = _catalog_states()
        identity_index = _identity_index(states)
        duplicate_ids = {key for key, names in identity_index.items() if len(names) > 1}
        source, source_ambiguous, source_requested = _resolve_project_ref(source_chapter, states)
        target, target_ambiguous, target_requested = _resolve_project_ref(target_book, states)
        conflicts: list[MergeConflict] = []

        source_name = source.name if source else source_requested
        target_name = target.name if target else target_requested
        if source is None:
            code = "DUPLICATE_PROJECT_ID" if source_ambiguous else "SOURCE_NOT_FOUND"
            message = "来源 Chapter 的 project_id 不唯一" if source_ambiguous else "来源 Chapter 不存在或不可解析"
            _add_conflict(conflicts, code, ERROR, "validation", message, blocking=True, source_ref=source_requested)
        if target is None:
            code = "DUPLICATE_PROJECT_ID" if target_ambiguous else "TARGET_NOT_FOUND"
            message = "目标 Book 的 project_id 不唯一" if target_ambiguous else "目标 Book 不存在或不可解析"
            _add_conflict(conflicts, code, ERROR, "validation", message, blocking=True, target_ref=target_requested)

        source_reference = _public_project_reference(source) if source else None
        target_reference = _public_project_reference(target) if target else None
        source_records = _segment_records(source) if source else []
        target_records = _segment_records(target) if target else []
        source_ids = [record["segment_id"] for record in source_records]
        target_ids = [record["segment_id"] for record in target_records]
        source_id_set = {item for item in source_ids if item}
        target_id_set = {item for item in target_ids if item}

        if source is not None:
            if source.project_kind != "chapter":
                _add_conflict(conflicts, "SOURCE_NOT_CHAPTER", ERROR, "validation", "来源项目不是 Chapter", blocking=True, source_ref=source.name)
            if not source.project_id:
                _add_conflict(conflicts, "SOURCE_ID_MISSING", ERROR, "validation", "来源 Chapter 缺少稳定 project_id", blocking=True, source_ref=source.name)
            if source.project_id in duplicate_ids:
                _add_conflict(conflicts, "DUPLICATE_PROJECT_ID", ERROR, "validation", "来源 Chapter 的 project_id 重复", blocking=True, source_ref=source.name, details={"project_id": source.project_id, "projects": identity_index.get(source.project_id, [])})
            validation = _script_validation(source)
            if validation:
                _add_conflict(conflicts, "SOURCE_SCRIPT_INVALID", ERROR, "validation", "来源 Chapter 的结构化剧本校验失败", blocking=True, source_ref=source.name, details={"issues": validation[:10]})
            if len(_script_chapters(source.script)) != 1:
                _add_conflict(conflicts, "SOURCE_SCRIPT_STRUCTURE_UNSAFE", ERROR, "placement", "来源 Chapter 项目实际包含的 chapters 不是恰好一章，无法安全定义导入边界", blocking=True, source_ref=source.name, details={"chapter_count": len(_script_chapters(source.script))})
            if source.read_errors:
                _add_conflict(conflicts, "SOURCE_METADATA_UNREADABLE", ERROR, "validation", "来源项目存在不可读取的持久化元数据", blocking=True, source_ref=source.name, details={"errors": list(source.read_errors)})

        if target is not None:
            if target.project_kind != "book":
                _add_conflict(conflicts, "TARGET_NOT_BOOK", ERROR, "validation", "目标项目不是 Book", blocking=True, target_ref=target.name)
            if not target.project_id:
                _add_conflict(conflicts, "TARGET_ID_MISSING", ERROR, "validation", "目标 Book 缺少稳定 project_id", blocking=True, target_ref=target.name)
            if target.project_id in duplicate_ids:
                _add_conflict(conflicts, "DUPLICATE_PROJECT_ID", ERROR, "validation", "目标 Book 的 project_id 重复", blocking=True, target_ref=target.name, details={"project_id": target.project_id, "projects": identity_index.get(target.project_id, [])})
            validation = _script_validation(target)
            if validation:
                _add_conflict(conflicts, "TARGET_SCRIPT_INVALID", ERROR, "validation", "目标 Book 的结构化剧本校验失败", blocking=True, target_ref=target.name, details={"issues": validation[:10]})
            if target.read_errors:
                _add_conflict(conflicts, "TARGET_METADATA_UNREADABLE", ERROR, "validation", "目标项目存在不可读取的持久化元数据", blocking=True, target_ref=target.name, details={"errors": list(target.read_errors)})

        if source is not None and target is not None:
            if source.name == target.name or (source.project_id and source.project_id == target.project_id):
                _add_conflict(conflicts, "SOURCE_TARGET_SAME", ERROR, "validation", "来源与目标不能是同一个项目", blocking=True, source_ref=source.name, target_ref=target.name)
            if target.parent_project_id:
                _add_conflict(conflicts, "TARGET_RELATION_UNSAFE", ERROR, "hierarchy", "目标 Book 自身仍挂在其他项目下，层级关系不安全", blocking=True, target_ref=target.name, details={"parent_project_id": target.parent_project_id})
            if not allow_unrelated_target and source.parent_project_id != target.project_id:
                _add_conflict(conflicts, "RELATION_UNSAFE", ERROR, "hierarchy", "来源 Chapter 不属于指定目标 Book；未启用显式跨 Book 规划模式", blocking=True, source_ref=source.name, target_ref=target.name, details={"source_parent_project_id": source.parent_project_id, "target_project_id": target.project_id})
            elif allow_unrelated_target and source.parent_project_id != target.project_id:
                _add_conflict(conflicts, "RELATION_NOT_CURRENT_PARENT", WARNING, "hierarchy", "这是显式跨 Book 规划，未修改当前层级关系", source_ref=source.name, target_ref=target.name, details={"source_parent_project_id": source.parent_project_id, "target_project_id": target.project_id})

        if source is not None and target is not None:
            duplicate_source_segments = sorted({item for item in source_ids if source_ids.count(item) > 1 and item})
            duplicate_target_segments = sorted({item for item in target_ids if target_ids.count(item) > 1 and item})
            if duplicate_source_segments:
                _add_conflict(conflicts, "DUPLICATE_SEGMENT_ID", ERROR, "segments", "来源剧本存在重复 segment ID", blocking=True, source_ref=source.name, details={"segment_ids": duplicate_source_segments})
            if duplicate_target_segments:
                _add_conflict(conflicts, "DUPLICATE_SEGMENT_ID", ERROR, "segments", "目标剧本存在重复 segment ID", blocking=True, target_ref=target.name, details={"segment_ids": duplicate_target_segments})

        collisions = tuple(sorted(source_id_set & target_id_set))
        if collisions:
            segment_remap = SegmentRemapPlan(
                policy=UNRESOLVABLE_COLLISION,
                preserve_source_ids=False,
                collisions=collisions,
                remap_required=True,
                reason="现有 status/QA/revision/task/cache 引用没有统一的安全重写契约；C.1 不猜测 remap",
            )
            _add_conflict(conflicts, "SEGMENT_ID_COLLISION", ERROR, "segments", "来源与目标存在 segment ID 冲突，当前版本不能安全推导 remap", blocking=True, source_ref=source_name, target_ref=target_name, details={"segment_ids": list(collisions), "policy": UNRESOLVABLE_COLLISION})
        else:
            segment_remap = SegmentRemapPlan(policy=NO_COLLISION, preserve_source_ids=True, proposed_mapping={item: item for item in source_ids if item})

        if source is not None and target is not None:
            source_audio, target_audio = _audio_inventory(source, target, source_records, target_records, conflicts)
            placement = _placement(source, target, conflicts)
            voice_plan, voice_assets = _voice_compatibility(source, target, conflicts)
            qa_inventory, revision_inventory = _quality_inventory(source, source_id_set)
            target_qa, target_revisions = _quality_inventory(target, target_id_set)
            qa_inventory = {**qa_inventory, "target_record_count": target_qa["record_count"], "target_segment_coverage": target_qa["segment_coverage"]}
            revision_inventory = {**revision_inventory, "target_record_count": target_revisions["record_count"], "target_segment_ids": target_revisions["segment_ids"]}
            if qa_inventory["orphan_revision_ids"]:
                _add_conflict(conflicts, "ORPHAN_QA_RECORD", WARNING, "qa", "来源存在无法关联当前 segment 的 QA 记录", source_ref=source.name, target_ref=target.name, details={"revision_ids": qa_inventory["orphan_revision_ids"]})
            if revision_inventory["record_count"]:
                revision_inventory["remap_required"] = bool(collisions)
                _add_conflict(conflicts, "UNSUPPORTED_REVISION_MAPPING", ERROR, "revision", "来源存在 Revision 历史，但 C.1 没有跨项目 revision 保留/重绑契约", blocking=True, source_ref=source.name, target_ref=target.name, details={"record_count": revision_inventory["record_count"], "transfer_policy": revision_inventory["transfer_policy"]})
            qa_inventory["remap_required"] = bool(collisions and qa_inventory["record_count"])
            if qa_inventory["remap_required"]:
                _add_conflict(conflicts, "UNSUPPORTED_QA_REMAP", ERROR, "qa", "segment ID 冲突会要求 QA 重绑，但 C.1 不执行 QA remap", blocking=True, source_ref=source.name, target_ref=target.name)
            source_tasks = _task_inventory(source, source.name)
            target_tasks = _task_inventory(target, target.name)
            task_state = {"source": source_tasks, "target": target_tasks, "transfer_policy": "NOT_TRANSFERABLE"}
            for side, name, inventory in (("source", source.name, source_tasks), ("target", target.name, target_tasks)):
                if inventory["database_read_error"]:
                    _add_conflict(conflicts, "TASK_DB_UNREADABLE", ERROR, "tasks", "任务数据库无法以只读模式读取", blocking=True, source_ref=name if side == "source" else "", target_ref=name if side == "target" else "", details={"error": inventory["database_read_error"]})
                if inventory["active_count"]:
                    _add_conflict(conflicts, "ACTIVE_TASK", ERROR, "tasks", "项目存在活动运行任务；任务状态不可转移", blocking=True, source_ref=name if side == "source" else "", target_ref=name if side == "target" else "", details={"tasks": inventory["active_tasks"]})
            source_repairs = _repair_inventory(source, source_tasks)
            target_repairs = _repair_inventory(target, target_tasks)
            if source_repairs["active_count"]:
                _add_conflict(conflicts, "ACTIVE_REPAIR", ERROR, "repair", "来源存在活动 Repair 工作流", blocking=True, source_ref=source.name, details={"repairs": source_repairs["active_repairs"]})
            if target_repairs["active_count"]:
                _add_conflict(conflicts, "ACTIVE_REPAIR", ERROR, "repair", "目标存在活动 Repair 工作流", blocking=True, target_ref=target.name, details={"repairs": target_repairs["active_repairs"]})
            task_state["source"]["repair"] = source_repairs
            task_state["target"]["repair"] = target_repairs
            provenance = {
                "source_task_count": len(source_tasks["provenance_records"]),
                "target_task_count": len(target_tasks["provenance_records"]),
                "source_revision_count": sum(1 for item in _json_dict(source.quality.get("revisions")).values() if isinstance(item, dict) and _json_dict(item.get("params")).get("engine_snapshot")),
                "target_revision_count": sum(1 for item in _json_dict(target.quality.get("revisions")).values() if isinstance(item, dict) and _json_dict(item.get("params")).get("engine_snapshot")),
                "policy": "HISTORICAL_ONLY_NOT_TRANSFERRED",
                "remap_required": bool(collisions),
            }
            if provenance["source_task_count"] or provenance["source_revision_count"]:
                _add_conflict(conflicts, "HISTORICAL_PROVENANCE_EXCLUDED", WARNING, "provenance", "历史 engine provenance 只做审计，不进入 C.1 合并执行计划", source_ref=source.name, target_ref=target.name, details=provenance)
            export_policy = {
                "source_export_job_count": len(_json_dict(source.quality.get("export_jobs"))),
                "source_delivery_manifest_count": len(_json_dict(source.quality.get("delivery_manifests"))),
                "target_export_job_count": len(_json_dict(target.quality.get("export_jobs"))),
                "target_delivery_manifest_count": len(_json_dict(target.quality.get("delivery_manifests"))),
                "transfer_policy": "EXCLUDED_FROM_EXECUTION_PLAN",
            }
            backup_policy = {"target_backup": "REQUIRED_BEFORE_C2_EXECUTION", "source_backup": "NOT_REQUIRED_BY_DEFAULT", "created": False}
            integrity = {"source": _integrity_snapshot(source.name), "target": _integrity_snapshot(target.name), "repair_performed": False}
            if integrity["source"]["status"] == "FAIL":
                _add_conflict(conflicts, "SOURCE_INTEGRITY_FAILED", ERROR, "integrity", "来源完整性扫描失败；C.1 不执行修复", blocking=True, source_ref=source.name, details=integrity["source"])
            if integrity["target"]["status"] == "FAIL":
                _add_conflict(conflicts, "TARGET_INTEGRITY_FAILED", ERROR, "integrity", "目标完整性扫描失败；C.1 不执行修复", blocking=True, target_ref=target.name, details=integrity["target"])
            source_state_fingerprint = _state_fingerprint(source, voice_assets)
            target_state_fingerprint = _state_fingerprint(target, voice_assets)
        else:
            source_audio = {"coverage": NO_AUDIO, "expected_count": 0, "present_count": 0, "missing_count": 0, "missing_segment_ids": [], "files": [], "unexpected_files": []}
            target_audio = {"existing_segment_count": 0, "files": [], "unexpected_files": [], "destination_conflicts": [], "proposed_destination_unique": False}
            placement = {"status": "BLOCKED", "mode": "UNRESOLVABLE"}
            voice_plan = VoiceCompatibilityPlan()
            qa_inventory = {}
            revision_inventory = {}
            task_state = {}
            provenance = {"policy": "HISTORICAL_ONLY_NOT_TRANSFERRED"}
            export_policy = {"transfer_policy": "EXCLUDED_FROM_EXECUTION_PLAN"}
            backup_policy = {"target_backup": "REQUIRED_BEFORE_C2_EXECUTION", "source_backup": "NOT_REQUIRED_BY_DEFAULT", "created": False}
            integrity = {"repair_performed": False}
            source_state_fingerprint = _digest({"requested": source_requested})
            target_state_fingerprint = _digest({"requested": target_requested})

        opened: set[str] = set()
        if session is not None:
            opened_name = str(getattr(session, "project", "") or "").strip()
            if opened_name:
                opened.add(opened_name)
        for name in opened_projects or ():
            if str(name or "").strip():
                opened.add(str(name).strip())
        if source is not None and source.name in opened:
            _add_conflict(conflicts, "SOURCE_OPENED", ERROR, "runtime", "来源项目当前已打开；未来执行必须先离开该项目", blocking=True, source_ref=source.name)
        if target is not None and target.name in opened:
            _add_conflict(conflicts, "TARGET_OPENED", ERROR, "runtime", "目标项目当前已打开；未来执行必须先离开该项目", blocking=True, target_ref=target.name)

        if source is not None and target is not None:
            # The audio policy is deliberately explicit: partial audio is a
            # warning, no audio blocks an audio-preserving future execution.
            audio_policy = {
                "missing_audio_policy": "PARTIAL_WARN_NO_AUDIO_BLOCK",
                "structural_merge_with_missing_audio": "NOT_EXECUTED_IN_C1",
                "source_coverage": source_audio["coverage"],
                "target_destination_unique": target_audio["proposed_destination_unique"],
            }
        else:
            audio_policy = {"missing_audio_policy": "PARTIAL_WARN_NO_AUDIO_BLOCK"}

        planning_blockers = {
            "SOURCE_NOT_FOUND",
            "TARGET_NOT_FOUND",
            "DUPLICATE_PROJECT_ID",
            "SOURCE_NOT_CHAPTER",
            "TARGET_NOT_BOOK",
            "SOURCE_TARGET_SAME",
            "SOURCE_ID_MISSING",
            "TARGET_ID_MISSING",
            "TARGET_RELATION_UNSAFE",
            "SOURCE_METADATA_UNREADABLE",
            "TARGET_METADATA_UNREADABLE",
            "SOURCE_SCRIPT_INVALID",
            "TARGET_SCRIPT_INVALID",
            "SOURCE_SCRIPT_STRUCTURE_UNSAFE",
            "TARGET_SCRIPT_STRUCTURE_UNSAFE",
            "TASK_DB_UNREADABLE",
        }
        planning_allowed = bool(source is not None and target is not None and not any(item.code in planning_blockers for item in conflicts))
        blocking_conflicts = tuple(item for item in conflicts if item.blocking)
        if blocking_conflicts:
            eligibility = BLOCKED
        elif conflicts:
            eligibility = READY_WITH_WARNINGS
        else:
            eligibility = READY
        validation = MergeValidationResult(
            valid=planning_allowed,
            planning_allowed=planning_allowed,
            source_project_id=source.project_id if source else "",
            target_project_id=target.project_id if target else "",
            errors=tuple(item.code for item in conflicts if item.code in planning_blockers),
        )
        token_scope = {
            "schema_version": PLANNER_SCHEMA_VERSION,
            "source_project_id": source.project_id if source else "",
            "target_project_id": target.project_id if target else "",
            "source_state_fingerprint": source_state_fingerprint,
            "target_state_fingerprint": target_state_fingerprint,
            "catalog_identity_index": identity_index,
            "segment_policy": segment_remap.policy,
            "allow_unrelated_target": bool(allow_unrelated_target),
        }
        plan_token = _digest(token_scope)
        plan = MergePlan(
            schema_version=PLANNER_SCHEMA_VERSION,
            source_project=source_reference,
            target_project=target_reference,
            validation=validation,
            source_inventory=MergeSourceInventory(
                total_segments=len(source_records),
                ordered_segments=tuple(source_records),
                segment_ids=tuple(source_ids),
                chapter_count=len(_script_chapters(source.script)) if source else 0,
                audio=source_audio,
            ),
            target_inventory=MergeTargetInventory(
                total_segments=len(target_records),
                ordered_segments=tuple(target_records),
                segment_ids=tuple(target_ids),
                chapter_count=len(_script_chapters(target.script)) if target else 0,
                audio=target_audio,
            ),
            segment_remap=segment_remap,
            placement=placement,
            audio_policy=audio_policy,
            voice_compatibility=voice_plan,
            qa_inventory=qa_inventory,
            revision_inventory=revision_inventory,
            task_state=task_state,
            provenance=provenance,
            export_policy=export_policy,
            backup_policy=backup_policy,
            integrity=integrity,
            conflicts=tuple(conflicts),
            planning_status=PLANNING_ALLOWED if planning_allowed else PLANNING_BLOCKED,
            execution_eligibility=eligibility,
            plan_token=plan_token,
            fingerprint=plan_token,
            token_scope=token_scope,
        )
        return plan

    @classmethod
    def is_plan_current(cls, plan: MergePlan) -> bool:
        """Re-plan the same identities and compare the stable token."""
        source = plan.source_project.project_name if plan.source_project else ""
        target = plan.target_project.project_name if plan.target_project else ""
        if not source or not target:
            return False
        current = cls.plan_merge(
            source,
            target,
            allow_unrelated_target=bool(plan.token_scope.get("allow_unrelated_target")),
        )
        return current.plan_token == plan.plan_token


def plan_merge(source_chapter: Any, target_book: Any, **kwargs: Any) -> MergePlan:
    """Module-level convenience API for callers that prefer functional style."""
    return ChapterMergePlanner.plan_merge(source_chapter, target_book, **kwargs)


__all__ = [
    "BLOCKED",
    "COLLISION_REMAP_REQUIRED",
    "COMPLETE_AUDIO",
    "ERROR",
    "INFO",
    "NO_AUDIO",
    "NO_COLLISION",
    "PARTIAL_AUDIO",
    "PLANNING_ALLOWED",
    "PLANNING_BLOCKED",
    "READY",
    "READY_WITH_WARNINGS",
    "UNRESOLVABLE_COLLISION",
    "ChapterMergePlanner",
    "MergeConflict",
    "MergePlan",
    "MergeSourceInventory",
    "MergeTargetInventory",
    "MergeValidationResult",
    "ProjectReference",
    "SegmentRemapPlan",
    "VoiceCompatibilityPlan",
    "plan_merge",
]
