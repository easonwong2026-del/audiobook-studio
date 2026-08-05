"""ProjectRepository：项目 CRUD + 原子写 project.json + 快照集成。

将 ``lib/project_manager.py`` 的所有磁盘操作收拢到此，
全部方法为 @staticmethod，无实例状态。

WORKSPACE_ROOT / LEGACY_ROOT 为类变量（与 pm 模块级变量语义一致），
测试可通过 monkeypatch.setattr 覆盖。
初值延迟到首次调用时通过 lib.config 初始化，避免循环导入。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
import time as _time
from dataclasses import dataclass, field
from typing import ClassVar, Optional

from lib import script_loader
from lib.types import ProjectMeta
from lib.snapshot import ProjectSnapshot

from ._atomic import atomic_write as _atomic_write
from .exceptions import ProjectNotFoundError

logger = logging.getLogger(__name__)

_PROJECT_MARKERS = (
    "project.json",
    "structured_script.json",
    "voice_bindings.json",
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
    def _meta_path(project_dir: str) -> str:
        return os.path.join(project_dir, "project.json")

    @staticmethod
    def _load_meta(project_dir: str) -> ProjectMeta:
        """从 project_dir 加载 ProjectMeta，自动修复 segments_status。"""
        with open(ProjectRepository._meta_path(project_dir), encoding="utf-8") as f:
            data = json.load(f)
        meta = ProjectMeta(**data)
        ProjectRepository._repair_meta(project_dir, meta)
        return meta

    @staticmethod
    def _repair_meta(project_dir: str, meta: ProjectMeta) -> None:
        """自动修复：确保 segments_status 的 key 与 structured_script.json 的 seg_id 一致。"""
        script_path = os.path.join(project_dir, "structured_script.json")
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
        seg_dir = os.path.join(project_dir, "segments")
        return segment_cache.has_segment_wav(seg_dir, seg_id)

    @staticmethod
    def _save_meta(project_dir: str, meta: ProjectMeta) -> None:
        """原子写 project.json。"""
        path = ProjectRepository._meta_path(project_dir)
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
        }
        _atomic_write(path, payload)

    # --- 公开 API ---

    @staticmethod
    def scan_projects() -> list[str]:
        """扫描所有项目名（新数据目录 + legacy 目录合并，新目录优先去重）。

        Returns:
            排序后的项目名列表。
        """
        ProjectRepository._ensure_roots()
        names = set()
        ws = ProjectRepository.WORKSPACE_ROOT or ""
        lg = ProjectRepository.LEGACY_ROOT or ""
        for root in (ws, lg):
            if root and os.path.isdir(root):
                names.update(
                    d for d in os.listdir(root)
                    if d != ".trash"
                    and ProjectRepository._is_valid_project_dir(os.path.join(root, d))
                )
        return sorted(names)

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
            if not os.path.isfile(os.path.join(path, marker))
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
                with open(os.path.join(path, marker), encoding="utf-8") as file:
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
            if not os.path.isfile(os.path.join(project_dir, marker)):
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
        now = _time.time()
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
        with open(os.path.join(project_dir, "structured_script.json"),
                  encoding="utf-8") as f:
            script = script_loader.canonicalize_collections(json.load(f))
        with open(os.path.join(project_dir, "voice_bindings.json"),
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
        ProjectRepository._ensure_roots()
        name = sanitize_project_name(name)
        ws = ProjectRepository.WORKSPACE_ROOT or ""
        project_dir = os.path.join(ws, name)
        if os.path.exists(project_dir):
            raise FileExistsError(f"项目 '{name}' 已存在")
        os.makedirs(ws, exist_ok=True)
        tmp_dir = os.path.join(ws, f".tmp_{name}_{uuid.uuid4().hex}")
        try:
            for sub in ("voices", "segments", "chapters", "output"):
                os.makedirs(os.path.join(tmp_dir, sub), exist_ok=True)
            shutil.copy2(script_path, os.path.join(tmp_dir, "structured_script.json"))
            with open(script_path, encoding="utf-8") as f:
                raw_script = json.load(f)
            parsed_script = script_loader.from_dict(raw_script)
            total_segments = sum(len(ch.segments) for ch in parsed_script.chapters)
            bindings = {"bindings": {n: None for n in parsed_script.voices},
                        "bound_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "verified": []}
            ProjectRepository.save_bindings(tmp_dir, bindings)
            meta = ProjectMeta(project_name=name, created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                               updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                               total_chapters=len(parsed_script.chapters), total_segments=total_segments,
                               pending_count=total_segments,
                               segments_status={seg.id: "pending" for ch in parsed_script.chapters for seg in ch.segments})
            ProjectRepository._save_meta(tmp_dir, meta)
            if os.path.exists(project_dir):
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
        project_dir = ProjectRepository._resolve_dir(name)
        meta = ProjectRepository._load_meta(project_dir)
        meta.segments_status[seg_id] = status

        # 重新统计
        meta.completed_count = sum(
            1 for s in meta.segments_status.values() if s == "done")
        meta.failed_count = sum(
            1 for s in meta.segments_status.values() if s == "failed")
        meta.pending_count = sum(
            1 for s in meta.segments_status.values() if s == "pending")
        meta.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")

        ProjectRepository._save_meta(project_dir, meta)

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
    def list_projects() -> list[dict]:
        """扫描所有项目并产出多书摘要（O4 书架用，纯函数无 gradio）。

        Returns:
            项目摘要字典列表，按项目名排序。
        """
        summaries: list[dict] = []
        for name in ProjectRepository.scan_projects():
            try:
                project_dir = ProjectRepository._resolve_dir(name)
                meta = ProjectRepository._load_meta(project_dir)
            except Exception as exc:
                logger.warning("list_projects 读 %s 失败: %s", name, exc)
                continue
            total = getattr(meta, "total_segments", 0) or 0
            done = getattr(meta, "completed_count", 0) or 0
            failed = getattr(meta, "failed_count", 0) or 0
            status = ProjectRepository._project_status(total, done, failed)
            progress = (done / total) if total else 0.0
            summaries.append({
                "name": name,
                "chapters": getattr(meta, "total_chapters", 0) or 0,
                "done": done,
                "failed": failed,
                "total": total,
                "progress": progress,
                "status": status,
            })
        return summaries

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
    def get_remaining(name: str) -> list[str]:
        """返回所有待合成的段 ID（pending + failed + done 但 wav 不存在）。

        Args:
            name: 项目名。

        Returns:
            待合成段 ID 列表。
        """
        project_dir = ProjectRepository._resolve_dir(name)
        meta = ProjectRepository._load_meta(project_dir)
        seg_dir = os.path.join(project_dir, "segments")
        remaining: list[str] = []
        for seg_id, status in meta.segments_status.items():
            if status in ("pending", "failed"):
                remaining.append(seg_id)
            elif status == "done":
                # 标记 done 但对应 wav 实际不存在 → 重置为 pending
                from lib import segment_cache
                if not segment_cache.has_segment_wav(seg_dir, seg_id):
                    meta.segments_status[seg_id] = "pending"
                    meta.completed_count -= 1
                    meta.pending_count += 1
                    remaining.append(seg_id)
        if meta.completed_count < 0:
            meta.completed_count = 0
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
        path = os.path.join(project_dir, "synthesis_overrides.json")
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
        path = os.path.join(project_dir, "synthesis_overrides.json")
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
        path = os.path.join(project_dir, "synthesis_selections.json")
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
        path = os.path.join(project_dir, "synthesis_selections.json")
        _atomic_write(path, selections if isinstance(selections, dict) else {})

    # --- voice_bindings.json ---

    @staticmethod
    def load_bindings(project_dir: str) -> dict:
        """读 project_dir/voice_bindings.json，返回完整 dict。

        Args:
            project_dir: 项目目录绝对路径。

        Returns:
            voice_bindings dict；文件不存在时返回空 dict。
        """
        path = os.path.join(project_dir, "voice_bindings.json")
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
        """原子写 project_dir/voice_bindings.json。

        Args:
            project_dir: 项目目录绝对路径。
            bindings: voice_bindings dict。

        Raises:
            AtomicWriteError: 写入失败时抛出。
        """
        path = os.path.join(project_dir, "voice_bindings.json")
        _atomic_write(path, bindings if isinstance(bindings, dict) else {})
