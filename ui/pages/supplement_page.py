"""角色补录页 UI builder。"""
from __future__ import annotations
import gradio as gr
from lib import config as _cfg


def _browse_voices():
    vlib = _cfg.get_voice_library()
    import os
    os.makedirs(vlib, exist_ok=True)
    return [f for f in os.listdir(vlib) if f.endswith(('.wav', '.mp3'))]


def create_supplement_page() -> dict:
    """创建角色补录页组件。"""
    with gr.Group(visible=False, elem_id="grp-supplement") as grp_supplement:
        gr.Markdown("> 💡 为「已绑定音色」的角色单独补录 / 补合成若干句（缺音重合成，或补充书里原本没有的内容），单独导出独立音频片段（**不进整本拼接**）。")
        gr.Markdown("---")
        # 角色选择（仅已绑定音色角色）；进入页面时懒刷新。
        with gr.Row():
            sup_role = gr.Dropdown(
                label="选择角色（仅已绑定音色）", choices=[], value=None,
                interactive=False, scale=4,
                info="请先打开项目并绑定角色音色",
            )
            sup_refresh = gr.Button("🔄 刷新角色", size="sm", scale=1)

        # ��入模式：粘贴文本 / 上传小 JSON
        gr.Markdown("#### 📝 输入内容")
        with gr.Tabs():
            with gr.Tab("粘贴文本"):
                sup_text = gr.Textbox(
                    label="按行粘贴内容（每行一句；可选按标点切分长段）",
                    lines=8, placeholder="第一句\n第二句\n第三句", scale=1,
                )
                sup_split_punct = gr.Checkbox(
                    label="按标���（。！？；）切分长段（保留标点）", value=True,
                )
            with gr.Tab("上传小 JSON"):
                sup_json = gr.File(
                    label="上传小 JSON（单角色单章，voices 必须命中项目角色）",
                    file_types=[".json"],
                )
                sup_json_parse = gr.Button("解析小 JSON")
                sup_json_role = gr.State("")     # 解析得到的角色
                sup_json_lines = gr.State([])    # 解析得到的句子列表

        # 合成参数（P1 全局覆盖，本次生效，不回写）
        gr.Markdown("#### 🎛 合成参数（全局覆盖，本次生效）")
        with gr.Row():
            sup_emotion = gr.Dropdown(
                label="情感", value="(按默认)",
                choices=["(按默认)", "neutral", "angry", "happy", "sad",
                         "excited", "whisper", "sarcastic"], scale=2,
            )
            sup_emo_alpha = gr.Slider(label="情绪强度", minimum=0.0, maximum=1.0, value=1.0, step=0.1, scale=1)
            sup_rate = gr.Slider(label="语速", minimum=0.7, maximum=1.5, value=1.0, step=0.1, scale=1)
        with gr.Row():
            sup_quality = gr.Dropdown(label="合成质量 / 速度（beam）", choices=[1, 2, 3], value=2, scale=1)
            sup_voice = gr.Dropdown(
                label="换音色（留空 = 已绑定音色，仅本次覆盖不回写）",
                choices=_browse_voices(), value=None, scale=2,
            )
        sup_mode = gr.Radio(
            label="输入模式", value="paste",
            choices=[("粘贴文本", "paste"), ("小 JSON", "json")],
        )

        # 逐句补合成
        sup_synth = gr.Button("🎙 逐句补合成", variant="primary")
        sup_synth_status = gr.Markdown("*尚未合成*")
        sup_wavs = gr.State([])  # 本次合成得到的 wav 路径列表

        # 试听（P1）
        gr.Markdown("#### 🎧 试听")
        with gr.Row():
            sup_play_all = gr.Button("▶ 试听整段", scale=1)
            sup_play_seg = gr.Button("▶ 试听逐句（首段）", scale=1)
        sup_audio = gr.Audio(label="试听", type="filepath", interactive=True)

        # 导出
        gr.Markdown("#### 💾 导出")
        with gr.Row():
            sup_format = gr.Dropdown(label="导出格式", choices=["wav", "mp3", "m4b"], value="mp3", scale=1)
            sup_bitrate = gr.Dropdown(label="比特率", choices=["128k", "192k", "320k"], value="192k", scale=1)
        sup_export = gr.Button("📦 导出补录音频")
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
