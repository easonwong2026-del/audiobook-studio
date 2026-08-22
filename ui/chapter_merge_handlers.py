"""Gradio handlers for the independent Chapter → Book merge workflow."""
from __future__ import annotations

import html
from typing import Any

import gradio as gr

from services.chapter_merge_executor import (
    ChapterMergeExecutor,
    MergeExecutionError,
    MergeExecutionResult,
)
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


def _resolution_defaults(plan: MergePlan | None) -> dict[str, Any]:
    if plan is None:
        return {"voice_conflicts": {}}
    return {
        "voice_conflicts": {
            str(row.get("role_key") or ""): ""
            for row in plan.voice_compatibility.roles
            if str(row.get("status") or "") in {"SOURCE_ONLY", "CONFLICT"}
        }
    }


def clear_merge_execution_controls() -> tuple:
    """Clear only C.2 transient controls; catalog/planner arity is untouched."""
    return (
        _update(value={"voice_conflicts": {}}),
        _update(value=False, interactive=False),
        None,
        _update(interactive=False),
        "",
        None,
    )


def prepare_merge_execution_controls(plan: MergePlan | None) -> tuple:
    """Expose resolution controls after Analyze, without executing anything."""
    if plan is None:
        return clear_merge_execution_controls()
    planning_allowed = bool(plan.planning_status == "PLANNING_ALLOWED")
    return (
        _update(value=_resolution_defaults(plan)),
        _update(value=False, interactive=planning_allowed),
        None,
        _update(interactive=False),
        "",
        None,
    )


def invalidate_merge_execution_state() -> tuple:
    """Invalidate confirmation/result when a resolution or input changes."""
    return (
        _update(value=False, interactive=True),
        None,
        _update(interactive=False),
        "",
        None,
    )


def confirm_merge_plan(
    confirmed: bool,
    source_name: str,
    target_name: str,
    plan: MergePlan | None,
    resolutions: Any,
    ss=None,
) -> tuple:
    """Create a fresh identity-bound confirmation; this callback never mutates projects."""
    if not confirmed:
        return None, _update(interactive=False), "", None
    if plan is None or not source_name or not target_name:
        return None, _update(interactive=False), "⚠️ 请先 Analyze 当前 Chapter → Book 计划。", None
    try:
        confirmation = ChapterMergeExecutor.prepare_confirmation(
            plan, resolutions, session=ss
        )
    except MergeExecutionError as exc:
        return None, _update(interactive=False), f"⛔ 确认失败：`{html.escape(exc.code)}` — {html.escape(str(exc))}", None
    return (
        confirmation.as_dict(),
        _update(interactive=True),
        "✅ 已生成当前 source / target / plan / resolution / selection revision 的新确认态；现在才可执行。",
        None,
    )


def _render_execution_result(result: MergeExecutionResult) -> str:
    lines = [
        "### Chapter → Book 执行结果",
        f"- 状态：**{html.escape(result.status)}**；阶段：`{html.escape(result.stage)}`",
        f"- Transaction ID：`{html.escape(result.transaction_id)}`",
        f"- Backup：`{html.escape(result.backup_path or '—')}`",
        f"- 导入 segments：{result.imported_segment_count}；音频文件：{result.imported_audio_count}",
    ]
    if result.error_code:
        lines.append(f"- 错误：`{html.escape(result.error_code)}` — {html.escape(result.error)}")
    if result.rollback_status != "NOT_STARTED":
        lines.append(f"- Rollback：`{html.escape(result.rollback_status)}`")
    if result.integrity:
        target = result.integrity.get("target") or result.integrity.get("rollback") or result.integrity.get("staged")
        if isinstance(target, dict):
            lines.append(f"- Integrity：`{'PASS' if target.get('ok') else 'FAIL'}`")
    if result.warnings:
        lines.extend(["", "#### Warnings", *[f"- {html.escape(item)}" for item in result.warnings]])
    lines.extend(["", f"- Journal：`{html.escape(result.journal_path)}`"])
    return "\n".join(lines)


def execute_merge_plan(
    plan: MergePlan | None,
    resolutions: Any,
    confirmation: Any,
    ss=None,
) -> tuple:
    """Call the service-owned executor and render its structured result."""
    if plan is None:
        return "⛔ 没有可执行的 MergePlan。", None, _update(interactive=False), _update(value=False, interactive=False), None
    result = ChapterMergeExecutor.execute(
        plan,
        resolutions,
        confirmation,
        session=ss,
    )
    return (
        _render_execution_result(result),
        result.as_dict(),
        _update(interactive=False),
        _update(value=False, interactive=False),
        None,
    )


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
            f"- Voice Cast：{plan.voice_compatibility.compatible_count} compatible；{plan.voice_compatibility.source_only_count} source-only；{plan.voice_compatibility.conflict_count} conflict",
            f"- QA records：{plan.qa_inventory.get('record_count', 0)}；Revision records：{plan.revision_inventory.get('record_count', 0)}",
            f"- Target backup：`{html.escape(str(plan.backup_policy.get('target_backup') or 'REQUIRED'))}`",
            f"- Plan token：`{plan.plan_token}`",
            "",
            "#### 冲突与警告",
            _conflict_markdown(plan),
            "",
            "该结果仍是只读 Plan；执行必须经过显式 resolution 与 fresh confirmation。",
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


def refresh_merge_workflow_controls(ss=None) -> tuple:
    """Refresh planner controls and clear all C.2 transient execution state."""
    return (*refresh_merge_planner_controls(ss), *clear_merge_execution_controls())


__all__ = [
    "analyze_merge_plan",
    "clear_merge_execution_controls",
    "confirm_merge_plan",
    "execute_merge_plan",
    "invalidate_merge_execution_state",
    "invalidate_merge_plan",
    "is_planner_enabled",
    "prepare_merge_execution_controls",
    "refresh_merge_planner_controls",
    "refresh_merge_workflow_controls",
    "render_merge_plan",
]
