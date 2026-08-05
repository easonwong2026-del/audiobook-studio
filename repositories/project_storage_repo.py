"""Safe project storage operations.

The project repository owns JSON persistence.  This repository owns the
filesystem concerns around a project: bounded path resolution, recursive
statistics, reversible deletion, preview-cache cleanup and integrity scans.
All walkers deliberately avoid following symlinks so a malformed project
cannot make a maintenance action escape its intended root.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import Any

from lib import config, project_paths, script_loader, segment_cache

from ._atomic import atomic_write as _atomic_write
from .exceptions import ProjectNotFoundError
from .project_repo import ProjectRepository, sanitize_project_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectStorageSummary:
    project_name: str
    project_dir: str
    total_bytes: int
    source_bytes: int
    voices_bytes: int
    segments_bytes: int
    chapter_audio_bytes: int
    merged_audio_bytes: int
    output_bytes: int
    preview_bytes: int
    file_count: int
    modified_at: float | None

    @property
    def chapters_bytes(self) -> int:
        """Compatibility alias for callers that call chapter audio ``chapters``."""
        return self.chapter_audio_bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "project_dir": self.project_dir,
            "total_bytes": self.total_bytes,
            "source_bytes": self.source_bytes,
            "voices_bytes": self.voices_bytes,
            "segments_bytes": self.segments_bytes,
            "chapter_audio_bytes": self.chapter_audio_bytes,
            "chapters_bytes": self.chapter_audio_bytes,
            "merged_audio_bytes": self.merged_audio_bytes,
            "output_bytes": self.output_bytes,
            "preview_bytes": self.preview_bytes,
            "file_count": self.file_count,
            "modified_at": self.modified_at,
        }


@dataclass(frozen=True)
class CleanupCandidate:
    path: str
    relative_path: str
    size: int
    mtime_ns: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CleanupPlan:
    project_name: str
    token: str
    candidates: tuple[CleanupCandidate, ...]
    total_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "token": self.token,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "total_bytes": self.total_bytes,
        }


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def _tree_measure(path: str) -> tuple[int, int, float | None]:
    """Measure regular files below ``path`` without following symlinks."""
    if not path or not os.path.isdir(path) or os.path.islink(path):
        return 0, 0, None
    total = 0
    count = 0
    modified: float | None = None
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
        for name in files:
            file_path = os.path.join(root, name)
            if os.path.islink(file_path):
                continue
            try:
                stat = os.stat(file_path, follow_symlinks=False)
            except OSError as exc:
                logger.warning("读取项目文件信息失败 %s: %s", file_path, exc)
                continue
            total += stat.st_size
            count += 1
            modified = max(modified or stat.st_mtime, stat.st_mtime)
    try:
        root_mtime = os.path.getmtime(path)
        modified = max(modified or root_mtime, root_mtime)
    except OSError:
        pass
    return total, count, modified


def _safe_relative(root: str, path: str) -> str:
    relative = os.path.relpath(path, root)
    if relative in {".", ".."} or relative.startswith(f"..{os.sep}"):
        raise ValueError("路径不在项目目录内")
    return relative.replace(os.sep, "/")


class ProjectStorageRepository:
    """Filesystem repository for project management operations."""

    @staticmethod
    def _resolve_project(name: str) -> tuple[str, str]:
        raw = str(name or "").strip()
        if (
            not raw
            or raw in {".", ".."}
            or os.path.basename(raw) != raw
            or "/" in raw
            or "\\" in raw
        ):
            raise ValueError("项目名称无效")
        safe_name = sanitize_project_name(raw)
        if safe_name != raw:
            raise ValueError("项目名称与实际目录名不一致")
        ProjectRepository._ensure_roots()
        project_dir = os.path.normpath(ProjectRepository.get_project_dir(safe_name))
        if os.path.islink(project_dir):
            raise ValueError("拒绝操作符号链接项目目录")
        roots = [
            ProjectRepository.WORKSPACE_ROOT,
            ProjectRepository.LEGACY_ROOT,
        ]
        if not any(root and _inside(project_dir, root) for root in roots):
            raise ValueError("项目目录不在受管理的数据根目录内")
        if not os.path.isdir(project_dir):
            raise ProjectNotFoundError(f"项目 '{safe_name}' 不存在")
        return safe_name, project_dir

    @staticmethod
    def _preview_dir(name: str) -> str:
        safe_name = sanitize_project_name(name)
        data_root = os.path.realpath(config.get_data_dir())
        preview_root = os.path.realpath(os.path.join(config.get_preview_dir(), safe_name))
        if not _inside(preview_root, data_root) or preview_root == data_root:
            raise ValueError("预览缓存路径不安全")
        return os.path.normpath(preview_root)

    @staticmethod
    def _measure_category(project_dir: str, key: str) -> tuple[int, int, float | None]:
        return _tree_measure(project_paths.project_dir(project_dir, key))

    @staticmethod
    def summarize(name: str) -> ProjectStorageSummary:
        safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
        root_bytes, root_count, root_mtime = _tree_measure(project_dir)
        preview_bytes, preview_count, preview_mtime = _tree_measure(
            ProjectStorageRepository._preview_dir(safe_name)
        )

        category_sizes: dict[str, int] = {}
        for key in ("source", "voices", "segments", "chapter_audio", "merged_audio", "exports"):
            category_sizes[key] = ProjectStorageRepository._measure_category(project_dir, key)[0]

        return ProjectStorageSummary(
            project_name=safe_name,
            project_dir=os.path.normpath(project_dir),
            total_bytes=root_bytes + preview_bytes,
            source_bytes=category_sizes["source"],
            voices_bytes=category_sizes["voices"],
            segments_bytes=category_sizes["segments"],
            chapter_audio_bytes=category_sizes["chapter_audio"],
            merged_audio_bytes=category_sizes["merged_audio"],
            output_bytes=category_sizes["exports"],
            preview_bytes=preview_bytes,
            file_count=root_count + preview_count,
            modified_at=max(root_mtime or 0.0, preview_mtime or 0.0) or None,
        )

    @staticmethod
    def archive_project(name: str) -> str:
        """Move a project into the data-root trash area, preserving recovery."""
        safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
        data_root = os.path.realpath(config.get_data_dir())
        trash_root = os.path.join(data_root, ".trash", "projects")
        os.makedirs(trash_root, exist_ok=True)
        if not _inside(trash_root, data_root):
            raise ValueError("回收站路径不安全")
        target = os.path.join(
            trash_root,
            f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
        )
        if os.path.lexists(target):
            raise FileExistsError("回收站目标已存在，请稍后重试")
        try:
            os.replace(project_dir, target)
        except OSError as exc:
            logger.info("项目不支持原子归档，改用安全复制: %s", exc)
            try:
                shutil.copytree(project_dir, target, symlinks=True)
                if not all(os.path.isfile(os.path.join(target, marker)) for marker in ("project.json", "structured_script.json")):
                    raise OSError("归档副本缺少项目核心文件")
                shutil.rmtree(project_dir)
            except Exception:
                if os.path.lexists(target):
                    shutil.rmtree(target, ignore_errors=True)
                raise
        return os.path.normpath(target)

    @staticmethod
    def permanently_delete_project(name: str) -> None:
        """Permanently remove a project after the caller has explicit consent."""
        _safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
        shutil.rmtree(project_dir)

    @staticmethod
    def _catalog_path() -> str:
        data_root = os.path.realpath(config.get_data_dir())
        path = os.path.normpath(os.path.join(data_root, ".project_catalog.json"))
        if not _inside(path, data_root):
            raise ValueError("项目列表目录不安全")
        return path

    @staticmethod
    def _catalog() -> dict[str, Any]:
        try:
            with open(ProjectStorageRepository._catalog_path(), encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def remove_from_list(name: str) -> None:
        safe_name, _project_dir = ProjectStorageRepository._resolve_project(name)
        catalog = ProjectStorageRepository._catalog()
        hidden = {str(value) for value in catalog.get("hidden_projects", []) if isinstance(value, (str, int))}
        hidden.add(safe_name)
        catalog["hidden_projects"] = sorted(hidden)
        _atomic_write(ProjectStorageRepository._catalog_path(), catalog)

    @staticmethod
    def restore_to_list(name: str) -> None:
        safe_name, _project_dir = ProjectStorageRepository._resolve_project(name)
        catalog = ProjectStorageRepository._catalog()
        hidden = {str(value) for value in catalog.get("hidden_projects", []) if isinstance(value, (str, int))}
        hidden.discard(safe_name)
        catalog["hidden_projects"] = sorted(hidden)
        _atomic_write(ProjectStorageRepository._catalog_path(), catalog)

    @staticmethod
    def clear_preview_cache(name: str) -> dict[str, int]:
        """Clear only the external preview cache for one project."""
        safe_name, _project_dir = ProjectStorageRepository._resolve_project(name)
        cache_dir = ProjectStorageRepository._preview_dir(safe_name)
        removed_files = 0
        removed_bytes = 0
        if not os.path.isdir(cache_dir) or os.path.islink(cache_dir):
            return {"files": 0, "bytes": 0}
        for entry in os.scandir(cache_dir):
            target = entry.path
            try:
                if entry.is_symlink():
                    entry.unlink()
                elif entry.is_dir(follow_symlinks=False):
                    size, count, _mtime = _tree_measure(target)
                    shutil.rmtree(target)
                    removed_files += count
                    removed_bytes += size
                else:
                    removed_bytes += entry.stat(follow_symlinks=False).st_size
                    entry.unlink()
                    removed_files += 1
            except OSError as exc:
                logger.warning("清理试听缓存失败 %s: %s", target, exc)
        return {"files": removed_files, "bytes": removed_bytes}

    @staticmethod
    def _current_segment_ids(project_dir: str) -> set[str]:
        script_path = os.path.join(project_dir, "structured_script.json")
        try:
            with open(script_path, encoding="utf-8") as file:
                raw = script_loader.canonicalize_collections(json.load(file))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return set()
        ids: set[str] = set()
        for chapter in raw.get("chapters", []) if isinstance(raw, dict) else []:
            if not isinstance(chapter, dict):
                continue
            for segment in chapter.get("segments", []):
                if isinstance(segment, dict) and segment.get("id") is not None:
                    ids.add(str(segment["id"]))
        return ids

    @staticmethod
    def _is_temp_file(path: str) -> bool:
        lower = os.path.basename(path).lower()
        return lower.startswith(".tmp_") or lower.endswith((".tmp", ".part", ".partial", ".crdownload"))

    @staticmethod
    def _cleanup_candidates(name: str) -> tuple[str, list[CleanupCandidate]]:
        safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
        segment_dir = project_paths.project_dir(project_dir, "segments")
        preview_dir = ProjectStorageRepository._preview_dir(safe_name)
        segment_ids = ProjectStorageRepository._current_segment_ids(project_dir)
        candidates: list[CleanupCandidate] = []
        roots = [(project_dir, "project"), (preview_dir, "preview")]
        seen: set[str] = set()
        for root, root_label in roots:
            if not os.path.isdir(root) or os.path.islink(root):
                continue
            for current_root, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [entry for entry in dirs if not os.path.islink(os.path.join(current_root, entry))]
                for filename in files:
                    path = os.path.normpath(os.path.join(current_root, filename))
                    if os.path.islink(path) or path in seen:
                        continue
                    in_segments = _inside(path, segment_dir)
                    try:
                        stat = os.stat(path, follow_symlinks=False)
                    except OSError:
                        continue
                    reason = ""
                    if ProjectStorageRepository._is_temp_file(path):
                        reason = "临时文件"
                    elif in_segments and stat.st_size == 0:
                        stem = os.path.splitext(os.path.basename(path))[0]
                        is_current = any(stem == sid or stem.startswith(f"{sid}_") for sid in segment_ids)
                        reason = "空的分段音频" if is_current else "空的非当前段音频"
                    # Non-empty files with unknown names are deliberately kept:
                    # they may be user-managed/manual audio and are not safe to infer.
                    if not reason:
                        continue
                    seen.add(path)
                    relative = f"{root_label}/{_safe_relative(root, path)}"
                    candidates.append(CleanupCandidate(
                        path=path,
                        relative_path=relative,
                        size=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        reason=reason,
                    ))
        candidates.sort(key=lambda item: item.relative_path)
        return safe_name, candidates

    @staticmethod
    def scan_cleanup(name: str) -> CleanupPlan:
        safe_name, candidates = ProjectStorageRepository._cleanup_candidates(name)
        token_payload = [
            (candidate.relative_path, candidate.size, candidate.mtime_ns)
            for candidate in candidates
        ]
        token = hashlib.sha256(
            json.dumps([safe_name, token_payload], ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return CleanupPlan(
            project_name=safe_name,
            token=token,
            candidates=tuple(candidates),
            total_bytes=sum(candidate.size for candidate in candidates),
        )

    @staticmethod
    def execute_cleanup(name: str, token: str) -> dict[str, Any]:
        """Delete exactly the latest scan result; refuse stale scans."""
        plan = ProjectStorageRepository.scan_cleanup(name)
        if not token or token != plan.token:
            return {"ok": False, "stale": True, "removed_files": 0, "removed_bytes": 0, "plan": plan.as_dict()}
        removed_files = 0
        removed_bytes = 0
        for candidate in plan.candidates:
            try:
                stat = os.stat(candidate.path, follow_symlinks=False)
                if stat.st_size != candidate.size or stat.st_mtime_ns != candidate.mtime_ns:
                    continue
                if os.path.islink(candidate.path):
                    continue
                os.remove(candidate.path)
                removed_files += 1
                removed_bytes += candidate.size
            except OSError as exc:
                logger.warning("清理候选文件失败 %s: %s", candidate.path, exc)
        return {"ok": True, "stale": False, "removed_files": removed_files, "removed_bytes": removed_bytes}

    @staticmethod
    def check_project_integrity(name: str) -> dict[str, Any]:
        """Return a structured, non-destructive integrity report."""
        safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
        issues: list[dict[str, Any]] = []

        def add_issue(
            code: str,
            message: str,
            *,
            severity: str = "error",
            path: str | None = None,
            repairable: bool = False,
            segment_id: str | None = None,
        ) -> None:
            issues.append({
                "code": code,
                "severity": severity,
                "message": message,
                "path": path,
                "repairable": repairable,
                "segment_id": segment_id,
            })

        parsed_meta: dict[str, Any] = {}
        meta_path = os.path.join(project_dir, "project.json")
        script_path = os.path.join(project_dir, "structured_script.json")
        bindings_path = os.path.join(project_dir, "voice_bindings.json")
        for path in (meta_path, script_path, bindings_path):
            if not os.path.isfile(path):
                add_issue("missing_file", f"缺少项目核心文件：{os.path.basename(path)}", path=path, repairable=False)

        try:
            with open(meta_path, encoding="utf-8") as file:
                parsed_meta = json.load(file)
            if not isinstance(parsed_meta, dict):
                add_issue("invalid_project_meta", "project.json 不是对象", path=meta_path)
            elif parsed_meta.get("project_name") != safe_name:
                add_issue("project_name_mismatch", "project.json 中的项目名与目录不一致", path=meta_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            add_issue("invalid_project_meta", f"project.json 无法读取：{exc}", path=meta_path)

        script: dict[str, Any] = {}
        if os.path.isfile(script_path):
            try:
                with open(script_path, encoding="utf-8") as file:
                    raw = json.load(file)
                script = script_loader.canonicalize_collections(raw)
                validation_errors = script_loader.validate_script(script_loader.from_dict(script))
                if validation_errors:
                    add_issue("invalid_script", "章节文本校验失败：" + "；".join(validation_errors[:3]), path=script_path)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                add_issue("invalid_script", f"structured_script.json 无法读取：{exc}", path=script_path)

        segment_ids: list[str] = []
        for chapter in script.get("chapters", []) if isinstance(script, dict) else []:
            if not isinstance(chapter, dict):
                continue
            for segment in chapter.get("segments", []):
                if isinstance(segment, dict) and segment.get("id") is not None:
                    segment_ids.append(str(segment["id"]))
        duplicates = sorted({sid for sid in segment_ids if segment_ids.count(sid) > 1})
        if duplicates:
            add_issue("duplicate_segment_id", f"发现重复段落 ID：{', '.join(duplicates[:10])}", path=script_path)

        statuses = parsed_meta.get("segments_status", {}) if isinstance(parsed_meta, dict) else {}
        if isinstance(statuses, dict):
            id_set = set(segment_ids)
            for sid in sorted(id_set - {str(value) for value in statuses}):
                add_issue("missing_segment_status", f"段落 {sid} 缺少状态记录", path=meta_path, repairable=True, segment_id=sid)
            for sid in sorted({str(value) for value in statuses} - id_set):
                add_issue("orphan_segment_status", f"状态记录 {sid} 不对应当前剧本段落", path=meta_path, repairable=True, segment_id=sid)
        elif os.path.isfile(meta_path):
            add_issue("invalid_segment_status", "project.json 的 segments_status 不是对象", path=meta_path, repairable=True)

        if os.path.isfile(bindings_path):
            try:
                with open(bindings_path, encoding="utf-8") as file:
                    bindings_raw = json.load(file)
                bindings = bindings_raw.get("bindings", {}) if isinstance(bindings_raw, dict) else {}
                if not isinstance(bindings, dict):
                    add_issue("invalid_bindings", "voice_bindings.json 的 bindings 不是对象", path=bindings_path)
                else:
                    for role, value in bindings.items():
                        if not value:
                            continue
                        binding_path = str(value)
                        if not os.path.isabs(binding_path):
                            binding_path = os.path.join(project_dir, binding_path)
                        if not os.path.isfile(binding_path):
                            add_issue("missing_voice_binding", f"角色「{role}」的参考音频不存在", path=binding_path)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                add_issue("invalid_bindings", f"voice_bindings.json 无法读取：{exc}", path=bindings_path)

        segment_dir = project_paths.project_dir(project_dir, "segments")
        for chapter in script.get("chapters", []) if isinstance(script, dict) else []:
            if not isinstance(chapter, dict):
                continue
            for segment in chapter.get("segments", []):
                if not isinstance(segment, dict) or segment.get("id") is None:
                    continue
                sid = str(segment["id"])
                status = statuses.get(sid) if isinstance(statuses, dict) else None
                if status == "done" and not segment_cache.has_segment_wav(segment_dir, sid):
                    add_issue(
                        "done_audio_missing",
                        f"段落 {sid} 标记为已完成，但未找到对应音频",
                        path=segment_dir,
                        repairable=True,
                        segment_id=sid,
                    )

        for key in ("segments", "chapter_audio", "merged_audio", "exports"):
            directory = project_paths.project_dir(project_dir, key)
            if project_paths.is_v2_project(project_dir) and not os.path.isdir(directory):
                add_issue("missing_directory", f"缺少项目目录：{project_paths.CANONICAL_DIRS[key]}", path=directory, repairable=True)
            if os.path.isdir(directory) and not os.path.islink(directory):
                for root, dirs, files in os.walk(directory, followlinks=False):
                    dirs[:] = [entry for entry in dirs if not os.path.islink(os.path.join(root, entry))]
                    for filename in files:
                        path = os.path.join(root, filename)
                        if os.path.islink(path):
                            continue
                        try:
                            if os.path.getsize(path) == 0 and key in {"chapter_audio", "merged_audio", "exports"}:
                                add_issue("empty_output", f"发现空的输出文件：{filename}", path=path, repairable=True)
                        except OSError:
                            continue

        return {
            "project_name": safe_name,
            "project_dir": os.path.normpath(project_dir),
            "ok": not any(issue["severity"] == "error" for issue in issues),
            "issue_count": len(issues),
            "issues": issues,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }


__all__ = [
    "CleanupCandidate",
    "CleanupPlan",
    "ProjectStorageRepository",
    "ProjectStorageSummary",
]
