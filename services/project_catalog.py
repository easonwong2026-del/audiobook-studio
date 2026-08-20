"""统一项目目录服务：扫描 + 轻量摘要 + 搜索（纯服务层，禁止 import gradio）。

书架 / 项目 Dropdown / 搜索共用同一份 ``ProjectSummary`` 数据源，磁盘读路径
唯一收敛到 ``ProjectRepository.list_project_summaries()``；搜索算法不复制。
"""
from __future__ import annotations

import logging

from lib.types import ProjectSummary
from repositories.project_repo import ProjectRepository

logger = logging.getLogger(__name__)


class ProjectCatalogService:
    """统一项目目录：扫描 + 轻量摘要 + 搜索。纯服务层，不依赖 UI。"""

    @staticmethod
    def scan() -> list[ProjectSummary]:
        """扫描全部项目并产出轻量摘要（含 title/author）。

        Returns:
            按项目名排序的 ``ProjectSummary`` 列表。
        """
        return ProjectRepository.list_project_summaries()

    @staticmethod
    def search_projects(query: str = "") -> list[ProjectSummary]:
        """按 query 过滤（空查询 = 全部）。

        匹配域：``project_name`` / ``title`` / ``author``；大小写不敏感
        （``q.lower() in field.lower()``）；中文 substring 由 Python ``in``
        天然支持。返回按 ``project_name`` 排序。

        Args:
            query: 搜索关键词；空 / None 表示返回全部。

        Returns:
            过滤后的 ``ProjectSummary`` 列表。
        """
        return ProjectCatalogService.filter_projects(
            ProjectCatalogService.scan(), query
        )

    @staticmethod
    def filter_projects(
        summaries: list[ProjectSummary], query: str = ""
    ) -> list[ProjectSummary]:
        """Filter an already-scanned catalog without reading the disk again."""
        q = str(query or "").strip().lower()
        if not q:
            return summaries
        return [
            summary for summary in summaries
            if q in (summary.project_name + summary.title + summary.author).lower()
        ]

    @staticmethod
    def get_summary(project_name: str) -> ProjectSummary | None:
        """单项目摘要；不存在 / 损坏返回 None（不抛）。

        Args:
            project_name: 项目名。

        Returns:
            ``ProjectSummary`` 或 ``None``。
        """
        target = str(project_name or "")
        if not target:
            return None
        try:
            for summary in ProjectRepository.list_project_summaries():
                if summary.project_name == target:
                    return summary
        except Exception as exc:  # pragma: no cover - 防御性容错
            logger.warning("get_summary 读取失败 %s: %s", target, exc)
        return None


__all__ = ["ProjectCatalogService"]
