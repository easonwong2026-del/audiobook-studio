"""补录与临时配音 UI builder。

页面只提供一个共享的临时配音 / 补录操作区。声音来源决定业务模式：

* ``project_role``：项目补录，使用项目 Voice Cast 和 durable project task；
* ``library_voice``：Quick TTS，使用全局声音库和 durable utility task。

两种模式共享文本、合成、进度、试听和导出控件，但不共享底层持久化边界。
"""
from __future__ import annotations

import gradio as gr


def create_supplement_page() -> dict:
    """创建统一的补录 / Quick TTS 工具。"""
    with gr.Group(visible=False, elem_id="grp-supplement") as grp_supplement:
        with gr.Accordion(
            "补录与临时配音", open=False, elem_classes=["supplement-accordion"]
        ):
            utility_mode = gr.Radio(
                label="声音来源",
                choices=[
                    ("使用项目角色", "project_role"),
                    ("自行选择音色", "library_voice"),
                ],
                value="project_role",
                interactive=True,
            )

            with gr.Group(visible=True) as utility_project_group:
                with gr.Row():
                    utility_role = gr.Dropdown(
                        label="项目角色",
                        choices=[],
                        value=None,
                        interactive=False,
                        scale=4,
                        info="请先打开项目并绑定角色声音",
                    )
                    utility_role_refresh = gr.Button("刷新角色", size="sm", scale=1)

                with gr.Accordion("项目补录高级输入", open=False):
                    utility_json = gr.File(
                        label="导入补录 JSON",
                        file_types=[".json"],
                    )
                    utility_json_parse = gr.Button("解析内容", size="sm")

                utility_split_punct = gr.Checkbox(
                    label="按标点拆分项目补录文本",
                    value=True,
                )
                utility_override_voice = gr.Dropdown(
                    label="临时替换声音（可选）",
                    choices=[],
                    value=None,
                    scale=2,
                    info="仅本次项目补录使用，不修改项目 Voice Cast",
                )

            with gr.Group(visible=False) as utility_library_group:
                with gr.Row():
                    utility_voice = gr.Dropdown(
                        label="声音（全局声音库）",
                        choices=[],
                        value=None,
                        scale=2,
                        info="无需打开项目；Quick TTS 不会进入书架",
                    )
                    utility_engine = gr.Markdown(
                        "当前引擎：读取中…（跟随 Settings 默认 / 运行时已加载引擎）",
                        elem_classes=["tts-runtime-status"],
                    )

            utility_text = gr.Textbox(
                label="文本",
                lines=6,
                placeholder="每行一句台词",
            )

            with gr.Accordion("合成设置", open=False):
                with gr.Row():
                    utility_emotion = gr.Dropdown(
                        label="情感",
                        value="(按默认)",
                        choices=[
                            "(按默认)",
                            "neutral",
                            "angry",
                            "happy",
                            "sad",
                            "excited",
                            "whisper",
                            "sarcastic",
                        ],
                        scale=2,
                    )
                    utility_emo_alpha = gr.Slider(
                        label="情绪强度",
                        minimum=0.0,
                        maximum=1.0,
                        value=1.0,
                        step=0.1,
                    )
                    utility_rate = gr.Slider(
                        label="语速",
                        minimum=0.7,
                        maximum=1.5,
                        value=1.0,
                        step=0.1,
                    )
                utility_quality = gr.Dropdown(
                    label="合成质量",
                    choices=[("快速", 1), ("标准", 2), ("高质量", 3)],
                    value=2,
                )

            utility_synth = gr.Button("生成", variant="primary")
            utility_status = gr.Markdown("尚未生成音频。")
            utility_wavs = gr.State([])
            utility_result_mode = gr.State("")
            utility_result_project = gr.State("")

            utility_preview = gr.Button("试听")
            utility_audio = gr.Audio(
                label="试听",
                type="filepath",
                interactive=True,
            )

            gr.Markdown("##### 导出")
            with gr.Row():
                utility_export_name = gr.Textbox(
                    label="导出名称",
                    placeholder="留空使用默认名称",
                    scale=2,
                )
                utility_format = gr.Dropdown(
                    label="格式",
                    choices=["wav", "mp3", "m4b"],
                    value="mp3",
                )
                utility_bitrate = gr.Dropdown(
                    label="比特率",
                    choices=["128k", "192k", "320k"],
                    value="192k",
                )
            with gr.Row():
                utility_export = gr.Button("导出", variant="secondary")
                utility_open_folder = gr.Button("打开所在文件夹", size="sm")
            utility_save_loc = gr.Markdown("请选择声音来源后显示导出保存位置。")
            utility_out = gr.File(label="下载音频", interactive=False)
            utility_path = gr.Textbox(label="文件路径", interactive=False)

    return {
        "group": grp_supplement,
        "utility_mode": utility_mode,
        "utility_project_group": utility_project_group,
        "utility_role": utility_role,
        "utility_role_refresh": utility_role_refresh,
        "utility_json": utility_json,
        "utility_json_parse": utility_json_parse,
        "utility_split_punct": utility_split_punct,
        "utility_override_voice": utility_override_voice,
        "utility_library_group": utility_library_group,
        "utility_voice": utility_voice,
        "utility_engine": utility_engine,
        "utility_text": utility_text,
        "utility_emotion": utility_emotion,
        "utility_emo_alpha": utility_emo_alpha,
        "utility_rate": utility_rate,
        "utility_quality": utility_quality,
        "utility_synth": utility_synth,
        "utility_status": utility_status,
        "utility_wavs": utility_wavs,
        "utility_result_mode": utility_result_mode,
        "utility_result_project": utility_result_project,
        "utility_preview": utility_preview,
        "utility_audio": utility_audio,
        "utility_export_name": utility_export_name,
        "utility_format": utility_format,
        "utility_bitrate": utility_bitrate,
        "utility_export": utility_export,
        "utility_open_folder": utility_open_folder,
        "utility_save_loc": utility_save_loc,
        "utility_out": utility_out,
        "utility_path": utility_path,
    }
