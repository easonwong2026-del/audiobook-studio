"""Project storage orchestration for UI handlers and scripts."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.project_storage_repo import ProjectStorageRepository, ProjectStorageSummary

from .project import ensure_project_mutation_allowed


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def format_size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"


class ProjectStorageService:
    """Business operations for project folders, cache and recovery."""

    @staticmethod
    def summary(name: str) -> ProjectStorageSummary:
        return ProjectStorageRepository.summarize(name)

    @staticmethod
    def format_summary(name: str) -> str:
        summary = ProjectStorageRepository.summarize(name)
        modified = "未知"
        if summary.modified_at is not None:
            import datetime

            modified = datetime.datetime.fromtimestamp(summary.modified_at).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            "#### 数据占用",
            f"- **项目名称**：`{summary.project_name}`",
            f"- **总占用**：**{format_size(summary.total_bytes)}**（{summary.file_count} 个文件）",
            f"- **最近修改**：{modified}",
            "",
            "| 数据分类 | 占用 |",
            "|---|---:|",
            f"| 原始文件与章节文本 | {format_size(summary.source_bytes)} |",
            f"| 角色与声音 | {format_size(summary.voices_bytes)} |",
            f"| 分段音频 | {format_size(summary.segments_bytes)} |",
            f"| 章节音频 | {format_size(summary.chapter_audio_bytes)} |",
            f"| 合并音频 | {format_size(summary.merged_audio_bytes)} |",
            f"| 导出文件 | {format_size(summary.output_bytes)} |",
            f"| 试听预览缓存 | {format_size(summary.preview_bytes)} |",
            "",
            "> 原始文件、音频和导出物不会被“清理试听缓存”删除；删除项目默认先移入回收站。",
        ]
        return "\n".join(rows)

    @staticmethod
    def open_directory(name: str) -> tuple[bool, str]:
        try:
            _safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
            if not os.path.isdir(project_dir) or os.path.islink(project_dir):
                return False, "项目目录不存在或不安全。"
            if sys.platform == "win32":
                os.startfile(project_dir)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", project_dir])
            else:
                subprocess.Popen(["xdg-open", project_dir])
            return True, f"已打开项目目录：`{project_dir}`"
        except (OSError, ValueError) as exc:
            return False, f"打开项目目录失败：{exc}"

    @staticmethod
    def archive(name: str) -> str:
        ensure_project_mutation_allowed(name, "archive_project")
        return ProjectStorageRepository.archive_project(name)

    @staticmethod
    def list_archived() -> list[dict[str, Any]]:
        return [item.as_dict() for item in ProjectStorageRepository.list_archived_projects()]

    @staticmethod
    def restore_archived(archive_id: str) -> dict[str, Any]:
        return ProjectStorageRepository.restore_archived_project(archive_id)

    @staticmethod
    def permanently_delete_archived(archive_id: str) -> None:
        ProjectStorageRepository.permanently_delete_archived_project(archive_id)

    @staticmethod
    def permanently_delete(archive_id: str) -> None:
        """Compatibility entry point accepting only a trash archive id."""
        ProjectStorageRepository.permanently_delete_project(archive_id)

    @staticmethod
    def remove_from_list(name: str) -> None:
        ProjectStorageRepository.remove_from_list(name)

    @staticmethod
    def restore_to_list(name: str) -> None:
        ProjectStorageRepository.restore_to_list(name)

    @staticmethod
    def clear_preview_cache(name: str) -> dict[str, int]:
        return ProjectStorageRepository.clear_preview_cache(name)

    @staticmethod
    def scan_cleanup(name: str) -> dict[str, Any]:
        return ProjectStorageRepository.scan_cleanup(name).as_dict()

    @staticmethod
    def execute_cleanup(name: str, token: str) -> dict[str, Any]:
        ensure_project_mutation_allowed(name, "execute_cleanup")
        return ProjectStorageRepository.execute_cleanup(name, token)

    @staticmethod
    def check_integrity(name: str) -> dict[str, Any]:
        return ProjectStorageRepository.check_project_integrity(name)

    @staticmethod
    def repair_integrity(name: str) -> dict[str, Any]:
        """Repair only safe metadata/directories, then return a fresh report."""
        ensure_project_mutation_allowed(name, "repair_integrity")
        _safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
        repaired: list[str] = []
        if project_paths.is_v2_project(project_dir):
            paths = project_paths.canonical_project_dirs(project_dir)
            for key, path in paths.items():
                if not os.path.exists(path):
                    os.makedirs(path, exist_ok=True)
                    repaired.append(f"创建目录：{project_paths.CANONICAL_DIRS[key]}")

        report = ProjectStorageRepository.check_project_integrity(name)
        for issue in report["issues"]:
            if issue["code"] == "done_audio_missing" and issue.get("segment_id"):
                ProjectRepository.update_segment_status(name, str(issue["segment_id"]), "pending")
                repaired.append(f"重置缺失音频段落状态：{issue['segment_id']}")
            elif issue["code"] == "empty_output" and issue.get("path"):
                path = os.path.normpath(str(issue["path"]))
                if os.path.isfile(path) and os.path.getsize(path) == 0:
                    os.remove(path)
                    repaired.append(f"移除空输出文件：{os.path.basename(path)}")

        # ProjectRepository's normal load path also rebuilds missing/orphaned
        # segment status keys from the current JSON, preserving done only when
        # a matching audio file exists.
        try:
            ProjectRepository.load_project(name)
        except Exception:
            pass
        fresh = ProjectStorageRepository.check_project_integrity(name)
        fresh["repaired"] = repaired
        return fresh

    @staticmethod
    def migrate_to_projects_root(name: str, target_root: str) -> str:
        """Copy a project into a chosen managed project root, then verify it.

        The source is retained.  This makes a location change recoverable; the
        caller can archive the original after confirming the copied project.
        """
        ensure_project_mutation_allowed(name, "migrate_project")
        safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
        raw_target = str(target_root or "").strip()
        if not raw_target:
            raise ValueError("迁移目标目录无效")
        destination_root = os.path.abspath(os.path.expanduser(raw_target))
        if os.path.islink(destination_root):
            raise ValueError("迁移目标目录无效")
        destination = os.path.normpath(os.path.join(destination_root, safe_name))
        if os.path.abspath(destination) == os.path.abspath(project_dir):
            return destination
        if _inside(destination, project_dir) or _inside(destination_root, project_dir):
            raise ValueError("迁移目标不能位于源项目目录内或包含源项目目录")
        if os.path.exists(destination):
            raise FileExistsError(f"迁移目标已存在：{destination}")
        os.makedirs(destination_root, exist_ok=True)
        import shutil

        shutil.copytree(project_dir, destination, symlinks=True)
        if not all(os.path.isfile(os.path.join(destination, marker)) for marker in ("project.json", "structured_script.json", "voice_bindings.json")):
            shutil.rmtree(destination, ignore_errors=True)
            raise OSError("迁移副本缺少项目核心文件")
        return destination


__all__ = ["ProjectStorageService", "format_size"]
