"""设置页事件接线；仅包含本地数据、项目残留和环境诊断。"""
from __future__ import annotations

from services.environment_diagnostics import (
    diagnostics_table,
    diagnostics_to_markdown,
    run_environment_diagnostics,
)
from ui import settings_handlers


def run_diagnostics_ui():
    report = run_environment_diagnostics()
    symbol = {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(report["status"], "❓")
    return (
        f"### {symbol} 总体状态：{report['status']}",
        diagnostics_table(report),
        diagnostics_to_markdown(report),
    )


def wire_settings_page(page: dict) -> None:
    page["s_data_apply"].click(
        settings_handlers.apply_data_dir,
        [page["s_data_dir"]],
        [page["s_data_msg"], page["s_data_dir"]],
    )
    page["s_data_open"].click(
        settings_handlers.open_data_dir,
        [],
        [page["s_data_msg"]],
    )
    page["s_orphan_refresh"].click(
        settings_handlers.refresh_abnormal_projects,
        [],
        [page["s_orphan_table"], page["s_orphan_name"], page["s_orphan_status"]],
    )
    page["s_orphan_open"].click(
        settings_handlers.open_abnormal_project,
        [page["s_orphan_name"]],
        [page["s_orphan_status"]],
    )
    page["s_orphan_archive"].click(
        settings_handlers.archive_abnormal_project,
        [page["s_orphan_name"]],
        [page["s_orphan_status"]],
    ).then(
        settings_handlers.refresh_abnormal_project_data,
        [],
        [page["s_orphan_table"], page["s_orphan_name"]],
    )
    page["s_diagnostics_run"].click(
        run_diagnostics_ui,
        [],
        [
            page["s_diagnostics_status"],
            page["s_diagnostics_table"],
            page["s_diagnostics_report"],
        ],
    )
