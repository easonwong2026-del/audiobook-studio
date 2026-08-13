"""Durable task repository.

Production tasks use a project-local SQLite database as their source of truth.
SQLite transactions provide the cross-process active-task and idempotency
guarantees that the former collection of JSON files could not provide.

Legacy global JSON records remain readable and are imported, once per project,
without being deleted.  Supplement tasks and synthetic test fixtures whose
project directory does not exist continue to use the legacy JSON backend.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from lib import project_paths

from ._atomic import atomic_write as _atomic_write
from .project_repo import ProjectRepository

logger = logging.getLogger(__name__)

_DB_FILENAME = "production_tasks.sqlite3"
_PRODUCTION_TYPE = "synthesis"
_RUNTIME_TASK_TYPES = frozenset({
    "synthesis",
    "supplement",
    "voice_preview",
    "export",
})
_ACTIVE_STATES = ("pending", "running", "pausing", "paused", "recovering", "cancelling")
_TERMINAL_STATES = ("cancelled", "done", "error", "interrupted", "needs_attention")

# P1-1: schema-once。同一 path + 进程内只执行一次重 schema ensure / legacy
# 迁移；缓存值 (schema_version, legacy_done)。失效条件见 `_ensure_schema_once`：
# 探针（sqlite_master + repository_meta + 必需列 LIMIT 0）失败时自动走重路径，
# 因此外部 DROP TABLE / DB 重建 / 测试换根都能自愈。
_SCHEMA_VERSION = 1
_SCHEMA_CACHE: dict[str, tuple[int, bool]] = {}
_SCHEMA_LOCK = threading.Lock()


class RuntimePendingSignal:
    """跨进程“可能有 pending 任务”信号：一个原子写的时间戳文件。

    语义：信号 = “MAYBE pending”，不是权威状态；claim 扫描仍是权威。
    写方在 SQLite 提交成功后才更新文件（写后置），读方用 mtime_ns 变化检测，
    天然避免 lost-wakeup。信号文件被清理/删除时由 Runtime 的周期兜底扫描覆盖。
    """

    SIGNAL_FILENAME = "runtime_pending.signal"

    @staticmethod
    def default_path() -> str:
        from lib import config as _cfg

        return os.path.join(_cfg.get_data_dir(), RuntimePendingSignal.SIGNAL_FILENAME)

    def __init__(self, path: str | None = None) -> None:
        self._path = path or self.default_path()
        self._last_seen_ns = 0

    def may_have_pending(self) -> bool:
        """O(1) stat：文件存在且 mtime_ns 比上次所见新 → 需要扫描。

        消费语义：返回 True 时记录“已见”（``_last_seen_ns`` 前移），后续
        ``peek()`` 将返回 False，直到写方再次 notify() 更新 mtime。
        """
        try:
            stamp = os.stat(self._path).st_mtime_ns
        except OSError:
            return False
        if stamp > self._last_seen_ns:
            self._last_seen_ns = stamp  # 记录所见（不删除文件，避免删-写竞态）
            return True
        return False

    def peek(self) -> bool:
        """非消费式 fresh 检查：不推进 ``_last_seen_ns``。

        通用只读探针（诊断/测试可用）；需要“消费”语义时用
        ``may_have_pending()``，需要“已扫描到哪个戳”时用 ``stamp_ns()``。
        """
        try:
            stamp = os.stat(self._path).st_mtime_ns
        except OSError:
            return False
        return stamp > self._last_seen_ns

    def stamp_ns(self) -> int:
        """当前信号文件 mtime_ns（不存在返回 -1）。用于 tick 级“本 tick 无新写入”判断。"""
        try:
            return os.stat(self._path).st_mtime_ns
        except OSError:
            return -1

    def mark_seen(self) -> None:
        try:
            self._last_seen_ns = os.stat(self._path).st_mtime_ns
        except OSError:
            self._last_seen_ns = 0

    @staticmethod
    def notify() -> None:
        """写方（提交成功后）调用：原子写时间戳，使所有进程的下一次 stat 判定为 fresh。"""
        try:
            path = RuntimePendingSignal.default_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(datetime.now(timezone.utc).isoformat(timespec="microseconds"))
            os.replace(tmp, path)  # 原子：不会出现半写文件
        except OSError:
            pass  # best-effort；周期兜底扫描兜底


def _default_progress() -> dict[str, Any]:
    return {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "percent": 0.0,
        "current_chapter": None,
        "current_segment": None,
    }


def _json_dict(value: str | None, default: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return dict(default)
    return parsed if isinstance(parsed, dict) else dict(default)


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_scope(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    chapter_ids = sorted({
        str(item).strip()
        for item in raw.get("chapter_ids", [])
        if str(item).strip()
    }) if isinstance(raw.get("chapter_ids", []), list) else []
    segment_ids = sorted({
        str(item).strip()
        for item in raw.get("segment_ids", [])
        if str(item).strip()
    }) if isinstance(raw.get("segment_ids", []), list) else []
    all_scope = bool(raw.get("all", not (chapter_ids or segment_ids)))
    return {
        "all": all_scope,
        "chapter_ids": [] if all_scope else chapter_ids,
        "segment_ids": [] if all_scope else segment_ids,
    }


def _canonical_optional_number(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _canonical_options(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    try:
        beams = max(int(raw.get("num_beams", 2) or 2), 1)
    except (TypeError, ValueError):
        beams = 2
    emotion = raw.get("emotion")
    raw_voice_overrides = raw.get("voice_overrides")
    voice_overrides = {
        str(segment_id).strip(): str(path).strip()
        for segment_id, path in sorted(
            raw_voice_overrides.items(),
            key=lambda item: str(item[0]),
        )
        if str(segment_id).strip() and str(path).strip()
    } if isinstance(raw_voice_overrides, dict) else {}
    engine_raw = raw.get("engine_snapshot")
    engine_snapshot = {
        str(key): engine_raw.get(key)
        for key in (
            "engine_backend", "engine_version", "engine_identity",
            "model_identity", "precision", "device", "model_dir", "cache_identity",
        )
        if isinstance(engine_raw, dict) and engine_raw.get(key) not in (None, "")
    }
    return {
        "num_beams": beams,
        "emotion": str(emotion) if emotion not in (None, "") else None,
        "emo_alpha": _canonical_optional_number(raw.get("emo_alpha")),
        "speech_rate": _canonical_optional_number(raw.get("speech_rate")),
        "voice_overrides": voice_overrides,
        "engine_snapshot": engine_snapshot,
    }


@dataclass
class TaskRecord:
    """JSON-safe durable task record.

    The first fields intentionally retain the legacy positional constructor.
    Runtime ownership fields are persisted only for production tasks.
    """

    task_id: str
    task_type: str
    project: str
    status: str
    artifact_dir: str = ""
    error_summary: str = ""
    created_at: str = ""
    source: str = "system"
    scope: dict[str, Any] = field(default_factory=lambda: {
        "all": True,
        "chapter_ids": [],
        "segment_ids": [],
    })
    options: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=_default_progress)
    failed_segment_ids: list[str] = field(default_factory=list)
    attempt: int = 1
    idempotency_key: str = ""
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    parent_task_id: str = ""
    recovery_of: str = ""
    owner_id: str = ""
    heartbeat_at: str = ""
    control_intent: str = ""
    log_lines: list[str] = field(default_factory=list)
    version: int = 0
    startup: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        scope = {"all": False, "chapter_ids": [], "segment_ids": []}
        if isinstance(self.scope, dict):
            scope.update(self.scope)
        progress = _default_progress()
        if isinstance(self.progress, dict):
            progress.update(self.progress)
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "project": self.project,
            "status": self.status,
            "source": self.source,
            "scope": scope,
            "options": dict(self.options) if isinstance(self.options, dict) else {},
            "progress": progress,
            "failed_segment_ids": [str(item) for item in self.failed_segment_ids],
            "attempt": max(int(self.attempt or 1), 1),
            "idempotency_key": self.idempotency_key,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "artifact_dir": self.artifact_dir,
            "error_summary": self.error_summary,
            "created_at": self.created_at,
            "parent_task_id": self.parent_task_id,
            "recovery_of": self.recovery_of,
            "owner_id": self.owner_id,
            "heartbeat_at": self.heartbeat_at,
            "control_intent": self.control_intent,
            "log_lines": [str(item) for item in self.log_lines[-50:]],
            "version": max(int(self.version or 0), 0),
            "startup": dict(self.startup) if isinstance(self.startup, dict) else {},
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TaskRecord":
        raw_scope = data.get("scope")
        scope = {"all": True, "chapter_ids": [], "segment_ids": []}
        if isinstance(raw_scope, dict):
            scope.update(raw_scope)
        progress = _default_progress()
        raw_progress = data.get("progress")
        if isinstance(raw_progress, dict):
            progress.update(raw_progress)
        raw_failed = data.get("failed_segment_ids", [])
        failed = [str(item) for item in raw_failed] if isinstance(raw_failed, list) else []
        raw_logs = data.get("log_lines", [])
        logs = [str(item) for item in raw_logs[-50:]] if isinstance(raw_logs, list) else []
        try:
            attempt = max(int(data.get("attempt", 1) or 1), 1)
        except (TypeError, ValueError):
            attempt = 1
        try:
            version = max(int(data.get("version", 0) or 0), 0)
        except (TypeError, ValueError):
            version = 0
        return TaskRecord(
            task_id=str(data.get("task_id") or ""),
            task_type=str(data.get("task_type") or ""),
            project=str(data.get("project") or ""),
            status=str(data.get("status") or "pending"),
            artifact_dir=str(data.get("artifact_dir") or ""),
            error_summary=str(data.get("error_summary") or ""),
            created_at=str(data.get("created_at") or ""),
            source=str(data.get("source") or "system"),
            scope=scope,
            options=data.get("options", {}) if isinstance(data.get("options", {}), dict) else {},
            progress=progress,
            failed_segment_ids=failed,
            attempt=attempt,
            idempotency_key=str(data.get("idempotency_key") or ""),
            started_at=str(data.get("started_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            finished_at=str(data.get("finished_at") or ""),
            parent_task_id=str(data.get("parent_task_id") or ""),
            recovery_of=str(data.get("recovery_of") or ""),
            owner_id=str(data.get("owner_id") or ""),
            heartbeat_at=str(data.get("heartbeat_at") or ""),
            control_intent=str(data.get("control_intent") or ""),
            log_lines=logs,
            version=version,
            startup=data.get("startup", {}) if isinstance(data.get("startup", {}), dict) else {},
        )


class TaskRepository:
    """Task persistence with transactional production operations."""

    @staticmethod
    def _resolve_preview_dir() -> str:
        from lib import config as _cfg

        return _cfg.get_preview_dir()

    @staticmethod
    def get_task_dir() -> str:
        """Legacy global JSON directory."""
        task_dir = os.path.join(TaskRepository._resolve_preview_dir(), "task_records")
        os.makedirs(task_dir, exist_ok=True)
        return task_dir

    canonical_scope = staticmethod(_canonical_scope)
    canonical_options = staticmethod(_canonical_options)

    @staticmethod
    def same_production_payload(
        record: TaskRecord,
        scope: Any,
        options: Any,
    ) -> bool:
        return (
            _canonical_scope(record.scope) == _canonical_scope(scope)
            and _canonical_options(record.options) == _canonical_options(options)
        )

    @staticmethod
    def get_database_path(project: str, *, create: bool = False) -> str | None:
        """Return the project-local production database path."""
        name = str(project or "").strip()
        if not name:
            return None
        try:
            project_dir = ProjectRepository.get_project_dir(name)
        except Exception:
            return None
        if not os.path.isdir(project_dir) or not os.path.isfile(
            os.path.join(project_dir, "project.json")
        ):
            return None
        config_dir = project_paths.project_dir(project_dir, "config", create=create)
        return os.path.join(config_dir, _DB_FILENAME)

    @staticmethod
    def _connect(
        project: str,
        *,
        create: bool = True,
        skip_schema: bool = False,
    ) -> sqlite3.Connection | None:
        path = TaskRepository.get_database_path(project, create=create)
        if path is None or (not create and not os.path.isfile(path)):
            return None
        connection = sqlite3.connect(path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        if create and not skip_schema:
            TaskRepository._ensure_schema_once(connection, project, path)
        return connection

    @staticmethod
    def _probe_schema(connection: sqlite3.Connection) -> tuple[bool, bool]:
        """廉价探针：确认 production_tasks 存在且 schema 版本就绪。

        仅轻量查询（不做 executescript / PRAGMA table_info）：
        1. sqlite_master 中 production_tasks 是否存在；
        2. repository_meta.schema_version 是否 == _SCHEMA_VERSION；
        3. 当前 schema 的必需列 startup_json 是否可查询（LIMIT 0 不扫描行）。

        第 3 步是必要兜底：外部 DROP TABLE 后以旧 schema 重建（迁移测试模拟）
        时，表存在且 version 标记仍在，但列集合已过期 —— 只有列探针能发现。
        """
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='production_tasks'"
        ).fetchone()
        if row is None:
            return False, False
        try:
            meta = connection.execute(
                "SELECT value FROM repository_meta WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            # repository_meta 缺失（极旧 DB）→ 需要重路径创建。
            return True, False
        if meta is None or meta["value"] != str(_SCHEMA_VERSION):
            return True, False
        try:
            connection.execute("SELECT startup_json FROM production_tasks LIMIT 0")
        except sqlite3.OperationalError:
            return True, False
        return True, True

    @staticmethod
    def _ensure_schema_once(
        connection: sqlite3.Connection,
        project: str,
        path: str,
    ) -> None:
        """每个 path + 进程只执行一次重操作；探针失败时自动重跑（自愈）。"""
        key = os.path.normcase(os.path.abspath(path))
        with _SCHEMA_LOCK:
            cached = _SCHEMA_CACHE.get(key)
        if cached is not None and cached[0] == _SCHEMA_VERSION:
            exists, version_ok = TaskRepository._probe_schema(connection)
            if exists and version_ok:
                return
        # 重路径：executescript + commit + PRAGMA table_info + 可能 ALTER（保留现有实现）
        TaskRepository._ensure_schema(connection)
        # legacy 迁移：仅在缓存未标记完成时执行（内部由 repository_meta 标记保证幂等）
        if cached is None or not cached[1]:
            TaskRepository._migrate_legacy_json(project, connection)
        with _SCHEMA_LOCK:
            _SCHEMA_CACHE[key] = (_SCHEMA_VERSION, True)

    @staticmethod
    def reset_schema_cache() -> None:
        """测试/换根用：清空 per-process schema 缓存。"""
        with _SCHEMA_LOCK:
            _SCHEMA_CACHE.clear()

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS repository_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS production_tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                project TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                options_json TEXT NOT NULL,
                progress_json TEXT NOT NULL,
                failed_segment_ids_json TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                parent_task_id TEXT NOT NULL DEFAULT '',
                recovery_of TEXT NOT NULL DEFAULT '',
                artifact_dir TEXT NOT NULL DEFAULT '',
                error_summary TEXT NOT NULL DEFAULT '',
                owner_id TEXT NOT NULL DEFAULT '',
                heartbeat_at TEXT NOT NULL DEFAULT '',
                control_intent TEXT NOT NULL DEFAULT '',
                log_lines_json TEXT NOT NULL DEFAULT '[]',
                startup_json TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_production_active_project
            ON production_tasks(project)
            WHERE status IN ('pending','running','pausing','paused','cancelling');
            CREATE UNIQUE INDEX IF NOT EXISTS uq_production_idempotency
            ON production_tasks(project, task_type, idempotency_key)
            WHERE idempotency_key <> '';
            CREATE INDEX IF NOT EXISTS ix_production_updated
            ON production_tasks(updated_at DESC, task_id DESC);
            """
        )
        connection.commit()
        # 旧库迁移：为已存在的 production_tasks 表补 startup_json 列。
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(production_tasks)")
        }
        if "startup_json" not in columns:
            connection.execute(
                "ALTER TABLE production_tasks "
                "ADD COLUMN startup_json TEXT NOT NULL DEFAULT '{}'"
            )
            connection.commit()
        # schema 版本标记：重路径执行（且表结构已就绪）后写入。
        connection.execute(
            "INSERT OR REPLACE INTO repository_meta(key,value) VALUES('schema_version',?)",
            (str(_SCHEMA_VERSION),),
        )
        connection.commit()

    @staticmethod
    def _migrate_legacy_json(project: str, connection: sqlite3.Connection) -> None:
        marker = connection.execute(
            "SELECT value FROM repository_meta WHERE key='legacy_json_imported'"
        ).fetchone()
        if marker is not None:
            return
        task_dir = TaskRepository.get_task_dir()
        try:
            names = list(os.listdir(task_dir))
        except OSError:
            names = []
        with connection:
            for name in names:
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(task_dir, name), encoding="utf-8") as file:
                        data = json.load(file)
                    record = TaskRecord.from_dict(data) if isinstance(data, dict) else None
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if (
                    record is None
                    or record.task_type not in _RUNTIME_TASK_TYPES
                    or record.project != project
                ):
                    continue
                TaskRepository._insert_record(connection, record, ignore=True)
            connection.execute(
                "INSERT OR REPLACE INTO repository_meta(key,value) VALUES('legacy_json_imported',?)",
                (_utc_now(),),
            )

    @staticmethod
    def _insert_record(
        connection: sqlite3.Connection,
        record: TaskRecord,
        *,
        ignore: bool = False,
    ) -> None:
        values = record.to_dict()
        command = "INSERT OR IGNORE" if ignore else "INSERT"
        connection.execute(
            f"""
            {command} INTO production_tasks (
                task_id,task_type,project,status,source,scope_json,options_json,
                progress_json,failed_segment_ids_json,attempt,idempotency_key,
                created_at,started_at,updated_at,finished_at,parent_task_id,
                recovery_of,artifact_dir,error_summary,owner_id,heartbeat_at,
                control_intent,log_lines_json,startup_json,version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                values["task_id"], values["task_type"], values["project"],
                values["status"], values["source"],
                json.dumps(values["scope"], ensure_ascii=False),
                json.dumps(values["options"], ensure_ascii=False),
                json.dumps(values["progress"], ensure_ascii=False),
                json.dumps(values["failed_segment_ids"], ensure_ascii=False),
                values["attempt"], values["idempotency_key"],
                values["created_at"], values["started_at"], values["updated_at"],
                values["finished_at"], values["parent_task_id"],
                values["recovery_of"], values["artifact_dir"],
                values["error_summary"], values["owner_id"],
                values["heartbeat_at"], values["control_intent"],
                json.dumps(values["log_lines"], ensure_ascii=False),
                json.dumps(values["startup"], ensure_ascii=False),
                values["version"],
            ),
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            task_type=row["task_type"],
            project=row["project"],
            status=row["status"],
            source=row["source"],
            scope=_json_dict(
                row["scope_json"],
                {"all": True, "chapter_ids": [], "segment_ids": []},
            ),
            options=_json_dict(row["options_json"], {}),
            progress=_json_dict(row["progress_json"], _default_progress()),
            failed_segment_ids=[
                str(item) for item in _json_list(row["failed_segment_ids_json"])
            ],
            attempt=max(int(row["attempt"] or 1), 1),
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"],
            parent_task_id=row["parent_task_id"],
            recovery_of=row["recovery_of"],
            artifact_dir=row["artifact_dir"],
            error_summary=row["error_summary"],
            owner_id=row["owner_id"],
            heartbeat_at=row["heartbeat_at"],
            control_intent=row["control_intent"],
            log_lines=[str(item) for item in _json_list(row["log_lines_json"])][-50:],
            startup=_json_dict(row["startup_json"], {}),
            version=max(int(row["version"] or 0), 0),
        )

    @staticmethod
    def _legacy_save(record: TaskRecord) -> None:
        task_dir = TaskRepository.get_task_dir()
        path = os.path.join(task_dir, f"{record.task_id}.json")
        _atomic_write(path, record.to_dict())

    @staticmethod
    def _legacy_load(task_id: str) -> Optional[TaskRecord]:
        path = os.path.join(TaskRepository.get_task_dir(), f"{task_id}.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as file:
                data = json.load(file)
            return TaskRecord.from_dict(data) if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError, UnicodeError) as exc:
            logger.warning("读取任务记录 %s 失败: %s", task_id, exc)
            return None

    @staticmethod
    def _project_names(project: str | None = None) -> list[str]:
        if project:
            return [str(project)]
        names: set[str] = set()
        try:
            names.update(ProjectRepository.scan_projects())
        except Exception:
            pass
        # Production ownership must also see projects hidden from the bookshelf;
        # otherwise a hidden active project could be bypassed by a data-dir
        # switch or a second task lookup.
        try:
            ProjectRepository._ensure_roots()
            for root in (
                ProjectRepository.WORKSPACE_ROOT,
                ProjectRepository.LEGACY_ROOT,
            ):
                if not root or not os.path.isdir(root):
                    continue
                for entry in os.scandir(root):
                    if (
                        entry.name == ".trash"
                        or entry.name.startswith(".tmp_")
                        or not entry.is_dir(follow_symlinks=False)
                    ):
                        continue
                    if os.path.isfile(os.path.join(entry.path, "project.json")):
                        names.add(entry.name)
        except OSError:
            pass
        return sorted(names)

    @staticmethod
    def save_task(record: TaskRecord) -> None:
        """Persist a task in project SQLite for every runtime task type."""
        if record.task_type not in _RUNTIME_TASK_TYPES:
            TaskRepository._legacy_save(record)
            return
        connection = TaskRepository._connect(record.project, create=True)
        if connection is None:
            TaskRepository._legacy_save(record)
            return
        values = record.to_dict()
        try:
            with connection:
                existing = connection.execute(
                    "SELECT task_id FROM production_tasks WHERE task_id=?", (record.task_id,)
                ).fetchone()
                if existing is None:
                    inserted = True
                    TaskRepository._insert_record(connection, record)
                else:
                    inserted = False
                    connection.execute(
                        """
                        UPDATE production_tasks SET
                          status=?,source=?,scope_json=?,options_json=?,progress_json=?,
                          failed_segment_ids_json=?,attempt=?,idempotency_key=?,
                          created_at=?,started_at=?,updated_at=?,finished_at=?,
                          parent_task_id=?,recovery_of=?,artifact_dir=?,error_summary=?,
                          owner_id=?,heartbeat_at=?,control_intent=?,log_lines_json=?,
                          startup_json=?,version=?
                        WHERE task_id=?
                        """,
                        (
                            values["status"], values["source"],
                            json.dumps(values["scope"], ensure_ascii=False),
                            json.dumps(values["options"], ensure_ascii=False),
                            json.dumps(values["progress"], ensure_ascii=False),
                            json.dumps(values["failed_segment_ids"], ensure_ascii=False),
                            values["attempt"], values["idempotency_key"],
                            values["created_at"], values["started_at"],
                            values["updated_at"], values["finished_at"],
                            values["parent_task_id"], values["recovery_of"],
                            values["artifact_dir"], values["error_summary"],
                            values["owner_id"], values["heartbeat_at"],
                            values["control_intent"],
                            json.dumps(values["log_lines"], ensure_ascii=False),
                            json.dumps(values["startup"], ensure_ascii=False),
                            values["version"], values["task_id"],
                        ),
                    )
            # 提交成功后才发信号：新插入的 pending 行是 claim 的候选。
            if inserted and record.status == "pending":
                RuntimePendingSignal.notify()
        finally:
            connection.close()

    @staticmethod
    def create_production_task(record: TaskRecord) -> tuple[str, TaskRecord]:
        """Atomically create a production task.

        Returns ``("created"|"idempotent"|"idempotency_conflict"|"active", record)``.
        """
        record.scope = _canonical_scope(record.scope)
        record.options = _canonical_options(record.options)
        connection = TaskRepository._connect(record.project, create=True)
        if connection is None:
            raise FileNotFoundError(f"项目不存在: {record.project}")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if record.idempotency_key:
                row = connection.execute(
                    """
                    SELECT * FROM production_tasks
                    WHERE project=? AND task_type=? AND idempotency_key=?
                    ORDER BY updated_at DESC, task_id DESC LIMIT 1
                    """,
                    (record.project, record.task_type, record.idempotency_key),
                ).fetchone()
                if row is not None:
                    existing = TaskRepository._row_to_record(row)
                    connection.commit()
                    if TaskRepository.same_production_payload(
                        existing, record.scope, record.options
                    ):
                        return "idempotent", existing
                    return "idempotency_conflict", existing
            placeholders = ",".join("?" for _ in _ACTIVE_STATES)
            row = connection.execute(
                f"""
                SELECT * FROM production_tasks
                WHERE project=? AND status IN ({placeholders})
                ORDER BY updated_at DESC, task_id DESC LIMIT 1
                """,
                (record.project, *_ACTIVE_STATES),
            ).fetchone()
            if row is not None:
                connection.commit()
                return "active", TaskRepository._row_to_record(row)
            TaskRepository._insert_record(connection, record)
            connection.commit()
            RuntimePendingSignal.notify()
            return "created", record
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def create_runtime_task(record: TaskRecord) -> tuple[str, TaskRecord]:
        """Atomically enqueue a non-production task for the singleton runtime."""
        if record.task_type not in _RUNTIME_TASK_TYPES - {_PRODUCTION_TYPE}:
            raise ValueError(f"不支持的 runtime task_type: {record.task_type}")
        # Preview/supplement are TTS tasks too: freeze the profile before the
        # row becomes claimable, so a later global setting change cannot alter
        # a queued utility task.
        if record.task_type in {"voice_preview", "preview", "supplement"}:
            try:
                from lib.tts_profile import resolve_profile

                options = dict(record.options or {})
                options.setdefault("engine_snapshot", resolve_profile({}))
                record.options = options
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                pass
        connection = TaskRepository._connect(record.project, create=True)
        if connection is None:
            raise FileNotFoundError(f"项目不存在: {record.project}")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if record.idempotency_key:
                row = connection.execute(
                    """
                    SELECT * FROM production_tasks
                    WHERE project=? AND task_type=? AND idempotency_key=?
                    ORDER BY updated_at DESC, task_id DESC LIMIT 1
                    """,
                    (record.project, record.task_type, record.idempotency_key),
                ).fetchone()
                if row is not None:
                    existing = TaskRepository._row_to_record(row)
                    connection.commit()
                    if (
                        existing.scope == record.scope
                        and existing.options == record.options
                    ):
                        return "idempotent", existing
                    return "idempotency_conflict", existing
            placeholders = ",".join("?" for _ in _ACTIVE_STATES)
            row = connection.execute(
                f"""
                SELECT * FROM production_tasks
                WHERE project=? AND status IN ({placeholders})
                ORDER BY updated_at DESC, task_id DESC LIMIT 1
                """,
                (record.project, *_ACTIVE_STATES),
            ).fetchone()
            if row is not None:
                connection.commit()
                return "active", TaskRepository._row_to_record(row)
            TaskRepository._insert_record(connection, record)
            connection.commit()
            RuntimePendingSignal.notify()
            return "created", record
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def load_project_task(project: str, task_id: str) -> Optional[TaskRecord]:
        """Project-local O(1) load。

        - 不扫描其它项目；project 无 DB（create=False）→ 直接返回 None，不产生副作用。
        - 不做 legacy JSON 兜底（生产任务必有项目 DB；未知调用方继续用 load_task）。
        - schema-once（P1-1）保证即使 DB 刚重建，也能在首次访问时自愈。
        - 若 DB 存在但 schema 过期（如恢复旧备份），首次 SELECT 抛
          OperationalError → 回退 create=True（触发 _ensure_schema_once 自愈）
          后重查一次，避免从 Runtime 主循环一路炸出。
        """
        connection = TaskRepository._connect(str(project), create=False)
        if connection is None:
            return None
        try:
            try:
                row = connection.execute(
                    "SELECT * FROM production_tasks WHERE task_id=?", (str(task_id),)
                ).fetchone()
            except sqlite3.OperationalError:
                connection.close()
                connection = TaskRepository._connect(str(project), create=True)
                if connection is None:
                    return None
                row = connection.execute(
                    "SELECT * FROM production_tasks WHERE task_id=?", (str(task_id),)
                ).fetchone()
            return TaskRepository._row_to_record(row) if row is not None else None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def load_task(task_id: str) -> Optional[TaskRecord]:
        identifier = str(task_id or "").strip()
        if not identifier:
            return None
        for project in TaskRepository._project_names():
            connection = TaskRepository._connect(project, create=True)
            if connection is None:
                continue
            try:
                row = connection.execute(
                    "SELECT * FROM production_tasks WHERE task_id=?", (identifier,)
                ).fetchone()
                if row is not None:
                    return TaskRepository._row_to_record(row)
            finally:
                connection.close()
        return TaskRepository._legacy_load(identifier)

    @staticmethod
    def list_tasks(
        project: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        source: Optional[str] = None,
    ) -> list[TaskRecord]:
        records: list[TaskRecord] = []
        for name in TaskRepository._project_names(project):
            connection = TaskRepository._connect(name, create=True)
            if connection is None:
                continue
            try:
                query = "SELECT * FROM production_tasks WHERE 1=1"
                params: list[Any] = []
                if task_type:
                    query += " AND task_type=?"
                    params.append(task_type)
                if status:
                    query += " AND status=?"
                    params.append(status)
                if source:
                    query += " AND source=?"
                    params.append(source)
                for row in connection.execute(query, params):
                    records.append(TaskRepository._row_to_record(row))
            finally:
                connection.close()

        # Preserve legacy supplements and records for projects without a DB.
        try:
            names = os.listdir(TaskRepository.get_task_dir())
        except OSError:
            names = []
        seen = {record.task_id for record in records}
        for filename in names:
            if not filename.endswith(".json"):
                continue
            record = TaskRepository._legacy_load(filename[:-5])
            if record is None or record.task_id in seen:
                continue
            if project and record.project != project:
                continue
            if task_type and record.task_type != task_type:
                continue
            if status and record.status != status:
                continue
            if source and record.source != source:
                continue
            records.append(record)
        return sorted(
            records,
            key=lambda item: (item.updated_at or item.created_at or "", item.task_id),
            reverse=True,
        )

    @staticmethod
    def find_by_idempotency(
        project: str,
        task_type: str,
        idempotency_key: str,
    ) -> Optional[TaskRecord]:
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        for record in TaskRepository.list_tasks(project=project, task_type=task_type):
            if record.idempotency_key == key:
                return record
        return None

    @staticmethod
    def request_control(task_id: str, action: str) -> TaskRecord:
        """Persist a pause/resume/cancel request transactionally."""
        record = TaskRepository.load_task(task_id)
        if record is None or record.task_type not in _RUNTIME_TASK_TYPES:
            raise KeyError(task_id)
        connection = TaskRepository._connect(record.project, create=True)
        if connection is None:
            raise KeyError(task_id)
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM production_tasks WHERE task_id=?", (record.task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            current = TaskRepository._row_to_record(row)
            new_status = current.status
            intent = current.control_intent
            if current.task_type == "export" and action != "cancel":
                raise ValueError("正式导出仅支持 cancel")
            if action == "pause":
                if current.status in {"pausing", "paused"}:
                    pass
                elif current.status == "pending" and not current.owner_id:
                    new_status, intent = "paused", "pause"
                elif current.status == "running":
                    new_status, intent = "pausing", "pause"
                elif current.status == "recovering":
                    # Pause wins over an in-flight engine recovery: the
                    # worker completes safe teardown and confirms paused.
                    intent = "pause"
                else:
                    raise ValueError(current.status)
            elif action == "resume":
                if current.status != "paused":
                    raise ValueError(current.status)
                if current.owner_id:
                    intent = "resume"
                else:
                    new_status, intent = "pending", ""
            elif action == "cancel":
                if current.status in {"cancelled", "done", "error"}:
                    connection.commit()
                    return current
                if current.status in {"interrupted", "needs_attention"}:
                    new_status, intent = "cancelled", ""
                elif current.status in {"pending", "paused"} and not current.owner_id:
                    new_status, intent = "cancelled", ""
                elif current.status == "cancelling":
                    # A second cancel is an idempotent acknowledgement of the
                    # existing request; do not churn version/updated_at.
                    connection.commit()
                    return current
                else:
                    new_status, intent = "cancelling", "cancel"
            else:
                raise ValueError(action)
            finished_at = now if new_status == "cancelled" else current.finished_at
            connection.execute(
                """
                UPDATE production_tasks
                SET status=?,control_intent=?,updated_at=?,finished_at=?,version=version+1
                WHERE task_id=?
                """,
                (new_status, intent, now, finished_at, current.task_id),
            )
            connection.commit()
            # 状态变为 pending（未持有者 resume）→ 通知 runtime 可能有可 claim 任务。
            if new_status == "pending":
                RuntimePendingSignal.notify()
            row = connection.execute(
                "SELECT * FROM production_tasks WHERE task_id=?", (current.task_id,)
            ).fetchone()
            return TaskRepository._row_to_record(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def claim_next_pending(
        owner_id: str,
        task_types: set[str] | frozenset[str] | None = None,
        *,
        signal: RuntimePendingSignal | None = None,
        force: bool = False,
    ) -> Optional[TaskRecord]:
        """Claim the oldest pending task for the singleton runtime.

        ``task_types`` lets the runtime keep the GPU synthesis lane separate
        from CPU/IO export work while retaining one SQLite ownership protocol.

        ``signal`` gates the authoritative full scan: when a signal is supplied
        and it reports no fresh pending marker (and ``force`` is false), the
        method returns ``None`` immediately without scanning any project DB.
        Without a signal (tests / unknown callers) behavior is unchanged.

        Note: the Runtime main loop no longer passes ``signal`` here — it uses
        per-claim-type scan stamps (``_claim_pending`` in production_runtime)
        for dedup and calls this with ``force=True``.  The ``signal``/``force``
        parameters remain part of the public API for direct callers and tests.
        """
        if signal is not None and not force and not signal.may_have_pending():
            return None
        pending = [
            record
            for record in TaskRepository.list_tasks(status="pending")
            if record.task_type in _RUNTIME_TASK_TYPES
            and (task_types is None or record.task_type in task_types)
        ]
        pending.sort(key=lambda item: (item.created_at or "", item.task_id))
        for candidate in pending:
            connection = TaskRepository._connect(candidate.project, create=True)
            if connection is None:
                continue
            try:
                now = _utc_now()
                connection.execute("BEGIN IMMEDIATE")
                changed = connection.execute(
                    """
                    UPDATE production_tasks
                    SET owner_id=?,heartbeat_at=?,updated_at=?,version=version+1
                    WHERE task_id=? AND status='pending' AND owner_id=''
                    """,
                    (owner_id, now, now, candidate.task_id),
                ).rowcount
                connection.commit()
                if changed:
                    row = connection.execute(
                        "SELECT * FROM production_tasks WHERE task_id=?",
                        (candidate.task_id,),
                    ).fetchone()
                    return TaskRepository._row_to_record(row)
            finally:
                connection.close()
        return None

    @staticmethod
    def update_runtime_heartbeat(
        owner_id: str,
        projects: list[str] | None = None,
    ) -> None:
        """Update heartbeat for rows owned by ``owner_id``.

        ``projects`` given → only those project DBs are touched (Runtime knows
        which projects it currently owns).  ``projects`` None → full scan of
        every project (default; used by unknown callers and the periodic
        fallback that preserves orphan-takeover observability).
        """
        now = _utc_now()
        names = projects if projects is not None else TaskRepository._project_names()
        for project in names:
            connection = TaskRepository._connect(project, create=True)
            if connection is None:
                continue
            try:
                with connection:
                    connection.execute(
                        """
                        UPDATE production_tasks SET heartbeat_at=?
                        WHERE owner_id=? AND status IN
                          ('pending','running','pausing','paused','cancelling')
                        """,
                        (now, owner_id),
                    )
            finally:
                connection.close()

    @staticmethod
    def update_startup(
        task_id: str,
        owner_id: str = "",
        *,
        project: str | None = None,
        **fields: Any,
    ) -> Optional[TaskRecord]:
        """Merge startup phase fields into a task row (durable, guarded).

        Ownership guard:
        - ``owner_id`` given → must match the row owner (runtime wrote it) or the
          row must not be claimed yet.
        - ``owner_id`` empty → only allowed while the row is still unclaimed
          (client-side pre-claim writes such as ``task_submitted``).

        ``project`` given → project-local O(1) lookup instead of the full
        ``load_task`` scan (Runtime hot path).  Returns the refreshed record,
        or ``None`` when the row is missing / ownership was lost (the caller
        should treat it as a no-op).
        """
        record = (
            TaskRepository.load_project_task(project, task_id)
            if project
            else TaskRepository.load_task(task_id)
        )
        if record is None:
            return None
        connection = TaskRepository._connect(record.project, create=True)
        if connection is None:
            return None
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM production_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            current = TaskRepository._row_to_record(row)
            if owner_id and current.owner_id and current.owner_id != owner_id:
                connection.commit()
                return current
            if not owner_id and current.owner_id:
                connection.commit()
                return current
            startup = _json_dict(row["startup_json"], {})
            merged = dict(startup)
            for key, value in fields.items():
                if value is not None:
                    merged[key] = value
            now = _utc_now()
            connection.execute(
                "UPDATE production_tasks SET startup_json=?, updated_at=? WHERE task_id=?",
                (json.dumps(merged, ensure_ascii=False), now, task_id),
            )
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM production_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            return TaskRepository._row_to_record(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def update_startup_phase(
        task_id: str,
        phase: str,
        owner_id: str = "",
        *,
        project: str | None = None,
        **extra: Any,
    ) -> Optional[TaskRecord]:
        """Advance the durable startup phase with a fresh phase timestamp."""
        return TaskRepository.update_startup(
            task_id,
            owner_id,
            project=project,
            phase=phase,
            phase_started_at=_utc_now(),
            **extra,
        )

    @staticmethod
    def mark_orphaned_interrupted(new_owner_id: str) -> list[str]:
        """Mark prior claimed tasks interrupted after the caller owns the OS lock."""
        changed: list[str] = []
        now = _utc_now()
        for project in TaskRepository._project_names():
            connection = TaskRepository._connect(project, create=True)
            if connection is None:
                continue
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT task_id FROM production_tasks
                    WHERE status IN ('pending','running','pausing','paused','cancelling')
                      AND owner_id<>'' AND owner_id<>?
                    """,
                    (new_owner_id,),
                ).fetchall()
                ids = [row["task_id"] for row in rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    connection.execute(
                        f"""
                        UPDATE production_tasks
                        SET status='interrupted',control_intent='',updated_at=?,
                            finished_at=?,error_summary=CASE
                              WHEN error_summary='' THEN '生产运行时异常退出'
                              ELSE error_summary END,
                            version=version+1
                        WHERE task_id IN ({placeholders})
                        """,
                        (now, now, *ids),
                    )
                    changed.extend(ids)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return changed

    @staticmethod
    def persist_runtime_state(
        task_id: str,
        owner_id: str,
        *,
        status: str,
        progress: dict[str, Any],
        failed_segment_ids: list[str],
        error_summary: str,
        log_lines: list[str],
        project: str | None = None,
    ) -> Optional[TaskRecord]:
        """Persist a worker snapshot without erasing a stronger control request.

        ``project`` given → project-local O(1) lookup instead of the full
        ``load_task`` scan (Runtime hot path).
        """
        record = (
            TaskRepository.load_project_task(project, task_id)
            if project
            else TaskRepository.load_task(task_id)
        )
        if record is None:
            return None
        connection = TaskRepository._connect(record.project, create=True)
        if connection is None:
            return None
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM production_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            current = TaskRepository._row_to_record(row)
            if (
                current.owner_id != owner_id
                or current.status not in _ACTIVE_STATES
            ):
                connection.commit()
                return current
            effective_status = status
            intent = current.control_intent
            if intent == "cancel" and status not in _TERMINAL_STATES:
                effective_status = "cancelling"
            elif intent == "pause" and status == "running":
                effective_status = "pausing"
            if status == "running" and intent == "resume":
                intent = ""
            if effective_status in _TERMINAL_STATES:
                intent = ""
            started_at = current.started_at
            if status == "running" and not started_at:
                started_at = now
            finished_at = current.finished_at
            if effective_status in _TERMINAL_STATES:
                finished_at = now
            connection.execute(
                """
                UPDATE production_tasks SET
                  status=?,progress_json=?,failed_segment_ids_json=?,
                  error_summary=?,log_lines_json=?,started_at=?,updated_at=?,
                  finished_at=?,heartbeat_at=?,control_intent=?,version=version+1
                WHERE task_id=? AND owner_id=?
                """,
                (
                    effective_status,
                    json.dumps(progress, ensure_ascii=False),
                    json.dumps([str(item) for item in failed_segment_ids], ensure_ascii=False),
                    str(error_summary or "")[:500],
                    json.dumps([str(item) for item in log_lines[-50:]], ensure_ascii=False),
                    started_at, now, finished_at, now, intent, task_id, owner_id,
                ),
            )
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM production_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            return TaskRepository._row_to_record(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def delete_task(task_id: str) -> None:
        record = TaskRepository.load_task(task_id)
        if record is not None and record.task_type in _RUNTIME_TASK_TYPES:
            connection = TaskRepository._connect(record.project, create=True)
            if connection is not None:
                try:
                    with connection:
                        connection.execute(
                            "DELETE FROM production_tasks WHERE task_id=?", (record.task_id,)
                        )
                finally:
                    connection.close()
        path = os.path.join(TaskRepository.get_task_dir(), f"{task_id}.json")
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as exc:
            logger.warning("删除任务记录 %s 失败: %s", task_id, exc)

    @staticmethod
    def cleanup_old_tasks(max_age_days: int = 7) -> int:
        """Delete old terminal tasks; active runtime rows are never removed."""
        cutoff = time.time() - max_age_days * 86400
        cleaned = 0
        for record in TaskRepository.list_tasks():
            if record.status not in _TERMINAL_STATES:
                continue
            timestamp = record.finished_at or record.created_at
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                record_time = parsed.timestamp()
            except (ValueError, TypeError, OSError):
                continue
            if record_time < cutoff:
                TaskRepository.delete_task(record.task_id)
                cleaned += 1
        return cleaned

    @staticmethod
    def _normalize_task_connection(
        connection: sqlite3.Connection,
        *,
        project: str,
    ) -> int:
        """Normalize active rows in one already-open project task database.

        Restore must be able to normalize a database while it is still under
        ``.tmp_restore_*``.  Calling ``save_task`` for those rows would resolve
        the project name through ``ProjectRepository`` and could accidentally
        write a different, already-published project.  Keeping the transaction
        on the supplied connection makes the operation both path-safe and
        atomic.
        """
        now = _utc_now()
        changed = 0
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                """
                SELECT task_id FROM production_tasks
                WHERE project=? AND status IN ('pending','running','pausing','paused','cancelling')
                """,
                (project,),
            ).fetchall()
            if rows:
                ids = [str(row["task_id"]) for row in rows]
                connection.execute(
                    """
                    UPDATE production_tasks
                    SET status='interrupted', owner_id='', heartbeat_at='',
                        control_intent='', finished_at=?, updated_at=?,
                        error_summary=CASE
                          WHEN error_summary='' THEN '从项目备份恢复后需重新启动任务'
                          ELSE error_summary END,
                        version=version+1
                    WHERE project=? AND status IN
                      ('pending','running','pausing','paused','cancelling')
                    """,
                    (now, now, project),
                )
                changed = len(ids)
            connection.commit()
            return changed
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def normalize_restored_task_database(
        project_dir: str,
        *,
        project: str,
    ) -> int:
        """Normalize a copied task DB rooted at ``project_dir``.

        ``project_dir`` may be a temporary restore tree that is not visible to
        ``ProjectRepository`` yet.  No database is created when the backup did
        not contain one; malformed or unreadable databases deliberately raise
        so restore can discard the temporary tree instead of publishing a
        partially normalized project.
        """
        root = os.path.abspath(str(project_dir or ""))
        if not root or not os.path.isdir(root):
            raise FileNotFoundError(f"恢复项目目录不存在: {project_dir}")
        config_dir = project_paths.project_dir(root, "config", create=False)
        database = os.path.join(config_dir, _DB_FILENAME)
        if not os.path.isfile(database):
            return 0
        connection = sqlite3.connect(database, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            # Do not silently repair a malformed copied database here.  The
            # schema must already be present for task normalization to be
            # trustworthy; failures abort restore before publication.
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='production_tasks'"
            ).fetchone()
            if tables is None:
                raise sqlite3.DatabaseError("恢复任务数据库缺少 production_tasks 表")
            return TaskRepository._normalize_task_connection(
                connection,
                project=str(project),
            )
        finally:
            connection.close()

    @staticmethod
    def normalize_restored_tasks(
        project: str,
        *,
        project_dir: str | None = None,
    ) -> int:
        """Turn copied active runtime rows into recoverable interruptions.

        A backup can contain a stale owner/heartbeat from another machine.  A
        restored project must never advertise those rows as live work; the
        next explicit retry can create a fresh attempt under the new runtime.
        When ``project_dir`` is supplied, normalization happens directly in
        that tree (normally before an atomic restore publish).
        """
        if project_dir is not None:
            return TaskRepository.normalize_restored_task_database(
                project_dir,
                project=str(project),
            )
        database = TaskRepository.get_database_path(str(project), create=False)
        if database and os.path.isfile(database):
            connection = sqlite3.connect(database, timeout=10.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            try:
                return TaskRepository._normalize_task_connection(
                    connection,
                    project=str(project),
                )
            finally:
                connection.close()

        # Legacy task records have no project-local SQLite database.  Keep the
        # compatibility path for those projects, but use the same repository
        # save behavior as before.
        now = _utc_now()
        changed = 0
        for record in TaskRepository.list_tasks(project=project):
            if record.status not in _ACTIVE_STATES:
                continue
            record.status = "interrupted"
            record.owner_id = ""
            record.heartbeat_at = ""
            record.control_intent = ""
            record.finished_at = now
            record.updated_at = now
            record.error_summary = record.error_summary or "从项目备份恢复后需重新启动任务"
            record.version += 1
            TaskRepository.save_task(record)
            changed += 1
        return changed


__all__ = ["TaskRecord", "TaskRepository", "RuntimePendingSignal"]
