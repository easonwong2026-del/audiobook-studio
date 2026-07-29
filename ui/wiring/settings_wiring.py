"""设置页事件接线；不包含业务逻辑。"""
from __future__ import annotations

from services.environment_diagnostics import (
    diagnostics_table,
    diagnostics_to_markdown,
    run_environment_diagnostics,
)
from ui import director_handlers


def run_diagnostics_ui():
    report = run_environment_diagnostics()
    symbol = {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(report["status"], "❓")
    return (
        f"### {symbol} 总体状态：{report['status']}",
        diagnostics_table(report),
        diagnostics_to_markdown(report),
    )


def wire_settings_page(page: dict) -> None:
    page["s_provider"].change(
        director_handlers.update_provider_config_fields,
        [page["s_provider"]],
        [
            page["s_provider_config"], page["s_model"], page["s_api_key"],
            page["s_base_url"], page["s_clear_key"],
        ],
    ).then(
        director_handlers.describe_ai_model_source,
        [page["s_provider"], page["s_model"]],
        [page["s_model_source"]],
    )
    page["s_model"].change(
        director_handlers.describe_ai_model_source,
        [page["s_provider"], page["s_model"]],
        [page["s_model_source"]],
    )
    page["s_models_refresh"].click(
        director_handlers.refresh_ai_models,
        [
            page["s_provider"], page["s_model"], page["s_api_key"],
            page["s_base_url"], page["s_timeout"],
        ],
        [page["s_model"], page["s_status"], page["s_model_source"]],
    )
    page["s_model_default"].click(
        director_handlers.restore_default_ai_model,
        [page["s_provider"]],
        [page["s_model"], page["s_model_source"], page["s_status"]],
    )
    page["s_save"].click(
        director_handlers.save_ai_settings,
        [
            page["s_provider"], page["s_model"], page["s_api_key"],
            page["s_base_url"], page["s_timeout"],
        ],
        [
            page["s_status"], page["s_provider_config"],
            page["s_api_key"], page["s_clear_key"],
        ],
    )
    page["s_test"].click(
        director_handlers.test_ai_connection,
        [
            page["s_provider"], page["s_model"], page["s_api_key"],
            page["s_base_url"], page["s_timeout"],
        ],
        [page["s_status"]],
    )
    page["s_clear_key"].click(
        director_handlers.clear_ai_api_key,
        [page["s_provider"]],
        [
            page["s_provider_config"], page["s_api_key"],
            page["s_clear_key"], page["s_status"],
        ],
    )
    page["s_data_apply"].click(
        director_handlers.apply_data_dir,
        [page["s_data_dir"]],
        [page["s_data_msg"], page["s_data_dir"]],
    )
    page["s_data_open"].click(
        director_handlers.open_data_dir, [], [page["s_data_msg"]],
    )
    page["s_orphan_refresh"].click(
        director_handlers.refresh_abnormal_projects,
        [],
        [
            page["s_orphan_table"], page["s_orphan_name"],
            page["s_orphan_status"],
        ],
    )
    page["s_orphan_open"].click(
        director_handlers.open_abnormal_project,
        [page["s_orphan_name"]],
        [page["s_orphan_status"]],
    )
    page["s_orphan_archive"].click(
        director_handlers.archive_abnormal_project,
        [page["s_orphan_name"]],
        [page["s_orphan_status"]],
    ).then(
        director_handlers.refresh_abnormal_project_data,
        [],
        [
            page["s_orphan_table"], page["s_orphan_name"],
        ],
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
