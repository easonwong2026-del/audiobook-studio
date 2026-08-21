"""Dedicated Gradio event wiring for Whole-book Assembly.

The returned output list is intentionally separate from the 25/33-output
bookshelf contracts and from the Chapter Merge Planner/Executor tuple.
"""
from __future__ import annotations

from typing import Any

from ui import whole_book_assembly_handlers as assembly_handlers


def assembly_workflow_outputs(page: dict) -> list:
    return [
        page["assembly_target_book"],
        page["assembly_analyze"],
        page["assembly_dashboard"],
        page["assembly_plan_result"],
        page["assembly_plan_state"],
        page["assembly_resolution"],
        page["assembly_confirm"],
        page["assembly_confirmation_state"],
        page["assembly_execute"],
        page["assembly_execution_result"],
        page["assembly_transaction_state"],
        page["assembly_resume"],
    ]


def assembly_execution_outputs(page: dict) -> list:
    return [
        page["assembly_resolution"],
        page["assembly_confirm"],
        page["assembly_confirmation_state"],
        page["assembly_execute"],
        page["assembly_execution_result"],
        page["assembly_transaction_state"],
    ]


def wire_whole_book_assembly(page: dict, deps: dict[str, Any]) -> None:
    session = deps["session"]
    execution_outputs = assembly_execution_outputs(page)
    plan_result = page["assembly_plan_result"]
    plan_state = page["assembly_plan_state"]
    dashboard = page["assembly_dashboard"]
    target = page["assembly_target_book"]
    analyze = page["assembly_analyze"]
    resolution = page["assembly_resolution"]
    confirm = page["assembly_confirm"]
    confirmation_state = page["assembly_confirmation_state"]
    execute = page["assembly_execute"]
    execution_result = page["assembly_execution_result"]
    transaction_state = page["assembly_transaction_state"]
    resume = page["assembly_resume"]

    analyze_chain = analyze.click(
        assembly_handlers.analyze_assembly,
        [target, session],
        [plan_result, plan_state, dashboard],
    )
    analyze_chain.then(
        assembly_handlers.prepare_assembly_execution_controls,
        [plan_state, session],
        execution_outputs,
    ).then(
        assembly_handlers.refresh_assembly_dashboard,
        [target, resolution, session],
        [dashboard, resume],
    )

    target.change(
        assembly_handlers.invalidate_assembly_plan,
        [],
        [plan_result, plan_state],
    ).then(
        assembly_handlers.clear_assembly_execution_controls,
        [],
        execution_outputs,
    ).then(
        assembly_handlers.refresh_assembly_dashboard,
        [target, resolution, session],
        [dashboard, resume],
    )
    resolution.change(
        assembly_handlers.invalidate_assembly_execution_state,
        [],
        [confirm, confirmation_state, execute, execution_result, transaction_state],
    ).then(
        assembly_handlers.refresh_assembly_dashboard,
        [target, resolution, session],
        [dashboard, resume],
    )
    confirm.change(
        assembly_handlers.confirm_assembly_plan,
        [confirm, target, plan_state, resolution, session],
        [confirmation_state, execute, execution_result, transaction_state],
    ).then(
        assembly_handlers.refresh_assembly_dashboard,
        [target, resolution, session],
        [dashboard, resume],
    )
    execute.click(
        assembly_handlers.execute_assembly_plan,
        [plan_state, resolution, confirmation_state, session],
        [execution_result, transaction_state, execute, confirm, confirmation_state],
    ).then(
        assembly_handlers.refresh_assembly_dashboard,
        [target, resolution, session],
        [dashboard, resume],
    )
    resume.click(
        assembly_handlers.resume_assembly_plan,
        [confirm, target, resolution, session],
        [
            dashboard,
            execution_result,
            transaction_state,
            resume,
            execute,
            confirm,
            confirmation_state,
        ],
    )


__all__ = [
    "assembly_execution_outputs",
    "assembly_workflow_outputs",
    "wire_whole_book_assembly",
]
