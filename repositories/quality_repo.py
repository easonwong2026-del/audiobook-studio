"""Project-local quality, revision, repair, export and delivery persistence.

The repository deliberately stores only JSON-safe, project-relative public
records.  Audio files remain in the normal project layout; this file records
which immutable revision is active and the history needed by QA, repair and
delivery services.
"""
from __future__ import annotations

import copy
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar

from lib import project_paths
from repositories._atomic import atomic_write
from repositories._file_lock import RepositoryFileLock
from repositories.project_repo import ProjectRepository


SCHEMA_VERSION = 1
_STATE_FILE = "quality_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    """Return a detached JSON-safe value or raise for unsupported objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return str(value.isoformat())
    raise TypeError(f"值不是 JSON-safe 类型: {type(value).__name__}")


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision_counters": {},
        "revisions": {},
        "active_revisions": {},
        "technical_qa": {},
        "human_reviews": {},
        "repair_history": {},
        "export_jobs": {},
        "delivery_manifests": {},
        "updated_at": "",
    }


class QualityRepository:
    """Atomic project-local persistence for production quality state."""

    # Lock order is always:
    #   1. this process-local RLock
    #   2. the project quality-state OS file lock
    #   3. state load -> callback -> atomic save
    # Public reads rely on atomic replacement and never acquire either lock, so
    # no call path can invert the order or deadlock a mutation.
    _lock: ClassVar[threading.RLock] = threading.RLock()

    @staticmethod
    def state_path(project_name: str, *, create: bool = True) -> str:
        project_dir = ProjectRepository.get_project_dir(str(project_name))
        return project_paths.project_file(
            project_dir, "quality_state", create=create
        )

    @classmethod
    def lock_path(cls, project_name: str) -> str:
        return cls.state_path(project_name, create=True) + ".lock"

    @classmethod
    def _load_unlocked(cls, project_name: str) -> dict[str, Any]:
        path = cls.state_path(project_name, create=True)
        if not os.path.isfile(path):
            return _empty_state()
        import json

        with open(path, encoding="utf-8") as file:
            value = json.load(file)
        if not isinstance(value, dict):
            raise ValueError("quality_state.json 顶层必须是对象")
        state = _empty_state()
        state.update(value)
        for key in (
            "revision_counters",
            "revisions",
            "active_revisions",
            "technical_qa",
            "human_reviews",
            "repair_history",
            "export_jobs",
            "delivery_manifests",
        ):
            if not isinstance(state.get(key), dict):
                state[key] = {}
        state["schema_version"] = SCHEMA_VERSION
        return state

    @classmethod
    def load(cls, project_name: str) -> dict[str, Any]:
        """Read one atomic snapshot without serializing independent readers."""
        return cls._load_unlocked(project_name)

    @classmethod
    def _save_unlocked(cls, project_name: str, state: dict[str, Any]) -> None:
        payload = _json_safe(copy.deepcopy(state))
        payload["schema_version"] = SCHEMA_VERSION
        payload["updated_at"] = _now()
        atomic_write(cls.state_path(project_name, create=True), payload)

    @classmethod
    def save(cls, project_name: str, state: dict[str, Any]) -> None:
        """Replace a state snapshot while excluding cross-process mutations."""
        with cls._lock:
            with RepositoryFileLock(cls.lock_path(project_name)):
                cls._save_unlocked(project_name, state)

    @classmethod
    def _mutate(
        cls,
        project_name: str,
        callback: Callable[[dict[str, Any]], Any],
    ) -> Any:
        with cls._lock:
            with RepositoryFileLock(cls.lock_path(project_name)):
                state = cls._load_unlocked(project_name)
                result = callback(state)
                cls._save_unlocked(project_name, state)
                return copy.deepcopy(result)

    @classmethod
    def create_revision(
        cls,
        project_name: str,
        segment_id: str,
        *,
        relative_path: str = "",
        cache_identity: str = "",
        voice_fingerprint: str = "",
        params: dict[str, Any] | None = None,
        source_task_id: str = "",
        status: str = "regenerating",
        activate: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        segment = str(segment_id or "").strip()
        if not segment:
            raise ValueError("segment_id 不能为空")

        def change(state: dict[str, Any]) -> dict[str, Any]:
            counters = state["revision_counters"]
            number = int(counters.get(segment, 0) or 0) + 1
            counters[segment] = number
            revision_id = f"rev_{uuid.uuid4().hex[:20]}"
            record = {
                "revision_id": revision_id,
                "segment_id": segment,
                "audio_revision": number,
                "relative_path": str(relative_path or ""),
                "cache_identity": str(cache_identity or ""),
                "voice_fingerprint": str(voice_fingerprint or ""),
                "params": _json_safe(params or {}),
                "source_task_id": str(source_task_id or ""),
                "status": str(status or "regenerating"),
                "active": False,
                "created_at": _now(),
                "updated_at": _now(),
                "metadata": _json_safe(metadata or {}),
            }
            state["revisions"][revision_id] = record
            if activate:
                cls._activate_in_state(state, revision_id)
            return record

        return cls._mutate(project_name, change)

    @classmethod
    def bootstrap_revisions(
        cls,
        project_name: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Create many initial active revisions in one atomic state write."""
        prepared = [_json_safe(item) for item in candidates if isinstance(item, dict)]

        def change(state: dict[str, Any]) -> list[dict[str, Any]]:
            created: list[dict[str, Any]] = []
            for candidate in prepared:
                segment = str(candidate.get("segment_id") or "").strip()
                if not segment or state["active_revisions"].get(segment):
                    continue
                counters = state["revision_counters"]
                number = int(counters.get(segment, 0) or 0) + 1
                counters[segment] = number
                revision_id = f"rev_{uuid.uuid4().hex[:20]}"
                record = {
                    "revision_id": revision_id,
                    "segment_id": segment,
                    "audio_revision": number,
                    "relative_path": str(candidate.get("relative_path") or ""),
                    "cache_identity": str(candidate.get("cache_identity") or ""),
                    "voice_fingerprint": str(candidate.get("voice_fingerprint") or ""),
                    "params": _json_safe(candidate.get("params") or {}),
                    "source_task_id": str(candidate.get("source_task_id") or ""),
                    "status": str(candidate.get("status") or "ready"),
                    "active": True,
                    "created_at": _now(),
                    "updated_at": _now(),
                    "metadata": _json_safe(candidate.get("metadata") or {}),
                }
                state["revisions"][revision_id] = record
                state["active_revisions"][segment] = revision_id
                created.append(record)
            return created

        return cls._mutate(project_name, change) if prepared else []

    @staticmethod
    def _activate_in_state(state: dict[str, Any], revision_id: str) -> dict[str, Any]:
        record = state["revisions"].get(revision_id)
        if not isinstance(record, dict):
            raise KeyError(f"音频 revision 不存在: {revision_id}")
        segment_id = str(record.get("segment_id") or "")
        for item in state["revisions"].values():
            if isinstance(item, dict) and item.get("segment_id") == segment_id:
                item["active"] = item.get("revision_id") == revision_id
                item["updated_at"] = _now()
        state["active_revisions"][segment_id] = revision_id
        return record

    @classmethod
    def update_revision(
        cls,
        project_name: str,
        revision_id: str,
        *,
        activate: bool | None = None,
        **updates: Any,
    ) -> dict[str, Any]:
        allowed = {
            "relative_path",
            "cache_identity",
            "voice_fingerprint",
            "params",
            "source_task_id",
            "status",
            "metadata",
        }

        def change(state: dict[str, Any]) -> dict[str, Any]:
            record = state["revisions"].get(str(revision_id))
            if not isinstance(record, dict):
                raise KeyError(f"音频 revision 不存在: {revision_id}")
            for key, value in updates.items():
                if key in allowed:
                    record[key] = _json_safe(value)
            record["updated_at"] = _now()
            if activate:
                cls._activate_in_state(state, str(revision_id))
            elif activate is False and record.get("active"):
                record["active"] = False
                state["active_revisions"].pop(str(record.get("segment_id") or ""), None)
            return record

        return cls._mutate(project_name, change)

    @classmethod
    def get_revision(cls, project_name: str, revision_id: str) -> dict[str, Any] | None:
        record = cls.load(project_name)["revisions"].get(str(revision_id))
        return copy.deepcopy(record) if isinstance(record, dict) else None

    @classmethod
    def get_active_revision(
        cls, project_name: str, segment_id: str
    ) -> dict[str, Any] | None:
        state = cls.load(project_name)
        revision_id = state["active_revisions"].get(str(segment_id))
        record = state["revisions"].get(revision_id)
        return copy.deepcopy(record) if isinstance(record, dict) else None

    @classmethod
    def list_revisions(
        cls, project_name: str, segment_id: str | None = None
    ) -> list[dict[str, Any]]:
        records = [
            copy.deepcopy(record)
            for record in cls.load(project_name)["revisions"].values()
            if isinstance(record, dict)
            and (segment_id is None or record.get("segment_id") == str(segment_id))
        ]
        return sorted(
            records,
            key=lambda item: (
                str(item.get("segment_id") or ""),
                int(item.get("audio_revision", 0) or 0),
            ),
        )

    @classmethod
    def save_technical_qa(
        cls, project_name: str, revision_id: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        payload = _json_safe(result)
        payload["revision_id"] = str(revision_id)
        payload.setdefault("checked_at", _now())

        def change(state: dict[str, Any]) -> dict[str, Any]:
            if revision_id not in state["revisions"]:
                raise KeyError(f"音频 revision 不存在: {revision_id}")
            state["technical_qa"][str(revision_id)] = payload
            return payload

        return cls._mutate(project_name, change)

    @classmethod
    def save_technical_qa_batch(
        cls,
        project_name: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist many technical-QA results in one cross-process mutation.

        ``quality_state.json`` is a whole-state snapshot.  Calling the
        single-result mutation once per segment would therefore repeatedly
        acquire the OS lock and rewrite the complete snapshot.  Batch callers
        prepare one result per analyzed revision and pay that cost only once.
        Results without a revision (for example an ``AUDIO_MISSING`` finding)
        are returned to the caller but cannot be indexed in ``technical_qa``
        and are consequently skipped here.
        """
        prepared: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, tuple) and len(result) == 2:
                revision_id, raw_result = result
                if not isinstance(raw_result, dict):
                    continue
                payload = _json_safe(raw_result)
                payload.setdefault("revision_id", str(revision_id or ""))
            elif isinstance(result, dict):
                payload = _json_safe(result)
            else:
                continue
            revision_id = str(payload.get("revision_id") or "").strip()
            if not revision_id:
                continue
            payload["revision_id"] = revision_id
            payload.setdefault("checked_at", _now())
            prepared.append(payload)

        def change(state: dict[str, Any]) -> list[dict[str, Any]]:
            saved: list[dict[str, Any]] = []
            for payload in prepared:
                revision_id = str(payload["revision_id"])
                if revision_id not in state["revisions"]:
                    continue
                state["technical_qa"][revision_id] = payload
                saved.append(payload)
            return saved

        return cls._mutate(project_name, change) if prepared else []

    @classmethod
    def save_human_review(
        cls, project_name: str, revision_id: str, review: dict[str, Any]
    ) -> dict[str, Any]:
        payload = _json_safe(review)
        payload["revision_id"] = str(revision_id)
        payload.setdefault("reviewed_at", _now())

        def change(state: dict[str, Any]) -> dict[str, Any]:
            if revision_id not in state["revisions"]:
                raise KeyError(f"音频 revision 不存在: {revision_id}")
            state["human_reviews"][str(revision_id)] = payload
            return payload

        return cls._mutate(project_name, change)

    @classmethod
    def save_human_reviews_batch(
        cls,
        project_name: str,
        reviews: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Persist many human-review decisions in one cross-process mutation."""
        prepared = [
            (str(revision_id), _json_safe(review))
            for revision_id, review in reviews
            if str(revision_id)
        ]

        def change(state: dict[str, Any]) -> list[dict[str, Any]]:
            saved: list[dict[str, Any]] = []
            for revision_id, review in prepared:
                if revision_id not in state["revisions"]:
                    continue
                payload = dict(review)
                payload["revision_id"] = revision_id
                payload.setdefault("reviewed_at", _now())
                state["human_reviews"][revision_id] = payload
                saved.append(payload)
            return saved

        return cls._mutate(project_name, change) if prepared else []

    @classmethod
    def create_history_record(
        cls,
        project_name: str,
        collection: str,
        prefix: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if collection not in {"repair_history", "export_jobs", "delivery_manifests"}:
            raise ValueError(f"未知历史集合: {collection}")

        def change(state: dict[str, Any]) -> dict[str, Any]:
            identifier = f"{prefix}_{uuid.uuid4().hex[:20]}"
            record = {
                **_json_safe(payload),
                f"{prefix}_id": identifier,
                "created_at": _now(),
                "updated_at": _now(),
            }
            state[collection][identifier] = record
            return record

        return cls._mutate(project_name, change)

    @classmethod
    def update_history_record(
        cls,
        project_name: str,
        collection: str,
        identifier: str,
        **updates: Any,
    ) -> dict[str, Any]:
        if collection not in {"repair_history", "export_jobs", "delivery_manifests"}:
            raise ValueError(f"未知历史集合: {collection}")

        def change(state: dict[str, Any]) -> dict[str, Any]:
            record = state[collection].get(str(identifier))
            if not isinstance(record, dict):
                raise KeyError(f"历史记录不存在: {identifier}")
            record.update(_json_safe(updates))
            record["updated_at"] = _now()
            return record

        return cls._mutate(project_name, change)

    @classmethod
    def get_history_record(
        cls, project_name: str, collection: str, identifier: str
    ) -> dict[str, Any] | None:
        record = cls.load(project_name).get(collection, {}).get(str(identifier))
        return copy.deepcopy(record) if isinstance(record, dict) else None

    @classmethod
    def list_history(
        cls, project_name: str, collection: str
    ) -> list[dict[str, Any]]:
        records = [
            copy.deepcopy(record)
            for record in cls.load(project_name).get(collection, {}).values()
            if isinstance(record, dict)
        ]
        return sorted(
            records,
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )

    @classmethod
    def find_history_by_field(
        cls,
        project_name: str,
        collection: str,
        field: str,
        value: Any,
    ) -> dict[str, Any] | None:
        for record in cls.list_history(project_name, collection):
            if record.get(field) == value:
                return record
        return None


__all__ = ["QualityRepository", "SCHEMA_VERSION"]
