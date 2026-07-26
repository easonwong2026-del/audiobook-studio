"""生产阶段中的质检与局部修复 UI builder — 对齐 Pencil 试听质检画板。"""
from __future__ import annotations

import gradio as gr


def create_review_page() -> dict:
    """创建章节试听、段落检查与按需重合成工具。"""
    with gr.Group(visible=False, elem_id="grp-review") as grp_review:
        gr.Markdown("##### 章节试听")
        e_chapter_table = gr.Markdown("打开项目并完成部分合成后，章节状态会显示在这里。")
        with gr.Row():
            e_chapter_sel = gr.Dropdown(
                label="章节", choices=[], value=None, interactive=True, scale=2,
            )
            e_chapter_audio = gr.Audio(
                label="合并试听", type="filepath", interactive=False, scale=3,
            )

        gr.Markdown("##### 段落试听与修复")
        e_seg_sel = gr.Dropdown(
            label="选择段落", choices=[], interactive=True, multiselect=True,
        )
        e_seg_audio = gr.Audio(label="段落试听", type="filepath", interactive=True)

        with gr.Accordion("修复参数", open=False):
            with gr.Row():
                e_emo = gr.Dropdown(
                    label="情感",
                    choices=["neutral", "angry", "happy", "sad", "excited", "whisper", "sarcastic"],
                    value="neutral",
                )
                e_alpha = gr.Slider(label="情绪强度", minimum=0.0, maximum=1.0, value=1.0, step=0.1)
                e_rate = gr.Slider(label="语速", minimum=0.7, maximum=1.5, value=1.0, step=0.1)
            with gr.Row():
                e_voice = gr.Dropdown(label="临时替换声音（可选）", choices=[], value=None, scale=2)
                e_regenerate = gr.Button("重合成所选段落", variant="primary", scale=1)
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
