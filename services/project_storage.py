"""Project storage orchestration for UI handlers and scripts."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
from typing import Any

from lib import project_paths, procutil
from repositories.project_repo import ProjectRepository
from repositories.project_storage_repo import ProjectStorageRepository, ProjectStorageSummary

from .project import ProjectMutationBlockedError, ensure_project_mutation_allowed

logger = logging.getLogger(__name__)

# 迁移目标：v1/v2 旧相对路径前缀 → v3 相对路径前缀。
# 顺序敏感：长前缀优先（如 09_导出文件/exports/ 先于 09_导出文件/；
# 01_项目配置/production_tasks.sqlite3 先于 01_项目配置/）。
_MIGRATION_REWRITE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("09_导出文件/exports/", "03_导出成品/正式导出/"),
    ("09_导出文件/supplement_", "03_导出成品/补录/supplement_"),
    ("09_导出文件/", "03_导出成品/正式导出/legacy_output/"),
    ("01_项目配置/production_tasks.sqlite3", "99_系统数据/任务/production_tasks.sqlite3"),
    ("01_项目配置/", "99_系统数据/配置/"),
    ("08_质检记录/", "99_系统数据/质检/"),
    ("05_分段音频/", "02_生成音频/分段音频/"),
    ("04_角色与声音/", "01_原始资料/项目音色/"),
    ("03_章节文本/", "99_系统数据/章节数据/"),
    ("02_原始文件/", "01_原始资料/书稿/"),
    ("06_章节音频/", "02_生成音频/章节音频/"),
    ("07_合并音频/", "02_生成音频/合并音频/"),
    ("cache/supplement_tasks/", "02_生成音频/补录音频/"),
    ("output/", "03_导出成品/正式导出/legacy_output/"),
    ("exports/", "03_导出成品/正式导出/"),
    ("segments/", "02_生成音频/分段音频/"),
    ("voices/", "01_原始资料/项目音色/"),
    ("chapters/", "99_系统数据/章节数据/"),
    ("cache/", "99_系统数据/缓存/"),
    ("project.json", "99_系统数据/配置/project.json"),
    ("structured_script.json", "99_系统数据/配置/structured_script.json"),
    ("voice_bindings.json", "99_系统数据/配置/voice_bindings.json"),
    ("character_roster.json", "99_系统数据/配置/character_roster.json"),
    ("voice_cast.json", "99_系统数据/配置/voice_cast.json"),
    ("quality_state.json", "99_系统数据/质检/quality_state.json"),
)

# 迁移时旧布局根级条目清单（known），其余一律视为 unknown（preserve）。
# v2 项目也可能存在 v1 legacy 名目录（ensure_layout(compatibility=True) 建的
# junction/空目录），一并视为 known，由迁移器按空目录清理。
_KNOWN_ROOT_NAMES_V2 = {
    "01_项目配置", "02_原始文件", "03_章节文本", "04_角色与声音",
    "05_分段音频", "06_章节音频", "07_合并音频", "08_质检记录",
    "09_导出文件", "cache", "logs",
    "voices", "segments", "chapters", "output", "exports",
}
_KNOWN_ROOT_NAMES_V1 = {
    "voices", "segments", "chapters", "output", "cache", "logs",
    "08_质检记录", "01_项目配置",
}
_ROOT_JSON_FILES = {
    "project.json", "structured_script.json", "voice_bindings.json",
    "character_roster.json", "voice_cast.json",
    "synthesis_overrides.json", "synthesis_selections.json",
}


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


def _rewrite_legacy_relative(project_dir: str, value: Any) -> tuple[str, bool]:
    """把一条旧版本相对路径重写为 v3 相对路径。

    Returns:
        ``(new_value, changed)``。无法解析/已在项目外/未知前缀的记录保留原值
        （``changed=False``），迁移后由 ``project_paths.resolve_relative`` 兜底。
    """
    if value is None:
        return value, False
    raw = str(value)
    if not raw:
        return value, False
    normalized = raw.replace("\\", "/")
    if os.path.isabs(normalized):
        try:
            inside = os.path.commonpath(
                [os.path.realpath(normalized), os.path.realpath(project_dir)]
            ) == os.path.realpath(project_dir)
        except ValueError:
            inside = False
        if not inside:
            return value, False
        normalized = os.path.relpath(
            os.path.realpath(normalized), os.path.realpath(project_dir)
        ).replace(os.sep, "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return value, False
    cleaned = "/".join(parts)
    first = cleaned.split("/", 1)[0]
    if first in {name.split("/", 1)[0] for name in project_paths.V3_DIRS.values() if name}:
        # 已是 v3 相对路径：保持不变。
        return raw, False
    for old_prefix, new_prefix in _MIGRATION_REWRITE_PREFIXES:
        if cleaned == old_prefix.rstrip("/") or cleaned.startswith(old_prefix):
            rewritten = new_prefix + cleaned[len(old_prefix):]
            return rewritten, True
    return value, False


def _rewrite_document_paths(project_dir: str, document: Any) -> tuple[Any, int]:
    """递归重写文档内的 ``relative_path`` / ``project_voice_path`` / bindings 路径字段。

    Args:
        project_dir: 项目目录（v3 目标态）。
        document: JSON-safe 文档。

    Returns:
        ``(重写后的文档, 变更条数)``。
    """
    changed = 0

    def _walk(node: Any) -> Any:
        nonlocal changed
        if isinstance(node, dict):
            result: dict[str, Any] = {}
            for key, value in node.items():
                if key in {"relative_path", "project_voice_path"} and isinstance(value, (str, type(None))):
                    new_value, did_change = _rewrite_legacy_relative(project_dir, value)
                    if did_change:
                        changed += 1
                    result[key] = new_value
                elif key == "bindings" and isinstance(value, dict):
                    inner: dict[str, Any] = {}
                    for role, path_value in value.items():
                        new_value, did_change = _rewrite_legacy_relative(project_dir, path_value)
                        if did_change:
                            changed += 1
                        inner[str(role)] = new_value
                    result[key] = inner
                else:
                    result[key] = _walk(value)
            return result
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(document), changed


def _load_json_document(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: str, document: Any) -> None:
    from repositories._atomic import atomic_write

    atomic_write(path, document)


class ProjectStorageService:
    """Business operations for project folders, cache and recovery."""

    @staticmethod
    def summary(name: str) -> ProjectStorageSummary:
        return ProjectStorageRepository.summarize(name)

    @staticmethod
    def format_summary(name: str) -> str:
        summary = ProjectStorageRepository.summarize(name)
        _safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
        layout_version = project_paths.detect_storage_version(project_dir)
        layout_label = {
            1: "v1（旧版英文布局）",
            2: "v2（中文 canonical 布局）",
            3: "v3（新版布局）",
        }.get(layout_version, f"v{layout_version}")
        modified = "未知"
        if summary.modified_at is not None:
            import datetime

            modified = datetime.datetime.fromtimestamp(summary.modified_at).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            "#### 数据占用",
            f"- **项目名称**：`{summary.project_name}`",
            f"- **Storage Layout**：**{layout_label}**",
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
        if layout_version < 3:
            rows.append(
                "\n> ⚠ **旧版项目目录**：当前为 v1/v2 布局，可整理为新版 v3 目录"
                "（书架「整理存储布局」显式迁移，迁移前自动备份）。"
            )
        return "\n".join(rows)

    @staticmethod
    def open_directory(name: str, key: str = "") -> tuple[bool, str]:
        """在资源管理器中打开项目目录或逻辑子目录（Windows 无黑框）。

        Args:
            name: 项目名。
            key: 逻辑目录 key（``""`` 打开项目根；``segments`` / ``delivery_official``
                等按当前布局解析；v3 项目自动落到对应 v3 目录）。

        Returns:
            ``(ok, message)`` 元组。
        """
        try:
            _safe_name, project_dir = ProjectStorageRepository._resolve_project(name)
            if not os.path.isdir(project_dir) or os.path.islink(project_dir):
                return False, "项目目录不存在或不安全。"
            target = project_dir
            if key:
                try:
                    target = project_paths.project_dir(project_dir, key, create=True)
                except KeyError:
                    return False, f"未知的项目目录类型：{key}"
                if not os.path.isdir(target) or os.path.islink(target):
                    return False, "项目子目录不存在或不安全。"
            ok = procutil.open_in_folder(target)
            if not ok:
                return False, "打开目录失败（目录已存在，但系统无法打开）。"
            label = {
                "segments": "分段音频",
                "delivery_official": "正式导出",
                "delivery_supplement": "补录导出",
            }.get(key, "子目录")
            if key:
                return True, f"已打开{label}目录：`{target}`"
            return True, f"已打开项目目录：`{target}`"
        except (OSError, ValueError) as exc:
            return False, f"打开目录失败：{exc}"

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
        version = project_paths.detect_storage_version(project_dir)
        if version >= 2:
            table = project_paths.V3_DIRS if version >= 3 else project_paths.V2_DIRS
            for key, relative in table.items():
                if key == "migration_preserved" or not relative:
                    continue
                path = os.path.join(project_dir, *relative.split("/"))
                if not os.path.exists(path):
                    os.makedirs(path, exist_ok=True)
                    repaired.append(f"创建目录：{relative}")

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

    # ──────────────────────────────────────────────────────────
    # Storage Layout v3 迁移器：plan → token → execute（T04）
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _storage_live_blockers(safe_name: str) -> list[dict[str, Any]]:
        """返回阻止迁移的 live 任务清单（**纯只读**，不创建/修改任何文件）。

        覆盖：production/supplement/export（与正式 guard 同语义）+ repair_history
        活动记录 + 尚未完成的 audio revision。任务库不存在/不可读时保守视为无 live
        任务（正式执行时 ``ensure_project_mutation_allowed`` 仍会严格校验）。
        Quick TTS（无 idempotency_key 的 utility）不阻塞项目迁移（#46 stale/live
        语义保持，不修改 ProductionRuntime）。
        """
        blockers: list[dict[str, Any]] = []
        from services.production_jobs import ACTIVE_PRODUCTION_STATES
        from repositories.task_repo import TaskRepository

        db_path = TaskRepository.get_database_path(safe_name, create=False)
        rows: list[tuple[str, str, str]] = []
        if db_path and os.path.isfile(db_path):
            try:
                connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
                try:
                    try:
                        rows = connection.execute(
                            "SELECT task_id, task_type, status FROM production_tasks"
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
                finally:
                    connection.close()
            except (OSError, sqlite3.Error):
                rows = []
        for task_id, task_type, status in rows:
            if (
                str(status) in ACTIVE_PRODUCTION_STATES
                and str(task_type) in {"synthesis", "supplement", "export"}
            ):
                blockers.append({
                    "code": "LIVE_TASK",
                    "message": "项目存在正在运行的任务，无法整理目录",
                    "task_id": str(task_id),
                    "status": str(status),
                })

        quality = ProjectStorageService._read_quality_state_ro(safe_name)
        repairs = quality.get("repair_history", {})
        for repair_id, repair in repairs.items():
            if not isinstance(repair, dict):
                continue
            if str(repair.get("status") or "") in {
                "preparing", "submitting", "pending", "running",
                "pausing", "paused", "cancelling",
            }:
                blockers.append({
                    "code": "LIVE_REPAIR",
                    "message": "项目存在正在运行的修复任务",
                    "repair_id": repair_id,
                    "task_id": repair.get("task_id"),
                    "status": repair.get("status"),
                })
        regenerating = [
            str(sid) for sid, revision in quality.get("revisions", {}).items()
            if isinstance(revision, dict) and revision.get("status") == "regenerating"
        ]
        if regenerating:
            blockers.append({
                "code": "REVISION_REGENERATING",
                "message": "项目存在尚未完成的音频 revision",
                "segment_ids": sorted(set(regenerating)),
            })
        return blockers

    @staticmethod
    def _read_quality_state_ro(safe_name: str) -> dict[str, Any]:
        """只读读取 quality_state.json（不创建目录/文件）。"""
        from repositories.quality_repo import QualityRepository

        try:
            path = QualityRepository.state_path(safe_name, create=False)
        except Exception:
            return {}
        if not path or not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as file:
                value = json.load(file)
            return value if isinstance(value, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _known_root_names(from_version: int) -> set[str]:
        names = set(_KNOWN_ROOT_NAMES_V1 if from_version < 2 else _KNOWN_ROOT_NAMES_V2)
        names |= _ROOT_JSON_FILES
        return names

    @staticmethod
    def _measure_entries(paths: list[str]) -> tuple[int, int]:
        total_bytes = 0
        count = 0
        for path in paths:
            if os.path.isfile(path):
                try:
                    total_bytes += os.path.getsize(path)
                    count += 1
                except OSError:
                    pass
            elif os.path.isdir(path) and not os.path.islink(path):
                for root, dirs, files in os.walk(path, followlinks=False):
                    dirs[:] = [entry for entry in dirs if not os.path.islink(os.path.join(root, entry))]
                    for name in files:
                        file_path = os.path.join(root, name)
                        if os.path.islink(file_path):
                            continue
                        try:
                            stat = os.stat(file_path, follow_symlinks=False)
                        except OSError:
                            continue
                        total_bytes += stat.st_size
                        count += 1
        return count, total_bytes

    @staticmethod
    def _dir_non_empty(path: str) -> bool:
        if not os.path.isdir(path) or os.path.islink(path):
            return False
        try:
            return any(True for _ in os.scandir(path))
        except OSError:
            return False

    @staticmethod
    def _count_relative_records(project_dir: str, from_version: int) -> list[dict[str, Any]]:
        """统计将被重写的持久化 relative path 记录（plan 只读）。"""
        records: list[dict[str, Any]] = []
        quality_path = project_paths.project_file(
            project_dir, "quality_state", prefer_version=from_version
        )
        quality = _load_json_document(quality_path)
        if quality:
            counts = {
                "revisions[].relative_path": sum(
                    1 for rev in quality.get("revisions", {}).values()
                    if isinstance(rev, dict) and rev.get("relative_path")
                ),
                "export_jobs[].outputs[].relative_path": sum(
                    len([o for o in item.get("outputs", []) if isinstance(o, dict) and o.get("relative_path")])
                    for item in quality.get("export_jobs", {}).values()
                    if isinstance(item, dict)
                ),
                "delivery_manifests[].outputs[].relative_path": sum(
                    len([o for o in item.get("outputs", []) if isinstance(o, dict) and o.get("relative_path")])
                    for item in quality.get("delivery_manifests", {}).values()
                    if isinstance(item, dict)
                ),
            }
            for field, count in counts.items():
                if count:
                    records.append({"document": "quality_state.json", "field": field, "count": count})

        bindings_path = project_paths.project_file(
            project_dir, "voice_bindings", prefer_version=from_version
        )
        bindings = _load_json_document(bindings_path)
        if bindings:
            bindings_count = sum(
                1 for value in bindings.get("bindings", {}).values()
                if isinstance(value, str) and value and not os.path.isabs(value)
            )
            if bindings_count:
                records.append({"document": "voice_bindings.json", "field": "bindings[*]", "count": bindings_count})
            role_count = sum(
                1 for item in bindings.get("role_bindings", {}).values()
                if isinstance(item, dict) and item.get("project_voice_path")
            )
            if role_count:
                records.append({"document": "voice_bindings.json", "field": "role_bindings[].project_voice_path", "count": role_count})

        cast_path = project_paths.project_file(
            project_dir, "voice_cast", prefer_version=from_version
        )
        cast = _load_json_document(cast_path)
        if cast:
            cast_count = sum(
                1 for item in (cast.get("roles", {}).values() if isinstance(cast.get("roles"), dict) else [])
                if isinstance(item, dict) and item.get("project_voice_path")
            )
            if cast_count:
                records.append({"document": "voice_cast.json", "field": "roles[].project_voice_path", "count": cast_count})

        meta_path = project_paths.project_file(
            project_dir, "project_meta", prefer_version=from_version
        )
        meta = _load_json_document(meta_path)
        if meta:
            if meta.get("source_file"):
                records.append({"document": "project.json", "field": "source_file", "count": 1})
            if meta.get("voice_bindings_path"):
                records.append({"document": "project.json", "field": "voice_bindings_path", "count": 1})

        task_path = project_paths.project_file(
            project_dir, "task_db", prefer_version=from_version
        )
        if os.path.isfile(task_path):
            task_count = 0
            try:
                connection = sqlite3.connect(f"file:{task_path}?mode=ro", uri=True, timeout=10.0)
                try:
                    try:
                        rows = connection.execute(
                            "SELECT options_json FROM production_tasks WHERE options_json <> ''"
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
                    for (raw_options,) in rows:
                        if not raw_options:
                            continue
                        try:
                            options = json.loads(raw_options)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        snapshot = options.get("revision_snapshot", []) if isinstance(options, dict) else []
                        task_count += sum(
                            1 for item in snapshot if isinstance(item, dict) and item.get("relative_path")
                        )
                        delivery = options.get("delivery_input_snapshot", {}) if isinstance(options, dict) else {}
                        if isinstance(delivery, dict):
                            revisions = delivery.get("active_revisions", [])
                            task_count += sum(
                                1 for item in revisions if isinstance(item, dict) and item.get("relative_path")
                            )
                finally:
                    connection.close()
            except (OSError, sqlite3.Error):
                pass
            if task_count:
                records.append({
                    "document": "production_tasks.sqlite3",
                    "field": "options_json.revision_snapshot[].relative_path",
                    "count": task_count,
                })
        return records

    @staticmethod
    def plan_storage_upgrade(project_name: str) -> dict[str, Any]:
        """返回 v1/v2 → v3 迁移计划（dry-run，只读，不移动任何文件）。

        Returns:
            MigrationPlan dict（含 token）；项目已是最新版时返回
            ``{"code": "ALREADY_CURRENT", ...}``。
        """
        safe_name, project_dir = ProjectStorageRepository._resolve_project(project_name)
        from_version = project_paths.detect_storage_version(project_dir)
        if from_version >= project_paths.STORAGE_VERSION:
            return {
                "code": "ALREADY_CURRENT",
                "project": safe_name,
                "from_version": from_version,
                "to_version": project_paths.STORAGE_VERSION,
                "source_paths": {},
                "target_paths": {},
                "file_count": 0,
                "total_bytes": 0,
                "conflicts": [],
                "unknown_paths": [],
                "relative_path_records": [],
                "backup_required": True,
                "backup_target": "",
                "blockers": [{
                    "code": "MIGRATION_ALREADY_DONE",
                    "message": "项目已是最新存储布局（v3）",
                }],
                "token": "",
            }

        source_map = project_paths.directory_map(project_dir, prefer_version=from_version)
        target_map = project_paths.directory_map(project_dir, prefer_version=project_paths.STORAGE_VERSION)

        # 已知源路径（用于 conflict / unknown / 计数）
        known_roots = ProjectStorageService._known_root_names(from_version)
        move_paths: list[str] = []
        # 根 JSON
        root_files: list[str] = []
        for file_key in (
            "project_meta", "structured_script", "voice_bindings",
            "character_roster", "voice_cast",
            "synthesis_overrides", "synthesis_selections",
        ):
            src = project_paths.project_file(project_dir, file_key, prefer_version=from_version)
            if os.path.isfile(src):
                root_files.append(src)
        move_paths.extend(root_files)
        # 整目录移动的源
        whole_dir_keys = (
            "source_book", "chapter_data", "segments", "chapter_audio",
            "merged_audio", "project_voices", "quality", "logs",
        )
        for key in whole_dir_keys:
            src = source_map.get(key)
            if src and src != project_dir and os.path.isdir(src) and not os.path.islink(src):
                move_paths.append(src)
        # 特殊拆分目录（config / cache / exports / output / supplement）
        config_src = os.path.join(project_dir, "01_项目配置")
        if os.path.isdir(config_src) and not os.path.islink(config_src):
            move_paths.append(config_src)
        exports_src = os.path.join(project_dir, "09_导出文件")
        if os.path.isdir(exports_src) and not os.path.islink(exports_src):
            move_paths.append(exports_src)
        output_src = os.path.join(project_dir, "output")
        if os.path.isdir(output_src) and not os.path.islink(output_src):
            move_paths.append(output_src)
        root_exports_src = os.path.join(project_dir, "exports")
        if os.path.isdir(root_exports_src) and not os.path.islink(root_exports_src):
            move_paths.append(root_exports_src)
        cache_src = os.path.join(project_dir, "cache")
        if os.path.isdir(cache_src) and not os.path.islink(cache_src):
            move_paths.append(cache_src)
        supplement_src = os.path.join(cache_src, "supplement_tasks") if cache_src else ""
        if os.path.isdir(supplement_src) and not os.path.islink(supplement_src):
            move_paths.append(supplement_src)

        file_count, total_bytes = ProjectStorageService._measure_entries(move_paths)

        # conflicts：v3 目标已存在非空目录 → 需要人工确认
        conflicts: list[dict[str, Any]] = []
        for key in ("segments", "chapter_audio", "merged_audio", "delivery_official",
                    "delivery_supplement", "config", "chapter_data", "quality",
                    "tasks", "cache", "logs", "temp", "source_book", "project_voices"):
            target = target_map.get(key)
            if not target:
                continue
            exists = os.path.lexists(target)
            non_empty = ProjectStorageService._dir_non_empty(target)
            if exists or non_empty:
                action = "overwrite" if non_empty else "merge"
                conflicts.append({
                    "target": target,
                    "exists": exists,
                    "non_empty": non_empty,
                    "action": action,
                })

        # unknown：项目根一级不属于任何已知 key 的条目 → preserve
        unknown_paths: list[dict[str, Any]] = []
        try:
            entries = os.scandir(project_dir)
        except OSError:
            entries = []
        for entry in entries:
            if entry.name in known_roots:
                continue
            if entry.name.startswith(".tmp_") or entry.name == ".DS_Store":
                continue
            try:
                size = _tree_measure_single(entry)
            except OSError:
                size = 0
            unknown_paths.append({
                "path": entry.path,
                "kind": "dir" if entry.is_dir(follow_symlinks=False) else "file",
                "size": size,
                "action": "preserve_to_migration_keep",
            })
        unknown_paths.sort(key=lambda item: item["path"])

        from lib import config as _config

        backup_target = os.path.join(
            _config.get_data_dir(), "backups",
            f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}.audiobook-project.zip",
        )
        token_payload = [
            safe_name,
            {key: value for key, value in source_map.items() if key in {
                "source_book", "chapter_data", "segments", "chapter_audio",
                "merged_audio", "project_voices", "quality", "config",
                "delivery_official", "delivery_supplement", "cache", "logs",
            }},
            {key: value for key, value in target_map.items() if key in {
                "source_book", "chapter_data", "segments", "chapter_audio",
                "merged_audio", "project_voices", "quality", "config",
                "delivery_official", "delivery_supplement", "cache", "logs",
            }},
            file_count,
            total_bytes,
            conflicts,
            unknown_paths,
        ]
        token = hashlib.sha256(
            json.dumps(token_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        return {
            "code": "PLAN_OK",
            "project": safe_name,
            "from_version": from_version,
            "to_version": project_paths.STORAGE_VERSION,
            "source_paths": {key: value for key, value in source_map.items() if value and value != project_dir},
            "target_paths": {key: value for key, value in target_map.items() if value and value != project_dir},
            "file_count": file_count,
            "total_bytes": total_bytes,
            "conflicts": conflicts,
            "unknown_paths": unknown_paths,
            "relative_path_records": ProjectStorageService._count_relative_records(project_dir, from_version),
            "backup_required": True,
            "backup_target": backup_target,
            "blockers": ProjectStorageService._storage_live_blockers(safe_name),
            "token": token,
        }

    @staticmethod
    def upgrade_storage(project_name: str, token: str) -> dict[str, Any]:
        """执行 v1/v2 → v3 迁移（token 校验 → live blocker → backup → 移动 → 重写 → 校验）。

        Args:
            project_name: 项目名。
            token: plan 阶段返回的 confirmation token。

        Returns:
            MigrationResult（含 backup_path / moved / rewritten / unknown 报告）。

        Raises:
            ValueError: token 过期 / 项目已是最新版 / 存在 live blocker。
            OSError: 迁移失败（已回滚，backup 保留）。
        """
        safe_name, project_dir = ProjectStorageRepository._resolve_project(project_name)
        plan = ProjectStorageService.plan_storage_upgrade(safe_name)
        if plan.get("code") == "ALREADY_CURRENT":
            raise ValueError("项目已是最新存储布局，无需整理")
        if str(token or "") != str(plan.get("token") or ""):
            raise ValueError("整理令牌已过期或不匹配，请重新扫描后再确认。")
        # live blocker 校验：只读判定 + 正式 guard（guard 复用 project.py，可能
        # 创建任务库——迁移执行本来就是写操作，允许）。
        live = ProjectStorageService._storage_live_blockers(safe_name)
        if live or plan.get("blockers"):
            first = (live or plan.get("blockers"))[0]
            raise RuntimeError(
                f"项目存在活动任务，无法整理目录（{first.get('code')}）"
            )
        try:
            ensure_project_mutation_allowed(safe_name, "storage_upgrade")
        except ProjectMutationBlockedError as exc:
            raise RuntimeError(
                f"项目存在活动任务，无法整理目录（{exc.code}：{exc.task_id}）"
            ) from exc

        # 备份必须先成功；失败即中止，不移动任何文件。
        from services.project_backup import ProjectBackupService

        backup_path = ProjectBackupService.create_backup(safe_name)
        result = ProjectStorageService._execute_upgrade(
            safe_name, project_dir, plan, backup_path
        )
        return result

    @staticmethod
    def _execute_upgrade(
        safe_name: str,
        project_dir: str,
        plan: dict[str, Any],
        backup_path: str,
    ) -> dict[str, Any]:
        from_version = int(plan.get("from_version") or 1)
        target_map = project_paths.directory_map(
            project_dir, prefer_version=project_paths.STORAGE_VERSION
        )
        source_map = project_paths.directory_map(project_dir, prefer_version=from_version)

        moved: list[tuple[str, str]] = []  # (dst, src) 逆序回滚
        rewritten_paths: list[str] = []
        try:
            # 1) 创建 v3 布局（不建 legacy 空目录/junction）
            project_paths.ensure_layout(
                project_dir, prefer_version=project_paths.STORAGE_VERSION, compatibility=False
            )

            # 2) 根系统 JSON → 99_系统数据/配置/
            for file_key in (
                "project_meta", "structured_script", "voice_bindings",
                "character_roster", "voice_cast",
                "synthesis_overrides", "synthesis_selections",
            ):
                src = project_paths.project_file(project_dir, file_key, prefer_version=from_version)
                dst = project_paths.project_file(
                    project_dir, file_key, prefer_version=project_paths.STORAGE_VERSION
                )
                if os.path.isfile(src) and os.path.normpath(src) != os.path.normpath(dst):
                    moved.extend(ProjectStorageService._move_into(src, dst))

            # 3) 整目录移动（含内容合并，冲突加后缀）
            whole_dir_keys = (
                "source_book", "chapter_data", "segments", "chapter_audio",
                "merged_audio", "project_voices", "quality", "logs",
            )
            for key in whole_dir_keys:
                src = source_map.get(key)
                dst = target_map.get(key)
                if not src or not dst or os.path.normpath(src) == os.path.normpath(dst):
                    continue
                if os.path.isdir(src) and not os.path.islink(src):
                    moved.extend(ProjectStorageService._move_tree_contents(src, dst))

            # 4) 特殊拆分目录
            #   4a. cache/supplement_tasks → 02_生成音频/补录音频/
            cache_src = os.path.join(project_dir, "cache")
            supplement_src = os.path.join(cache_src, "supplement_tasks")
            supplement_dst = target_map.get("supplement_audio")
            if (
                supplement_src and supplement_dst
                and os.path.isdir(supplement_src) and not os.path.islink(supplement_src)
                and os.path.normpath(supplement_src) != os.path.normpath(supplement_dst)
            ):
                moved.extend(ProjectStorageService._move_tree_contents(supplement_src, supplement_dst))

            #   4b. cache 其余 → 99_系统数据/缓存/
            cache_dst = target_map.get("cache")
            if (
                cache_src and cache_dst
                and os.path.isdir(cache_src) and not os.path.islink(cache_src)
                and os.path.normpath(cache_src) != os.path.normpath(cache_dst)
            ):
                moved.extend(
                    ProjectStorageService._move_tree_contents(
                        cache_src, cache_dst, skip_names={"supplement_tasks"}
                    )
                )

            #   4c. 01_项目配置 → 99_系统数据/配置/ + 99_系统数据/任务/
            config_src = os.path.join(project_dir, "01_项目配置")
            config_dst = target_map.get("config")
            tasks_dst = target_map.get("tasks")
            if os.path.isdir(config_src) and not os.path.islink(config_src):
                redirect: dict[str, str] = {}
                if tasks_dst:
                    redirect["production_tasks.sqlite3"] = os.path.join(tasks_dst, "production_tasks.sqlite3")
                moved.extend(
                    ProjectStorageService._move_tree_contents(
                        config_src, config_dst or project_dir,
                        skip_names={"project.json"},
                        redirect_files=redirect,
                    )
                )

            #   4d. 09_导出文件 → 正式导出/<task_id> / 补录 / legacy_output
            exports_src = os.path.join(project_dir, "09_导出文件")
            official_dst = target_map.get("delivery_official")
            supplement_delivery_dst = target_map.get("delivery_supplement")
            if os.path.isdir(exports_src) and not os.path.islink(exports_src):
                moved.extend(
                    ProjectStorageService._move_exports(
                        exports_src, official_dst, supplement_delivery_dst
                    )
                )

            #   4e. output → 正式导出/legacy_output（v1 手工导出 / v2 旧手工导出）
            output_src = os.path.join(project_dir, "output")
            if os.path.isdir(output_src) and not os.path.islink(output_src):
                legacy_dst = os.path.join(official_dst or project_dir, "legacy_output")
                moved.extend(ProjectStorageService._move_tree_contents(output_src, legacy_dst))

            #   4f. root exports/ → 正式导出/<task_id>/（export.py 历史直写目录，
            #       结构为 exports/<task_id>/<file>；relative_path 记录 exports/ 前缀）
            root_exports_src = os.path.join(project_dir, "exports")
            if os.path.isdir(root_exports_src) and not os.path.islink(root_exports_src):
                for entry in sorted(os.scandir(root_exports_src), key=lambda e: e.name):
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        moved.extend(
                            ProjectStorageService._move_tree_contents(
                                entry.path, os.path.join(official_dst or project_dir, entry.name)
                            )
                        )
                    else:
                        moved.extend(
                            ProjectStorageService._move_into(
                                entry.path, os.path.join(official_dst or project_dir, entry.name)
                            )
                        )

            #   4g. unknown → 99_系统数据/迁移保留/（默认 preserve，禁止删除）
            preserved_root = project_paths.project_dir(
                project_dir, "migration_preserved", create=True,
                prefer_version=project_paths.STORAGE_VERSION,
            )
            for unknown in plan.get("unknown_paths") or []:
                src = unknown.get("path") if isinstance(unknown, dict) else None
                if not src or not os.path.lexists(src):
                    continue
                base = os.path.basename(os.path.normpath(src))
                if os.path.isdir(src) and not os.path.islink(src):
                    moved.extend(
                        ProjectStorageService._move_tree_contents(
                            src, os.path.join(preserved_root, base)
                        )
                    )
                else:
                    moved.extend(
                        ProjectStorageService._move_into(
                            src, os.path.join(preserved_root, base)
                        )
                    )

            # 5) 删除 legacy 空目录 / junction（只删空目录与符号链接，绝不删用户文件）
            ProjectStorageService._remove_legacy_empty_dirs(project_dir, from_version)

            # 6) 重写持久化 relative_path（先 move 后 rewrite）
            ProjectStorageService._rewrite_persisted_paths(project_dir, from_version)
            rewritten_paths = [
                "quality_state.json", "voice_bindings.json", "voice_cast.json",
                "project.json", "production_tasks.sqlite3",
            ]

            # 7) project.json 更新 storage_version=3 / directories / source_file
            meta_dst = project_paths.project_file(
                project_dir, "project_meta", prefer_version=project_paths.STORAGE_VERSION
            )
            meta = _load_json_document(meta_dst)
            if meta is None:
                raise OSError("迁移后找不到 project.json，无法写入 v3 标记")
            meta["storage_version"] = project_paths.STORAGE_VERSION
            meta["directories"] = project_paths.layout_manifest(project_dir)
            source_file = str(meta.get("source_file") or "")
            if source_file:
                new_source, _ = _rewrite_legacy_relative(project_dir, source_file)
                meta["source_file"] = new_source
            if str(meta.get("voice_bindings_path") or ""):
                new_bindings_path, _ = _rewrite_legacy_relative(
                    project_dir, meta["voice_bindings_path"]
                )
                meta["voice_bindings_path"] = new_bindings_path
            _atomic_write_json(meta_dst, meta)

            # 8) 校验
            report = ProjectStorageRepository.check_project_integrity(safe_name)
            if not report.get("ok"):
                raise OSError(
                    "迁移后完整性校验失败："
                    + "; ".join(item.get("message", "") for item in report.get("issues", [])[:3])
                )
        except Exception:
            # 失败回滚：已移动条目逆序移回；已重写文档从 backup 恢复（backup 含全部原文件）。
            ProjectStorageService._rollback_moves(moved)
            ProjectStorageService._restore_mutated_state_from_backup(
                project_dir, backup_path, from_version
            )
            raise

        return {
            "project": safe_name,
            "from_version": from_version,
            "to_version": project_paths.STORAGE_VERSION,
            "ok": True,
            "backup_path": backup_path,
            "file_count": plan.get("file_count", 0),
            "total_bytes": plan.get("total_bytes", 0),
            "moved_entries": len(moved),
            "rewritten_documents": rewritten_paths,
            "unknown_paths": plan.get("unknown_paths", []),
            "relative_path_records": plan.get("relative_path_records", []),
            "integrity": report,
        }

    @staticmethod
    def _unique_target(path: str) -> str:
        """返回不冲突的目标路径（重名加后缀）。"""
        if not os.path.lexists(path):
            return path
        stem, ext = os.path.splitext(path)
        for index in range(1, 100):
            candidate = f"{stem}_{index}{ext}"
            if not os.path.lexists(candidate):
                return candidate
        return f"{stem}_{uuid_suffix()}{ext}"

    @staticmethod
    def _move_into(src: str, dst: str) -> list[tuple[str, str]]:
        """移动单个文件/目录到目标位置（冲突自动加后缀）。"""
        if os.path.isdir(src) and not os.path.islink(src):
            return ProjectStorageService._move_tree_contents(src, dst)
        if not os.path.isfile(src) and not os.path.islink(src):
            return []
        final_dst = ProjectStorageService._unique_target(dst)
        os.makedirs(os.path.dirname(os.path.abspath(final_dst)), exist_ok=True)
        shutil.move(src, final_dst)
        return [(final_dst, src)]

    @staticmethod
    def _move_tree_contents(
        src_dir: str,
        dst_dir: str,
        *,
        skip_names: set[str] | None = None,
        redirect_files: dict[str, str] | None = None,
    ) -> list[tuple[str, str]]:
        """把 ``src_dir`` 下所有条目移动到 ``dst_dir``（合并；冲突加后缀）。

        Returns:
            ``[(dst, src), ...]``（回滚用，逆序执行移回）。
        """
        moved: list[tuple[str, str]] = []
        skip = skip_names or set()
        redirect = redirect_files or {}
        if not os.path.isdir(src_dir):
            return moved
        os.makedirs(dst_dir, exist_ok=True)
        try:
            entries = sorted(os.scandir(src_dir), key=lambda entry: entry.name)
        except OSError:
            return moved
        for entry in entries:
            if entry.name in skip:
                continue
            source = entry.path
            if entry.is_symlink():
                continue
            target_name = entry.name
            if target_name in redirect:
                target = redirect[target_name]
            else:
                target = os.path.join(dst_dir, target_name)
            final_dst = ProjectStorageService._unique_target(target)
            os.makedirs(os.path.dirname(os.path.abspath(final_dst)), exist_ok=True)
            try:
                shutil.move(source, final_dst)
                moved.append((final_dst, source))
            except OSError as exc:
                logger.warning("迁移移动失败 %s → %s: %s", source, final_dst, exc)
                continue
        return moved

    @staticmethod
    def _move_exports(
        exports_src: str,
        official_dst: str | None,
        supplement_dst: str | None,
    ) -> list[tuple[str, str]]:
        """拆分 ``09_导出文件``：exports/<task_id>/ → 正式导出/<task_id>；
        顶层 supplement_* → 补录；其余 → 正式导出/legacy_output。"""
        moved: list[tuple[str, str]] = []
        if not os.path.isdir(exports_src):
            return moved
        try:
            entries = sorted(os.scandir(exports_src), key=lambda entry: entry.name)
        except OSError:
            return moved
        for entry in entries:
            source = entry.path
            if entry.is_symlink():
                continue
            if entry.name == "exports" and entry.is_dir(follow_symlinks=False):
                # exports/<task_id>/ → 正式导出/<task_id>/
                for task_entry in sorted(os.scandir(source), key=lambda item: item.name):
                    if task_entry.is_symlink():
                        continue
                    target_root = official_dst
                    if not target_root:
                        continue
                    task_name = task_entry.name
                    target = os.path.join(target_root, task_name)
                    if task_entry.is_dir(follow_symlinks=False):
                        moved.extend(ProjectStorageService._move_tree_contents(task_entry.path, target))
                    else:
                        moved.extend(ProjectStorageService._move_into(task_entry.path, target))
            elif entry.name.startswith("supplement_") and entry.is_file(follow_symlinks=False):
                target_root = supplement_dst or os.path.join(
                    os.path.dirname(exports_src), "legacy_output"
                )
                moved.extend(
                    ProjectStorageService._move_into(source, os.path.join(target_root, entry.name))
                )
            else:
                # 顶层旧手工导出 / 其它内容 → 正式导出/legacy_output
                legacy_dst = os.path.join(official_dst or os.path.dirname(exports_src), "legacy_output")
                if entry.is_dir(follow_symlinks=False):
                    moved.extend(ProjectStorageService._move_tree_contents(source, legacy_dst))
                else:
                    moved.extend(ProjectStorageService._move_into(source, os.path.join(legacy_dst, entry.name)))
        return moved

    @staticmethod
    def _remove_legacy_empty_dirs(project_dir: str, from_version: int) -> None:
        """删除 v1/v2 遗留空目录与 legacy junction；非空目录保留并告警。"""
        legacy_names: set[str]
        if from_version >= 2:
            legacy_names = {
                "01_项目配置", "02_原始文件", "03_章节文本", "04_角色与声音",
                "05_分段音频", "06_章节音频", "07_合并音频", "08_质检记录",
                "09_导出文件", "cache", "logs",
                "voices", "segments", "chapters", "output", "exports",
            }
        else:
            legacy_names = {
                "voices", "segments", "chapters", "output", "cache", "logs",
                "08_质检记录", "01_项目配置",
            }
        for name in legacy_names:
            path = os.path.join(project_dir, name)
            if not os.path.lexists(path):
                continue
            if os.path.islink(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            if not os.path.isdir(path):
                continue
            try:
                os.rmdir(path)  # 只删空目录
            except OSError:
                logger.info("遗留目录非空，保留（用户文件不删除）：%s", path)

    @staticmethod
    def _rewrite_persisted_paths(project_dir: str, from_version: int) -> None:
        """重写 quality_state / voice_bindings / voice_cast / task DB 内的相对路径。"""
        v3 = project_paths.STORAGE_VERSION

        quality_path = project_paths.project_file(project_dir, "quality_state", prefer_version=v3)
        quality = _load_json_document(quality_path)
        if quality is not None:
            for rev_id, revision in list(quality.get("revisions", {}).items()):
                if isinstance(revision, dict) and revision.get("relative_path"):
                    new_value, changed = _rewrite_legacy_relative(project_dir, revision["relative_path"])
                    if changed:
                        quality["revisions"][rev_id]["relative_path"] = new_value
            for coll in ("export_jobs", "delivery_manifests"):
                for record_id, record in list(quality.get(coll, {}).items()):
                    if not isinstance(record, dict):
                        continue
                    outputs = record.get("outputs")
                    if not isinstance(outputs, list):
                        continue
                    for index, output in enumerate(outputs):
                        if isinstance(output, dict) and output.get("relative_path"):
                            new_value, changed = _rewrite_legacy_relative(project_dir, output["relative_path"])
                            if changed:
                                outputs[index]["relative_path"] = new_value
            # repair_history：每条记录内含 prepared 列表，列表项携带
            # target_relative_path / preserved_relative_path（均为 v2 项目相对路径）。
            # 这些字段之前未被覆盖，迁移成功后会残留旧 v2 路径，故此处补齐。
            for repair_id, repair in list(quality.get("repair_history", {}).items()):
                if not isinstance(repair, dict):
                    continue
                prepared = repair.get("prepared")
                if not isinstance(prepared, list):
                    continue
                for index, item in enumerate(prepared):
                    if not isinstance(item, dict):
                        continue
                    for field in ("target_relative_path", "preserved_relative_path"):
                        value = item.get(field)
                        if value:
                            new_value, changed = _rewrite_legacy_relative(project_dir, value)
                            if changed:
                                prepared[index][field] = new_value
            _atomic_write_json(quality_path, quality)

        bindings_path = project_paths.project_file(project_dir, "voice_bindings", prefer_version=v3)
        bindings = _load_json_document(bindings_path)
        if bindings is not None:
            inner = bindings.get("bindings")
            if isinstance(inner, dict):
                for role, path_value in list(inner.items()):
                    new_value, changed = _rewrite_legacy_relative(project_dir, path_value)
                    if changed:
                        inner[role] = new_value
            role_bindings = bindings.get("role_bindings")
            if isinstance(role_bindings, dict):
                for role_id, item in role_bindings.items():
                    if isinstance(item, dict) and item.get("project_voice_path"):
                        new_value, changed = _rewrite_legacy_relative(
                            project_dir, item["project_voice_path"]
                        )
                        if changed:
                            item["project_voice_path"] = new_value
            _atomic_write_json(bindings_path, bindings)

        cast_path = project_paths.project_file(project_dir, "voice_cast", prefer_version=v3)
        cast = _load_json_document(cast_path)
        if cast is not None:
            roles = cast.get("roles")
            if isinstance(roles, dict):
                for role_id, item in roles.items():
                    if isinstance(item, dict) and item.get("project_voice_path"):
                        new_value, changed = _rewrite_legacy_relative(
                            project_dir, item["project_voice_path"]
                        )
                        if changed:
                            item["project_voice_path"] = new_value
            elif isinstance(roles, list):
                for item in roles:
                    if isinstance(item, dict) and item.get("project_voice_path"):
                        new_value, changed = _rewrite_legacy_relative(
                            project_dir, item["project_voice_path"]
                        )
                        if changed:
                            item["project_voice_path"] = new_value
            _atomic_write_json(cast_path, cast)

        task_path = project_paths.project_file(project_dir, "task_db", prefer_version=v3)
        ProjectStorageService._rewrite_task_database(project_dir, task_path)

    @staticmethod
    def _rewrite_task_database(project_dir: str, task_path: str) -> None:
        """重写 production_tasks.sqlite3 内 options_json 的路径字段与 artifact_dir。"""
        if not os.path.isfile(task_path):
            return
        try:
            connection = sqlite3.connect(task_path, timeout=10.0)
        except sqlite3.Error as exc:
            logger.warning("重写任务库失败（跳过，resolver 兜底）: %s", exc)
            return
        try:
            try:
                rows = connection.execute(
                    "SELECT task_id, options_json, artifact_dir FROM production_tasks"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for task_id, raw_options, artifact_dir in rows:
                updates: dict[str, Any] = {}
                if raw_options:
                    try:
                        options = json.loads(raw_options)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        options = None
                    if isinstance(options, dict):
                        new_options, _ = _rewrite_document_paths(project_dir, options)
                        updated_json = json.dumps(new_options, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        if updated_json != raw_options:
                            updates["options_json"] = updated_json
                if artifact_dir:
                    new_dir, changed = _rewrite_legacy_relative(project_dir, artifact_dir)
                    if changed and new_dir != artifact_dir:
                        updates["artifact_dir"] = os.path.abspath(os.path.join(project_dir, new_dir))
                if updates:
                    placeholders = ", ".join(f"{key}=?" for key in updates)
                    connection.execute(
                        f"UPDATE production_tasks SET {placeholders} WHERE task_id=?",
                        (*updates.values(), str(task_id)),
                    )
            connection.commit()
        except sqlite3.Error as exc:
            logger.warning("重写任务库失败（跳过，resolver 兜底）: %s", exc)
        finally:
            connection.close()

    @staticmethod
    def _rollback_moves(moved: list[tuple[str, str]]) -> None:
        """逆序把已移动条目移回原位置（失败仅告警，backup 已保留）。"""
        for dst, src in reversed(moved):
            try:
                if os.path.lexists(dst):
                    os.makedirs(os.path.dirname(os.path.abspath(src)), exist_ok=True)
                    shutil.move(dst, src)
            except OSError as exc:
                logger.warning("迁移回滚失败 %s → %s: %s", dst, src, exc)

    # 迁移在执行过程中会「就地重写」的 authoritative 文档集合。必须与
    # ``_rewrite_persisted_paths``（step6）及 finalization（step7）保持同步：任何在
    # 生产迁移里被原地改写内容的文件，都必须进入此集合，否则回滚时会出现「位置回到
    # v2、内容仍是 v3」的混合态。此集合是回滚恢复的唯一权威来源，新增就地改写文档时
    # 必须同步扩展，避免再回到「手写固定文件名单、易漏项」的脆弱设计。
    _MUTATED_STATE_KEYS: tuple[str, ...] = (
        "project_meta",      # project.json        (step7: storage_version/directories/source_file/voice_bindings_path)
        "quality_state",     # quality_state.json  (step6: revisions/export_jobs/delivery_manifests relative_path)
        "voice_bindings",    # voice_bindings.json (step6: bindings/role_bindings project_voice_path)
        "voice_cast",        # voice_cast.json     (step6: roles project_voice_path)
        "task_db",           # production_tasks.sqlite3 (step6: options_json_string/artifact_dir)
    )


    @staticmethod
    def _restore_mutated_state_from_backup(
        project_dir: str, backup_path: str, from_version: int
    ) -> None:
        """从 migration 前完整 backup 恢复所有被「就地重写」的 authoritative state。

        迁移在 physical move 之后会对 project_meta / quality_state / voice_bindings /
        voice_cast / task_db 五类文档原地改写相对路径（含 SQLite）。回滚时不能只靠
        moved-log 把文件搬回原位——内容已是 v3。必须把这些文件的内容从 migration
        前的完整快照恢复，并清理可能残留的 v3 副本，杜绝「一半 v2 / 一半 v3」。

        恢复严格限定在本项目的 migration transaction：仅用 ``project_paths`` 解析器
        定位这 5 类文档的原始（``from_version``）与 v3 路径，从 backup 恢复其原始
        内容（二进制拷贝，SQLite 与 JSON 一视同仁），并删除可能残留的 v3 副本。不
        触碰 migration transaction 之外、或 backup 中不存在的文件。
        """
        import zipfile

        if not os.path.isfile(backup_path):
            return
        try:
            with zipfile.ZipFile(backup_path, "r") as archive:
                names = set(archive.namelist())
                for key in ProjectStorageService._MUTATED_STATE_KEYS:
                    original_path = project_paths.project_file(
                        project_dir, key, prefer_version=from_version
                    )
                    v3_path = project_paths.project_file(
                        project_dir, key, prefer_version=project_paths.STORAGE_VERSION
                    )
                    original_relative = os.path.relpath(original_path, project_dir).replace(os.sep, "/")
                    member = original_relative if original_relative in names else None
                    if member is None:
                        # 该文档在原项目中不存在（例如无任务库），跳过。
                        continue
                    target = original_path
                    # 清理可能残留的 v3 副本（move 回滚未完全覆盖时）。
                    if (
                        os.path.lexists(v3_path)
                        and os.path.normpath(v3_path) != os.path.normpath(target)
                    ):
                        try:
                            if os.path.isdir(v3_path) and not os.path.islink(v3_path):
                                os.rmdir(v3_path)
                            else:
                                os.remove(v3_path)
                        except OSError:
                            pass
                    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                    with archive.open(member) as input_file, open(target, "wb") as output_file:
                        shutil.copyfileobj(input_file, output_file)
        except (OSError, zipfile.BadZipFile) as exc:
            logger.warning("从备份恢复迁移改写状态失败（人工恢复需使用备份文件）: %s", exc)


def _tree_measure_single(entry) -> int:
    """统计一个根级条目的大小（unknown 报告用）。"""
    if entry.is_dir(follow_symlinks=False):
        total = 0
        for root, dirs, files in os.walk(entry.path, followlinks=False):
            dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
            for name in files:
                file_path = os.path.join(root, name)
                if os.path.islink(file_path):
                    continue
                try:
                    total += os.path.getsize(file_path)
                except OSError:
                    pass
        return total
    try:
        return entry.stat(follow_symlinks=False).st_size
    except OSError:
        return 0


def uuid_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:6]


__all__ = ["ProjectStorageService", "format_size"]
