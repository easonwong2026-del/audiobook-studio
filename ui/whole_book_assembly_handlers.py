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
from services.whole_book_assembly_operations import (
    CHAPTER_CRITICAL_FAILURE,
    CHAPTER_FAILED_ROLLED_BACK_STATE,
    CHAPTER_INVALID_RELATIONSHIP,
    CHAPTER_OPERATIONAL_ALREADY_MERGED,
    CHAPTER_OPERATIONAL_BLOCKED,
    CHAPTER_PENDING,
    CHAPTER_READY_WITH_WARNINGS,
    CHAPTER_SOURCE_CHANGED,
    INTEGRITY_FAIL,
    INTEGRITY_PASS,
    INTEGRITY_UNKNOWN,
    INTEGRITY_WARN,
    AssemblyOperationsSnapshot,
    AssemblyResumeResult,
    WholeBookAssemblyOperationsService,
)

_RESOLVABLE_ASSEMBLY_BLOCKERS = frozenset(
    {"SOURCE_ONLY_ROLE", "VOICE_BINDING_CONFLICT"}
)


def _update(**kwargs: Any) -> Any:
    return gr.update(**kwargs)


def is_assembly_target_eligible(summary) -> bool:
    """Return whether one catalog summary may be a Whole-book target.

    Assembly requires the same stable identity that ``_book_names()`` exposes
    to the target Dropdown. A legacy standalone Book without ``project_id`` is
    still valid for bookshelf management and opening, but is not yet an
    Assembly target.
    """
    return bool(
        summary is not None
        and summary.project_kind == "book"
        and summary.relation_status == RELATION_STANDALONE
        and summary.project_id
    )


def _book_names() -> list[str]:
    hierarchy = ProjectCatalogService.scan_hierarchy()
    return [
        item.project_name
        for item in hierarchy.books
        if is_assembly_target_eligible(item)
    ]


def _selected_book(ss) -> str:
    selected = str(getattr(ss, "selected_project", "") or "") if ss is not None else ""
    summary = ProjectCatalogService.get_summary(selected) if selected else None
    if is_assembly_target_eligible(summary):
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


def _status_text(status: str) -> str:
    return {
        CHAPTER_OPERATIONAL_ALREADY_MERGED: "已装配",
        CHAPTER_PENDING: "待装配",
        CHAPTER_READY_WITH_WARNINGS: "待装配（有警告）",
        CHAPTER_SOURCE_CHANGED: "阻塞 — 来源已变化",
        CHAPTER_INVALID_RELATIONSHIP: "阻塞 — 层级关系无效",
        CHAPTER_OPERATIONAL_BLOCKED: "阻塞",
        CHAPTER_FAILED_ROLLED_BACK_STATE: "上次失败 — 已回滚，可重试",
        CHAPTER_CRITICAL_FAILURE: "CRITICAL / DEGRADED",
    }.get(status, status or "—")


def _integrity_text(status: str) -> str:
    return {
        INTEGRITY_PASS: "PASS",
        INTEGRITY_WARN: "WARN",
        INTEGRITY_FAIL: "FAIL",
        INTEGRITY_UNKNOWN: "UNKNOWN",
    }.get(status, status or "UNKNOWN")


def render_assembly_operations(snapshot: AssemblyOperationsSnapshot) -> str:
    lines = [
        "### 整书装配状态",
        f"- 目标 Book：`{html.escape(snapshot.target_book_name or '—')}`",
        f"- 总体状态：**{html.escape(snapshot.overall_status)}**",
        (
            f"- 进度：{snapshot.merged_count} / {snapshot.total_chapters}；"
            f"待装配：{snapshot.pending_count}；阻塞：{snapshot.blocked_count}；"
            f"失败：{snapshot.failed_count}"
        ),
        f"- 目标完整性：**{html.escape(_integrity_text(snapshot.integrity_status))}**",
        "",
        "| 顺序 | Chapter | 当前状态 | Planner | 最近事务 | 说明 |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for item in snapshot.chapter_states:
        transaction = item.last_transaction_id or "—"
        explanation = item.blocking_reason or item.failure_summary
        if not explanation and item.warning_summary:
            explanation = "；".join(item.warning_summary)
        lines.append(
            f"| {item.order:02d} | `{html.escape(item.chapter_title or item.chapter_project_name)}` | "
            f"**{html.escape(_status_text(item.status))}** | "
            f"{html.escape(item.planner_status or '—')} | "
            f"`{html.escape(transaction)}` | {html.escape(explanation or '—')} |"
        )
    if snapshot.active_transactions:
        lines.extend(
            [
                "",
                "#### 未完成事务（已阻止继续装配）",
                *[
                    f"- `{html.escape(item.transaction_id)}` stage="
                    f"`{html.escape(item.stage)}`；journal："
                    f"`{html.escape(item.journal_reference)}`"
                    for item in snapshot.active_transactions
                ],
            ]
        )
    if snapshot.latest_run:
        run = snapshot.latest_run
        summary = _json_summary(run.get("result_summary"))
        lines.extend(
            [
                "",
                "#### 最近一次装配",
                (
                    f"- `{html.escape(str(run.get('assembly_run_id') or '—'))}` "
                    f"结果：**{html.escape(str(run.get('status') or '—'))}**；"
                    f"开始：{html.escape(str(run.get('started_at') or '—'))}"
                ),
                f"- {html.escape(summary)}",
            ]
        )
        current = run.get("current_chapter")
        if isinstance(current, dict) and current:
            lines.append(
                f"- 当前：Chapter {html.escape(str(current.get('order') or '—'))} "
                f"`{html.escape(str(current.get('project_name') or '—'))}`"
            )
    if snapshot.historical_merges:
        lines.extend(
            [
                "",
                "#### 历史合并（已不在当前 Chapter hierarchy）",
                *[
                    f"- `{html.escape(str(item.get('source_project_name') or item.get('source_project_id') or '—'))}` "
                    f"transaction=`{html.escape(str(item.get('transaction_id') or '—'))}`；"
                    "目标内容保留，不自动删除。"
                    for item in snapshot.historical_merges
                ],
            ]
        )
    if snapshot.blocking_reasons:
        lines.extend(
            [
                "",
                "#### 阻塞原因",
                *[f"- `{html.escape(item)}`" for item in snapshot.blocking_reasons],
            ]
        )
    if snapshot.warnings:
        lines.extend(
            ["", "#### Warnings", *[f"- {html.escape(item)}" for item in snapshot.warnings]]
        )
    return "\n".join(lines)


def _json_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return "运行摘要不可用"
    return (
        f"完成 {value.get('merged_this_run', 0)}；已装配 "
        f"{value.get('already_merged', 0)}；阻塞 {value.get('blocked', 0)}；"
        f"失败 {value.get('failed', 0)}；未尝试 {value.get('not_attempted', 0)}"
    )


def refresh_assembly_workflow_controls(ss=None) -> tuple:
    """Refresh only Assembly-owned components; Catalog output arity is untouched."""
    books = _book_names()
    selected = str(getattr(ss, "selected_project", "") or "") if ss is not None else ""
    selected_summary = (
        ProjectCatalogService.get_summary(selected) if selected else None
    )
    target = _selected_book(ss)
    if target not in books:
        target = ""
    if target:
        snapshot = WholeBookAssemblyOperationsService.reconstruct(target, session=ss)
        summary = render_assembly_operations(snapshot)
    elif (
        selected_summary is not None
        and selected_summary.project_kind == "book"
        and selected_summary.relation_status == RELATION_STANDALONE
        and not selected_summary.project_id
    ):
        summary = "当前项目可正常管理和打开，但尚不具备整书装配资格。"
    elif getattr(ss, "selected_project", None):
        summary = "当前选择不是可装配的 Book；请选择一个独立 Book 项目。"
    else:
        summary = "从书架选择一个 Book 后，可分析其关联 Chapter 的整书装配。"
    return (
        _update(choices=books, value=target or None, interactive=False),
        _update(interactive=bool(target)),
        summary,
        None,
        None,
        _update(value={"chapters": {}}),
        _update(value=False, interactive=False),
        None,
        _update(interactive=False),
        "",
        None,
        _update(interactive=bool(target and snapshot.resume_allowed) if target else False),
    )


def refresh_assembly_after_data_dir(new_dir: str, ss=None) -> tuple:
    """Clear Assembly after a successful data-root switch only.

    ``apply_data_dir`` returns an empty path on failure, so the previous
    transient state remains visible and recoverable in that case.
    """
    if not str(new_dir or "").strip():
        return tuple(_update() for _ in range(12))
    return refresh_assembly_workflow_controls(ss)


def refresh_assembly_dashboard(
    target_name: str, resolutions: Any = None, ss=None
) -> tuple[str, Any]:
    selected = _selected_book(ss)
    if not target_name or not selected or str(target_name) != selected:
        return "请从书架选择一个 Book 后重新分析整书装配。", _update(interactive=False)
    snapshot = WholeBookAssemblyOperationsService.reconstruct(
        target_name, resolutions=resolutions, session=ss
    )
    return render_assembly_operations(snapshot), _update(
        interactive=snapshot.resume_allowed
    )


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


def prepare_assembly_execution_controls(plan: BookAssemblyPlan | None, ss=None) -> tuple:
    if plan is None:
        return _empty_execution_controls()
    blocker_codes = {item.code for item in plan.blocking_conflicts}
    can_confirm = not blocker_codes or blocker_codes <= _RESOLVABLE_ASSEMBLY_BLOCKERS
    try:
        snapshot = WholeBookAssemblyOperationsService.reconstruct(
            plan.target_book_name, session=ss
        )
        can_confirm = can_confirm and not snapshot.degraded
    except (OSError, TypeError, ValueError, RuntimeError):
        can_confirm = False
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


def analyze_assembly(
    target_name: str, ss=None
) -> tuple[str, BookAssemblyPlan | None, str]:
    selected = _selected_book(ss)
    if not target_name or not selected:
        return "⚪ 请先从书架选择一个 Book。", None, ""
    if str(target_name) != selected:
        return (
            "⚠️ 目标 Book 与 bookshelf selection 不一致，请重新选择后再分析。",
            None,
            "",
        )
    analysis = WholeBookAssemblyOperationsService.analyze(target_name, session=ss)
    return (
        render_assembly_plan(analysis.plan),
        analysis.plan,
        render_assembly_operations(analysis.snapshot),
    )


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
    try:
        outcome = WholeBookAssemblyOperationsService.execute_confirmed(
            plan, resolutions, confirmation, session=ss
        )
    except WholeBookAssemblyError as exc:
        return (
            f"⛔ 执行阻止：`{html.escape(exc.code)}` — {html.escape(str(exc))}",
            None,
            _update(interactive=False),
            _update(value=False, interactive=False),
            None,
        )
    result = outcome.execution_result
    return (
        _render_execution_result(result),
        result.as_dict(),
        _update(interactive=False),
        _update(value=False, interactive=False),
        None,
    )


def resume_assembly_plan(
    confirmed: bool,
    target_name: str,
    resolutions: Any,
    ss=None,
) -> tuple:
    try:
        outcome: AssemblyResumeResult = WholeBookAssemblyOperationsService.resume(
            target_name,
            resolutions,
            confirmed=confirmed,
            session=ss,
        )
    except WholeBookAssemblyError as exc:
        snapshot = WholeBookAssemblyOperationsService.reconstruct(
            target_name, resolutions=resolutions, session=ss
        )
        return (
            render_assembly_operations(snapshot),
            f"⛔ 继续装配阻止：`{html.escape(exc.code)}` — {html.escape(str(exc))}",
            None,
            _update(value=False, interactive=snapshot.resume_allowed),
            _update(interactive=False),
            _update(value=False, interactive=False),
            None,
        )
    result = outcome.execution_result
    return (
        render_assembly_operations(outcome.snapshot),
        _render_execution_result(result),
        result.as_dict(),
        _update(value=False, interactive=False),
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
    "is_assembly_target_eligible",
    "prepare_assembly_execution_controls",
    "refresh_assembly_after_data_dir",
    "refresh_assembly_dashboard",
    "refresh_assembly_workflow_controls",
    "render_assembly_operations",
    "render_assembly_plan",
    "resume_assembly_plan",
]
