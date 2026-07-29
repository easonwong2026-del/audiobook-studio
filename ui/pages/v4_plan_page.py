"""Isolated Phase 3 synthesis-plan preview panel."""
from __future__ import annotations

import gradio as gr


def create_v4_plan_page(*, visible: bool = False) -> dict:
    with gr.Group(visible=visible, elem_id="grp-v4-synthesis-plan") as group:
        gr.Markdown("### TTS 合成计划")
        summary = gr.Markdown("尚未生成计划")
        table = gr.Dataframe(
            headers=[
                "task_id", "chapter", "speaker", "voice",
                "segments", "长度", "续接", "尾停顿(ms)",
            ],
            interactive=False,
            row_count=10,
        )
        with gr.Row():
            generate = gr.Button("重新生成计划", variant="primary")
            refresh = gr.Button("刷新预览")
        warnings = gr.Markdown("")
    return {
        "group": group,
        "summary": summary,
        "table": table,
        "generate": generate,
        "refresh": refresh,
        "warnings": warnings,
    }
