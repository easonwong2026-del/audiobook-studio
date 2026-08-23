"""Project View handlers for the opened project's chapter tree.

This module owns the UI-facing HTML rendering boundary.  Project data is
loaded through :class:`services.project.ProjectService`; the application
module only wires the returned HTML into the existing project-page component.
"""
from __future__ import annotations

import logging

from lib import chapter_identity
from services.project import ProjectService
from services.project_storage import ProjectStorageService

logger = logging.getLogger(__name__)


def refresh_project_storage(ss) -> str:
    """Show the active project root and the recursive storage summary."""
    if not ss or not ss.project:
        return "项目目录、存储占用和完整性状态会显示在这里。"
    try:
        return ProjectStorageService.format_summary(ss.project)
    except Exception as exc:
        logger.warning("读取项目存储信息失败: %s", exc)
        return f"#### 项目存储\n❌ 无法读取项目目录：{exc}"


def render_chapter_tree(project: str | None) -> str:
    """Render the opened project's chapter tree without changing its contract.

    The returned HTML intentionally preserves the legacy project-page shape:
    chapter ``<details>`` blocks, completion counts, status icons, segment
    ordering, role labels, and the empty-project fallback.
    """
    if not project:
        return "<i>未打开项目</i>"
    try:
        meta, script, _ = ProjectService.open_project(project)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("render_chapter_tree 读 %s 失败: %s", project, exc)
        return "<i>未打开项目</i>"

    status_map = meta.segments_status
    lines = []
    chapters = script.get("chapters", [])
    for chapter_index, chapter in enumerate(chapters):
        segments = chapter.get("segments", [])
        done_n = sum(
            1 for segment in segments if status_map.get(segment["id"]) == "done"
        )
        lines.append(
            f"<details><summary>📖 "
            f"{chapter_identity.chapter_label(chapter, chapter_index, len(chapters))}"
            f"（{done_n}/{len(segments)} 完成）</summary>"
        )
        for segment in segments:
            segment_id = segment["id"]
            status = status_map.get(segment_id, "pending")
            icon = "✅" if status == "done" else ("❌" if status == "failed" else "⬜")
            text = (segment.get("text", "") or "")[:40]
            lines.append(
                f"<div style='margin-left:18px;font-size:13px'>"
                f"{icon} <b>{segment_id}</b> [{segment.get('role', '')}] {text}</div>"
            )
        lines.append("</details>")
    return "\n".join(lines)
