"""生产阶段中的质检与局部修复 UI builder — 对齐 Pencil 试听质检画板。"""
from __future__ import annotations

import gradio as gr


def create_review_page() -> dict:
    """创建章节试听、段落检查与按需重合成工具。"""
    with gr.Group(visible=False, elem_id="grp-review") as grp_review:
        with gr.Row(equal_height=True):
            gr.Markdown("##### 章节试听")
            e_review_refresh = gr.Button("刷新质检状态", size="sm")
        e_quality_summary = gr.Markdown(
            "质量状态将在打开项目后显示。",
            elem_classes=["review-status"],
        )
        e_chapter_table = gr.Markdown("打开项目并完成部分合成后，章节状态会显示在这里。")
        with gr.Row():
            e_chapter_sel = gr.Dropdown(
                label="章节", choices=[], value=None, interactive=True, scale=2,
            )
            e_chapter_reload = gr.Button("重新加载试听", size="sm", scale=1)
            e_chapter_audio = gr.Audio(
                label="章节合并试听", type="filepath", interactive=False, scale=3,
            )
        e_chapter_audio_status = gr.Markdown(
            "请选择章节。没有生成音频时，这里会显示明确原因。",
            elem_classes=["review-status"],
        )

        gr.Markdown("##### 段落试听与修复")
        with gr.Row():
            e_quality_filter = gr.Dropdown(
                label="状态筛选",
                choices=[
                    ("全部", "all"),
                    ("待检查", "needs_review"),
                    ("需修复", "needs_fix"),
                    ("技术警告", "technical_warning"),
                    ("已通过", "passed"),
                ],
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
            label="选择需要重合成的段落", choices=[], interactive=True, multiselect=True, value=[],
        )
        e_seg_audio = gr.Audio(label="段落试听", type="filepath", interactive=False)
        e_seg_audio_status = gr.Markdown(
            "请选择已生成音频的段落。",
            elem_classes=["review-status"],
        )
        e_segment_quality = gr.Markdown(
            "选择段落后显示技术 QA 与人工 Review。",
            elem_classes=["review-status"],
        )
        with gr.Row():
            e_run_qa = gr.Button("运行技术 QA", size="sm")
            e_review_status = gr.Dropdown(
                label="人工 QA 状态",
                choices=["needs_review", "needs_fix", "passed"],
                value="needs_review",
            )
            e_issue_type = gr.Dropdown(
                label="问题标签",
                choices=[
                    "emotion", "voice", "speed", "pronunciation",
                    "pause", "noise", "clipping", "other",
                ],
                value="other",
            )
        e_review_note = gr.Textbox(
            label="质检备注",
            placeholder="记录情绪、语速、断句、发音等问题",
            lines=2,
        )
        with gr.Row():
            e_mark_review = gr.Button("保存质检标记")
            e_mark_passed = gr.Button("通过并跳到下一未检", variant="primary")
            e_bulk_pass = gr.Button("批量通过本章技术 QA=pass 段")
        e_bulk_pass_msg = gr.Markdown("")

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
        "e_quality_summary": e_quality_summary,
        "e_chapter_table": e_chapter_table,
        "e_chapter_sel": e_chapter_sel,
        "e_chapter_reload": e_chapter_reload,
        "e_chapter_audio": e_chapter_audio,
        "e_chapter_audio_status": e_chapter_audio_status,
        # ``e_chapter_status`` / ``e_seg_sel`` / ``e_seg_status`` remain aliases
        # for integrations written against the pre-3.3.3 page dictionary.
        "e_chapter_status": e_chapter_audio_status,
        "e_seg_preview_sel": e_seg_preview_sel,
        "e_quality_filter": e_quality_filter,
        "e_prev": e_prev,
        "e_next": e_next,
        "e_seg_regen_sel": e_seg_regen_sel,
        "e_seg_sel": e_seg_preview_sel,
        "e_emo": e_emo,
        "e_alpha": e_alpha,
        "e_rate": e_rate,
        "e_voice": e_voice,
        "e_regenerate": e_regenerate,
        "e_seg_audio": e_seg_audio,
        "e_seg_audio_status": e_seg_audio_status,
        "e_segment_quality": e_segment_quality,
        "e_run_qa": e_run_qa,
        "e_review_status": e_review_status,
        "e_issue_type": e_issue_type,
        "e_review_note": e_review_note,
        "e_mark_review": e_mark_review,
        "e_mark_passed": e_mark_passed,
        "e_bulk_pass": e_bulk_pass,
        "e_bulk_pass_msg": e_bulk_pass_msg,
        "e_seg_status": e_seg_audio_status,
        "e_regenerate_msg": e_regenerate_msg,
    }
