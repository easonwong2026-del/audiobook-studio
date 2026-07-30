"""Isolated v4 speaker review panel; intentionally not in the v3 navigation."""
from __future__ import annotations

import gradio as gr


def create_v4_speaker_review_page() -> dict:
    """Build Phase 2 review controls for later v4 project-shell integration."""
    with gr.Group(visible=False, elem_id="grp-v4-speaker-review") as group:
        gr.Markdown("### 待确认片段")
        gr.Markdown("仅显示 unresolved 对白；人工结果会锁定为 manual。")
        summary = gr.Markdown("尚未加载 v4 项目")
        table = gr.Dataframe(
            headers=["segment_id", "chapter_id", "原文"],
            datatype=["str", "str", "str"],
            interactive=False,
            row_count=8,
        )
        with gr.Row():
            speaker = gr.Dropdown(label="指定已有角色", choices=[])
            new_speaker = gr.Textbox(label="或新建角色")
            lock_speaker = gr.Checkbox(label="锁定角色", value=True)
        with gr.Row():
            assign = gr.Button("应用到选中片段", variant="primary")
            refresh = gr.Button("刷新待确认列表")
        status = gr.Markdown("")
    return {
        "group": group,
        "summary": summary,
        "table": table,
        "speaker": speaker,
        "new_speaker": new_speaker,
        "lock_speaker": lock_speaker,
        "assign": assign,
        "refresh": refresh,
        "status": status,
    }
