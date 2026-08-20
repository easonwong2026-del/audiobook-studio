"""Gradio handlers for the read-only Chapter → Book merge planner."""
from __future__ import annotations

import html
from typing import Any

import gradio as gr

from services.chapter_merge_planner import (
    ChapterMergePlanner,
    MergePlan,
)


def _update(**kwargs: Any) -> Any:
    return gr.update(**kwargs)


def _choice(reference) -> tuple[str, str]:
    title = str(reference.chapter_title or reference.title or reference.project_name)
    label = f"{reference.project_name} · {title}"
    return label, reference.project_name


def _parent_name(source_name: str) -> str | None:
    if not source_name:
        return None
    for reference in ChapterMergePlanner.list_source_chapters():
        if reference.project_name == source_name:
            target_id = reference.parent_project_id
            if not target_id:
                return None
            for book in ChapterMergePlanner.list_target_books(source_name):
                if book.project_id == target_id:
                    return book.project_name
            return None
    return None


def refresh_merge_planner_controls(ss=None) -> tuple:
    """Refresh the dedicated planner state without touching catalog state."""
    sources = ChapterMergePlanner.list_source_chapters()
    selected = str(getattr(ss, "selected_project", "") or "") if ss is not None else ""
    selected_source = next(
        (item for item in sources if item.project_name == selected), None
    )
    source_value = selected_source.project_name if selected_source else None
    targets = ChapterMergePlanner.list_target_books(source_value)
    parent = _parent_name(source_value or "")
    target_value = parent if parent in {item.project_name for item in targets} else None
    enabled = bool(source_value and target_value)
    return (
        _update(choices=[_choice(item) for item in sources], value=source_value, interactive=bool(selected_source)),
        _update(choices=[_choice(item) for item in targets], value=target_value, interactive=bool(selected_source and targets)),
        _update(interactive=enabled),
        "选择一个 Chapter 后，可分析其到目标 Book 的只读合并计划。",
        None,
    )


def invalidate_merge_plan() -> tuple[str, None]:
    """Detach a displayed plan whenever either planner input changes."""
    return "选择变更后，旧的合并计划已清除；请重新分析。", None


def _conflict_markdown(plan: MergePlan) -> str:
    if not plan.conflicts:
        return "- 无冲突或警告。"
    lines = []
    for conflict in plan.conflicts:
        marker = "⛔" if conflict.blocking else "⚠️" if conflict.severity == "WARNING" else "ℹ️"
        lines.append(
            f"- {marker} `{html.escape(conflict.code)}`："
            f"{html.escape(conflict.message)}"
        )
    return "\n".join(lines)


def render_merge_plan(plan: MergePlan) -> str:
    source = plan.source_project.project_name if plan.source_project else "—"
    target = plan.target_project.project_name if plan.target_project else "—"
    source_audio = plan.source_inventory.audio or {}
    return "\n".join(
        [
            "### Chapter → Book 合并计划（只读）",
            f"- 来源 Chapter：`{html.escape(source)}`",
            f"- 目标 Book：`{html.escape(target)}`",
            f"- 规划状态：**{plan.planning_status}**",
            f"- 执行资格：**{plan.execution_eligibility}**",
            f"- 来源段落：{plan.source_inventory.total_segments}；目标已有段落：{plan.target_inventory.total_segments}",
            f"- 音频覆盖：`{html.escape(str(source_audio.get('coverage') or 'UNKNOWN'))}`",
            f"- 目标插入：`{html.escape(str(plan.placement.get('mode') or 'UNRESOLVABLE'))}`",
            f"- Plan token：`{plan.plan_token}`",
            "",
            "#### 冲突与警告",
            _conflict_markdown(plan),
            "",
            "该结果仅用于 PLAN / REPORT / TOKEN；本版本没有合并执行按钮或执行 API。",
        ]
    )


def analyze_merge_plan(source_name: str, target_name: str, ss=None) -> tuple[str, MergePlan | None]:
    """Analyze the explicit dropdown pair without opening or switching projects."""
    selected = str(getattr(ss, "selected_project", "") or "") if ss is not None else ""
    if not source_name or not target_name:
        return "⚪ 请先选择来源 Chapter 和目标 Book。", None
    if not selected:
        return "⚪ 请先从书架选择一个 Chapter。", None
    if str(source_name) != selected:
        return "⚠️ 当前书架选择已变化，请先刷新 planner 再重新分析。", None
    plan = ChapterMergePlanner.plan_merge(source_name, target_name, session=ss)
    return render_merge_plan(plan), plan


def is_planner_enabled(source_name: str, target_name: str, ss=None) -> bool:
    selected = str(getattr(ss, "selected_project", "") or "") if ss is not None else ""
    return bool(source_name and target_name and source_name == selected)


__all__ = [
    "analyze_merge_plan",
    "invalidate_merge_plan",
    "is_planner_enabled",
    "refresh_merge_planner_controls",
    "render_merge_plan",
]
