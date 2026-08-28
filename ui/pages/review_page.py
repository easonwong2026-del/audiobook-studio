"""生产阶段中的统一试听与局部修复 UI builder。"""
from __future__ import annotations

import gradio as gr


EMOTION_CHOICES = [
    ("沿用原设定", None),
    "neutral",
    "angry",
    "happy",
    "sad",
    "excited",
    "whisper",
    "sarcastic",
]


def create_review_page() -> dict:
    """创建一个共享播放器的章节试听、段落试听与单段重合成工作区。"""
    with gr.Group(visible=False, elem_id="grp-review") as grp_review:
        gr.Markdown("### 试听与修复")
        e_review_summary = gr.Markdown(
            "生产与音频状态将在打开项目后显示。",
            elem_classes=["review-status"],
        )
        e_chapter_table = gr.Markdown("打开项目并完成部分合成后，章节状态会显示在这里。")
        with gr.Row():
            e_chapter_sel = gr.Dropdown(
                label="章节", choices=[], value=None, interactive=True, scale=2,
            )
            e_chapter_preview = gr.Button("试听整章", size="sm", scale=1)
        gr.Markdown("##### 当前段落")
        with gr.Row():
            e_current_segment = gr.Dropdown(
                label="当前段落", choices=[], interactive=True,
                multiselect=False, value=None, scale=5,
            )
            e_prev = gr.Button("上一段", size="sm", scale=1)
            e_next = gr.Button("下一段", size="sm", scale=1)
        e_review_audio = gr.Audio(label="统一试听播放器", type="filepath", interactive=False)
        e_review_audio_status = gr.Markdown(
            "当前试听：尚未选择段落。",
            elem_classes=["review-status"],
        )
        e_segment_detail = gr.Markdown("当前段落信息将在这里显示。")

        gr.Markdown("#### 重新合成当前段落")
        e_repair_target = gr.Markdown("目标：尚未选择段落。")
        with gr.Accordion("修复参数（可选覆盖）", open=True):
            with gr.Row():
                e_emo = gr.Dropdown(
                    label="情感",
                    choices=EMOTION_CHOICES,
                    value=None,
                )
                e_voice = gr.Dropdown(
                    label="临时声音覆盖（沿用当前声音）",
                    choices=[], value=None, scale=2,
                )
            with gr.Row():
                e_alpha_override = gr.Checkbox(label="覆盖情绪强度", value=False)
                e_alpha = gr.Slider(
                    label="情绪强度", minimum=0.0, maximum=1.0,
                    value=0.8, step=0.1, interactive=False,
                )
                e_rate_override = gr.Checkbox(label="覆盖语速", value=False)
                e_rate = gr.Slider(
                    label="语速", minimum=0.7, maximum=1.5,
                    value=1.0, step=0.1, interactive=False,
                )
            e_regenerate = gr.Button("重新合成当前段落", variant="primary")
            e_regenerate_msg = gr.Markdown("")

    return {
        "group": grp_review,
        "e_review_summary": e_review_summary,
        "e_chapter_table": e_chapter_table,
        "e_chapter_sel": e_chapter_sel,
        "e_chapter_preview": e_chapter_preview,
        "e_current_segment": e_current_segment,
        "e_prev": e_prev,
        "e_next": e_next,
        "e_emo": e_emo,
        "e_voice": e_voice,
        "e_alpha_override": e_alpha_override,
        "e_alpha": e_alpha,
        "e_rate_override": e_rate_override,
        "e_rate": e_rate,
        "e_regenerate": e_regenerate,
        "e_review_audio": e_review_audio,
        "e_review_audio_status": e_review_audio_status,
        "e_segment_detail": e_segment_detail,
        "e_repair_target": e_repair_target,
        "e_regenerate_msg": e_regenerate_msg,
        "e_review_repair_id": gr.State(""),
        "e_review_repair_task_id": gr.State(""),
        "e_review_repair_project": gr.State(""),
    }
