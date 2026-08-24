"""项目服务：项目业务编排 + ``ProjectRepository``（禁止 import gradio）。

把项目的 CRUD / 校验 / 绑定等纯业务逻辑从 ``app.py`` 抽离到这里，使 UI 层只负责
编排与展示。所有方法均为 ``staticmethod``，无实例状态，便于单测。

注意：角色绑定（``bind_voice``）只负责 durable write 并返回目标音频路径；
``app.bind_voice`` 在写盘后通过 Session Snapshot apply boundary 刷新 compatibility
mirror，而不是依赖原地 mutate 或返回值自动传播。这与 R1「多标签各自持有独立
``SessionState``」的隔离约定一致。
``save_to_lib`` ���把音频存入 ``voice_library`` 目录，不改变角色绑定表。

阶段四重构：所有磁盘操作从 ``lib.project_manager (pm)`` 改为
``repositories.ProjectRepository`` / ``ConfigRepository``；pm 仅作为旧调用方兼容壳。
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any

from lib import config, project_paths, script_loader
from repositories.config_repo import ConfigRepository
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRepository

logger = logging.getLogger(__name__)


def _safe_name(s: str) -> str:
    """把任意字符串转成安全的文件名（替换文件系统非法字符为下划线）。"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


class ProjectMutationBlockedError(RuntimeError):
    """Stable domain error for writes rejected during active production."""

    code = "PROJECT_HAS_ACTIVE_PRODUCTION"

    def __init__(self, operation: str, task_id: str, status: str, project: str) -> None:
        super().__init__("项目存在活动生产任务，当前变更已拒绝")
        self.operation = str(operation)
        self.task_id = str(task_id)
        self.status = str(status)
        self.project = str(project)

    def as_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "operation": self.operation,
            "task_id": self.task_id,
            "status": self.status,
            "project_name": self.project,
        }


def ensure_project_mutation_allowed(
    project: str | None,
    operation: str,
) -> None:
    """Reject project-destructive writes while production is active."""
    from .production_jobs import ACTIVE_PRODUCTION_STATES

    records = TaskRepository.list_tasks(
        project=str(project or "").strip() or None,
    )
    active = next(
        (
            record
            for record in records
            if record.status in ACTIVE_PRODUCTION_STATES
            and (
                record.task_type == "synthesis"
                or (
                    record.task_type in {"supplement", "voice_preview"}
                    and bool(record.idempotency_key)
                )
                or record.task_type == "export"
            )
        ),
        None,
    )
    if active is not None:
        raise ProjectMutationBlockedError(
            operation,
            active.task_id,
            active.status,
            active.project,
        )


class ProjectService:
    """项目 CRUD / 校验 / 绑定。包装 ``ProjectRepository`` + ``lib.script_loader``。"""

    @staticmethod
    def scan_projects() -> list[str]:
        """扫描所有项目名。"""
        return ProjectRepository.scan_projects()

    @staticmethod
    def validate_script_file(script_file: str) -> list[str]:
        """加载并校验剧本文件，返回错误列表（空列表表示通过）。"""
        script = script_loader.load_script(script_file)
        return script_loader.validate_script(script)

    @staticmethod
    def create_project(name: str, script_file: str) -> None:
        """创建项目（统一委托结构化 JSON 导入服务）。"""
        from services.project_creation import ProjectCreationService

        ProjectCreationService.create_from_structured_script(name, script_file)

    @staticmethod
    def open_project(name: str):
        """打开项目，返回 ``(meta, script, voice_bindings)`` 元组。"""
        return ProjectRepository.load_project(name)

    @staticmethod
    def open_project_as_snapshot(name: str):
        """打开项目并返回 ``ProjectSnapshot``（含自动拆出的 bindings / role_categories）。"""
        return ProjectRepository.load_snapshot(name)

    @staticmethod
    def get_synthesis_overrides(name: str) -> dict:
        """读取项目级合成覆盖参数。"""
        return ProjectRepository.get_synthesis_overrides(name)

    @staticmethod
    def set_synthesis_overrides(name: str, overrides: dict) -> None:
        """保存项目级合成覆盖参数。"""
        ProjectRepository.set_synthesis_overrides(name, overrides)

    @staticmethod
    def get_synthesis_selections(name: str) -> dict:
        """读取项目级合成范围选择。"""
        return ProjectRepository.get_synthesis_selections(name)

    @staticmethod
    def set_synthesis_selections(name: str, selections: dict) -> None:
        """保存项目级合成范围选择。"""
        ProjectRepository.set_synthesis_selections(name, selections)

    @staticmethod
    def create_project_from_data(name: str, script: dict) -> Any:
        """Create a project from an in-memory structured script."""
        from services.project_creation import ProjectCreationService

        return ProjectCreationService.create_from_structured_data(name, script)

    @staticmethod
    def list_project_summaries() -> list[dict[str, Any]]:
        """Return resilient machine-facing project summaries."""
        from services.project_storage import ProjectStorageService

        summaries: list[dict[str, Any]] = []
        for name in ProjectRepository.scan_projects():
            item: dict[str, Any] = {
                "project_name": name,
                "title": name,
                "chapter_count": 0,
                "segment_count": 0,
                "completed_segments": 0,
                "progress": 0.0,
                "storage_bytes": 0,
                "modified_at": None,
            }
            try:
                meta, script, _bindings = ProjectRepository.load_project(name)
                voices, chapters = script_loader.resolve_collections(script)
                script_meta = script.get("meta") if isinstance(script.get("meta"), dict) else {}
                segment_count = sum(
                    len(chapter.get("segments", []))
                    for chapter in chapters
                    if isinstance(chapter, dict) and isinstance(chapter.get("segments"), list)
                )
                total = int(getattr(meta, "total_segments", 0) or segment_count)
                done = int(getattr(meta, "completed_count", 0) or 0)
                item.update({
                    "title": str(script_meta.get("title") or name),
                    "chapter_count": len(chapters),
                    "segment_count": total,
                    "completed_segments": done,
                    "progress": (done / total) if total else 0.0,
                    "role_count": len(voices),
                })
                storage = ProjectStorageService.summary(name)
                item["storage_bytes"] = storage.total_bytes
                if storage.modified_at is not None:
                    from datetime import datetime

                    item["modified_at"] = datetime.fromtimestamp(storage.modified_at).isoformat(timespec="seconds")
            except Exception as exc:
                # A malformed project must not prevent an Agent from seeing
                # the rest of the bookshelf.
                logger.warning("读取 MCP 项目摘要失败 %s: %s", name, exc)
                item["error"] = {
                    "code": type(exc).__name__.upper(),
                    "message": "项目摘要读取失败，请运行完整性检查。",
                }
            summaries.append(item)
        return summaries

    @staticmethod
    def get_project_summary(name: str) -> dict[str, Any]:
        """Return project metadata without embedding the full script payload."""
        from services.project_storage import ProjectStorageService

        meta, script, bindings = ProjectRepository.load_project(name)
        voices, chapters = script_loader.resolve_collections(script)
        script_meta = script.get("meta") if isinstance(script.get("meta"), dict) else {}
        segments = [
            segment
            for chapter in chapters
            if isinstance(chapter, dict) and isinstance(chapter.get("segments"), list)
            for segment in chapter["segments"]
            if isinstance(segment, dict)
        ]
        binding_values = bindings.get("bindings", {}) if isinstance(bindings, dict) else {}
        role_bindings = []
        project_dir = os.path.realpath(ProjectRepository.get_project_dir(name))
        for role in voices:
            value = binding_values.get(role)
            path = str(value) if value else None
            if path and not os.path.isabs(path):
                try:
                    path = project_paths.resolve_relative(project_dir, path)
                except ValueError:
                    path = os.path.join(project_dir, path)
            relative_path = None
            if path:
                try:
                    resolved = os.path.realpath(path)
                    if os.path.commonpath([project_dir, resolved]) == project_dir:
                        relative_path = os.path.relpath(
                            resolved, project_dir
                        ).replace(os.sep, "/")
                except ValueError:
                    relative_path = None
            role_bindings.append({
                "role": role,
                "bound": bool(value),
                "project_relative_path": relative_path,
                "exists": bool(path and os.path.isfile(path)),
            })
        total = int(getattr(meta, "total_segments", 0) or len(segments))
        done = int(getattr(meta, "completed_count", 0) or 0)
        failed = int(getattr(meta, "failed_count", 0) or 0)
        integrity = ProjectStorageService.check_integrity(name)
        integrity_summary = {
            "ok": bool(integrity.get("ok")),
            "issue_count": int(integrity.get("issue_count", 0) or 0),
            "repairable_issues": sum(1 for issue in integrity.get("issues", []) if issue.get("repairable")),
            "codes": [issue.get("code") for issue in integrity.get("issues", [])],
        }
        return {
            "project_name": name,
            "meta": script_meta,
            "project_meta": {
                "created_at": getattr(meta, "created_at", None),
                "updated_at": getattr(meta, "updated_at", None),
                "storage_version": getattr(meta, "storage_version", None),
            },
            "script_summary": {
                "title": str(script_meta.get("title") or name),
                "author": str(script_meta.get("author") or "未填写"),
                "chapters": len(chapters),
                "segments": total,
                "roles": len(voices),
            },
            "roles": list(voices.keys()),
            "voice_bindings": role_bindings,
            "synthesis": {
                "total_segments": total,
                "completed_segments": done,
                "failed_segments": failed,
                "pending_segments": max(total - done - failed, 0),
                "progress": (done / total) if total else 0.0,
            },
            "storage": ProjectStorageService.summary(name).as_dict(),
            "integrity": integrity_summary,
        }

    @staticmethod
    def delete_project(name: str) -> None:
        """删除项目。"""
        ensure_project_mutation_allowed(name, "delete_project")
        ProjectRepository.delete_project(name)

    @staticmethod
    def get_project_dir(name: str) -> str:
        """返回项目目录绝对路径。"""
        return ProjectRepository.get_project_dir(name)

    @staticmethod
    def update_segment_status(name: str, seg_id: str, status: str) -> None:
        """更新单段状态（包 ``ProjectRepository.update_segment_status``）。"""
        ensure_project_mutation_allowed(name, "update_segment_status")
        ProjectRepository.update_segment_status(name, seg_id, status)

    @staticmethod
    def set_data_dir(new_dir: str) -> str:
        """设置并持久化数据目录，且立即对本会话生效（新项目 / 扫描切到新目录）。

        Args:
            new_dir: 用户指定的新数据根目录（绝对或相对路径均可）。

        Returns:
            规范化后的绝对路径。
        """
        ensure_project_mutation_allowed(None, "set_data_dir")
        d = ConfigRepository.set_data_dir(new_dir)
        # Immediately move both the canonical repository and the short-lived
        # compatibility wrapper.  The wrapper delegates every disk operation
        # back to ProjectRepository, but its mutable roots remain for legacy
        # tests/integrations until that API is retired.
        ProjectRepository.WORKSPACE_ROOT = config.get_projects_root()
        ProjectRepository.LEGACY_ROOT = config.get_legacy_dir()
        ProjectRepository._INITIALIZED = True
        from lib import project_manager as compatibility_manager

        compatibility_manager.WORKSPACE_ROOT = ProjectRepository.WORKSPACE_ROOT
        compatibility_manager.LEGACY_ROOT = ProjectRepository.LEGACY_ROOT
        return d

    @staticmethod
    def bind_voice(project: str, role: str, audio_path: str, category: str = "未分类") -> str:
        """拷贝参考音频到项目 ``voices/`` 并更新 ``voice_bindings.json``。

        Args:
            project: 项目名。
            role: 角色名。
            audio_path: 参考音频源路径。
            category: 该角色绑定音色的分类（用于 ``v_role`` 下拉分组），
                默认 ``"未分类"``。写入 ``voice_bindings.json`` 的 ``role_categories``
                映射（additive，旧项目无此键向后兼容）。

        Returns:
            拷贝后的参考音频绝对路径。

        Raises:
            ValueError: project / role / audio_path 任一为空。
        """
        if not project or not role or not audio_path:
            raise ValueError("bind_voice 需要 project / role / audio_path 均非空")
        ensure_project_mutation_allowed(project, "bind_voice")
        d = ProjectRepository.get_project_dir(project)
        vd = project_paths.project_dir(d, "project_voices", create=True)
        os.makedirs(vd, exist_ok=True)
        ext = os.path.splitext(audio_path)[1] or ".wav"
        dest = os.path.join(vd, f"{_safe_name(role)}{ext}")
        shutil.copy2(audio_path, dest)

        # 使用 ProjectRepository 读写 bindings（原子写）
        bd = ProjectRepository.load_bindings(d)
        if not bd:
            bd = {"bindings": {}, "bound_at": "", "verified": []}
        bd["bindings"][role] = dest
        bd.setdefault("role_categories", {})[role] = category
        bd["bound_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        ProjectRepository.save_bindings(d, bd)
        # v2 项目保留 04_角色与声音/voice_bindings.json 镜像；v3 无 legacy 目录。
        if project_paths.detect_storage_version(d) < 3:
            try:
                shutil.copy2(
                    project_paths.project_file(d, "voice_bindings"),
                    os.path.join(vd, "voice_bindings.json"),
                )
            except OSError as exc:
                logger.warning("同步角色声音配置副本失败: %s", exc)
        return dest

    @staticmethod
    def save_to_lib(recorded: str | None, uploaded: str | None, name: str,
                    category: str = "") -> str:
        """把录制 / 上传的音频保存到 ``voice_library``，返回目标绝对路径。

        5.2：音色库路径在「调用时」动态解析 ``config.get_voice_library()``，不再在
        模块导入期缓存。这样运行期切换数据目录后，新保存的音色会自动落到新目录，
        旧路径缓存失效问题不再发生。

        Args:
            recorded: 录制得到的音频路径。
            uploaded: 上传得到的音频路径。
            name: 音频名称（用于文件名）。
            category: 分类名称（非空时文件名前缀为 ``{category}_{name}``）。

        Returns:
            保存后的音频绝对路径。

        Raises:
            ValueError: 名称为空，或 recorded / uploaded 均未提供。
        """
        if not name:
            raise ValueError("请填写音频名称")
        audio_file = recorded or uploaded
        if not audio_file:
            raise ValueError("请先录制或上传音频")
        # 5.2：动态读取当前数据目录下的音色库（切换数据目录后立即生效）
        voice_lib = config.get_voice_library()
        os.makedirs(voice_lib, exist_ok=True)
        ext = os.path.splitext(audio_file)[1] or ".wav"
        safe = _safe_name(name)
        # 有分类时前缀 {category}_{name}，与 voice_lib._category_of 推导规则一致
        prefix = f"{_safe_name(category)}_" if category and category.strip() and category != "未分类" else ""
        dest = os.path.join(voice_lib, f"{prefix}{safe}{ext}")
        shutil.copy2(audio_file, dest)
        return dest
