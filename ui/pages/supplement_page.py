"""补录与临时配音 UI builder。

页面结构：
- 顶部模式 Tabs：``项目补录``（= 既有角色补录） / ``临时配音``（Quick TTS，无项目）。
- 项目补录：输入来源由 Tabs（粘贴台词 / 导入 JSON）唯一决定 —— 不再有第二套
  「输入来源」Radio（PR B 修复 3）。隐藏 ``sup_mode`` State 由 Tab select 事件驱动。
- 导出 UX（PR B 修复 4）：自定义导出名称（非法字符清洗 / 扩展名归一 / 重名后缀）、
  导出前显示保存位置、导出后显示最终文件、「打开所在文件夹」（no-window）。
- 临时配音：全局声音库 Dropdown + 台词 + 引擎信息 + 生成 + 试听 + 同一导出 UX。
"""
from __future__ import annotations

import gradio as gr

from lib import config


def create_supplement_page() -> dict:
    """创建补录与临时配音工具。"""
    with gr.Group(visible=False, elem_id="grp-supplement") as grp_supplement:
        with gr.Accordion(
            "补录与临时配音", open=False, elem_classes=["supplement-accordion"]
        ):
            with gr.Tabs():
                # ══════════════ 模式 A：项目补录 ══════════════
                with gr.Tab("项目补录"):
                    with gr.Row():
                        sup_role = gr.Dropdown(
                            label="角色", choices=[], value=None, interactive=False, scale=4,
                            info="请先打开项目并绑定角色声音",
                        )
                        sup_refresh = gr.Button("刷新角色", size="sm", scale=1)

                    # 输入来源唯一选择：Tabs（粘贴台词 / 导入 JSON）
                    with gr.Tabs():
                        with gr.Tab("粘贴台词") as sup_tab_paste:
                            sup_text = gr.Textbox(
                                label="每行一句", lines=6,
                                placeholder="第一句\n第二句\n第三句",
                            )
                            sup_split_punct = gr.Checkbox(label="按标点拆分", value=True)
                        with gr.Tab("导入 JSON") as sup_tab_json:
                            sup_json = gr.File(label="单角色小 JSON", file_types=[".json"])
                            sup_json_parse = gr.Button("解析内容", size="sm")
                            sup_json_role = gr.State("")
                            sup_json_lines = gr.State([])

                    with gr.Accordion("补录设置", open=False):
                        with gr.Row():
                            sup_emotion = gr.Dropdown(
                                label="情感", value="(按默认)",
                                choices=["(按默认)", "neutral", "angry", "happy", "sad", "excited", "whisper", "sarcastic"],
                                scale=2,
                            )
                            sup_emo_alpha = gr.Slider(label="情绪强度", minimum=0.0, maximum=1.0, value=1.0, step=0.1)
                            sup_rate = gr.Slider(label="语速", minimum=0.7, maximum=1.5, value=1.0, step=0.1)
                        with gr.Row():
                            sup_quality = gr.Dropdown(
                                label="合成质量",
                                choices=[("快速", 1), ("标准", 2), ("高质量", 3)],
                                value=2,
                            )
                            sup_voice = gr.Dropdown(label="临时替换声音（可选）", choices=[], value=None, scale=2)

                    sup_synth = gr.Button("生成补录音频", variant="primary")
                    sup_synth_status = gr.Markdown("尚未生成补录音频。")
                    sup_wavs = gr.State([])

                    with gr.Row():
                        sup_play_all = gr.Button("试听整段")
                        sup_play_seg = gr.Button("试听首句")
                    sup_audio = gr.Audio(label="补录试听", type="filepath", interactive=True)

                    gr.Markdown("##### 导出补录")
                    with gr.Row():
                        sup_export_name = gr.Textbox(
                            label="导出名称",
                            placeholder="留空使用 角色+时间戳",
                            scale=2,
                        )
                        sup_format = gr.Dropdown(label="格式", choices=["wav", "mp3", "m4b"], value="mp3")
                        sup_bitrate = gr.Dropdown(label="比特率", choices=["128k", "192k", "320k"], value="192k")
                    with gr.Row():
                        sup_export = gr.Button("导出", variant="secondary")
                        sup_open_folder = gr.Button("打开所在文件夹", size="sm")
                    sup_save_loc = gr.Markdown("打开项目后将在此显示导出保存位置。")
                    sup_out = gr.File(label="下载补录音频", interactive=False)
                    sup_path = gr.Textbox(label="文件路径", interactive=False)

                # ══════════════ 模式 B：临时配音（Quick TTS） ══════════════
                with gr.Tab("临时配音"):
                    with gr.Row():
                        qt_voice = gr.Dropdown(
                            label="声音（全局声音库）", choices=[], value=None, scale=2,
                            info="从全局声音库选择参考声音，无需打开项目",
                        )
                        qt_engine = gr.Markdown(
                            "当前引擎：读取中…（跟随 Settings 默认 / 运行时已加载引擎）",
                            elem_classes=["tts-runtime-status"],
                        )
                    qt_text = gr.Textbox(
                        label="台词", lines=4,
                        placeholder="请输入要临时配音的台词（每行一句，建议 1~2 句）",
                    )
                    qt_synth = gr.Button("生成临时配音", variant="primary")
                    qt_status = gr.Markdown("尚未生成临时配音。")
                    qt_wavs = gr.State([])

                    with gr.Row():
                        qt_play = gr.Button("试听")
                    qt_audio = gr.Audio(label="临时配音试听", type="filepath", interactive=True)

                    gr.Markdown("##### 导出临时配音")
                    with gr.Row():
                        qt_export_name = gr.Textbox(
                            label="导出名称",
                            placeholder="留空使用 quick_tts",
                            scale=2,
                        )
                        qt_format = gr.Dropdown(label="格式", choices=["wav", "mp3", "m4b"], value="mp3")
                        qt_bitrate = gr.Dropdown(label="比特率", choices=["128k", "192k", "320k"], value="192k")
                    with gr.Row():
                        qt_export = gr.Button("导出", variant="secondary")
                        qt_open_folder = gr.Button("打开所在文件夹", size="sm")
                    qt_save_loc = gr.Markdown(
                        f"**保存位置：** `{config.get_data_dir()}\\quick_tts\\exports`"
                    )
                    qt_out = gr.File(label="下载临时配音", interactive=False)
                    qt_path = gr.Textbox(label="文件路径", interactive=False)

            # 隐藏输入来源 State（由 Tab select 事件驱动，代替被删除的 Radio）
            sup_mode = gr.State("paste")
            sup_tab_paste.select(lambda: "paste", None, sup_mode)
            sup_tab_json.select(lambda: "json", None, sup_mode)

    return {
        "group": grp_supplement,
        # 模式 A：项目补录
        "sup_role": sup_role,
        "sup_refresh": sup_refresh,
        "sup_text": sup_text,
        "sup_split_punct": sup_split_punct,
        "sup_json": sup_json,
        "sup_json_parse": sup_json_parse,
        "sup_json_role": sup_json_role,
        "sup_json_lines": sup_json_lines,
        "sup_emotion": sup_emotion,
        "sup_emo_alpha": sup_emo_alpha,
        "sup_rate": sup_rate,
        "sup_quality": sup_quality,
        "sup_voice": sup_voice,
        "sup_mode": sup_mode,
        "sup_synth": sup_synth,
        "sup_synth_status": sup_synth_status,
        "sup_wavs": sup_wavs,
        "sup_play_all": sup_play_all,
        "sup_play_seg": sup_play_seg,
        "sup_audio": sup_audio,
        "sup_export_name": sup_export_name,
        "sup_format": sup_format,
        "sup_bitrate": sup_bitrate,
        "sup_export": sup_export,
        "sup_open_folder": sup_open_folder,
        "sup_save_loc": sup_save_loc,
        "sup_out": sup_out,
        "sup_path": sup_path,
        # 模式 B：临时配音
        "qt_voice": qt_voice,
        "qt_engine": qt_engine,
        "qt_text": qt_text,
        "qt_synth": qt_synth,
        "qt_status": qt_status,
        "qt_wavs": qt_wavs,
        "qt_play": qt_play,
        "qt_audio": qt_audio,
        "qt_export_name": qt_export_name,
        "qt_format": qt_format,
        "qt_bitrate": qt_bitrate,
        "qt_export": qt_export,
        "qt_open_folder": qt_open_folder,
        "qt_save_loc": qt_save_loc,
        "qt_out": qt_out,
        "qt_path": qt_path,
    }
