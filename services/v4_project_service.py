"""统一项目服务：V3 / V4 项目混合扫描、格式识别、打开、状态与迁移入口。

原五步页面（项目管理 / 概览等）通过本服务同时管理 V3 与 V4 项目，
差异在服务层内部判定，页面不写 ``if project_version == ...`` 分支。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from lib import config
from lib.snapshot import ProjectSnapshot
from repositories.project_repo import ProjectRepository
from repositories.project_v4_repository import ProjectV4Repository
from services.migration_v3_to_v4 import MigrationResult, V3ToV4MigrationService

SCHEMA_V4 = "audiobook-project-v4"


@dataclass(frozen=True)
class ProjectInfo:
    """项目列表中一行所需的信息（V3 / V4 通用）。"""

    name: str
    project_format: str  # "v3" | "v4"
    title: str
    updated_at: str
    total_chapters: int
    completed_segments: int
    total_segments: int
    status: str  # "ready" | "no-plan" | "incomplete" | "unknown"


@dataclass
class OpenProjectContext:
    """打开项目后的统一上下文（页面 handler 据此渲染，不关心底层格式差异）。"""

    name: str
    project_format: str
    project_path: Path
    # v4 only
    manifest: Optional[Any] = None
    script: Optional[Any] = None
    speakers: Optional[Any] = None
    production: Optional[Any] = None
    # v3 only
    v3_snapshot: Optional[ProjectSnapshot] = None

    @property
    def is_v4(self) -> bool:
        return self.project_format == "v4"


class V4ProjectService:
    """统一项目服务（静态方法，无实例状态，便于单测）。"""

    @staticmethod
    def root() -> Path:
        return Path(config.get_projects_root())

    @staticmethod
    def detect_format(name: str) -> str | None:
        """判断项目格式：``"v4"`` / ``"v3"`` / ``None``（不存在或非法）。"""
        path = V4ProjectService.root() / name
        manifest = path / "project.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            if data.get("schema_version") == SCHEMA_V4:
                return "v4"
        if (path / "structured_script.json").is_file():
            return "v3"
        return None

    @staticmethod
    def is_v4_project(name: str) -> bool:
        return V4ProjectService.detect_format(name) == "v4"

    @staticmethod
    def scan_projects() -> list[ProjectInfo]:
        """扫描数据目录下所有 V3 / V4 项目，返回信息列表（按名称排序）。"""
        root = V4ProjectService.root()
        if not root.is_dir():
            return []
        projects: list[ProjectInfo] = []
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if not path.is_dir():
                continue
            name = path.name
            if name.startswith(".") or name in {
                ".v3-backups", "voice_library", "exports",
            }:
                continue
            fmt = V4ProjectService.detect_format(name)
            if fmt == "v4":
                projects.append(V4ProjectService._scan_v4(path))
            elif fmt == "v3":
                projects.append(V4ProjectService._scan_v3(path))
        return projects

    @staticmethod
    def _scan_v4(path: Path) -> ProjectInfo:
        manifest: dict[str, Any] = {}
        try:
            manifest = json.loads(
                (path / "project.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            pass
        script: dict[str, Any] = {}
        try:
            script = json.loads(
                (path / "script/script.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            pass
        total_chapters = len(script.get("chapters", []))
        total_segments = sum(
            len(ch.get("segments", [])) for ch in script.get("chapters", [])
        )
        runtime_path = path / "runtime/runtime.db"
        completed = 0
        if runtime_path.is_file():
            try:
                with sqlite3.connect(runtime_path) as connection:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM synthesis_tasks "
                        "WHERE status = 'completed'"
                    ).fetchone()
                completed = int(row[0] or 0)
            except sqlite3.Error:
                completed = 0
        status = "no-plan" if total_segments == 0 or completed == 0 else (
            "ready" if completed >= total_segments else "incomplete"
        )
        return ProjectInfo(
            name=path.name,
            project_format="v4",
            title=str(manifest.get("title") or path.name),
            updated_at=str(manifest.get("updated_at") or ""),
            total_chapters=total_chapters,
            completed_segments=completed,
            total_segments=total_segments,
            status=status,
        )

    @staticmethod
    def _scan_v3(path: Path) -> ProjectInfo:
        try:
            snap = ProjectRepository.load_snapshot(path.name)
        except Exception:  # noqa: BLE001 - tolerate partial v3 projects
            return ProjectInfo(
                name=path.name, project_format="v3", title=path.name,
                updated_at="", total_chapters=0, completed_segments=0,
                total_segments=0, status="unknown",
            )
        meta = getattr(snap, "meta", None)
        script = getattr(snap, "script", {}) or {}
        total_chapters = len(script.get("chapters", []))
        total = int(getattr(meta, "total_segments", 0) or 0)
        done = int(getattr(meta, "completed_count", 0) or 0)
        return ProjectInfo(
            name=path.name,
            project_format="v3",
            title=str(script.get("meta", {}).get("title") or path.name),
            updated_at=getattr(meta, "updated_at", "") or "",
            total_chapters=total_chapters,
            completed_segments=done,
            total_segments=total,
            status="ready" if total and done >= total else (
                "incomplete" if total else "unknown"
            ),
        )

    @staticmethod
    def open_project(name: str) -> OpenProjectContext | None:
        """打开项目，返回统一上下文；项目不存在返回 None。

        - V4：读取 project.json / script.json / speakers.json，并保证 runtime.db 初始化。
        - V3：读取 ProjectSnapshot（与旧 ``ProjectService.open_project_as_snapshot`` 一致）。
        """
        fmt = V4ProjectService.detect_format(name)
        if fmt is None:
            return None
        path = V4ProjectService.root() / name
        context = OpenProjectContext(name=name, project_format=fmt, project_path=path)
        if fmt == "v4":
            from domain.v4 import ScriptDocument, SpeakersDocument
            from repositories.production_repository import ProductionRepository
            from repositories.runtime_repository import RuntimeRepository
            from domain.v4 import ProjectManifest
            RuntimeRepository(path / "runtime/runtime.db").initialize()
            try:
                context.manifest = ProjectManifest.from_dict(
                    json.loads(
                        (path / "project.json").read_text(encoding="utf-8")
                    )
                )
            except Exception:  # noqa: BLE001
                context.manifest = None
            source = (path / "source/source.txt").read_text(encoding="utf-8")
            context.script = ScriptDocument.from_dict(
                json.loads((path / "script/script.json").read_text(encoding="utf-8")),
                source,
            )
            context.speakers = SpeakersDocument.from_dict(
                json.loads(
                    (path / "script/speakers.json").read_text(encoding="utf-8")
                )
            )
            context.production = ProductionRepository(path)
            return context
        context.v3_snapshot = ProjectRepository.load_snapshot(name)
        return context

    @staticmethod
    def project_status(name: str) -> str:
        """项目状态文本（供 UI 展示，无需区分 V3/V4 细节）。"""
        info = next(
            (item for item in V4ProjectService.scan_projects() if item.name == name),
            None,
        )
        if info is None:
            return "未知项目"
        if info.project_format == "v4" and info.total_segments == 0:
            return "尚无章节内容"
        if info.total_segments and info.completed_segments >= info.total_segments:
            return f"已合成 {info.completed_segments}/{info.total_segments} 段"
        return (
            f"进行中 {info.completed_segments}/"
            f"{info.total_segments or 0} 段"
        )

    @staticmethod
    def migrate_to_v4(
        v3_name: str, destination_name: str | None = None
    ) -> MigrationResult:
        """复制迁移 V3 → V4（不覆盖原项目，含备份与幂等标记）。"""
        if V4ProjectService.detect_format(v3_name) != "v3":
            raise ValueError(f"{v3_name} 不是可迁移的 V3 项目")
        source = ProjectRepository.get_project_dir(v3_name)
        return V3ToV4MigrationService().migrate(
            source, V4ProjectService.root(), destination_name=destination_name
        )
