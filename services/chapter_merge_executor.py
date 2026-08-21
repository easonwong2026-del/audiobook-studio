"""Transactional Chapter → Book merge execution.

The executor is deliberately downstream of :mod:`chapter_merge_planner`.
It never invents a second eligibility model: it consumes a ``MergePlan``,
rechecks that exact plan token, applies only explicitly supported resolutions,
and performs the target mutation through a staged shadow copy with a
recoverable target backup and a durable transaction journal.

The source Chapter is never mutated.  Segment-ID collisions, persisted QA /
revision / repair history, unsupported Voice Cast resolutions, active projects,
and every other domain that cannot be transformed without guessing remain
structured blocking failures.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from lib import chapter_identity, config, project_paths, script_loader
from repositories._atomic import atomic_write
from repositories.project_repo import ProjectRepository
from repositories.project_storage_repo import ProjectStorageRepository
from services.chapter_merge_planner import (
    NO_AUDIO,
    NO_COLLISION,
    PLANNING_ALLOWED,
    ChapterMergePlanner,
    MergeConflict,
    MergePlan,
    merge_history_path,
    read_merge_history,
)
from services.project_backup import ProjectBackupService


class MergeExecutionStage(str, Enum):
    VALIDATING = "VALIDATING"
    BACKING_UP = "BACKING_UP"
    STAGING = "STAGING"
    COMMITTING = "COMMITTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    BACKUP_FAILED = "BACKUP_FAILED"
    STAGE_FAILED = "STAGE_FAILED"
    COMMIT_FAILED = "COMMIT_FAILED"
    VERIFY_FAILED = "VERIFY_FAILED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


MERGE_FAILED_ROLLED_BACK = "MERGE_FAILED_ROLLED_BACK"
MERGE_FAILED_ROLLBACK_FAILED = "MERGE_FAILED_ROLLBACK_FAILED"
CONFIRMATION_SCHEMA_VERSION = "chapter-merge-confirmation-v1"
CHAPTER_SELECTION_POLICY = "CHAPTER"
WHOLE_BOOK_SELECTION_POLICY = "WHOLE_BOOK_ASSEMBLY"

TRANSACTION_JOURNAL_ACTIVE_STAGES = frozenset(
    {
        MergeExecutionStage.VALIDATING.value,
        MergeExecutionStage.BACKING_UP.value,
        MergeExecutionStage.STAGING.value,
        MergeExecutionStage.COMMITTING.value,
        MergeExecutionStage.VERIFYING.value,
        MergeExecutionStage.ROLLING_BACK.value,
    }
)
TRANSACTION_JOURNAL_TERMINAL_STAGES = frozenset(
    {
        MergeExecutionStage.SUCCEEDED.value,
        MergeExecutionStage.VALIDATION_FAILED.value,
        MergeExecutionStage.BACKUP_FAILED.value,
        MergeExecutionStage.STAGE_FAILED.value,
        MergeExecutionStage.COMMIT_FAILED.value,
        MergeExecutionStage.ROLLED_BACK.value,
        MergeExecutionStage.ROLLBACK_FAILED.value,
    }
)


class MergeExecutionError(RuntimeError):
    """Structured service error used for validation and transaction failures."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str = MergeExecutionStage.VALIDATING.value,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.stage = str(stage)
        self.details = dict(details or {})


@dataclass(frozen=True)
class MergeConfirmation:
    """Fresh, identity-bound confirmation produced before execution."""

    schema_version: str
    confirmation_token: str
    plan_token: str
    source_project_id: str
    target_project_id: str
    source_project_name: str
    target_project_name: str
    resolution_fingerprint: str
    selection_revision: int
    opened_project: str
    data_root: str
    selection_policy: str = CHAPTER_SELECTION_POLICY
    assembly_token: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MergeExecutionResult:
    """Structured execution outcome; Gradio messages are only a rendering."""

    success: bool
    status: str
    stage: str
    transaction_id: str
    source_project_id: str = ""
    target_project_id: str = ""
    source_project_name: str = ""
    target_project_name: str = ""
    backup_path: str = ""
    journal_path: str = ""
    snapshot_path: str = ""
    error_code: str = ""
    error: str = ""
    failure_stage: str = ""
    rollback_status: str = "NOT_STARTED"
    imported_segment_count: int = 0
    imported_audio_count: int = 0
    warnings: tuple[str, ...] = ()
    conflicts: tuple[Mapping[str, Any], ...] = ()
    planned_mutations: tuple[str, ...] = ()
    completed_mutations: tuple[str, ...] = ()
    integrity: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        payload["conflicts"] = [dict(item) for item in self.conflicts]
        payload["planned_mutations"] = list(self.planned_mutations)
        payload["completed_mutations"] = list(self.completed_mutations)
        payload["integrity"] = dict(self.integrity)
        return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def _safe_component(value: Any, fallback: str = "merge") -> str:
    text = str(value or "").strip()
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in text)
    safe = safe.strip("._")
    return safe or fallback


def _audio_file_matches(name: str, segment_id: str) -> bool:
    stem, extension = os.path.splitext(str(name))
    return extension.lower() == ".wav" and (
        stem == str(segment_id) or stem.startswith(f"{segment_id}_")
    )


def _read_json(path: str, *, required: bool = True) -> Any:
    if not os.path.isfile(path):
        if required:
            raise MergeExecutionError("PERSISTED_FILE_MISSING", f"持久化文件缺失：{path}")
        return None
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise MergeExecutionError("PERSISTED_FILE_UNREADABLE", f"持久化文件无法读取：{path}: {exc}") from exc


def _write_json(path: str, value: Any) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_write(path, value)


def _tree_files(root: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not os.path.isdir(root):
        return result
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            path = os.path.join(current, name)
            if os.path.islink(path):
                raise MergeExecutionError("PROJECT_SYMLINK_UNSUPPORTED", f"项目树包含不支持的符号链接：{path}")
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            result[relative] = path
    return result


def _assert_no_links(root: str) -> None:
    if os.path.islink(root):
        raise MergeExecutionError("PROJECT_SYMLINK_UNSUPPORTED", f"项目目录是符号链接：{root}")
    if not os.path.isdir(root):
        raise MergeExecutionError("PROJECT_NOT_FOUND", f"项目目录不存在：{root}")
    _tree_files(root)
    for current, dirs, _files in os.walk(root, followlinks=False):
        for name in dirs:
            path = os.path.join(current, name)
            if os.path.islink(path):
                raise MergeExecutionError("PROJECT_SYMLINK_UNSUPPORTED", f"项目树包含符号链接目录：{path}")


def _copy_tree(
    source: str,
    destination: str,
    fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None = None,
) -> None:
    """Copy a project shadow tree while giving tests a file-level fault hook."""
    _assert_no_links(source)
    os.makedirs(destination, exist_ok=False)
    for current, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(dirs)
        relative_dir = os.path.relpath(current, source)
        if relative_dir == ".":
            relative_dir = ""
        destination_dir = os.path.join(destination, relative_dir)
        os.makedirs(destination_dir, exist_ok=True)
        for name in sorted(files):
            source_path = os.path.join(current, name)
            relative = os.path.relpath(source_path, source).replace(os.sep, "/")
            _maybe_inject(fault_injection, "stage_copy", relative)
            shutil.copy2(source_path, os.path.join(destination, relative))


def _atomic_copy_file(source: str, destination: str) -> None:
    """Copy one staged file into place through temp-file + replace."""
    if not os.path.isfile(source):
        raise OSError(f"staged file missing: {source}")
    parent = os.path.dirname(destination)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temporary = f"{destination}.merge-tmp-{uuid.uuid4().hex}"
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    except Exception:
        try:
            if os.path.lexists(temporary):
                os.remove(temporary)
        except OSError:
            pass
        raise


def _restore_tree(snapshot: str, target: str) -> None:
    """Restore a pre-merge shadow tree without touching the external backup."""
    _assert_no_links(snapshot)
    _assert_no_links(target)
    snapshot_files = _tree_files(snapshot)
    target_files = _tree_files(target)
    for relative in sorted(set(target_files) - set(snapshot_files)):
        os.remove(target_files[relative])

    snapshot_dirs = {""}
    for current, dirs, _files in os.walk(snapshot, followlinks=False):
        for name in dirs:
            relative = os.path.relpath(os.path.join(current, name), snapshot).replace(os.sep, "/")
            snapshot_dirs.add(relative)
    target_dirs: list[str] = []
    for current, dirs, _files in os.walk(target, topdown=False, followlinks=False):
        for name in dirs:
            path = os.path.join(current, name)
            relative = os.path.relpath(path, target).replace(os.sep, "/")
            if relative not in snapshot_dirs:
                target_dirs.append(path)
    for path in sorted(target_dirs, key=lambda item: item.count(os.sep), reverse=True):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)

    for relative in sorted(snapshot_dirs, key=lambda item: item.count("/")):
        os.makedirs(os.path.join(target, *relative.split("/")) if relative else target, exist_ok=True)

    for relative, source in sorted(snapshot_files.items()):
        _atomic_copy_file(source, os.path.join(target, *relative.split("/")))


def _journal_root() -> str:
    return os.path.join(os.path.realpath(config.get_data_dir()), "runtime", "merge_transactions")


def _journal_path(transaction_id: str) -> str:
    return os.path.join(_journal_root(), f"{transaction_id}.json")


def _write_journal(path: str, payload: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_json(path, dict(payload))


def read_transaction_journals() -> tuple[dict[str, Any], ...]:
    """Read durable merge journals for restart-safe operational diagnostics.

    This is deliberately read-only.  It exposes the executor's existing
    journal records to the operations layer without adding a second mutation
    path or changing transaction recovery semantics.
    """
    root = _journal_root()
    if not os.path.isdir(root):
        return ()
    records: list[dict[str, Any]] = []
    for filename in sorted(os.listdir(root)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(root, filename)
        if not os.path.isfile(path):
            continue
        transaction_id = filename[:-5]
        try:
            with open(path, encoding="utf-8") as file:
                value = json.load(file)
            if not isinstance(value, dict):
                raise TypeError("journal top-level must be an object")
            record = dict(value)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            record = {
                "schema_version": "chapter-merge-transaction-v1",
                "transaction_id": transaction_id,
                "stage": "JOURNAL_UNREADABLE",
                "journal_error": f"{type(exc).__name__}: {exc}",
            }
        record.setdefault("transaction_id", transaction_id)
        record["journal_path"] = path
        try:
            record["_journal_mtime_ns"] = int(os.stat(path).st_mtime_ns)
        except OSError:
            record["_journal_mtime_ns"] = 0
        records.append(record)
    return tuple(
        sorted(
            records,
            key=lambda item: (
                str(item.get("updated_at") or item.get("started_at") or ""),
                str(item.get("transaction_id") or ""),
            ),
        )
    )


def _maybe_inject(
    fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None,
    event: str,
    relative_path: str = "",
) -> None:
    if fault_injection is None:
        return
    if callable(fault_injection):
        fault_injection(event, relative_path)
        return
    value = fault_injection.get(event)
    if value is True or value == relative_path or value == "*":
        raise OSError(f"fault injection: {event} {relative_path}".strip())


def _resolution_document(resolutions: Any) -> dict[str, Any]:
    raw = resolutions if isinstance(resolutions, Mapping) else {}
    voice = raw.get("voice_conflicts", raw.get("voice", {}))
    if not isinstance(voice, Mapping):
        voice = {
            key: value
            for key, value in raw.items()
            if key not in {"schema_version", "voice_conflicts", "voice"}
        }
    normalized = {
        "voice_conflicts": {
            str(key): str(value).strip().upper()
            for key, value in sorted(voice.items(), key=lambda item: str(item[0]))
            if str(key).strip() and str(value).strip()
        }
    }
    return normalized


def _resolution_fingerprint(resolutions: Any) -> str:
    return _digest(_resolution_document(resolutions))


def _data_root() -> str:
    return os.path.realpath(config.get_data_dir())


def _session_values(session: Any) -> tuple[str, str, int]:
    selected = str(getattr(session, "selected_project", "") or "") if session is not None else ""
    opened = str(getattr(session, "project", "") or "") if session is not None else ""
    try:
        revision = int(getattr(session, "selection_revision", 0) or 0) if session is not None else 0
    except (TypeError, ValueError):
        revision = 0
    return selected, opened, revision


def _coerce_confirmation(value: Any) -> MergeConfirmation | None:
    if isinstance(value, MergeConfirmation):
        return value
    if not isinstance(value, Mapping):
        return None
    fields = {key: value.get(key) for key in MergeConfirmation.__dataclass_fields__}
    try:
        fields["selection_revision"] = int(fields.get("selection_revision") or 0)
    except (TypeError, ValueError):
        fields["selection_revision"] = -1
    if any(fields.get(key) in (None, "") for key in ("confirmation_token", "plan_token", "source_project_id", "target_project_id")):
        return None
    fields["selection_policy"] = str(
        fields.get("selection_policy") or CHAPTER_SELECTION_POLICY
    )
    fields["assembly_token"] = str(fields.get("assembly_token") or "")
    try:
        return MergeConfirmation(**fields)
    except TypeError:
        return None


def _conflict_payload(conflict: MergeConflict | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(conflict, MergeConflict):
        return conflict.as_dict()
    return dict(conflict)


def _role_resolution(plan: MergePlan, resolutions: Any) -> tuple[dict[str, str], list[dict[str, Any]]]:
    document = _resolution_document(resolutions)
    choices = document["voice_conflicts"]
    unresolved: list[dict[str, Any]] = []
    for row in plan.voice_compatibility.roles:
        role_key = str(row.get("role_key") or "")
        status = str(row.get("status") or "")
        choice = str(choices.get(role_key) or "").upper()
        if status == "SOURCE_ONLY" and choice != "ADD_SOURCE_ROLE":
            unresolved.append({"code": "VOICE_RESOLUTION_REQUIRED", "role_key": role_key, "allowed": ["ADD_SOURCE_ROLE"]})
        elif status == "CONFLICT" and choice != "KEEP_TARGET":
            unresolved.append({"code": "VOICE_RESOLUTION_REQUIRED", "role_key": role_key, "allowed": ["KEEP_TARGET"]})
        elif status not in {"SOURCE_ONLY", "CONFLICT"} and choice:
            unresolved.append({"code": "VOICE_RESOLUTION_UNEXPECTED", "role_key": role_key, "status": status, "choice": choice})
    return choices, unresolved


def _resolve_project_path(project_dir: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return os.path.realpath(raw)
    try:
        return os.path.realpath(project_paths.resolve_relative(project_dir, raw))
    except ValueError:
        return os.path.realpath(os.path.join(project_dir, raw))


def _role_records(document: Any) -> dict[str, dict[str, Any]]:
    raw = document.get("roles") if isinstance(document, dict) and "roles" in document else document
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            item = dict(value) if isinstance(value, dict) else {}
            item.setdefault("role_id", str(key))
            result[str(item.get("role_id") or key)] = item
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, dict):
                continue
            key = str(value.get("role_id") or "").strip()
            if key:
                result[key] = dict(value)
    return result


def _role_document(document: Any, project_name: str, roles: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document) if isinstance(document, dict) else {}
    result.setdefault("version", "1.0")
    result["project_name"] = project_name
    result["roles"] = {
        str(key): copy.deepcopy(value) if isinstance(value, dict) else {}
        for key, value in sorted(roles.items(), key=lambda item: str(item[0]))
    }
    return result


def _raw_role(document: Any, role_key: str, display_name: str) -> dict[str, Any]:
    records = _role_records(document)
    if role_key in records:
        return records[role_key]
    for record in records.values():
        if str(record.get("name") or record.get("role_name") or "") == display_name:
            return record
    return {}


def _source_role_asset(
    source_dir: str,
    role_key: str,
    display_name: str,
    source_bindings: Mapping[str, Any],
    source_cast: Any,
) -> dict[str, Any]:
    """Resolve a project-owned source voice snapshot; never use global paths."""
    cast_record = _raw_role(source_cast, role_key, display_name)
    role_bindings = source_bindings.get("role_bindings", {}) if isinstance(source_bindings, Mapping) else {}
    binding_record = role_bindings.get(role_key) if isinstance(role_bindings, Mapping) else None
    binding_record = binding_record if isinstance(binding_record, Mapping) else {}
    legacy = source_bindings.get("bindings", {}) if isinstance(source_bindings, Mapping) else {}
    legacy_value = legacy.get(display_name) if isinstance(legacy, Mapping) else None
    candidate = (
        cast_record.get("project_voice_path")
        or binding_record.get("project_voice_path")
        or legacy_value
    )
    path = _resolve_project_path(source_dir, candidate)
    if not path or not _inside(path, source_dir) or not os.path.isfile(path):
        raise MergeExecutionError(
            "SOURCE_ROLE_ASSET_UNSUPPORTED",
            f"来源角色「{display_name}」没有可验证的项目内 Voice Cast snapshot",
            details={"role_key": role_key, "candidate": str(candidate or "")},
        )
    expected_sha = str(
        cast_record.get("voice_sha256")
        or binding_record.get("voice_sha256")
        or ""
    ).strip()
    actual_sha = _hash_file(path)
    if expected_sha and expected_sha != actual_sha:
        raise MergeExecutionError(
            "SOURCE_ROLE_ASSET_CHANGED",
            f"来源角色「{display_name}」的 Voice Cast snapshot 校验失败",
            details={"role_key": role_key, "expected": expected_sha, "actual": actual_sha},
        )
    return {
        "path": path,
        "sha256": actual_sha,
        "voice_asset_id": str(
            cast_record.get("voice_asset_id")
            or binding_record.get("voice_asset_id")
            or ""
        ).strip(),
        "role_record": cast_record or binding_record,
    }


class ChapterMergeExecutor:
    """Service-owned transactional executor consuming the C.1 ``MergePlan``."""

    _RESOLVABLE_VOICE_CODES = frozenset({"SOURCE_ONLY_ROLE", "VOICE_BINDING_CONFLICT"})

    @staticmethod
    def _confirmation_scope(
        plan: MergePlan,
        resolutions: Any,
        session: Any = None,
        *,
        selection_policy: str = CHAPTER_SELECTION_POLICY,
        assembly_token: str = "",
    ) -> dict[str, Any]:
        selected, opened, selection_revision = _session_values(session)
        source = plan.source_project
        target = plan.target_project
        return {
            "schema_version": CONFIRMATION_SCHEMA_VERSION,
            "plan_token": plan.plan_token,
            "source_project_id": source.project_id if source else "",
            "target_project_id": target.project_id if target else "",
            "source_project_name": source.project_name if source else "",
            "target_project_name": target.project_name if target else "",
            "resolution_fingerprint": _resolution_fingerprint(resolutions),
            "selection_revision": selection_revision,
            "selected_project": selected,
            "opened_project": opened,
            "data_root": _data_root(),
            "selection_policy": str(selection_policy or CHAPTER_SELECTION_POLICY),
            "assembly_token": str(assembly_token or ""),
        }

    @classmethod
    def _validate_plan(
        cls,
        plan: MergePlan,
        resolutions: Any,
        session: Any = None,
        *,
        require_current: bool = True,
        selection_policy: str = CHAPTER_SELECTION_POLICY,
        assembly_token: str = "",
    ) -> dict[str, str]:
        if not isinstance(plan, MergePlan):
            raise MergeExecutionError("PLAN_REQUIRED", "executor 必须接收现有 MergePlan")
        if plan.planning_status != PLANNING_ALLOWED:
            raise MergeExecutionError("PLAN_NOT_ALLOWED", "该 MergePlan 未通过基础规划校验")
        if not plan.source_project or not plan.target_project:
            raise MergeExecutionError("PLAN_IDENTITY_MISSING", "MergePlan 缺少 source/target identity")
        if not plan.source_project.project_id or not plan.target_project.project_id:
            raise MergeExecutionError("PROJECT_ID_REQUIRED", "source/target 必须具备稳定 project_id")
        if plan.segment_remap.policy != NO_COLLISION:
            raise MergeExecutionError(
                "SEGMENT_ID_COLLISION_UNSUPPORTED",
                "当前 executor 不猜测 segment ID remap；存在 collision 的计划保持 BLOCKED",
                details={"policy": plan.segment_remap.policy, "collisions": list(plan.segment_remap.collisions)},
            )
        if require_current and not ChapterMergePlanner.is_plan_current(plan):
            raise MergeExecutionError("STALE_PLAN", "MergePlan 已过期，请重新 Analyze")

        choices, unresolved = _role_resolution(plan, resolutions)
        if unresolved:
            raise MergeExecutionError(
                "VOICE_RESOLUTION_REQUIRED",
                "Voice Cast 冲突必须先提交显式 resolution choice",
                details={"unresolved": unresolved},
            )
        for conflict in plan.conflicts:
            if not conflict.blocking:
                continue
            if conflict.code in cls._RESOLVABLE_VOICE_CODES:
                continue
            raise MergeExecutionError(
                "PLAN_BLOCKED",
                f"MergePlan 存在未解决 blocking conflict：{conflict.code}",
                details={"conflict": conflict.as_dict()},
            )

        source_dir = ProjectRepository.get_project_dir(plan.source_project.project_name)
        target_dir = ProjectRepository.get_project_dir(plan.target_project.project_name)
        for name, directory, expected_id, expected_kind in (
            ("source", source_dir, plan.source_project.project_id, "chapter"),
            ("target", target_dir, plan.target_project.project_id, "book"),
        ):
            meta_path = project_paths.project_file(directory, "project_meta")
            meta = _read_json(meta_path)
            actual_id = str(meta.get("project_id") or "").strip() if isinstance(meta, dict) else ""
            actual_kind = str(meta.get("project_kind") or "book").strip().lower() if isinstance(meta, dict) else ""
            if actual_id != expected_id:
                raise MergeExecutionError("PROJECT_ID_CHANGED", f"{name} project_id 已变化")
            if actual_kind != expected_kind:
                raise MergeExecutionError("PROJECT_KIND_CHANGED", f"{name} project_kind 已变化")
            if name == "target" and str(meta.get("parent_project_id") or "").strip():
                raise MergeExecutionError("TARGET_RELATION_UNSAFE", "目标 Book 仍挂在其他父项目下")
        if plan.source_inventory.audio.get("coverage") == NO_AUDIO:
            raise MergeExecutionError("SOURCE_AUDIO_MISSING", "来源没有任何可迁移分段音频")

        selected, opened, _selection_revision = _session_values(session)
        if selection_policy == WHOLE_BOOK_SELECTION_POLICY:
            if not str(assembly_token or "").strip():
                raise MergeExecutionError(
                    "ASSEMBLY_TOKEN_REQUIRED",
                    "Whole-book execution 必须绑定 assembly token",
                )
            if session is not None and selected != plan.target_project.project_name:
                raise MergeExecutionError(
                    "ASSEMBLY_TARGET_SELECTION_CHANGED",
                    "当前 selected Book 已变化",
                )
        elif session is not None and selected != plan.source_project.project_name:
            raise MergeExecutionError("SOURCE_SELECTION_CHANGED", "当前 selected Chapter 已变化")
        if opened in {plan.source_project.project_name, plan.target_project.project_name}:
            raise MergeExecutionError(
                "OPENED_PROJECT_BLOCKED",
                "source/target 当前处于 opened production session，拒绝在 live session 后台改盘",
                details={"opened_project": opened},
            )
        source_bindings = _read_json(project_paths.project_file(source_dir, "voice_bindings"), required=False) or {}
        source_cast = _read_json(project_paths.project_file(source_dir, "voice_cast"), required=False) or {}
        for row in plan.voice_compatibility.roles:
            if str(row.get("status") or "") == "SOURCE_ONLY":
                _source_role_asset(
                    source_dir,
                    str(row.get("role_key") or ""),
                    str(row.get("display_name") or row.get("role_key") or ""),
                    source_bindings if isinstance(source_bindings, Mapping) else {},
                    source_cast,
                )
        return choices

    @classmethod
    def prepare_confirmation(
        cls,
        plan: MergePlan,
        resolutions: Any = None,
        *,
        session: Any = None,
        selection_policy: str = CHAPTER_SELECTION_POLICY,
        assembly_token: str = "",
    ) -> MergeConfirmation:
        cls._validate_plan(
            plan,
            resolutions,
            session,
            require_current=True,
            selection_policy=selection_policy,
            assembly_token=assembly_token,
        )
        scope = cls._confirmation_scope(
            plan,
            resolutions,
            session,
            selection_policy=selection_policy,
            assembly_token=assembly_token,
        )
        token = _digest(scope)
        return MergeConfirmation(
            schema_version=CONFIRMATION_SCHEMA_VERSION,
            confirmation_token=token,
            plan_token=plan.plan_token,
            source_project_id=str(scope["source_project_id"]),
            target_project_id=str(scope["target_project_id"]),
            source_project_name=str(scope["source_project_name"]),
            target_project_name=str(scope["target_project_name"]),
            resolution_fingerprint=str(scope["resolution_fingerprint"]),
            selection_revision=int(scope["selection_revision"]),
            opened_project=str(scope["opened_project"]),
            data_root=str(scope["data_root"]),
            selection_policy=str(scope["selection_policy"]),
            assembly_token=str(scope["assembly_token"]),
        )

    @classmethod
    def _validate_confirmation(
        cls,
        plan: MergePlan,
        resolutions: Any,
        confirmation: Any,
        session: Any = None,
        *,
        selection_policy: str = CHAPTER_SELECTION_POLICY,
        assembly_token: str = "",
    ) -> MergeConfirmation:
        current = _coerce_confirmation(confirmation)
        if current is None:
            raise MergeExecutionError("CONFIRMATION_REQUIRED", "必须先生成新的 execution confirmation")
        expected = cls.prepare_confirmation(
            plan,
            resolutions,
            session=session,
            selection_policy=selection_policy,
            assembly_token=assembly_token,
        )
        if current != expected:
            raise MergeExecutionError(
                "STALE_CONFIRMATION",
                "确认态与 plan / resolution / selection / data-root 不一致",
                details={"expected": expected.as_dict(), "received": current.as_dict()},
            )
        return current

    @staticmethod
    def _project_paths(project_dir: str) -> dict[str, str]:
        """Resolve all executor-owned paths through the Storage Layout resolver."""
        keys = (
            "project_meta",
            "structured_script",
            "voice_bindings",
            "character_roster",
            "voice_cast",
            "synthesis_overrides",
            "synthesis_selections",
            "quality_state",
            "task_db",
            "segment_status_journal",
        )
        return {
            key: project_paths.project_file(project_dir, key)
            for key in keys
        }

    @staticmethod
    def _read_stage_json(stage_dir: str, key: str, *, required: bool = False) -> Any:
        path = project_paths.project_file(stage_dir, key)
        return _read_json(path, required=required)

    @staticmethod
    def _write_stage_json(stage_dir: str, key: str, value: Any) -> str:
        path = project_paths.project_file(stage_dir, key, create=True)
        _write_json(path, value)
        return path

    @staticmethod
    def _script_chapters(script: Any) -> list[dict[str, Any]]:
        if not isinstance(script, dict):
            return []
        normalized = script_loader.canonicalize_collections(script)
        chapters = normalized.get("chapters")
        return [item for item in chapters if isinstance(item, dict)] if isinstance(chapters, list) else []

    @staticmethod
    def _segment_ids(script: Any) -> list[str]:
        result: list[str] = []
        for chapter in ChapterMergeExecutor._script_chapters(script):
            segments = chapter.get("segments")
            if not isinstance(segments, list):
                continue
            for segment in segments:
                if isinstance(segment, dict):
                    value = str(segment.get("id") or "").strip()
                    if value:
                        result.append(value)
        return result

    @staticmethod
    def _chapter_ids(script: Any) -> list[str]:
        return [
            str(chapter.get("id") or "").strip()
            for chapter in ChapterMergeExecutor._script_chapters(script)
            if str(chapter.get("id") or "").strip()
        ]

    @staticmethod
    def _merge_script(
        source_script: Any,
        target_script: Any,
        placement: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int]:
        source = script_loader.canonicalize_collections(source_script)
        target = script_loader.canonicalize_collections(target_script)
        source_chapters = ChapterMergeExecutor._script_chapters(source)
        target_chapters = ChapterMergeExecutor._script_chapters(target)
        if len(source_chapters) != 1:
            raise MergeExecutionError(
                "SOURCE_SCRIPT_STRUCTURE_UNSAFE",
                "来源 Chapter 必须恰好包含一章",
                stage=MergeExecutionStage.STAGING.value,
            )
        source_ids = ChapterMergeExecutor._segment_ids(source)
        target_ids = ChapterMergeExecutor._segment_ids(target)
        if len(source_ids) != len(set(source_ids)) or len(target_ids) != len(set(target_ids)):
            raise MergeExecutionError(
                "DUPLICATE_SEGMENT_ID",
                "来源或目标剧本包含重复 segment ID",
                stage=MergeExecutionStage.STAGING.value,
            )
        collisions = sorted(set(source_ids) & set(target_ids))
        if collisions:
            raise MergeExecutionError(
                "SEGMENT_ID_COLLISION_UNSUPPORTED",
                "发现未实现安全 remap 的 segment ID collision",
                stage=MergeExecutionStage.STAGING.value,
                details={"segment_ids": collisions},
            )
        source_chapter_id = str(source_chapters[0].get("id") or "").strip()
        target_chapter_ids = set(ChapterMergeExecutor._chapter_ids(target))
        if source_chapter_id and source_chapter_id in target_chapter_ids:
            raise MergeExecutionError(
                "CHAPTER_ID_COLLISION",
                "来源 chapter ID 与目标 chapter ID 冲突，无法在不猜测的情况下合并",
                stage=MergeExecutionStage.STAGING.value,
                details={"chapter_id": source_chapter_id},
            )

        merged = copy.deepcopy(target)
        merged["chapters"] = copy.deepcopy(target_chapters)
        source_chapter = copy.deepcopy(source_chapters[0])
        index_value = placement.get("target_index", len(target_chapters))
        try:
            target_index = int(index_value)
        except (TypeError, ValueError) as exc:
            raise MergeExecutionError(
                "PLACEMENT_INVALID",
                "规划器没有给出有效的目标章节插入位置",
                stage=MergeExecutionStage.STAGING.value,
            ) from exc
        if target_index < 0 or target_index > len(target_chapters):
            raise MergeExecutionError(
                "PLACEMENT_INVALID",
                "目标章节插入位置超出当前 Book 结构",
                stage=MergeExecutionStage.STAGING.value,
                details={"target_index": target_index, "target_chapter_count": len(target_chapters)},
            )
        merged["chapters"].insert(target_index, source_chapter)

        target_voices = merged.get("voices")
        if not isinstance(target_voices, dict):
            target_voices = {}
        source_voices = source.get("voices") if isinstance(source.get("voices"), dict) else {}
        for role_name, role_value in source_voices.items():
            if role_name not in target_voices:
                target_voices[role_name] = copy.deepcopy(role_value)
        merged["voices"] = target_voices
        merged = chapter_identity.normalize_script_for_project(merged)
        return merged, len(source_ids)

    @staticmethod
    def _load_meta_for_stage(stage_dir: str) -> dict[str, Any]:
        value = ChapterMergeExecutor._read_stage_json(stage_dir, "project_meta", required=True)
        if not isinstance(value, dict):
            raise MergeExecutionError(
                "PROJECT_METADATA_UNREADABLE",
                "目标 project metadata 不是对象",
                stage=MergeExecutionStage.STAGING.value,
            )
        return value

    @staticmethod
    def _source_statuses(source_meta: Mapping[str, Any]) -> dict[str, Any]:
        raw = source_meta.get("segments_status")
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _source_audio_files(
        source_dir: str,
        record: Mapping[str, Any],
    ) -> list[tuple[str, str]]:
        """Return only files explicitly owned by one planner segment record."""
        segment_id = str(record.get("segment_id") or "").strip()
        audio = record.get("audio") if isinstance(record.get("audio"), Mapping) else {}
        raw_files = audio.get("files") if isinstance(audio, Mapping) else []
        result: list[tuple[str, str]] = []
        if not isinstance(raw_files, list):
            return result
        for item in raw_files:
            if not isinstance(item, Mapping):
                continue
            relative = str(item.get("path") or "").strip()
            if not relative:
                continue
            try:
                absolute = project_paths.resolve_relative(source_dir, relative)
            except (ValueError, OSError):
                continue
            basename = os.path.basename(absolute)
            if not _audio_file_matches(basename, segment_id):
                continue
            if not _inside(absolute, source_dir) or not os.path.isfile(absolute):
                continue
            expected_sha = str(item.get("sha256") or "").strip()
            actual_sha = _hash_file(absolute)
            if expected_sha and expected_sha != actual_sha:
                raise MergeExecutionError(
                    "SOURCE_AUDIO_CHANGED",
                    f"来源段落 {segment_id} 的音频在规划后发生变化",
                    stage=MergeExecutionStage.STAGING.value,
                    details={"path": relative, "expected": expected_sha, "actual": actual_sha},
                )
            result.append((relative, absolute))
        return result

    @staticmethod
    def _merge_statuses(
        target_meta: dict[str, Any],
        source_meta: Mapping[str, Any],
        source_records: tuple[Mapping[str, Any], ...],
    ) -> tuple[dict[str, Any], int]:
        target_statuses = target_meta.get("segments_status")
        statuses = dict(target_statuses) if isinstance(target_statuses, dict) else {}
        source_statuses = ChapterMergeExecutor._source_statuses(source_meta)
        imported_audio_count = 0
        for record in source_records:
            segment_id = str(record.get("segment_id") or "").strip()
            if not segment_id:
                continue
            audio = record.get("audio") if isinstance(record.get("audio"), Mapping) else {}
            has_audio = bool(audio.get("present")) if isinstance(audio, Mapping) else False
            if has_audio:
                imported_audio_count += int(audio.get("file_count") or 0)
            status = str(source_statuses.get(segment_id) or "pending").strip().lower()
            if status == "done" and not has_audio:
                status = "pending"
            if status not in {"done", "failed", "pending", "skipped"}:
                status = "pending"
            statuses[segment_id] = status
        return statuses, imported_audio_count

    @staticmethod
    def _finalize_meta(
        target_meta: dict[str, Any],
        merged_script: Mapping[str, Any],
        statuses: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = copy.deepcopy(target_meta)
        chapters = ChapterMergeExecutor._script_chapters(merged_script)
        segment_ids = ChapterMergeExecutor._segment_ids(merged_script)
        normalized_statuses = {
            str(segment_id): str(status)
            for segment_id, status in statuses.items()
            if str(segment_id) in set(segment_ids)
        }
        for segment_id in segment_ids:
            normalized_statuses.setdefault(segment_id, "pending")
        result["total_chapters"] = len(chapters)
        result["total_segments"] = len(segment_ids)
        result["completed_count"] = sum(value == "done" for value in normalized_statuses.values())
        result["failed_count"] = sum(value == "failed" for value in normalized_statuses.values())
        result["pending_count"] = len(segment_ids) - result["completed_count"] - result["failed_count"]
        result["segments_status"] = normalized_statuses
        result["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return result

    @staticmethod
    def _target_voice_roles(document: Any) -> dict[str, dict[str, Any]]:
        return _role_records(document)

    @staticmethod
    def _source_voice_record(
        source_dir: str,
        row: Mapping[str, Any],
        source_bindings: Mapping[str, Any],
        source_cast: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        role_key = str(row.get("role_key") or "")
        display_name = str(row.get("display_name") or role_key)
        cast_record = _raw_role(source_cast, role_key, display_name)
        role_bindings = source_bindings.get("role_bindings", {}) if isinstance(source_bindings, Mapping) else {}
        binding_record = role_bindings.get(role_key) if isinstance(role_bindings, Mapping) else None
        binding_record = dict(binding_record) if isinstance(binding_record, Mapping) else {}
        if not binding_record:
            legacy = source_bindings.get("bindings", {}) if isinstance(source_bindings, Mapping) else {}
            value = legacy.get(display_name) if isinstance(legacy, Mapping) else None
            if value:
                binding_record = {"project_voice_path": value}
        role_record = cast_record or binding_record
        asset = _source_role_asset(source_dir, role_key, display_name, source_bindings, source_cast)
        return copy.deepcopy(role_record), asset

    @staticmethod
    def _merge_voice_cast(
        source_dir: str,
        target_stage: str,
        source_bindings: Mapping[str, Any],
        source_roster: Any,
        source_cast: Any,
        target_bindings: Any,
        target_roster: Any,
        target_cast: Any,
        plan: MergePlan,
        choices: Mapping[str, str],
        fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None,
    ) -> list[str]:
        changed: list[str] = []
        source_only_rows = [
            row for row in plan.voice_compatibility.roles
            if str(row.get("status") or "") == "SOURCE_ONLY"
        ]
        if not source_only_rows:
            return changed

        target_roles = ChapterMergeExecutor._target_voice_roles(target_cast)
        roster_roles = _role_records(target_roster)
        cast_roles = _role_records(target_cast)
        target_role_bindings = dict(target_bindings.get("role_bindings", {})) if isinstance(target_bindings, Mapping) and isinstance(target_bindings.get("role_bindings"), Mapping) else {}
        target_legacy_bindings = dict(target_bindings.get("bindings", {})) if isinstance(target_bindings, Mapping) and isinstance(target_bindings.get("bindings"), Mapping) else {}
        target_bindings_document = copy.deepcopy(target_bindings) if isinstance(target_bindings, dict) else {}

        for row in source_only_rows:
            role_key = str(row.get("role_key") or "")
            display_name = str(row.get("display_name") or role_key)
            if choices.get(role_key) != "ADD_SOURCE_ROLE":
                raise MergeExecutionError(
                    "VOICE_RESOLUTION_REQUIRED",
                    f"来源角色「{display_name}」没有 ADD_SOURCE_ROLE resolution",
                    stage=MergeExecutionStage.STAGING.value,
                )
            if role_key in target_roles:
                raise MergeExecutionError(
                    "VOICE_ROLE_ID_COLLISION",
                    f"目标已存在同 role_id 的角色「{display_name}」",
                    stage=MergeExecutionStage.STAGING.value,
                )
            if any(
                str(record.get("name") or record.get("role_name") or "") == display_name
                for key, record in target_roles.items()
                if key != role_key
            ):
                raise MergeExecutionError(
                    "VOICE_DISPLAY_NAME_COLLISION",
                    f"目标已有同显示名但不同 role_id 的角色「{display_name}」，当前版本不猜测映射",
                    stage=MergeExecutionStage.STAGING.value,
                )
            role_record, asset = ChapterMergeExecutor._source_voice_record(
                source_dir, row, source_bindings, source_cast
            )
            source_path = str(asset["path"])
            extension = os.path.splitext(source_path)[1].lower() or ".wav"
            filename = f"merge_{_safe_component(role_key)}_{asset['sha256'][:12]}{extension}"
            target_voice_dir = project_paths.project_dir(target_stage, "project_voices", create=True)
            destination = os.path.join(target_voice_dir, filename)
            relative_voice = os.path.relpath(destination, target_stage).replace(os.sep, "/")
            if os.path.isfile(destination):
                if _hash_file(destination) != asset["sha256"]:
                    raise MergeExecutionError(
                        "TARGET_VOICE_ASSET_CONFLICT",
                        f"目标 Voice Cast snapshot 路径冲突：{relative_voice}",
                        stage=MergeExecutionStage.STAGING.value,
                    )
            else:
                _maybe_inject(fault_injection, "stage_copy", relative_voice)
                shutil.copy2(source_path, destination)
            role_record = role_record if isinstance(role_record, dict) else {}
            role_record.setdefault("role_id", role_key)
            role_record.setdefault("name", display_name)
            role_record["project_voice_path"] = relative_voice
            role_record["voice_sha256"] = asset["sha256"]
            if asset.get("voice_asset_id"):
                role_record.setdefault("voice_asset_id", asset["voice_asset_id"])
            cast_roles[role_key] = role_record
            source_roster_record = _raw_role(source_roster, role_key, display_name)
            roster_roles[role_key] = source_roster_record or {
                "role_id": role_key,
                "name": display_name,
            }
            source_binding = source_bindings.get("role_bindings", {}).get(role_key) if isinstance(source_bindings.get("role_bindings"), Mapping) else None
            binding = dict(source_binding) if isinstance(source_binding, Mapping) else {}
            binding["project_voice_path"] = relative_voice
            binding["voice_sha256"] = asset["sha256"]
            if asset.get("voice_asset_id"):
                binding.setdefault("voice_asset_id", asset["voice_asset_id"])
            target_role_bindings[role_key] = binding
            target_legacy_bindings[display_name] = relative_voice

        target_bindings_document["role_bindings"] = target_role_bindings
        target_bindings_document["bindings"] = target_legacy_bindings
        if cast_roles:
            cast_document = _role_document(target_cast, str(plan.target_project.project_name), cast_roles)
            ChapterMergeExecutor._write_stage_json(target_stage, "voice_cast", cast_document)
            changed.append("voice_cast")
        if roster_roles:
            roster_document = _role_document(target_roster, str(plan.target_project.project_name), roster_roles)
            ChapterMergeExecutor._write_stage_json(target_stage, "character_roster", roster_document)
            changed.append("character_roster")
        ChapterMergeExecutor._write_stage_json(target_stage, "voice_bindings", target_bindings_document)
        changed.append("voice_bindings")
        return changed

    @staticmethod
    def _validate_stage_tree(stage_dir: str) -> dict[str, Any]:
        """Validate the staged target without resolving it as a live project."""
        _assert_no_links(stage_dir)
        meta = ChapterMergeExecutor._read_stage_json(stage_dir, "project_meta", required=True)
        script_raw = ChapterMergeExecutor._read_stage_json(stage_dir, "structured_script", required=True)
        bindings = ChapterMergeExecutor._read_stage_json(stage_dir, "voice_bindings", required=True)
        if not isinstance(meta, dict) or not isinstance(bindings, dict):
            raise MergeExecutionError(
                "STAGED_CORE_INVALID",
                "staged target 核心文件不是对象",
                stage=MergeExecutionStage.VERIFYING.value,
            )
        script = script_loader.canonicalize_collections(script_raw)
        try:
            issues = script_loader.validate_script(script_loader.from_dict(script))
        except (TypeError, ValueError, KeyError) as exc:
            issues = [str(exc)]
        if issues:
            raise MergeExecutionError(
                "STAGED_SCRIPT_INVALID",
                "staged target 剧本校验失败",
                stage=MergeExecutionStage.VERIFYING.value,
                details={"issues": issues[:10]},
            )
        segment_ids = ChapterMergeExecutor._segment_ids(script)
        if len(segment_ids) != len(set(segment_ids)):
            raise MergeExecutionError(
                "STAGED_DUPLICATE_SEGMENT_ID",
                "staged target 存在重复 segment ID",
                stage=MergeExecutionStage.VERIFYING.value,
            )
        statuses = meta.get("segments_status")
        if not isinstance(statuses, dict) or set(map(str, statuses)) != set(segment_ids):
            raise MergeExecutionError(
                "STAGED_STATUS_MISMATCH",
                "staged target segments_status 与剧本不一致",
                stage=MergeExecutionStage.VERIFYING.value,
            )
        segment_dir = project_paths.project_dir(stage_dir, "segments")
        for segment_id, status in statuses.items():
            if str(status).lower() == "done" and (
                not os.path.isdir(segment_dir)
                or not any(
                    _audio_file_matches(name, str(segment_id))
                    for name in os.listdir(segment_dir)
                    if os.path.isfile(os.path.join(segment_dir, name))
                )
            ):
                raise MergeExecutionError(
                    "STAGED_DONE_AUDIO_MISSING",
                    f"staged target 段落 {segment_id} 标记 done 但缺少音频",
                    stage=MergeExecutionStage.VERIFYING.value,
                )
        return {
            "ok": True,
            "segment_count": len(segment_ids),
            "chapter_count": len(ChapterMergeExecutor._script_chapters(script)),
            "status_count": len(statuses),
        }

    @staticmethod
    def _merge_history_record(
        stage_dir: str,
        plan: MergePlan,
        transaction_id: str,
        backup_path: str,
        imported_segment_count: int,
        imported_audio_count: int,
    ) -> str:
        existing, error = read_merge_history(stage_dir)
        if error:
            raise MergeExecutionError(
                "MERGE_HISTORY_UNREADABLE",
                f"目标 merge history 无法读取：{error}",
                stage=MergeExecutionStage.STAGING.value,
            )
        source = plan.source_project
        target = plan.target_project
        record = {
            "schema_version": "chapter-merge-history-v1",
            "source_project_id": source.project_id if source else "",
            "source_project_name": source.project_name if source else "",
            "source_state_fingerprint": str(plan.token_scope.get("source_state_fingerprint") or ""),
            "target_project_id": target.project_id if target else "",
            "target_project_name": target.project_name if target else "",
            "target_premerge_state_fingerprint": str(plan.token_scope.get("target_state_fingerprint") or ""),
            "transaction_id": transaction_id,
            "plan_token": plan.plan_token,
            "segment_mapping": dict(plan.segment_remap.proposed_mapping),
            "imported_segment_count": imported_segment_count,
            "imported_audio_count": imported_audio_count,
            "backup_path": backup_path,
            "result_status": "SUCCEEDED",
            "created_at": _utc_now(),
            "source_unchanged": True,
            "export_policy": str(plan.export_policy.get("transfer_policy") or "EXCLUDED_FROM_EXECUTION_PLAN"),
        }
        path = merge_history_path(stage_dir)
        _write_json(path, [*existing, record])
        return path

    @staticmethod
    def _commit_event(relative: str) -> str:
        normalized = relative.replace("\\", "/")
        if normalized.endswith("structured_script.json"):
            return "script_commit"
        if normalized.endswith("project.json"):
            return "metadata_write"
        if normalized.endswith(("quality_state.json", "merge_history.json")):
            return "quality_commit"
        if normalized.startswith(("02_生成音频/分段音频/", "segments/")):
            return "audio_commit"
        return "commit"

    @classmethod
    def _commit_stage(
        cls,
        stage_dir: str,
        target_dir: str,
        fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None,
        journal: dict[str, Any],
        write_journal: Callable[[Mapping[str, Any]], None],
    ) -> tuple[list[str], bool]:
        _assert_no_links(stage_dir)
        _assert_no_links(target_dir)
        staged = _tree_files(stage_dir)
        target = _tree_files(target_dir)
        if _digest({key: _hash_file(path) for key, path in sorted(target.items())}) != journal.get("target_snapshot_digest"):
            raise MergeExecutionError(
                "TARGET_CHANGED_DURING_STAGE",
                "目标在 staging 期间发生变化，拒绝覆盖并要求重新 Analyze",
                stage=MergeExecutionStage.COMMIT_FAILED.value,
            )
        completed = list(journal.get("completed_mutations") or [])
        mutation_started = False
        for relative, source in sorted(staged.items()):
            event = cls._commit_event(relative)
            # A commit attempt is a mutation boundary even when the injected
            # failure fires before os.replace().  Rolling back that boundary
            # keeps all commit-fault tests on the same conservative path.
            mutation_started = True
            journal["mutation_started"] = True
            _maybe_inject(fault_injection, event, relative)
            if event == "quality_commit":
                _maybe_inject(fault_injection, "qa_revision_commit", relative)
            if event == "metadata_write":
                _maybe_inject(fault_injection, "metadata_commit", relative)
            _maybe_inject(fault_injection, "commit", relative) if event != "commit" else None
            _atomic_copy_file(source, os.path.join(target_dir, *relative.split("/")))
            completed.append(relative)
            journal["completed_mutations"] = completed
            write_journal(journal)
        for relative in sorted(set(target) - set(staged)):
            raise MergeExecutionError(
                "STAGED_TREE_INCOMPLETE",
                f"staged target 缺少原有文件：{relative}",
                stage=MergeExecutionStage.COMMIT_FAILED.value,
            )
        return completed, mutation_started

    @staticmethod
    def _integrity_report(target_name: str) -> dict[str, Any]:
        report = ProjectStorageRepository.check_project_integrity(target_name)
        if not isinstance(report, dict):
            return {"ok": False, "issues": [{"code": "INVALID_INTEGRITY_REPORT"}]}
        return report

    @classmethod
    def _rollback(
        cls,
        snapshot_dir: str,
        target_dir: str,
        target_name: str,
        fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None,
    ) -> dict[str, Any]:
        _maybe_inject(fault_injection, "rollback")
        _restore_tree(snapshot_dir, target_dir)
        report = cls._integrity_report(target_name)
        if not bool(report.get("ok")):
            raise MergeExecutionError(
                "ROLLBACK_INTEGRITY_FAILED",
                "rollback 完成但恢复后的目标完整性仍失败",
                stage=MergeExecutionStage.ROLLBACK_FAILED.value,
                details={"integrity": report},
            )
        return report

    @staticmethod
    def _base_result(
        plan: Any,
        transaction_id: str,
        journal_path: str,
        *,
        stage: str,
        status: str,
        error_code: str = "",
        error: str = "",
        failure_stage: str = "",
        backup_path: str = "",
        snapshot_path: str = "",
        rollback_status: str = "NOT_STARTED",
        integrity: Mapping[str, Any] | None = None,
        completed_mutations: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
        planned_mutations: tuple[str, ...] = (),
        imported_segment_count: int = 0,
        imported_audio_count: int = 0,
    ) -> MergeExecutionResult:
        source = getattr(plan, "source_project", None)
        target = getattr(plan, "target_project", None)
        conflicts = tuple(
            _conflict_payload(item)
            for item in (getattr(plan, "conflicts", ()) or ())
        )
        return MergeExecutionResult(
            success=status == "SUCCEEDED",
            status=status,
            stage=stage,
            transaction_id=transaction_id,
            source_project_id=str(getattr(source, "project_id", "") or ""),
            target_project_id=str(getattr(target, "project_id", "") or ""),
            source_project_name=str(getattr(source, "project_name", "") or ""),
            target_project_name=str(getattr(target, "project_name", "") or ""),
            backup_path=backup_path,
            journal_path=journal_path,
            snapshot_path=snapshot_path,
            error_code=error_code,
            error=error,
            failure_stage=failure_stage,
            rollback_status=rollback_status,
            warnings=warnings,
            conflicts=conflicts,
            planned_mutations=planned_mutations,
            completed_mutations=completed_mutations,
            integrity=dict(integrity or {}),
            imported_segment_count=int(imported_segment_count),
            imported_audio_count=int(imported_audio_count),
        )

    @staticmethod
    def _tree_digest(root: str) -> str:
        return _digest({
            relative: _hash_file(path)
            for relative, path in sorted(_tree_files(root).items())
        })

    @classmethod
    def _restore_backup_side_effects(
        cls,
        snapshot_dir: str,
        target_dir: str,
    ) -> None:
        """Undo only the known task-schema side effect of backup's guard.

        ``ProjectBackupService`` reuses the production mutation guard.  Its
        read-only task probe can materialize an empty project SQLite database.
        The executor records the target shadow before invoking backup and
        restores that exact shadow if this known bookkeeping file appears, so
        backup infrastructure cannot make a failed merge look like a target
        content mutation.
        """
        before = _tree_files(snapshot_dir)
        after = _tree_files(target_dir)
        before_hashes = {key: _hash_file(path) for key, path in sorted(before.items())}
        after_hashes = {key: _hash_file(path) for key, path in sorted(after.items())}
        if before_hashes == after_hashes:
            return
        task_relative = os.path.relpath(
            project_paths.project_file(target_dir, "task_db"), target_dir
        ).replace(os.sep, "/")
        changed = set(before_hashes) ^ set(after_hashes)
        changed.update(
            key for key in set(before_hashes) & set(after_hashes)
            if before_hashes[key] != after_hashes[key]
        )
        if changed != {task_relative} or task_relative in before_hashes:
            raise MergeExecutionError(
                "TARGET_CHANGED_DURING_BACKUP",
                "backup 期间目标发生了非 backup guard 的变化，拒绝继续覆盖",
                stage=MergeExecutionStage.BACKUP_FAILED.value,
                details={"changed_files": sorted(changed)},
            )
        _restore_tree(snapshot_dir, target_dir)

    @classmethod
    def execute(
        cls,
        plan: MergePlan,
        resolutions: Any = None,
        confirmation: Any = None,
        *,
        session: Any = None,
        fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None = None,
        selection_policy: str = CHAPTER_SELECTION_POLICY,
        assembly_token: str = "",
    ) -> MergeExecutionResult:
        """Execute one fresh, current plan through backup → stage → commit → verify."""
        transaction_id = f"merge-{uuid.uuid4().hex}"
        journal_path = _journal_path(transaction_id)
        journal: dict[str, Any] = {
            "schema_version": "chapter-merge-transaction-v1",
            "transaction_id": transaction_id,
            "plan_token": str(getattr(plan, "plan_token", "") or ""),
            "source_project_id": str(getattr(getattr(plan, "source_project", None), "project_id", "") or ""),
            "target_project_id": str(getattr(getattr(plan, "target_project", None), "project_id", "") or ""),
            "backup_path": "",
            "snapshot_path": "",
            "stage": MergeExecutionStage.VALIDATING.value,
            "started_at": _utc_now(),
            "updated_at": _utc_now(),
            "planned_mutations": [],
            "completed_mutations": [],
            "failure": {},
            "rollback_status": "NOT_STARTED",
        }

        def write_journal(payload: Mapping[str, Any]) -> None:
            payload = dict(payload)
            payload["updated_at"] = _utc_now()
            _write_journal(journal_path, payload)

        def transition(stage: MergeExecutionStage | str) -> None:
            journal["stage"] = str(stage.value if isinstance(stage, MergeExecutionStage) else stage)
            write_journal(journal)

        write_journal(journal)
        try:
            choices = cls._validate_plan(
                plan,
                resolutions,
                session,
                require_current=True,
                selection_policy=selection_policy,
                assembly_token=assembly_token,
            )
            cls._validate_confirmation(
                plan,
                resolutions,
                confirmation,
                session=session,
                selection_policy=selection_policy,
                assembly_token=assembly_token,
            )
        except MergeExecutionError as exc:
            journal["failure"] = {"code": exc.code, "message": str(exc), "stage": exc.stage, "details": exc.details}
            transition(MergeExecutionStage.VALIDATION_FAILED)
            return cls._base_result(
                plan,
                transaction_id,
                journal_path,
                stage=MergeExecutionStage.VALIDATION_FAILED.value,
                status="VALIDATION_FAILED",
                error_code=exc.code,
                error=str(exc),
                failure_stage=exc.stage,
            )
        except Exception as exc:  # pragma: no cover - defensive boundary  # noqa: BLE001
            journal["failure"] = {"code": "VALIDATION_EXCEPTION", "message": str(exc)}
            transition(MergeExecutionStage.VALIDATION_FAILED)
            return cls._base_result(
                plan,
                transaction_id,
                journal_path,
                stage=MergeExecutionStage.VALIDATION_FAILED.value,
                status="VALIDATION_FAILED",
                error_code="VALIDATION_EXCEPTION",
                error=str(exc),
                failure_stage=MergeExecutionStage.VALIDATING.value,
            )

        source = plan.source_project
        target = plan.target_project
        source_dir = ProjectRepository.get_project_dir(source.project_name)
        target_dir = ProjectRepository.get_project_dir(target.project_name)
        target_root = _data_root()
        if not _inside(source_dir, target_root) or not _inside(target_dir, target_root):
            exc = MergeExecutionError(
                "PROJECT_OUTSIDE_DATA_ROOT",
                "source/target 不属于当前 data root，拒绝执行",
                stage=MergeExecutionStage.VALIDATING.value,
            )
            journal["failure"] = {"code": exc.code, "message": str(exc), "stage": exc.stage}
            transition(MergeExecutionStage.VALIDATION_FAILED)
            return cls._base_result(
                plan, transaction_id, journal_path,
                stage=MergeExecutionStage.VALIDATION_FAILED.value,
                status="VALIDATION_FAILED", error_code=exc.code, error=str(exc), failure_stage=exc.stage,
            )

        warnings = tuple(
            str(item.message)
            for item in plan.conflicts
            if not item.blocking
        )
        planned_mutations = (
            "structured_script",
            "project_meta",
            "merge_history",
            "segments_status",
            "source_audio_copy_to_target_segments",
            "target_voice_cast_only_for_explicit_resolutions",
            "target_bindings_only_for_explicit_resolutions",
            "source_export_state_excluded",
            "source_task_history_excluded",
        )
        journal["planned_mutations"] = list(planned_mutations)
        backup_path = ""
        snapshot_dir = ""
        stage_dir = ""
        completed: list[str] = []
        mutation_started = False
        integrity: dict[str, Any] = {}
        imported_segment_count = 0
        imported_audio_count = 0
        transaction_root = os.path.join(_journal_root(), transaction_id)
        snapshot_dir = os.path.join(transaction_root, "snapshot")
        stage_dir = os.path.join(transaction_root, "stage")
        try:
            os.makedirs(transaction_root, exist_ok=True)
            _copy_tree(target_dir, snapshot_dir)
            journal["snapshot_path"] = snapshot_dir
            journal["target_snapshot_digest"] = cls._tree_digest(snapshot_dir)
            write_journal(journal)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            journal["failure"] = {"code": "SNAPSHOT_FAILED", "message": message, "stage": MergeExecutionStage.BACKUP_FAILED.value}
            transition(MergeExecutionStage.BACKUP_FAILED)
            return cls._base_result(
                plan, transaction_id, journal_path,
                stage=MergeExecutionStage.BACKUP_FAILED.value,
                status="BACKUP_FAILED",
                error_code="SNAPSHOT_FAILED",
                error=message,
                failure_stage=MergeExecutionStage.BACKUP_FAILED.value,
                snapshot_path=snapshot_dir,
                planned_mutations=planned_mutations,
                warnings=warnings,
            )
        try:
            transition(MergeExecutionStage.BACKING_UP)
            _maybe_inject(fault_injection, "backup")
            backup_path = ProjectBackupService.create_backup(target.project_name)
            if not backup_path or not os.path.isfile(backup_path):
                raise MergeExecutionError(
                    "BACKUP_NOT_CREATED",
                    "target backup service 未返回可恢复的 backup 文件",
                    stage=MergeExecutionStage.BACKUP_FAILED.value,
                )
            cls._restore_backup_side_effects(snapshot_dir, target_dir)
            journal["backup_path"] = backup_path
            write_journal(journal)
        except Exception as exc:  # noqa: BLE001
            side_effect_error = ""
            try:
                cls._restore_backup_side_effects(snapshot_dir, target_dir)
            except Exception as restore_exc:  # noqa: BLE001
                side_effect_error = str(restore_exc)
            if isinstance(exc, MergeExecutionError):
                code, message = exc.code, str(exc)
            else:
                code, message = "BACKUP_FAILED", str(exc)
            if side_effect_error:
                code = "BACKUP_SIDE_EFFECT_ROLLBACK_FAILED"
                message = f"{message}; target restore: {side_effect_error}"
            journal["failure"] = {"code": code, "message": message, "stage": MergeExecutionStage.BACKUP_FAILED.value}
            transition(MergeExecutionStage.BACKUP_FAILED)
            return cls._base_result(
                plan, transaction_id, journal_path,
                stage=MergeExecutionStage.BACKUP_FAILED.value, status="BACKUP_FAILED",
                error_code=code, error=message, failure_stage=MergeExecutionStage.BACKUP_FAILED.value,
                backup_path=backup_path,
                planned_mutations=planned_mutations,
                warnings=warnings,
                imported_segment_count=imported_segment_count,
                imported_audio_count=imported_audio_count,
            )

        try:
            transition(MergeExecutionStage.STAGING)
            _copy_tree(snapshot_dir, stage_dir, fault_injection)
            write_journal(journal)

            source_script = _read_json(project_paths.project_file(source_dir, "structured_script"), required=True)
            target_script = _read_json(project_paths.project_file(target_dir, "structured_script"), required=True)
            source_meta = _read_json(project_paths.project_file(source_dir, "project_meta"), required=True)
            target_meta = cls._load_meta_for_stage(stage_dir)
            merged_script, imported_segment_count = cls._merge_script(
                source_script, target_script, plan.placement
            )
            statuses, imported_audio_count = cls._merge_statuses(
                target_meta,
                source_meta if isinstance(source_meta, Mapping) else {},
                plan.source_inventory.ordered_segments,
            )

            segments_dir = project_paths.project_dir(stage_dir, "segments", create=True)
            for record in plan.source_inventory.ordered_segments:
                for relative, source_audio in cls._source_audio_files(source_dir, record):
                    basename = os.path.basename(source_audio)
                    destination = os.path.join(segments_dir, basename)
                    if os.path.exists(destination):
                        raise MergeExecutionError(
                            "TARGET_AUDIO_PATH_CONFLICT",
                            f"目标 staged 音频路径已经存在：{basename}",
                            stage=MergeExecutionStage.STAGING.value,
                        )
                    _maybe_inject(fault_injection, "stage_copy", os.path.relpath(destination, stage_dir).replace(os.sep, "/"))
                    shutil.copy2(source_audio, destination)

            cls._write_stage_json(stage_dir, "structured_script", merged_script)
            cls._write_stage_json(
                stage_dir,
                "project_meta",
                cls._finalize_meta(target_meta, merged_script, statuses),
            )

            source_bindings = _read_json(project_paths.project_file(source_dir, "voice_bindings"), required=False) or {}
            source_roster = _read_json(project_paths.project_file(source_dir, "character_roster"), required=False) or {}
            source_cast = _read_json(project_paths.project_file(source_dir, "voice_cast"), required=False) or {}
            target_bindings = cls._read_stage_json(stage_dir, "voice_bindings", required=False) or {}
            target_roster = cls._read_stage_json(stage_dir, "character_roster", required=False) or {}
            target_cast = cls._read_stage_json(stage_dir, "voice_cast", required=False) or {}
            voice_changed = cls._merge_voice_cast(
                source_dir,
                stage_dir,
                source_bindings if isinstance(source_bindings, Mapping) else {},
                source_roster,
                source_cast,
                target_bindings,
                target_roster,
                target_cast,
                plan,
                choices,
                fault_injection,
            )

            history_path = cls._merge_history_record(
                stage_dir, plan, transaction_id, backup_path,
                imported_segment_count, imported_audio_count,
            )
            _ = history_path
            staged_integrity = cls._validate_stage_tree(stage_dir)
            integrity = {"staged": staged_integrity}
            journal["staged_integrity"] = staged_integrity
            journal["voice_mutations"] = voice_changed
            write_journal(journal)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, MergeExecutionError):
                code, message, failure_stage = exc.code, str(exc), exc.stage
            else:
                code, message, failure_stage = "STAGE_FAILED", str(exc), MergeExecutionStage.STAGE_FAILED.value
            journal["failure"] = {"code": code, "message": message, "stage": failure_stage}
            transition(MergeExecutionStage.STAGE_FAILED)
            return cls._base_result(
                plan, transaction_id, journal_path,
                stage=MergeExecutionStage.STAGE_FAILED.value, status="STAGE_FAILED",
                error_code=code, error=message, failure_stage=failure_stage,
                backup_path=backup_path, snapshot_path=snapshot_dir,
                planned_mutations=planned_mutations, warnings=warnings,
                imported_segment_count=imported_segment_count,
                imported_audio_count=imported_audio_count,
            )

        try:
            transition(MergeExecutionStage.COMMITTING)
            completed, mutation_started = cls._commit_stage(
                stage_dir, target_dir, fault_injection, journal, write_journal
            )
            journal["completed_mutations"] = completed
            write_journal(journal)
            transition(MergeExecutionStage.VERIFYING)
            _maybe_inject(fault_injection, "integrity")
            integrity["target"] = cls._integrity_report(target.project_name)
            if not bool(integrity["target"].get("ok")):
                raise MergeExecutionError(
                    "TARGET_INTEGRITY_FAILED_AFTER_COMMIT",
                    "commit 后目标完整性校验失败",
                    stage=MergeExecutionStage.VERIFY_FAILED.value,
                    details={"integrity": integrity["target"]},
                )
            journal["integrity"] = integrity
            journal["rollback_status"] = "NOT_REQUIRED"
            transition(MergeExecutionStage.SUCCEEDED)
            return cls._base_result(
                plan, transaction_id, journal_path,
                stage=MergeExecutionStage.SUCCEEDED.value, status="SUCCEEDED",
                backup_path=backup_path, snapshot_path=snapshot_dir,
                rollback_status="NOT_REQUIRED", integrity=integrity,
                completed_mutations=tuple(completed), planned_mutations=planned_mutations,
                warnings=warnings,
                imported_segment_count=imported_segment_count,
                imported_audio_count=imported_audio_count,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, MergeExecutionError):
                code, message, failure_stage = exc.code, str(exc), exc.stage
            else:
                current_stage = str(journal.get("stage") or "")
                failure_stage = (
                    MergeExecutionStage.VERIFY_FAILED.value
                    if current_stage == MergeExecutionStage.VERIFYING.value
                    else MergeExecutionStage.COMMIT_FAILED.value
                )
                code = "VERIFY_FAILED" if failure_stage == MergeExecutionStage.VERIFY_FAILED.value else "COMMIT_FAILED"
                message = str(exc)
            journal["failure"] = {"code": code, "message": message, "stage": failure_stage}
            mutation_started = mutation_started or bool(journal.get("mutation_started")) or bool(completed)
            if not mutation_started:
                transition(MergeExecutionStage.COMMIT_FAILED)
                return cls._base_result(
                    plan, transaction_id, journal_path,
                    stage=MergeExecutionStage.COMMIT_FAILED.value, status="COMMIT_FAILED",
                    error_code=code, error=message, failure_stage=failure_stage,
                    backup_path=backup_path, snapshot_path=snapshot_dir,
                    planned_mutations=planned_mutations, completed_mutations=tuple(completed),
                    warnings=warnings, integrity=integrity,
                    imported_segment_count=imported_segment_count,
                    imported_audio_count=imported_audio_count,
                )
            transition(MergeExecutionStage.ROLLING_BACK)
            journal["rollback_status"] = "ROLLING_BACK"
            try:
                rollback_integrity = cls._rollback(
                    snapshot_dir, target_dir, target.project_name, fault_injection
                )
                integrity["rollback"] = rollback_integrity
                journal["rollback_status"] = "ROLLED_BACK"
                transition(MergeExecutionStage.ROLLED_BACK)
                return cls._base_result(
                    plan, transaction_id, journal_path,
                    stage=MergeExecutionStage.ROLLED_BACK.value,
                    status=MERGE_FAILED_ROLLED_BACK,
                    error_code=code, error=message, failure_stage=failure_stage,
                    backup_path=backup_path, snapshot_path=snapshot_dir,
                    rollback_status="ROLLED_BACK", integrity=integrity,
                    planned_mutations=planned_mutations, completed_mutations=tuple(completed),
                    warnings=warnings,
                    imported_segment_count=imported_segment_count,
                    imported_audio_count=imported_audio_count,
                )
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_code = rollback_exc.code if isinstance(rollback_exc, MergeExecutionError) else "ROLLBACK_FAILED"
                journal["rollback_status"] = "ROLLBACK_FAILED"
                journal["rollback_failure"] = {"code": rollback_code, "message": str(rollback_exc)}
                transition(MergeExecutionStage.ROLLBACK_FAILED)
                return cls._base_result(
                    plan, transaction_id, journal_path,
                    stage=MergeExecutionStage.ROLLBACK_FAILED.value,
                    status=MERGE_FAILED_ROLLBACK_FAILED,
                    error_code=rollback_code, error=f"{message}; rollback: {rollback_exc}",
                    failure_stage=failure_stage,
                    backup_path=backup_path, snapshot_path=snapshot_dir,
                    rollback_status="ROLLBACK_FAILED", integrity=integrity,
                    planned_mutations=planned_mutations, completed_mutations=tuple(completed),
                    warnings=warnings,
                    imported_segment_count=imported_segment_count,
                    imported_audio_count=imported_audio_count,
                )


def execute_merge(
    plan: MergePlan,
    resolutions: Any = None,
    confirmation: Any = None,
    *,
    session: Any = None,
    fault_injection: Mapping[str, Any] | Callable[[str, str], None] | None = None,
    selection_policy: str = CHAPTER_SELECTION_POLICY,
    assembly_token: str = "",
) -> MergeExecutionResult:
    """Functional convenience wrapper for non-UI callers and tests."""
    return ChapterMergeExecutor.execute(
        plan,
        resolutions,
        confirmation,
        session=session,
        fault_injection=fault_injection,
        selection_policy=selection_policy,
        assembly_token=assembly_token,
    )


__all__ = [
    "CHAPTER_SELECTION_POLICY",
    "CONFIRMATION_SCHEMA_VERSION",
    "MERGE_FAILED_ROLLBACK_FAILED",
    "MERGE_FAILED_ROLLED_BACK",
    "TRANSACTION_JOURNAL_ACTIVE_STAGES",
    "TRANSACTION_JOURNAL_TERMINAL_STAGES",
    "WHOLE_BOOK_SELECTION_POLICY",
    "ChapterMergeExecutor",
    "MergeConfirmation",
    "MergeExecutionError",
    "MergeExecutionResult",
    "MergeExecutionStage",
    "execute_merge",
    "read_transaction_journals",
]
