"""Isolated v4 speaker review panel; intentionally not in the v3 navigation."""
from __future__ import annotations

import gradio as gr


def create_v4_speaker_review_page(*, visible: bool = False) -> dict:
    """Build Phase 2 review controls for later v4 project-shell integration."""
    with gr.Group(visible=visible, elem_id="grp-v4-speaker-review") as group:
        gr.Markdown("### 待确认片段")
        gr.Markdown(
            "仅显示 unresolved 对白；明确规则角色可直接使用，AI 角色先进入候选角色区。"
        )
        summary = gr.Markdown("尚未加载 v4 项目")
        with gr.Row():
            extract = gr.Button("分析角色候选", variant="primary")
            route = gr.Button("按正式角色路由对白", variant="secondary")
            stop_route = gr.Button("停止分析")
        extract_status = gr.Markdown("")

        with gr.Accordion("候选角色", open=True):
            gr.Markdown(
                "AI 候选不会自动创建正式角色；请查看证据后确认、拒绝，或合并到已有角色。"
            )
            candidates_table = gr.Dataframe(
                headers=[
                    "candidate_id", "候选名称", "可能别名", "置信度",
                    "原文证据", "章节", "来源", "状态",
                ],
                datatype=["str"] * 8,
                interactive=False,
                row_count=(0, "dynamic"),
                wrap=True,
            )
            with gr.Row():
                candidate = gr.Dropdown(label="选择候选角色", choices=[], scale=2)
                candidate_target = gr.Dropdown(
                    label="合并到已有角色（可选）", choices=[], scale=2
                )
            with gr.Row():
                confirm_candidate = gr.Button("确认成为正式角色", variant="primary")
                reject_candidate = gr.Button("拒绝候选", variant="secondary")
                merge_candidate = gr.Button("合并到已有角色", variant="secondary")
            candidate_status = gr.Markdown("")
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
        "extract": extract,
        "extract_status": extract_status,
        "stop_route": stop_route,
        "candidates_table": candidates_table,
        "candidate": candidate,
        "candidate_target": candidate_target,
        "confirm_candidate": confirm_candidate,
        "reject_candidate": reject_candidate,
        "merge_candidate": merge_candidate,
        "candidate_status": candidate_status,
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
