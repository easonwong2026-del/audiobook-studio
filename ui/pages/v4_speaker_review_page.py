"""Isolated v4 speaker review panel; intentionally not in the v3 navigation."""
from __future__ import annotations

import gradio as gr


def create_v4_speaker_review_page(*, visible: bool = False) -> dict:
    """Build Phase 2 review controls for later v4 project-shell integration."""
    with gr.Group(visible=visible, elem_id="grp-v4-speaker-review") as group:
        gr.Markdown("### 待确认片段")
        gr.Markdown("仅显示 unresolved 对白；人工结果会锁定为 manual。")
        summary = gr.Markdown("尚未加载 v4 项目")
        with gr.Row():
            route = gr.Button("继续 AI 角色识别", variant="primary")
            stop_route = gr.Button("停止分析")
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
        with gr.Accordion("合并角色", open=False):
            merge_source = gr.Dropdown(label="来源角色", choices=[])
            merge_target = gr.Dropdown(label="目标角色", choices=[])
            merge = gr.Button("合并并保留来源名为别名")
        status = gr.Markdown("")
    return {
        "group": group,
        "summary": summary,
        "route": route,
        "stop_route": stop_route,
        "table": table,
        "speaker": speaker,
        "new_speaker": new_speaker,
        "lock_speaker": lock_speaker,
        "assign": assign,
        "refresh": refresh,
        "merge_source": merge_source,
        "merge_target": merge_target,
        "merge": merge,
        "status": status,
    }
