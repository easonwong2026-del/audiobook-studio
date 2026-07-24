"""试听与质检页 UI builder。"""
from __future__ import annotations
import gradio as gr
from lib import config as _cfg


def _browse_voices():
    vlib = _cfg.get_voice_library()
    import os
    os.makedirs(vlib, exist_ok=True)
    return [f for f in os.listdir(vlib) if f.endswith(('.wav', '.mp3'))]


def create_review_page() -> dict:
    """创建试听与质检页组件。"""
    with gr.Group(visible=False, elem_id="grp-review") as grp_review:
        gr.Markdown("> 👉 采用「段落下拉选择」方式：在上方下拉框选段落后点试听；改情感 / 语速 / 换音色后点「🔄 按指定参数重合成」即可更新该段。")
        gr.Markdown("#### 📊 章节预览")
        e_chapter_table = gr.Markdown("*打开项目并合成后自动显示*")

        # O13：章节级合并试听
        gr.Markdown("#### 🎧 ��节合并试听")
        with gr.Row():
            e_chapter_sel = gr.Dropdown(label="选择章节合并试听", choices=[], value=None, interactive=True, scale=2)
            e_chapter_audio = gr.Audio(label="合并试听", type="filepath", interactive=False, scale=3)

        gr.Markdown("#### 🎧 试听与重合成")
        with gr.Row():
            e_seg_sel = gr.Dropdown(label="选择段落（可多选）", choices=[], interactive=True, multiselect=True, scale=3)
        with gr.Row():
            e_emo = gr.Dropdown(label="情感", choices=["neutral","angry","happy","sad","excited","whisper","sarcastic"], value="neutral", scale=1)
            e_alpha = gr.Slider(label="情绪强度", minimum=0.0, maximum=1.0, value=1.0, step=0.1, scale=1)
            e_rate = gr.Slider(label="语速", minimum=0.7, maximum=1.5, value=1.0, step=0.1, scale=1)
        with gr.Row():
            e_voice = gr.Dropdown(label="换音色（留空=已绑定的音色）", choices=_browse_voices(), value=None, scale=2)
            e_regenerate = gr.Button("🔄 按指定参数重合成", variant="primary", scale=1)
        e_seg_audio = gr.Audio(label="试听", type="filepath", interactive=True)
        e_regenerate_msg = gr.Markdown("")
    return {
        "group": grp_review,
        "e_chapter_table": e_chapter_table,
        "e_chapter_sel": e_chapter_sel,
        "e_chapter_audio": e_chapter_audio,
        "e_seg_sel": e_seg_sel,
        "e_emo": e_emo,
        "e_alpha": e_alpha,
        "e_rate": e_rate,
        "e_voice": e_voice,
        "e_regenerate": e_regenerate,
        "e_seg_audio": e_seg_audio,
        "e_regenerate_msg": e_regenerate_msg,
    }
