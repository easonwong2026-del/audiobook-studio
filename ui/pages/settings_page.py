"""设置页面 — 数据、TTS/导出、系统与环境诊断。"""
from __future__ import annotations

import platform

import gradio as gr

from lib import __version__, config
from ui import settings_handlers


def create_settings_page() -> dict:
    tts_state = settings_handlers.get_tts_engine_ui_state()
    with gr.Group(
        visible=False,
        elem_id="grp-settings",
        elem_classes=["settings-page"],
    ) as grp:
        gr.Markdown("### 设置")

        with gr.Tabs(elem_classes=["settings-tabs"]):
            with gr.Tab("数据与项目"):
                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown("##### 数据保存位置")
                    s_data_dir = gr.Textbox(label="数据目录", value=config.get_data_dir())
                    with gr.Row(elem_classes=["settings-data-actions"]):
                        s_data_apply = gr.Button("应用", variant="primary")
                        s_data_open = gr.Button("打开数据文件夹")
                    s_data_msg = gr.Markdown("")
                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown("##### 异常与残留项目")
                    gr.Markdown(
                        "这里只列出不完整、损坏或临时目录。归档会移动到数据目录的 "
                        "`.trash/projects`，不会永久删除。"
                    )
                    s_orphan_table = gr.Dataframe(
                        headers=["项目名称", "状态", "路径", "缺失/损坏文件", "最后修改时间"],
                        datatype=["str", "str", "str", "str", "str"],
                        interactive=False,
                        wrap=True,
                    )
                    s_orphan_name = gr.Dropdown(label="选择要处理的异常项目", choices=[])
                    with gr.Row():
                        s_orphan_refresh = gr.Button("刷新")
                        s_orphan_open = gr.Button("打开目录")
                        s_orphan_archive = gr.Button("移动到回收站", variant="stop")
                    s_orphan_status = gr.Markdown("")

            with gr.Tab("TTS 与导出"):
                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown(
                        "##### TTS 引擎\n"
                        "Audiobook Studio 只使用本地 TTS 与 FFmpeg 完成合成和导出，"
                        "不会在启动、导入或绑定声音时连接外部模型服务。"
                    )
                    s_tts_engine = gr.Radio(
                        label="生产 TTS 引擎",
                        choices=settings_handlers.TTS_ENGINE_CHOICES,
                        value=tts_state["engine"],
                        info="空闲时切换会保存设置并受控回收/重载 runtime。生产、试听、补录或导出进行中不可切换。",
                        interactive=True,
                    )
                    with gr.Row(equal_height=True):
                        with gr.Column(scale=1):
                            s_legacy_model_dir = gr.Textbox(
                                label="IndexTTS 2 Legacy（回滚）模型目录",
                                value=tts_state["legacy_model_dir"],
                                interactive=True,
                            )
                            s_legacy_model_status = gr.Markdown(
                                tts_state["legacy_model_status"],
                                elem_classes=["tts-model-status"],
                            )
                        with gr.Column(scale=1):
                            s_indextts25_model_dir = gr.Textbox(
                                label="IndexTTS 2.5（推荐）模型目录",
                                value=tts_state["indextts25_model_dir"],
                                interactive=True,
                            )
                            s_indextts25_model_status = gr.Markdown(
                                tts_state["indextts25_model_status"],
                                elem_classes=["tts-model-status"],
                            )
                    with gr.Row():
                        s_tts_apply = gr.Button("应用 TTS 引擎", variant="primary")
                        s_tts_refresh = gr.Button("刷新状态")
                    s_tts_status = gr.Markdown("")
                    s_tts_runtime_engine = gr.Markdown(
                        tts_state["runtime_engine_message"],
                        elem_classes=["tts-runtime-status"],
                    )
                    s_tts_frozen_engine = gr.Markdown(
                        tts_state["frozen_engine_message"],
                        elem_classes=["tts-frozen-status"],
                    )
                    s_prewarm = gr.Checkbox(
                        label="启动后预热默认 TTS 引擎",
                        value=settings_handlers.get_prewarm_setting(),
                        info="可减少首次配音等待时间，但会提前占用显存。首次物理模型加载会在 Studio 启动后的后台完成，不阻塞界面。",
                    )
                    s_prewarm_apply = gr.Button("保存预热设置", size="sm")
                    s_prewarm_status = gr.Markdown("")

                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown("##### 本地生产环境")
                    s_ffmpeg_path = gr.Textbox(
                        label="FFmpeg",
                        value=config.get_ffmpeg_path(),
                        interactive=False,
                    )
                    gr.Markdown(
                        "合成参数（情绪、强度、语速、质量和章节范围）位于「生产与质检」；"
                        "导出格式、码率、字幕和输出目录位于「交付」。"
                    )

            with gr.Tab("系统信息"), gr.Group(elem_classes=["settings-card"]):
                    s_version = gr.Markdown(f"**版本**：v{__version__}")
                    s_python = gr.Markdown(f"**Python**：{platform.python_version()}")
                    s_status_info = gr.Markdown("")
                    gr.Markdown("##### 环境诊断")
                    gr.Markdown(
                        "只读取本地环境状态，不安装 CUDA、Torch、模型，也不会执行 GPU 推理。"
                    )
                    s_diagnostics_run = gr.Button("运行环境诊断", variant="primary")
                    s_diagnostics_status = gr.Markdown("")
                    s_diagnostics_table = gr.Dataframe(
                        headers=["检查项", "状态", "结果", "修复建议"],
                        datatype=["str", "str", "str", "str"],
                        interactive=False,
                        wrap=True,
                        elem_classes=["diagnostics-table"],
                    )
                    s_diagnostics_report = gr.Textbox(
                        label="可复制诊断报告（Markdown）",
                        lines=12,
                        show_copy_button=True,
                        interactive=False,
                        elem_classes=["diagnostics-report"],
                    )

    return {
        "group": grp,
        "s_data_dir": s_data_dir,
        "s_data_apply": s_data_apply,
        "s_data_open": s_data_open,
        "s_data_msg": s_data_msg,
        "s_orphan_table": s_orphan_table,
        "s_orphan_name": s_orphan_name,
        "s_orphan_refresh": s_orphan_refresh,
        "s_orphan_open": s_orphan_open,
        "s_orphan_archive": s_orphan_archive,
        "s_orphan_status": s_orphan_status,
        "s_tts_engine": s_tts_engine,
        "s_legacy_model_dir": s_legacy_model_dir,
        "s_indextts25_model_dir": s_indextts25_model_dir,
        "s_legacy_model_status": s_legacy_model_status,
        "s_indextts25_model_status": s_indextts25_model_status,
        "s_tts_apply": s_tts_apply,
        "s_tts_refresh": s_tts_refresh,
        "s_tts_status": s_tts_status,
        "s_tts_runtime_engine": s_tts_runtime_engine,
        "s_tts_frozen_engine": s_tts_frozen_engine,
        "s_prewarm": s_prewarm,
        "s_prewarm_apply": s_prewarm_apply,
        "s_prewarm_status": s_prewarm_status,
        "s_ffmpeg_path": s_ffmpeg_path,
        "s_version": s_version,
        "s_python": s_python,
        "s_status_info": s_status_info,
        "s_diagnostics_run": s_diagnostics_run,
        "s_diagnostics_status": s_diagnostics_status,
        "s_diagnostics_table": s_diagnostics_table,
        "s_diagnostics_report": s_diagnostics_report,
    }
