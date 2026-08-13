"""设置页事件接线；包含本地数据、TTS 引擎、项目残留和环境诊断。"""
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
    tts_inputs = [
        page["s_tts_engine"],
        page["s_legacy_model_dir"],
        page["s_indextts25_model_dir"],
    ]
    tts_outputs = [
        page["s_tts_status"],
        page["s_legacy_model_status"],
        page["s_indextts25_model_status"],
        page["s_tts_runtime_engine"],
        page["s_tts_frozen_engine"],
    ]
    page["s_tts_apply"].click(
        settings_handlers.apply_tts_engine,
        tts_inputs,
        tts_outputs,
    )
    page["s_tts_refresh"].click(
        settings_handlers.refresh_tts_engine_ui,
        tts_inputs,
        tts_outputs,
    )
    for component in (
        page["s_tts_engine"],
        page["s_legacy_model_dir"],
        page["s_indextts25_model_dir"],
    ):
        component.change(
            settings_handlers.refresh_tts_engine_ui,
            tts_inputs,
            tts_outputs,
        )
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
    page["s_shutdown"].click(
        settings_handlers.show_shutdown_confirmation,
        [],
        [page["s_shutdown_confirm_message"], page["s_shutdown_cancel"], page["s_shutdown_confirm"], page["s_shutdown"]],
    )
    page["s_shutdown_cancel"].click(
        settings_handlers.cancel_shutdown,
        [],
        [page["s_shutdown_confirm_message"], page["s_shutdown_cancel"], page["s_shutdown_confirm"], page["s_shutdown"]],
    )
    page["s_shutdown_confirm"].click(
        settings_handlers.request_application_shutdown,
        [],
        [page["s_shutdown_confirm_message"], page["s_shutdown_status"], page["s_shutdown_cancel"], page["s_shutdown_confirm"], page["s_shutdown"]],
    )
