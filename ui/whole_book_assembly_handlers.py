"""Gradio handlers for the dedicated Whole-book Assembly workflow."""
from __future__ import annotations

import html
from typing import Any

import gradio as gr

from services.project_catalog import RELATION_STANDALONE, ProjectCatalogService
from services.whole_book_assembly import (
    BookAssemblyChapterPlan,
    BookAssemblyExecutionResult,
    BookAssemblyPlan,
    WholeBookAssemblyError,
    WholeBookAssemblyService,
)

_RESOLVABLE_ASSEMBLY_BLOCKERS = frozenset(
    {"SOURCE_ONLY_ROLE", "VOICE_BINDING_CONFLICT"}
)


def _update(**kwargs: Any) -> Any:
    return gr.update(**kwargs)


def _book_names() -> list[str]:
    hierarchy = ProjectCatalogService.scan_hierarchy()
    return [
        item.project_name
        for item in hierarchy.books
        if item.relation_status == RELATION_STANDALONE and item.project_id
    ]


def _selected_book(ss) -> str:
    selected = str(getattr(ss, "selected_project", "") or "") if ss is not None else ""
    summary = ProjectCatalogService.get_summary(selected) if selected else None
    if (
        summary is not None
        and summary.project_kind == "book"
        and summary.relation_status == RELATION_STANDALONE
    ):
        return selected
    return ""


def _empty_execution_controls() -> tuple:
    return (
        _update(value={"chapters": {}}),
        _update(value=False, interactive=False),
        None,
        _update(interactive=False),
        "",
        None,
    )


def refresh_assembly_workflow_controls(ss=None) -> tuple:
    """Refresh only Assembly-owned components; Catalog output arity is untouched."""
    books = _book_names()
    target = _selected_book(ss)
    if target:
        summary = f"当前目标 Book：`{html.escape(target)}`；点击分析生成整书装配计划。"
    elif getattr(ss, "selected_project", None):
        summary = "当前选择不是可装配的 Book；请选择一个独立 Book 项目。"
    else:
        summary = "从书架选择一个 Book 后，可分析其关联 Chapter 的整书装配。"
    return (
        _update(choices=books, value=target or None, interactive=False),
        _update(interactive=bool(target)),
        summary,
        None,
        _update(value={"chapters": {}}),
        _update(value=False, interactive=False),
        None,
        _update(interactive=False),
        "",
        None,
    )


def refresh_assembly_after_data_dir(new_dir: str, ss=None) -> tuple:
    """Clear Assembly after a successful data-root switch only.

    ``apply_data_dir`` returns an empty path on failure, so the previous
    transient state remains visible and recoverable in that case.
    """
    if not str(new_dir or "").strip():
        return tuple(_update() for _ in range(10))
    return refresh_assembly_workflow_controls(ss)


def invalidate_assembly_plan() -> tuple[str, None]:
    return "选择或关系状态已变化，旧的整书装配计划已清除；请重新分析。", None


def _resolution_defaults(plan: BookAssemblyPlan | None) -> dict[str, Any]:
    if plan is None:
        return {"chapters": {}}
    chapters: dict[str, Any] = {}
    for chapter in plan.ordered_chapters:
        if chapter.merge_plan is None:
            continue
        conflicts = {
            str(row.get("role_key") or ""): ""
            for row in chapter.merge_plan.voice_compatibility.roles
            if str(row.get("status") or "") in {"SOURCE_ONLY", "CONFLICT"}
        }
        chapters[chapter.chapter_project_id or chapter.chapter_project_name] = {
            "voice_conflicts": conflicts
        }
    return {"chapters": chapters}


def prepare_assembly_execution_controls(plan: BookAssemblyPlan | None) -> tuple:
    if plan is None:
        return _empty_execution_controls()
    blocker_codes = {item.code for item in plan.blocking_conflicts}
    can_confirm = not blocker_codes or blocker_codes <= _RESOLVABLE_ASSEMBLY_BLOCKERS
    return (
        _update(value=_resolution_defaults(plan)),
        _update(value=False, interactive=can_confirm),
        None,
        _update(interactive=False),
        "",
        None,
    )


def clear_assembly_execution_controls() -> tuple:
    return _empty_execution_controls()


def invalidate_assembly_execution_state() -> tuple:
    return (
        _update(value=False, interactive=True),
        None,
        _update(interactive=False),
        "",
        None,
    )


def _conflict_text(chapter: BookAssemblyChapterPlan) -> str:
    if not chapter.conflicts:
        return "—"
    return ", ".join(item.code for item in chapter.conflicts)


def render_assembly_plan(plan: BookAssemblyPlan) -> str:
    status = plan.aggregate_status
    lines = [
        "### Whole-book Assembly Plan（只读）",
        f"- 目标 Book：`{html.escape(plan.target_book_name or '—')}`",
        f"- Aggregate status：**{html.escape(status)}**",
        f"- Chapters：{len(plan.ordered_chapters)}；segments：{plan.total_segment_count}；audio：{plan.total_audio_count}",
        f"- Assembly token：`{html.escape(plan.assembly_token)}`",
        "",
        "| 顺序 | Chapter | 初始状态 | 关系 | 冲突 |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for chapter in plan.ordered_chapters:
        lines.append(
            f"| {chapter.order:02d} | `{html.escape(chapter.chapter_project_name)}` | "
            f"**{html.escape(chapter.initial_plan_status)}** | "
            f"{html.escape(chapter.relation_status)} | "
            f"{html.escape(_conflict_text(chapter))} |"
        )
    if plan.warnings:
        lines.extend(["", "#### Warnings", *[f"- {html.escape(item)}" for item in plan.warnings]])
    if plan.blocking_conflicts:
        lines.extend(
            [
                "",
                "#### Blocking Chapters / Preconditions",
                *[
                    f"- `{html.escape(item.code)}` "
                    f"{html.escape(item.chapter_project_name or plan.target_book_name)}："
                    f"{html.escape(item.message)}"
                    for item in plan.blocking_conflicts
                ],
            ]
        )
    lines.extend(["", "执行必须使用当前计划、每 Chapter fresh replan 和显式确认。"])
    return "\n".join(lines)


def analyze_assembly(target_name: str, ss=None) -> tuple[str, BookAssemblyPlan | None]:
    selected = _selected_book(ss)
    if not target_name or not selected:
        return "⚪ 请先从书架选择一个 Book。", None
    if str(target_name) != selected:
        return "⚠️ 目标 Book 与 bookshelf selection 不一致，请重新选择后再分析。", None
    plan = WholeBookAssemblyService.plan_assembly(target_name, session=ss)
    return render_assembly_plan(plan), plan


def confirm_assembly_plan(
    confirmed: bool,
    target_name: str,
    plan: BookAssemblyPlan | None,
    resolutions: Any,
    ss=None,
) -> tuple:
    if not confirmed:
        return None, _update(interactive=False), "", None
    if plan is None or not target_name:
        return None, _update(interactive=False), "⚠️ 请先分析当前 Whole-book Assembly。", None
    try:
        confirmation = WholeBookAssemblyService.prepare_confirmation(
            plan, resolutions, session=ss
        )
    except WholeBookAssemblyError as exc:
        return (
            None,
            _update(interactive=False),
            f"⛔ 确认失败：`{html.escape(exc.code)}` — {html.escape(str(exc))}",
            None,
        )
    return (
        confirmation.as_dict(),
        _update(interactive=True),
        "✅ 已生成当前目标 / membership / order / resolution / selection revision 的整书确认态。",
        None,
    )


def _render_execution_result(result: BookAssemblyExecutionResult) -> str:
    lines = [
        "### Whole-book Assembly Result",
        f"- 状态：**{html.escape(result.status)}**；Assembly ID：`{html.escape(result.assembly_id)}`",
        (
            f"- merged this run：{result.merged_this_run}；already merged："
            f"{result.already_merged}；blocked：{result.blocked}；"
            f"failed：{result.failed}；not attempted：{result.not_attempted}"
        ),
        f"- segments added：{result.total_segments_added}；audio copied：{result.total_audio_copied}",
    ]
    if result.final_integrity:
        lines.append(
            f"- Final integrity：`{'PASS' if result.final_integrity.get('ok') else 'FAIL'}`"
        )
    if result.error_code:
        lines.append(
            f"- Error：`{html.escape(result.error_code)}` — {html.escape(result.error)}"
        )
    lines.extend(["", "| 顺序 | Chapter | 结果 | transaction | backup |", "| ---: | --- | --- | --- | --- |"])
    for item in result.chapter_results:
        lines.append(
            f"| {item.order:02d} | `{html.escape(item.chapter_project_name)}` | "
            f"**{html.escape(item.execution_result)}** | "
            f"`{html.escape(item.transaction_id or '—')}` | "
            f"`{html.escape(item.backup_reference or '—')}` |"
        )
    return "\n".join(lines)


def execute_assembly_plan(
    plan: BookAssemblyPlan | None,
    resolutions: Any,
    confirmation: Any,
    ss=None,
) -> tuple:
    if plan is None:
        return "⛔ 没有可执行的 BookAssemblyPlan。", None, _update(interactive=False), _update(value=False, interactive=False), None
    result = WholeBookAssemblyService.execute_assembly(
        plan, resolutions, confirmation, session=ss
    )
    return (
        _render_execution_result(result),
        result.as_dict(),
        _update(interactive=False),
        _update(value=False, interactive=False),
        None,
    )


__all__ = [
    "analyze_assembly",
    "clear_assembly_execution_controls",
    "confirm_assembly_plan",
    "execute_assembly_plan",
    "invalidate_assembly_execution_state",
    "invalidate_assembly_plan",
    "prepare_assembly_execution_controls",
    "refresh_assembly_after_data_dir",
    "refresh_assembly_workflow_controls",
    "render_assembly_plan",
]
