"""生产阶段中的角色补录 UI builder。"""
from __future__ import annotations

import os

import gradio as gr

from lib import config as _cfg


def _browse_voices() -> list[str]:
    vlib = _cfg.get_voice_library()
    os.makedirs(vlib, exist_ok=True)
    return [f for f in os.listdir(vlib) if f.endswith((".wav", ".mp3"))]


def create_supplement_page() -> dict:
    """创建非主流程的角色补录工具，默认收纳以避免干扰常规生产。"""
    with gr.Group(visible=False, elem_id="grp-supplement") as grp_supplement:
        with gr.Accordion("角色补录（可选）", open=False, elem_classes=["supplement-accordion"]):
            gr.Markdown("为已绑定声音的角色补录额外台词。补录音频不会写入整本书的自动拼接。")
            with gr.Row():
                sup_role = gr.Dropdown(
                    label="角色", choices=[], value=None, interactive=False, scale=4,
                    info="请先打开项目并绑定角色声音",
                )
                sup_refresh = gr.Button("刷新角色", size="sm", scale=1)

            with gr.Tabs():
                with gr.Tab("粘贴台词"):
                    sup_text = gr.Textbox(
                        label="每行一句", lines=6, placeholder="第一句\n第二句\n第三句",
                    )
                    sup_split_punct = gr.Checkbox(label="按标点拆分长段", value=True)
                with gr.Tab("导入小 JSON"):
                    sup_json = gr.File(
                        label="单角色小 JSON", file_types=[".json"],
                    )
                    sup_json_parse = gr.Button("解析内容", size="sm")
                    sup_json_role = gr.State("")
                    sup_json_lines = gr.State([])

            with gr.Accordion("补录高级设置", open=False):
                with gr.Row():
                    sup_emotion = gr.Dropdown(
                        label="情感", value="(按默认)",
                        choices=["(按默认)", "neutral", "angry", "happy", "sad", "excited", "whisper", "sarcastic"],
                        scale=2,
                    )
                    sup_emo_alpha = gr.Slider(label="情绪强度", minimum=0.0, maximum=1.0, value=1.0, step=0.1)
                    sup_rate = gr.Slider(label="语速", minimum=0.7, maximum=1.5, value=1.0, step=0.1)
                with gr.Row():
                    sup_quality = gr.Dropdown(label="质量 / 速度", choices=[1, 2, 3], value=2)
                    sup_voice = gr.Dropdown(label="临时替换声音（可选）", choices=_browse_voices(), value=None, scale=2)

            sup_mode = gr.Radio(label="输入来源", value="paste", choices=[("粘贴台词", "paste"), ("小 JSON", "json")])
            sup_synth = gr.Button("生成补录音频", variant="primary")
            sup_synth_status = gr.Markdown("尚未生成补录音频。")
            sup_wavs = gr.State([])

            with gr.Row():
                sup_play_all = gr.Button("试听整段")
                sup_play_seg = gr.Button("试听首句")
            sup_audio = gr.Audio(label="补录试听", type="filepath", interactive=True)

            gr.Markdown("##### 导出补录")
            with gr.Row():
                sup_format = gr.Dropdown(label="格式", choices=["wav", "mp3", "m4b"], value="mp3")
                sup_bitrate = gr.Dropdown(label="比特率", choices=["128k", "192k", "320k"], value="192k")
                sup_export = gr.Button("导出补录音频", variant="secondary")
            sup_out = gr.File(label="下载补录音频", interactive=False)
            sup_path = gr.Textbox(label="文件路径", interactive=False)

    return {
        "group": grp_supplement,
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
        "sup_format": sup_format,
        "sup_bitrate": sup_bitrate,
        "sup_export": sup_export,
        "sup_out": sup_out,
        "sup_path": sup_path,
    }
