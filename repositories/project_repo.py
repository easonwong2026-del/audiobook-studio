"""ProjectRepository：项目 CRUD + 原子写 project.json + 快照集成。

所有项目磁盘操作均集中于此，全部方法为 @staticmethod，无实例状态。

WORKSPACE_ROOT / LEGACY_ROOT 为类变量，测试可通过 monkeypatch.setattr 覆盖。
初值延迟到首次调用时通过 lib.config 初始化，避免循环导入。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field, fields
from typing import ClassVar, Optional, TextIO

from lib import chapter_identity, project_paths, script_loader
from lib.snapshot import ProjectSnapshot
from lib.types import ProjectMeta, ProjectSummary

from ._atomic import atomic_write as _atomic_write
from .exceptions import ProjectNotFoundError

logger = logging.getLogger(__name__)

_PROJECT_MARKERS = (
    "project.json",
    "structured_script.json",
    "voice_bindings.json",
)

# 根级 marker 文件名 → project_paths 文件级 key（v3 起这些 JSON 移入
# 99_系统数据/配置/，判定必须经 resolver 定位，禁止在根目录硬查）。
_MARKER_FILE_KEYS = {
    "project.json": "project_meta",
    "structured_script.json": "structured_script",
    "voice_bindings.json": "voice_bindings",
}

_RETIRED_PROJECT_METADATA = frozenset(
    {
        "project_id",
        "project_kind",
        "parent_project_id",
        "chapter_title",
        "chapter_order",
        "relation_status",
    }
)


def sanitize_project_name(value: str) -> str:
    """Return the single canonical project-directory name used by UI and storage."""
    text = str(value or "").strip()
    safe = "".join(
        character
        if character.isalnum() or character in {" ", "-", "_", "."}
        else "_"
        for character in text
    )
    safe = " ".join(safe.split()).strip()
    safe = safe.rstrip(".")
    while "__" in safe:
        safe = safe.replace("__", "_")
    if not safe or safe in {".", ".."}:
        raise ValueError("项目名称不包含可用字符")
    return safe


@dataclass(frozen=True)
class ProjectSlotInspection:
    """Structured inspection result for one project directory slot."""

    name: str
    status: str
    path: str
    location: str
    missing_files: list[str] = field(default_factory=list)
    invalid_files: list[str] = field(default_factory=list)
    modified_at: Optional[float] = None


@dataclass
class SegmentStatusBatch:
    """Incremental project-status writer for long production runs.

    The writer loads ``project.json`` once, updates counters in O(1) per
    segment and appends a crash-recovery journal.  Chapter boundaries call
    :meth:`checkpoint` to fsync that journal without rewriting the whole
    project JSON; the task boundary calls :meth:`flush` to consolidate one
    final snapshot.  ``close`` is an alias used from ``finally`` blocks.
    """

    project_dir: str
    meta: ProjectMeta
    flush_every: int = 100
    _dirty: int = 0
    _journal_file: TextIO | None = field(default=None, init=False, repr=False)

    _COUNTER_BY_STATUS: ClassVar[dict[str, str]] = {
        "done": "completed_count",
        "failed": "failed_count",
        "pending": "pending_count",
    }

    def update(self, segment_id: str, status: str) -> None:
        segment_id = str(segment_id)
        status = str(status)
        previous = str(self.meta.segments_status.get(segment_id, "pending"))
        if previous == status:
            return
        previous_counter = self._COUNTER_BY_STATUS.get(previous)
        next_counter = self._COUNTER_BY_STATUS.get(status)
        if previous_counter:
            setattr(
                self.meta,
                previous_counter,
                max(int(getattr(self.meta, previous_counter, 0) or 0) - 1, 0),
            )
        if next_counter:
            setattr(
                self.meta,
                next_counter,
                int(getattr(self.meta, next_counter, 0) or 0) + 1,
            )
        self.meta.segments_status[segment_id] = status
        self.meta.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if self._journal_file is None:
            path = ProjectRepository._status_journal_path(self.project_dir)
            self._journal_file = open(path, "a", encoding="utf-8", buffering=1)
        payload = {
            "segment_id": segment_id,
            "status": status,
            "updated_at": self.meta.updated_at,
        }
        self._journal_file.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self._dirty += 1
        if self.flush_every > 0 and self._dirty >= int(self.flush_every):
            self.flush()

    def checkpoint(self) -> None:
        """Durably flush the journal without rewriting the full project JSON."""
        if not self._dirty:
            return
        if self._journal_file is not None:
            self._journal_file.flush()
            os.fsync(self._journal_file.fileno())

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            self.checkpoint()
        finally:
            if self._journal_file is not None:
                self._journal_file.close()
                self._journal_file = None
        ProjectRepository._save_meta(self.project_dir, self.meta)
        ProjectRepository._clear_status_journal(self.project_dir)
        self._dirty = 0

    close = flush


class ProjectRepository:
    """项目仓库：项目 CRUD + 原子写 + 快照加载。

    全部 @staticmethod，无实例状态。
    """

    # WORKSPACE_ROOT 保持为模块级可变变量（测试用 monkeypatch 覆盖）；
    # 初值从配置读取，使项目默认存到程序目录之外。
    # 注意：lib.config 的导入延迟到首次方法调用时，避免与 lib/config.py → repositories 的循环导入。
    WORKSPACE_ROOT: ClassVar[Optional[str]] = None
    # 旧版项目目录（程序目录内），仅用于向后兼容打开，不参与新建。
    LEGACY_ROOT: ClassVar[Optional[str]] = None
    _INITIALIZED: ClassVar[bool] = False

    @classmethod
    def _ensure_roots(cls):
        """确保 WORKSPACE_ROOT / LEGACY_ROOT 已初始化。"""
        if cls._INITIALIZED:
            return
        # Tests and compatibility integrations historically assign both roots
        # directly.  Treat that explicit pair as authoritative instead of
        # silently replacing it from global config on the next operation.
        if cls.WORKSPACE_ROOT is not None and cls.LEGACY_ROOT is not None:
            cls._INITIALIZED = True
            return
        from lib import config as _cfg
        cls.WORKSPACE_ROOT = _cfg.get_projects_root()
        cls.LEGACY_ROOT = _cfg.get_legacy_dir()
        cls._INITIALIZED = True

    # --- 内部工具方法 ---

    @staticmethod
    def _resolve_dir(name: str) -> str:
        """返回项目实际目录：优先新数据目录，其次 legacy 目录，否则落在新目录。"""
        ProjectRepository._ensure_roots()
        ws = ProjectRepository.WORKSPACE_ROOT or ""
        lg = ProjectRepository.LEGACY_ROOT or ""
        new = os.path.join(ws, name)
        if ws and os.path.isdir(new):
            return new
        old = os.path.join(lg, name)
        if lg and os.path.isdir(old):
            return old
        return new

    @staticmethod
    def _meta_path(project_dir: str, *, version: int | None = None) -> str:
        """权威 project.json 路径（v3 → 99_系统数据/配置/project.json）。

        ``version`` 用于创建期（manifest 尚未落盘时按 meta.storage_version 解析）；
        缺省按 ``detect_storage_version`` 自动判定。
        """
        if version is not None:
            return project_paths.project_file(
                project_dir, "project_meta", prefer_version=max(int(version), 1)
            )
        return project_paths.project_file(project_dir, "project_meta")

    @staticmethod
    def _load_meta(project_dir: str) -> ProjectMeta:
        """从 project_dir 加载 ProjectMeta，并忽略已退休的关系字段。"""
        with open(ProjectRepository._meta_path(project_dir), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("project.json 顶层必须是对象")
        known = {item.name for item in fields(ProjectMeta)}
        meta = ProjectMeta(**{
            key: value for key, value in data.items() if key in known
        })
        if _RETIRED_PROJECT_METADATA.intersection(data):
            # Historical relation metadata is read-only compatibility data.
            # Do not let opening/scanning such a project rewrite the source file.
            return meta
        ProjectRepository._repair_meta(project_dir, meta)
        ProjectRepository._replay_status_journal(project_dir, meta)
        return meta

    @staticmethod
    def _status_journal_path(project_dir: str) -> str:
        return project_paths.project_file(
            project_dir, "segment_status_journal", create=True
        )

    @staticmethod
    def _replay_status_journal(project_dir: str, meta: ProjectMeta) -> None:
        """Overlay uncheckpointed segment states onto an in-memory snapshot."""
        path = ProjectRepository._status_journal_path(project_dir)
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as file:
                events = [json.loads(line) for line in file if line.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("读取段状态恢复日志失败: %s", exc)
            return
        for event in events:
            if not isinstance(event, dict):
                continue
            segment_id = str(event.get("segment_id") or "")
            status = str(event.get("status") or "")
            if not segment_id or not status:
                continue
            previous = str(meta.segments_status.get(segment_id, "pending"))
            if previous == status:
                continue
            previous_counter = SegmentStatusBatch._COUNTER_BY_STATUS.get(previous)
            next_counter = SegmentStatusBatch._COUNTER_BY_STATUS.get(status)
            if previous_counter:
                setattr(
                    meta,
                    previous_counter,
                    max(int(getattr(meta, previous_counter, 0) or 0) - 1, 0),
                )
            if next_counter:
                setattr(
                    meta,
                    next_counter,
                    int(getattr(meta, next_counter, 0) or 0) + 1,
                )
            meta.segments_status[segment_id] = status
            meta.updated_at = str(event.get("updated_at") or meta.updated_at)

    @staticmethod
    def _clear_status_journal(project_dir: str) -> None:
        path = ProjectRepository._status_journal_path(project_dir)
        if not os.path.isfile(path):
            return
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("清理段状态恢复日志失败: %s", exc)

    @staticmethod
    def _repair_meta(project_dir: str, meta: ProjectMeta) -> None:
        """自动修复：确保 segments_status 的 key 与 structured_script.json 的 seg_id 一致。"""
        script_path = project_paths.project_file(project_dir, "structured_script")
        if not os.path.isfile(script_path):
            return
        with open(script_path, encoding="utf-8") as f:
            script = script_loader.canonicalize_collections(json.load(f))

        # 收集 JSON 中所有段 ID
        json_ids = set()
        _, chapters = script_loader.resolve_collections(script)
        for ch in chapters:
            for seg in ch.get("segments", []):
                json_ids.add(seg["id"])

        old_ids = set(meta.segments_status.keys())
        if json_ids == old_ids:
            return

        logger.info("Repairing segments_status: %d → %d IDs", len(old_ids), len(json_ids))
        # 重建：保留 done 状态，其他重置为 pending
        new_status = {}
        for sid in json_ids:
            old_st = meta.segments_status.get(sid, "pending")
            # 用参数感知缓存键判定该段是否已真正合成（兼容历史裸文件）
            if old_st == "done" and ProjectRepository._has_segment_wav(project_dir, sid):
                new_status[sid] = "done"
            else:
                new_status[sid] = "pending"

        meta.segments_status = new_status
        meta.total_segments = len(json_ids)
        meta.completed_count = sum(1 for v in new_status.values() if v == "done")
        meta.failed_count = sum(1 for v in new_status.values() if v == "failed")
        meta.pending_count = meta.total_segments - meta.completed_count - meta.failed_count
        ProjectRepository._save_meta(project_dir, meta)

    @staticmethod
    def _has_segment_wav(project_dir: str, seg_id: str) -> bool:
        """检查段是否有对应的 wav 文件（兼容参数感知缓存键）。

        复用 lib/segment_cache 的逻辑，延迟导入避免循环依赖。
        """
        from lib import segment_cache
        seg_dir = project_paths.project_dir(project_dir, "segments")
        return segment_cache.has_segment_wav(seg_dir, seg_id)

    @staticmethod
    def _save_meta(project_dir: str, meta: ProjectMeta) -> None:
        """原子写 project.json。

        v3：权威副本在 ``99_系统数据/配置/project.json``（无根镜像）；
        v2：根 ``project.json`` 权威 + ``01_项目配置/project.json`` 镜像；
        v1：根 ``project.json`` 权威（无镜像）。
        """
        path = ProjectRepository._meta_path(project_dir, version=meta.storage_version)
        payload = {
            "project_name": meta.project_name,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
            "total_chapters": meta.total_chapters,
            "total_segments": meta.total_segments,
            "completed_count": meta.completed_count,
            "failed_count": meta.failed_count,
            "pending_count": meta.pending_count,
            "segments_status": meta.segments_status,
            "voice_bindings_path": meta.voice_bindings_path,
            "storage_version": meta.storage_version,
            "directories": meta.directories,
            "source_file": meta.source_file,
        }
        # Preserve already-present retired metadata without interpreting it.
        # New projects never receive these keys, and normal writes do not
        # invent or update their values.
        try:
            with open(path, encoding="utf-8") as file:
                existing = json.load(file)
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict):
            payload.update(
                {
                    key: existing[key]
                    for key in _RETIRED_PROJECT_METADATA
                    if key in existing
                }
            )
        _atomic_write(path, payload)
        # v2 项目保留「根权威 + 配置目录镜像」的历史行为；v3 已无根副本。
        if meta.storage_version == 2:
            config_dir = project_paths.project_dir(project_dir, "config", create=True)
            config_path = os.path.join(config_dir, "project.json")
            try:
                shutil.copy2(path, config_path)
            except OSError as exc:
                logger.warning("同步项目配置副本失败: %s", exc)

    # --- 公开 API ---

    @staticmethod
    def scan_projects() -> list[str]:
        """扫描所有项目名（新数据目录 + legacy 目录合并，新目录优先去重）。

        Returns:
            排序后的项目名列表。
        """
        ProjectRepository._ensure_roots()
        names = set()
        hidden = ProjectRepository._hidden_project_names()
        ws = ProjectRepository.WORKSPACE_ROOT or ""
        lg = ProjectRepository.LEGACY_ROOT or ""
        for root in (ws, lg):
            if root and os.path.isdir(root):
                names.update(
                    d for d in os.listdir(root)
                    if d != ".trash"
                    and d not in hidden
                    and ProjectRepository._is_valid_project_dir(os.path.join(root, d))
                )
        return sorted(names)

    @staticmethod
    def _hidden_project_names() -> set[str]:
        """Read the optional list-only catalog without making it authoritative."""
        try:
            from lib import config

            path = os.path.join(config.get_data_dir(), ".project_catalog.json")
            with open(path, encoding="utf-8") as file:
                data = json.load(file)
            hidden = data.get("hidden_projects", []) if isinstance(data, dict) else []
            return {str(value) for value in hidden if isinstance(value, (str, int))}
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return set()

    @staticmethod
    def inspect_project_slot(name: str) -> ProjectSlotInspection:
        """Inspect a project name without changing or deleting user data."""
        ProjectRepository._ensure_roots()
        safe_name = sanitize_project_name(name)
        workspace = ProjectRepository.WORKSPACE_ROOT or ""
        legacy = ProjectRepository.LEGACY_ROOT or ""
        workspace_path = os.path.join(workspace, safe_name)
        legacy_path = os.path.join(legacy, safe_name)

        if workspace and os.path.lexists(workspace_path):
            return ProjectRepository._inspect_existing_slot(
                safe_name, workspace_path, "workspace"
            )
        if legacy and os.path.lexists(legacy_path):
            inspected = ProjectRepository._inspect_existing_slot(
                safe_name, legacy_path, "legacy"
            )
            return ProjectSlotInspection(
                name=inspected.name,
                status="legacy",
                path=inspected.path,
                location=inspected.location,
                missing_files=inspected.missing_files,
                invalid_files=inspected.invalid_files,
                modified_at=inspected.modified_at,
            )
        return ProjectSlotInspection(
            name=safe_name,
            status="available",
            path=workspace_path,
            location="workspace",
        )

    @staticmethod
    def _inspect_existing_slot(
        name: str,
        path: str,
        location: str,
    ) -> ProjectSlotInspection:
        try:
            modified_at = os.path.getmtime(path)
        except OSError:
            modified_at = None

        if name.startswith(".tmp_"):
            return ProjectSlotInspection(
                name=name,
                status="temporary",
                path=path,
                location=location,
                modified_at=modified_at,
            )
        if not os.path.isdir(path):
            return ProjectSlotInspection(
                name=name,
                status="corrupted",
                path=path,
                location=location,
                invalid_files=["项目路径不是目录"],
                modified_at=modified_at,
            )

        missing = [
            marker
            for marker in _PROJECT_MARKERS
            if not os.path.isfile(
                project_paths.project_file(path, _MARKER_FILE_KEYS[marker])
            )
        ]
        if missing:
            return ProjectSlotInspection(
                name=name,
                status="incomplete",
                path=path,
                location=location,
                missing_files=missing,
                modified_at=modified_at,
            )

        invalid: list[str] = []
        parsed: dict[str, object] = {}
        for marker in _PROJECT_MARKERS:
            try:
                with open(
                    project_paths.project_file(path, _MARKER_FILE_KEYS[marker]),
                    encoding="utf-8",
                ) as file:
                    parsed[marker] = json.load(file)
            except (OSError, UnicodeError, json.JSONDecodeError):
                invalid.append(marker)

        project_meta = parsed.get("project.json")
        if isinstance(project_meta, dict):
            meta_name = project_meta.get("project_name")
            if meta_name != name:
                invalid.append("project.json:project_name")
        elif "project.json" not in invalid:
            invalid.append("project.json")

        raw_script = parsed.get("structured_script.json")
        if isinstance(raw_script, dict):
            try:
                from lib import script_loader

                errors = script_loader.validate_script(
                    script_loader.from_dict(raw_script)
                )
                if errors:
                    invalid.append("structured_script.json")
            except Exception:
                invalid.append("structured_script.json")
        elif "structured_script.json" not in invalid:
            invalid.append("structured_script.json")

        bindings = parsed.get("voice_bindings.json")
        if not isinstance(bindings, dict) and "voice_bindings.json" not in invalid:
            invalid.append("voice_bindings.json")

        invalid = list(dict.fromkeys(invalid))
        return ProjectSlotInspection(
            name=name,
            status="corrupted" if invalid else "valid",
            path=path,
            location=location,
            invalid_files=invalid,
            modified_at=modified_at,
        )

    @staticmethod
    def list_abnormal_projects() -> list[ProjectSlotInspection]:
        """List visible workspace remnants excluded from the normal bookshelf."""
        ProjectRepository._ensure_roots()
        workspace = ProjectRepository.WORKSPACE_ROOT or ""
        if not workspace or not os.path.isdir(workspace):
            return []
        inspections: list[ProjectSlotInspection] = []
        for name in sorted(os.listdir(workspace)):
            if name == ".trash":
                continue
            inspection = ProjectRepository._inspect_existing_slot(
                name,
                os.path.join(workspace, name),
                "workspace",
            )
            if (
                inspection.location == "workspace"
                and inspection.status in {"incomplete", "corrupted", "temporary"}
            ):
                inspections.append(inspection)
        return inspections

    @staticmethod
    def archive_orphan_project(name: str) -> str:
        """Move an incomplete/corrupted/temp workspace entry into data trash."""
        ProjectRepository._ensure_roots()
        raw_name = str(name or "").strip()
        if (
            not raw_name
            or os.path.basename(raw_name) != raw_name
            or raw_name in {".", ".."}
        ):
            raise ValueError("残留项目名称无效")
        workspace = ProjectRepository.WORKSPACE_ROOT or ""
        exact_path = os.path.join(workspace, raw_name)
        inspection = (
            ProjectRepository._inspect_existing_slot(
                raw_name,
                exact_path,
                "workspace",
            )
            if workspace and os.path.lexists(exact_path)
            else ProjectRepository.inspect_project_slot(raw_name)
        )
        if inspection.location != "workspace" or inspection.status not in {
            "incomplete",
            "corrupted",
            "temporary",
        }:
            raise ValueError(
                "仅可归档工作区中的不完整、损坏或临时项目；合法及 Legacy 项目不受影响"
            )

        data_dir = os.path.dirname(os.path.normpath(workspace))
        trash_root = os.path.join(data_dir, ".trash", "projects")
        os.makedirs(trash_root, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        target = os.path.join(trash_root, f"{inspection.name}_{timestamp}")
        if os.path.lexists(target):
            target = f"{target}_{uuid.uuid4().hex[:8]}"
        try:
            os.replace(inspection.path, target)
        except OSError:
            shutil.move(inspection.path, target)
        return target

    @staticmethod
    def _is_valid_project_dir(project_dir: str) -> bool:
        """检查目录是否为合法项目（排除 .tmp_ 目录，检查三个必需文件）。"""
        if not os.path.isdir(project_dir):
            return False
        name = os.path.basename(project_dir)
        if name.startswith(".tmp_"):
            return False
        for marker in _PROJECT_MARKERS:
            if not os.path.isfile(
                project_paths.project_file(project_dir, _MARKER_FILE_KEYS[marker])
            ):
                return False
        return True

    @staticmethod
    def cleanup_stale_project_temp_dirs(max_age_seconds: int = 86400) -> int:
        """清理 WORKSPACE_ROOT 下过期的 .tmp_ 临时项目目录。

        Args:
            max_age_seconds: 过期阈值，默认 24 小时。

        Returns:
            成功删除的目录数量。
        """
        ProjectRepository._ensure_roots()
        ws = ProjectRepository.WORKSPACE_ROOT or ""
        return ProjectRepository._cleanup_stale_tmp_dirs(ws, max_age_seconds)

    @staticmethod
    def _cleanup_stale_tmp_dirs(root: str, max_age_seconds: int = 86400) -> int:
        """清理指定根目录下过期的 .tmp_ 临时目录。"""
        removed = 0
        if not root or not os.path.isdir(root):
            return 0
        now = time.time()
        for name in os.listdir(root):
            if not name.startswith(".tmp_"):
                continue
            tmp_dir = os.path.join(root, name)
            if not os.path.isdir(tmp_dir):
                continue
            try:
                age = now - os.path.getmtime(tmp_dir)
            except OSError:
                logger.debug("跳过无法读取修改时间的临时目录: %s", tmp_dir)
                continue
            if age >= max_age_seconds:
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=False)
                    removed += 1
                except OSError as exc:
                    logger.warning("清理临时目录失败: %s (%s)", tmp_dir, exc)
        return removed

    @staticmethod
    def load_project(name: str) -> tuple[ProjectMeta, dict, dict]:
        """加载项目，返回 (meta, script, voice_bindings)。向后兼容 legacy 目录。

        Args:
            name: 项目名。

        Returns:
            (ProjectMeta, script_dict, bindings_dict) 元组。

        Raises:
            ProjectNotFoundError: 项目不存在时抛出。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        if not os.path.isdir(project_dir):
            raise ProjectNotFoundError(f"项目 '{name}' 不存在")

        meta = ProjectRepository._load_meta(project_dir)
        with open(project_paths.project_file(project_dir, "structured_script"),
                  encoding="utf-8") as f:
            script = script_loader.canonicalize_collections(json.load(f))
        with open(project_paths.project_file(project_dir, "voice_bindings"),
                  encoding="utf-8") as f:
            bindings = json.load(f)

        return meta, script, bindings

    @staticmethod
    def load_snapshot(name: str) -> ProjectSnapshot:
        """加载项目并产出 ProjectSnapshot。

        Args:
            name: 项目名。

        Returns:
            ProjectSnapshot 实例。
        """
        meta, script, bd = ProjectRepository.load_project(name)
        project_dir = ProjectRepository._resolve_dir(name)
        return ProjectSnapshot.build(name, meta, script, bd, project_dir)

    @staticmethod
    def create_project(name: str, script_path: str) -> str:
        """创建项目目录结构，复制 JSON，写 project.json（始终落在新数据目录）。

        Args:
            name: 项目名。
            script_path: 剧本 JSON 文件路径。

        Returns:
            项目名。

        Raises:
            FileExistsError: 项目已存在时抛出。
        """
        with open(script_path, encoding="utf-8") as file:
            raw_script = json.load(file)
        return ProjectRepository._create_project_from_raw(
            name,
            raw_script,
            source_file_path=script_path,
        )

    @staticmethod
    def create_project_from_data(name: str, raw_script: dict) -> str:
        """Create a project from an already-loaded JSON object.

        File and in-memory creation share the same canonical layout,
        normalization, metadata and atomic publish path.  The in-memory path
        only changes how the original source copy is materialized.
        """
        return ProjectRepository._create_project_from_raw(name, raw_script)

    @staticmethod
    def _create_project_from_raw(
        name: str,
        raw_script: dict,
        *,
        source_file_path: str | None = None,
    ) -> str:
        """Build one project in a temporary directory and publish it atomically.

        新项目一律创建 **纯 v3 布局**（``ensure_layout(compatibility=False)``，
        不建任何 legacy 空目录 / junction）；根目录只允许 4 个一级目录。
        """
        ProjectRepository._ensure_roots()
        name = sanitize_project_name(name)
        ws = ProjectRepository.WORKSPACE_ROOT or ""
        project_dir = os.path.join(ws, name)
        if os.path.lexists(project_dir):
            raise FileExistsError(f"项目 '{name}' 已存在")
        os.makedirs(ws, exist_ok=True)
        tmp_dir = os.path.join(ws, f".tmp_{name}_{uuid.uuid4().hex}")
        try:
            paths = project_paths.ensure_layout(
                tmp_dir, prefer_version=project_paths.STORAGE_VERSION, compatibility=False
            )
            normalized_script = chapter_identity.normalize_script_for_project(raw_script)
            script_path = project_paths.project_file(
                tmp_dir, "structured_script", create=True,
                prefer_version=project_paths.STORAGE_VERSION,
            )
            with open(script_path, "w", encoding="utf-8") as f:
                json.dump(normalized_script, f, ensure_ascii=False, indent=2)

            source_name = chapter_identity.safe_filename(
                os.path.basename(source_file_path) if source_file_path else "structured_script.json",
                "structured_script.json",
            )
            source_target = os.path.join(paths["source_book"], source_name)
            if source_file_path:
                # Keep the existing file-import copy behavior (and its
                # Windows-safe metadata) while sharing the rest of creation.
                shutil.copy2(source_file_path, source_target)
            else:
                with open(source_target, "w", encoding="utf-8") as f:
                    json.dump(normalized_script, f, ensure_ascii=False, indent=2)
            for index, chapter in enumerate(normalized_script.get("chapters", [])):
                if not isinstance(chapter, dict):
                    continue
                chapter_path = os.path.join(
                    paths["chapter_data"],
                    f"{chapter_identity.chapter_file_stem(chapter, index, len(normalized_script.get('chapters', [])))}.json",
                )
                with open(chapter_path, "w", encoding="utf-8") as f:
                    json.dump(chapter, f, ensure_ascii=False, indent=2)

            parsed_script = script_loader.from_dict(normalized_script)
            total_segments = sum(len(ch.segments) for ch in parsed_script.chapters)
            meta = ProjectMeta(project_name=name, created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                               updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                               total_chapters=len(parsed_script.chapters), total_segments=total_segments,
                               pending_count=total_segments,
                               segments_status={seg.id: "pending" for ch in parsed_script.chapters for seg in ch.segments},
                               storage_version=project_paths.STORAGE_VERSION,
                               directories=project_paths.layout_manifest(tmp_dir),
                               source_file=os.path.relpath(source_target, tmp_dir))
            # 先落权威 project.json（storage_version=3），后续 JSON 写入即可按 v3 解析。
            ProjectRepository._save_meta(tmp_dir, meta)
            bindings = {"bindings": {n: None for n in parsed_script.voices},
                        "bound_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "verified": []}
            ProjectRepository.save_bindings(tmp_dir, bindings)
            if os.path.lexists(project_dir):
                raise FileExistsError(f"项目 '{name}' 已存在")
            os.replace(tmp_dir, project_dir)
            return name
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    @staticmethod
    def delete_project(name: str) -> None:
        """删除项目。

        Args:
            name: 项目名。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        if os.path.isdir(project_dir):
            shutil.rmtree(project_dir)

    @staticmethod
    def archive_project(name: str) -> str:
        """Move a project to the recoverable data-root trash area."""
        from .project_storage_repo import ProjectStorageRepository

        return ProjectStorageRepository.archive_project(name)

    @staticmethod
    def permanently_delete_project(archive_id: str) -> None:
        """Permanently delete a project only by its trash archive id."""
        from .project_storage_repo import ProjectStorageRepository

        ProjectStorageRepository.permanently_delete_project(archive_id)

    @staticmethod
    def list_archived_projects() -> list[dict]:
        """List projects that are already in the recoverable trash."""
        from .project_storage_repo import ProjectStorageRepository

        return [item.as_dict() for item in ProjectStorageRepository.list_archived_projects()]

    @staticmethod
    def restore_archived_project(archive_id: str) -> dict:
        """Restore one trash entry without overwriting an active project."""
        from .project_storage_repo import ProjectStorageRepository

        return ProjectStorageRepository.restore_archived_project(archive_id)

    @staticmethod
    def permanently_delete_archived_project(archive_id: str) -> None:
        """Permanently delete exactly one trash entry."""
        from .project_storage_repo import ProjectStorageRepository

        ProjectStorageRepository.permanently_delete_archived_project(archive_id)

    @staticmethod
    def remove_project_from_list(name: str) -> None:
        """Hide a project from the bookshelf while retaining its local files."""
        from .project_storage_repo import ProjectStorageRepository

        ProjectStorageRepository.remove_from_list(name)

    @staticmethod
    def restore_project_to_list(name: str) -> None:
        """Make a list-only hidden project visible again."""
        from .project_storage_repo import ProjectStorageRepository

        ProjectStorageRepository.restore_to_list(name)

    @staticmethod
    def check_project_integrity(name: str) -> dict:
        """Run a non-destructive structured integrity check for one project."""
        from .project_storage_repo import ProjectStorageRepository

        return ProjectStorageRepository.check_project_integrity(name)

    @staticmethod
    def get_project_dir(name: str) -> str:
        """返回项目目录绝对路径（解析 legacy，便于读取既有项目产物）。

        Args:
            name: 项目名。

        Returns:
            项目目录绝对路径。
        """
        return ProjectRepository._resolve_dir(name)

    @staticmethod
    def update_segment_status(name: str, seg_id: str, status: str) -> None:
        """更新单段状态并写入 project.json。

        Args:
            name: 项目名。
            seg_id: 段 ID。
            status: 新状态（"pending" / "done" / "failed" 等）。
        """
        writer = ProjectRepository.segment_status_batch(name, flush_every=1)
        writer.update(seg_id, status)
        writer.flush()

    @staticmethod
    def segment_status_batch(
        name: str,
        *,
        flush_every: int = 100,
    ) -> SegmentStatusBatch:
        """Return a task-local incremental segment status writer."""
        project_dir = ProjectRepository._resolve_dir(name)
        return SegmentStatusBatch(
            project_dir=project_dir,
            meta=ProjectRepository._load_meta(project_dir),
            flush_every=max(int(flush_every or 0), 0),
        )

    @staticmethod
    def invalidate_done_segments(name: str, segment_ids: list[str]) -> int:
        """Reset only completed segments in ``segment_ids`` to ``pending``.

        Voice Cast force-rebinds use one atomic metadata write so unrelated
        roles keep their completed state and progress counters remain exact.
        """
        project_dir = ProjectRepository._resolve_dir(name)
        meta = ProjectRepository._load_meta(project_dir)
        targets = {str(value) for value in (segment_ids or [])}
        invalidated = 0
        for segment_id in targets:
            if meta.segments_status.get(segment_id) == "done":
                meta.segments_status[segment_id] = "pending"
                invalidated += 1
        if invalidated:
            meta.completed_count = sum(value == "done" for value in meta.segments_status.values())
            meta.failed_count = sum(value == "failed" for value in meta.segments_status.values())
            meta.pending_count = sum(value == "pending" for value in meta.segments_status.values())
            meta.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            ProjectRepository._save_meta(project_dir, meta)
        return invalidated

    @staticmethod
    def update_project_meta(name: str, **updates) -> None:
        """更新 project.json 的顶层字段。

        Args:
            name: 项目名。
            **updates: 要更新的键值对。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        meta = ProjectRepository._load_meta(project_dir)
        for key, value in updates.items():
            if hasattr(meta, key):
                setattr(meta, key, value)
        meta.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        ProjectRepository._save_meta(project_dir, meta)

    @staticmethod
    def list_project_summaries() -> list[ProjectSummary]:
        """扫描全部项目并产出统一轻量摘要（书架 / Dropdown / 搜索共用）。

        只做**一次** ``scan_projects()``；逐项目读 ``project.json`` meta 与
        ``structured_script.json`` 的 ``meta``（只取 title/author，不解析章节结构）。
        单项目异常以占位字段继续，坏项目不拖垮整个书架（对齐
        ``ProjectService.list_project_summaries`` 的容错惯例）。

        Returns:
            ``ProjectSummary`` 列表，按项目名排序。
        """
        from datetime import datetime

        summaries: list[ProjectSummary] = []
        for name in ProjectRepository.scan_projects():
            try:
                project_dir = ProjectRepository._resolve_dir(name)
                meta = ProjectRepository._load_meta(project_dir)
                title, author = ProjectRepository._script_meta(project_dir, name)
                modified_at: str | None = None
                try:
                    modified_at = datetime.fromtimestamp(
                        os.path.getmtime(project_dir)
                    ).isoformat(timespec="seconds")
                except OSError:
                    pass
                total = int(getattr(meta, "total_segments", 0) or 0)
                done = int(getattr(meta, "completed_count", 0) or 0)
                failed = int(getattr(meta, "failed_count", 0) or 0)
                status = ProjectRepository._project_status(total, done, failed)
                progress = (done / total) if total else 0.0
                summaries.append(ProjectSummary(
                    project_name=name,
                    title=title,
                    author=author,
                    chapters=int(getattr(meta, "total_chapters", 0) or 0),
                    segments=total,
                    completed=done,
                    modified_at=modified_at,
                    failed=failed,
                    status=status,
                    progress=progress,
                ))
            except Exception as exc:
                logger.warning("list_project_summaries 读 %s 失败: %s", name, exc)
                summaries.append(ProjectSummary(
                    project_name=name,
                    title=name,
                    author="未填写",
                    chapters=0,
                    segments=0,
                    completed=0,
                    modified_at=None,
                ))
        return summaries

    @staticmethod
    def _script_meta(project_dir: str, fallback_name: str) -> tuple[str, str]:
        """从 ``structured_script.json`` 读取 ``meta.title/author``（只取 meta）。

        文件缺失 / JSON 损坏 / 结构异常一律回退：title → ``fallback_name``，
        author → ``"未填写"``；不解析 chapters/segments，保持轻量。

        Returns:
            ``(title, author)`` 元组。
        """
        title = fallback_name
        author = "未填写"
        script_path = project_paths.project_file(project_dir, "structured_script")
        try:
            with open(script_path, encoding="utf-8") as file:
                data = json.load(file)
            meta = data.get("meta") if isinstance(data, dict) else None
            if isinstance(meta, dict):
                title = str(meta.get("title") or title)
                author = str(meta.get("author") or "未填写")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return title, author

    @staticmethod
    def _project_status(total: int, done: int, failed: int) -> str:
        """推导书架状态色块符号（§11.7）。"""
        if total == 0:
            return "⚪未开始"
        if failed > 0 and done == 0:
            return "🔴有失败"
        if done == total and failed == 0:
            return "✅完成"
        if failed > 0:
            return "🟡部分"
        if done == 0:
            return "⚪未开始"
        return "🟢进行中"

    @staticmethod
    def get_remaining(name: str, engine_identity: str | None = None) -> list[str]:
        """返回所有待合成的段 ID（pending + failed + done 但 wav 不存在）。

        Args:
            name: 项目名。

        Returns:
            待合成段 ID 列表。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        meta = ProjectRepository._load_meta(project_dir)
        seg_dir = project_paths.project_dir(project_dir, "segments")
        remaining: list[str] = []
        changed = False
        for seg_id, status in meta.segments_status.items():
            if status in ("pending", "failed", "skipped"):
                remaining.append(seg_id)
            elif status == "done":
                # 标记 done 但对应 wav 实际不存在 → 重置为 pending
                from lib import segment_cache
                if not segment_cache.has_segment_wav(
                    seg_dir, seg_id, engine_identity=engine_identity
                ):
                    meta.segments_status[seg_id] = "pending"
                    meta.completed_count -= 1
                    meta.pending_count += 1
                    remaining.append(seg_id)
                    changed = True
        if meta.completed_count < 0:
            meta.completed_count = 0
            changed = True
        if changed:
            meta.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            ProjectRepository._save_meta(project_dir, meta)
        return remaining

    # --- synthesis_overrides.json ---

    @staticmethod
    def get_synthesis_overrides(name: str) -> dict:
        """读取项目的全局合成覆盖参数（synthesis_overrides.json）。

        Args:
            name: 项目名。

        Returns:
            覆盖参数字典，缺省为 {}。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        path = project_paths.project_file(project_dir, "synthesis_overrides")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("读取 synthesis_overrides.json 失败，回退空覆盖: %s", exc)
            return {}

    @staticmethod
    def set_synthesis_overrides(name: str, overrides: dict) -> None:
        """原子写 synthesis_overrides.json。

        Args:
            name: 项目名。
            overrides: 覆盖参数字典。

        Raises:
            AtomicWriteError: 写入失败时抛出。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        path = project_paths.project_file(project_dir, "synthesis_overrides")
        _atomic_write(path, overrides if isinstance(overrides, dict) else {})

    # --- synthesis_selections.json ---

    @staticmethod
    def get_synthesis_selections(name: str) -> dict:
        """读取项目的合成章节勾选持久化（synthesis_selections.json）。

        Args:
            name: 项目名。

        Returns:
            勾选字典（含 chapters 键），缺省为 {}。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        path = project_paths.project_file(project_dir, "synthesis_selections")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("读取 synthesis_selections.json 失败，回退空: %s", exc)
            return {}

    @staticmethod
    def set_synthesis_selections(name: str, selections: dict) -> None:
        """原子写 synthesis_selections.json。

        Args:
            name: 项目名。
            selections: 勾选字典（约定含 chapters 键）。

        Raises:
            AtomicWriteError: 写入失败时抛出。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        path = project_paths.project_file(project_dir, "synthesis_selections")
        _atomic_write(path, selections if isinstance(selections, dict) else {})

    # --- voice_bindings.json ---

    @staticmethod
    def load_bindings(project_dir: str) -> dict:
        """读 project 的 voice_bindings.json（v3 → 99_系统数据/配置/），返回完整 dict。

        Args:
            project_dir: 项目目录绝对路径。

        Returns:
            voice_bindings dict；文件不存在时返回空 dict。
        """
        path = project_paths.project_file(project_dir, "voice_bindings")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("读取 voice_bindings.json 失败: %s", exc)
            return {}

    @staticmethod
    def save_bindings(project_dir: str, bindings: dict) -> None:
        """原子写 project 的 voice_bindings.json（v3 → 99_系统数据/配置/）。

        Args:
            project_dir: 项目目录绝对路径。
            bindings: voice_bindings dict。

        Raises:
            AtomicWriteError: 写入失败时抛出。
        """
        path = project_paths.project_file(
            project_dir, "voice_bindings", create=True
        )
        _atomic_write(path, bindings if isinstance(bindings, dict) else {})
