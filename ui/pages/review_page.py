"""生产阶段中的试听与局部修复 UI builder。"""
from __future__ import annotations

import gradio as gr


AUDIO_FILTER_CHOICES = [
    ("全部", "all"),
    ("已生成", "generated"),
    ("未生成", "missing"),
]


def create_review_page() -> dict:
    """创建章节试听、段落试听与按需重合成工具。"""
    with gr.Group(visible=False, elem_id="grp-review") as grp_review:
        with gr.Row(equal_height=True):
            gr.Markdown("##### 章节试听")
            e_review_refresh = gr.Button("刷新试听状态", size="sm")
        e_review_summary = gr.Markdown(
            "生产与音频状态将在打开项目后显示。",
            elem_classes=["review-status"],
        )
        e_chapter_table = gr.Markdown("打开项目并完成部分合成后，章节状态会显示在这里。")
        with gr.Row():
            e_chapter_sel = gr.Dropdown(
                label="章节", choices=[], value=None, interactive=True, scale=2,
            )
            e_chapter_reload = gr.Button("重新加载试听", size="sm", scale=1)
            e_chapter_audio = gr.Audio(
                label="章节试听", type="filepath", interactive=False, scale=3,
            )
        e_chapter_audio_status = gr.Markdown(
            "请选择章节。没有生成音频时，这里会显示明确原因。",
            elem_classes=["review-status"],
        )

        gr.Markdown("##### 段落试听与修复")
        with gr.Row():
            e_audio_filter = gr.Dropdown(
                label="音频筛选",
                choices=AUDIO_FILTER_CHOICES,
                value="all",
                scale=1,
            )
            e_seg_preview_sel = gr.Dropdown(
                label="试听段落", choices=[], interactive=True,
                multiselect=False, value=None, scale=3,
            )
            e_prev = gr.Button("上一段", size="sm", scale=1)
            e_next = gr.Button("下一段", size="sm", scale=1)
        e_seg_regen_sel = gr.Dropdown(
            label="批量选择段落（也用于修复/重合成）",
            choices=[], interactive=True, multiselect=True, value=[],
        )
        with gr.Row():
            e_select_chapter_segments = gr.Button("选择当前章全部", size="sm")
            e_select_filtered_segments = gr.Button("选择当前筛选结果", size="sm")
            e_clear_segment_selection = gr.Button("清空选择", size="sm")
        e_batch_repair = gr.Button(
            "批量加入修复/重合成", size="sm", variant="primary"
        )
        e_seg_audio = gr.Audio(label="段落试听", type="filepath", interactive=False)
        e_seg_audio_status = gr.Markdown(
            "请选择已生成音频的段落。",
            elem_classes=["review-status"],
        )
        e_segment_status = gr.Markdown(
            "选择段落后显示音频 revision 与修复状态。",
            elem_classes=["review-status"],
        )

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
        "e_review_refresh": e_review_refresh,
        "e_review_summary": e_review_summary,
        "e_chapter_table": e_chapter_table,
        "e_chapter_sel": e_chapter_sel,
        "e_chapter_reload": e_chapter_reload,
        "e_chapter_audio": e_chapter_audio,
        "e_chapter_audio_status": e_chapter_audio_status,
        "e_chapter_status": e_chapter_audio_status,
        "e_seg_preview_sel": e_seg_preview_sel,
        "e_audio_filter": e_audio_filter,
        "e_prev": e_prev,
        "e_next": e_next,
        "e_seg_regen_sel": e_seg_regen_sel,
        "e_select_chapter_segments": e_select_chapter_segments,
        "e_select_filtered_segments": e_select_filtered_segments,
        "e_clear_segment_selection": e_clear_segment_selection,
        "e_batch_repair": e_batch_repair,
        "e_emo": e_emo,
        "e_alpha": e_alpha,
        "e_rate": e_rate,
        "e_voice": e_voice,
        "e_regenerate": e_regenerate,
        "e_seg_audio": e_seg_audio,
        "e_seg_audio_status": e_seg_audio_status,
        "e_segment_status": e_segment_status,
        "e_seg_status": e_seg_audio_status,
        "e_regenerate_msg": e_regenerate_msg,
        "e_review_repair_id": gr.State(""),
        "e_review_repair_task_id": gr.State(""),
        "e_review_repair_project": gr.State(""),
    }
