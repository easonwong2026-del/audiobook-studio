"""设置页事件接线；包含本地数据、TTS 引擎、项目残留和环境诊断。"""
from __future__ import annotations

from ui import settings_handlers


def wire_settings_page(
    page: dict,
    catalog_refresh: tuple | None = None,
    session=None,
) -> None:
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
    data_dir_chain = page["s_data_apply"].click(
        settings_handlers.apply_data_dir,
        [page["s_data_dir"], session] if session is not None else [page["s_data_dir"]],
        [page["s_data_msg"], page["s_data_dir"]],
    )
    if catalog_refresh is not None:
        # 切换数据目录成功后统一刷新目录类组件（书架 / 回收站）
        fn, inputs, outputs = catalog_refresh
        data_dir_chain = data_dir_chain.then(fn, inputs, outputs)
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
        settings_handlers.run_diagnostics_ui,
        [],
        [
            page["s_diagnostics_status"],
            page["s_diagnostics_table"],
            page["s_diagnostics_report"],
        ],
    )
    page["s_prewarm_apply"].click(
        settings_handlers.apply_prewarm_setting,
        [page["s_prewarm"]],
        [page["s_prewarm_status"]],
    )
