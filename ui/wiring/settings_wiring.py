"""设置页事件接线；不包含业务逻辑。"""
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
    page["s_provider"].change(
        settings_handlers.update_provider_config_fields,
        [page["s_provider"]],
        [
            page["s_provider_config"], page["s_model"], page["s_api_key"],
            page["s_base_url"], page["s_clear_key"],
        ],
    ).then(
        settings_handlers.describe_ai_model_source,
        [page["s_provider"], page["s_model"]],
        [page["s_model_source"]],
    )
    page["s_model"].change(
        settings_handlers.describe_ai_model_source,
        [page["s_provider"], page["s_model"]],
        [page["s_model_source"]],
    )
    page["s_provider"].change(
        settings_handlers.load_ai_analysis_settings,
        [page["s_provider"]],
        [
            page["s_analysis_provider_info"], page["s_analysis_depth"],
            page["s_analysis_reasoning"], page["s_analysis_auto_upgrade"],
            page["s_analysis_capability"], page["s_analysis_prompt_core"],
            page["s_analysis_prompt_supplement"],
            page["s_analysis_prompt_preview"], page["s_analysis_prompt_version"],
        ],
    )
    page["s_analysis_prompt_supplement"].change(
        settings_handlers.preview_ai_analysis_prompt,
        [page["s_analysis_prompt_supplement"]],
        [page["s_analysis_prompt_preview"]],
    )
    page["s_analysis_save"].click(
        settings_handlers.save_ai_analysis_settings,
        [
            page["s_provider"], page["s_analysis_depth"],
            page["s_analysis_reasoning"], page["s_analysis_auto_upgrade"],
            page["s_analysis_prompt_supplement"],
        ],
        [
            page["s_analysis_status"], page["s_analysis_provider_info"],
            page["s_analysis_prompt_preview"],
        ],
    )
    page["s_analysis_reset"].click(
        settings_handlers.reset_ai_analysis_prompt,
        [],
        [
            page["s_analysis_prompt_supplement"],
            page["s_analysis_prompt_preview"], page["s_analysis_status"],
        ],
    )
    page["s_models_refresh"].click(
        settings_handlers.refresh_ai_models,
        [
            page["s_provider"], page["s_model"], page["s_api_key"],
            page["s_base_url"], page["s_timeout"],
        ],
        [page["s_model"], page["s_status"], page["s_model_source"]],
    )
    page["s_model_default"].click(
        settings_handlers.restore_default_ai_model,
        [page["s_provider"]],
        [page["s_model"], page["s_model_source"], page["s_status"]],
    )
    page["s_save"].click(
        settings_handlers.save_ai_settings,
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
        settings_handlers.test_ai_connection,
        [
            page["s_provider"], page["s_model"], page["s_api_key"],
            page["s_base_url"], page["s_timeout"],
        ],
        [page["s_status"]],
    )
    page["s_clear_key"].click(
        settings_handlers.clear_ai_api_key,
        [page["s_provider"]],
        [
            page["s_provider_config"], page["s_api_key"],
            page["s_clear_key"], page["s_status"],
        ],
    )
    page["s_data_apply"].click(
        settings_handlers.apply_data_dir,
        [page["s_data_dir"]],
        [page["s_data_msg"], page["s_data_dir"]],
    )
    page["s_data_open"].click(
        settings_handlers.open_data_dir, [], [page["s_data_msg"]],
    )
    page["s_orphan_refresh"].click(
        settings_handlers.refresh_abnormal_projects,
        [],
        [
            page["s_orphan_table"], page["s_orphan_name"],
            page["s_orphan_status"],
        ],
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
    page["s_shutdown"].click(
        settings_handlers.shutdown_service,
        [],
        [page["s_shutdown_status"]],
        js="() => window.confirm('关闭服务将停止当前 AI 分析和音频任务，并释放后台端口。确定继续吗？')",
    )
