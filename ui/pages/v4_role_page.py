"""V4 高级角色整理 UI builder — AI 识别 / 指派 / 合并 / 锁定 / 别名。

组件默认作为开发调试页隐藏，也可以嵌入「③ 角色与声音」的高级折叠区，
让普通用户在同一角色工作流中使用 V4 的高级能力。
"""
from __future__ import annotations

import gradio as gr


def create_v4_role_page(
    *, visible: bool = False, elem_id: str = "grp-v4-role"
) -> dict:
    """创建 V4 角色工作台；可嵌入角色页或作为开发调试入口。"""
    with gr.Group(visible=visible, elem_id=elem_id) as grp_v4_role:
        gr.Markdown("## V4 角色工作台")
        gr.Markdown(
            "V4 项目使用稳定角色 ID。未确认片段可先由 AI 自动识别，"
            "再人工指派、合并（旧角色保留为别名）、锁定或修改别名。"
        )
        with gr.Row():
            v4r_project = gr.Dropdown(label="v4 项目", choices=[], scale=3)
            v4r_refresh = gr.Button("刷新", size="sm", scale=1)
            v4r_open = gr.Button("打开", variant="primary", scale=1)
        v4r_summary = gr.Markdown("请选择 v4 项目")

        with gr.Accordion("① 待确认片段与 AI 识别", open=True):
            v4r_unresolved_table = gr.Dataframe(
                headers=["segment_id", "chapter_id", "text"],
                datatype=["str", "str", "str"],
                row_count=(0, "dynamic"),
                col_count=(3, "fixed"),
                interactive=False,
                wrap=True,
                label="待确认片段（unresolved）",
            )
            with gr.Row():
                v4r_extract_btn = gr.Button("分析角色候选", variant="primary", size="sm")
                v4r_route_btn = gr.Button("🤖 AI 自动识别角色", variant="primary", size="sm")
                v4r_route_msg = gr.Markdown("")

        with gr.Accordion("② 候选角色确认", open=True):
            gr.Markdown("AI 候选不会自动创建正式角色；确认前请查看原文证据。")
            v4r_candidates_table = gr.Dataframe(
                headers=[
                    "candidate_id", "候选名称", "可能别名", "置信度",
                    "原文证据", "章节", "来源", "状态",
                ],
                datatype=["str"] * 8,
                row_count=(0, "dynamic"),
                interactive=False,
                wrap=True,
            )
            with gr.Row():
                v4r_candidate = gr.Dropdown(label="选择候选角色", choices=[], scale=1)
                v4r_candidate_target = gr.Dropdown(
                    label="合并到已有角色", choices=[], scale=1
                )
            with gr.Row():
                v4r_confirm_candidate = gr.Button("确认成为正式角色", variant="primary", size="sm")
                v4r_reject_candidate = gr.Button("拒绝候选", size="sm")
                v4r_merge_candidate = gr.Button("合并到已有角色", size="sm")
                v4r_candidate_msg = gr.Markdown("")

        with gr.Accordion("② 人工指派片段", open=False):
            with gr.Row():
                v4r_assign_segs = gr.Textbox(
                    label="要指派的片段 ID（逗号分隔）", scale=2,
                    placeholder="例如：segment_000012, segment_000013",
                )
                v4r_assign_speaker = gr.Dropdown(
                    label="指定现有角色", choices=[], scale=1,
                )
                v4r_assign_new = gr.Textbox(
                    label="或新建角色名", scale=1,
                )
                v4r_assign_lock = gr.Checkbox(label="锁定该角色", value=False, scale=1)
            with gr.Row():
                v4r_assign_btn = gr.Button("指派片段", variant="secondary", size="sm")
                v4r_assign_msg = gr.Markdown("")

        with gr.Accordion("③ 合并角色", open=False), gr.Row():
            v4r_merge_source = gr.Dropdown(label="合并来源角色", choices=[], scale=1)
            v4r_merge_target = gr.Dropdown(label="合并到角色", choices=[], scale=1)
            v4r_merge_btn = gr.Button("合并角色", variant="secondary", size="sm")
            v4r_merge_msg = gr.Markdown("")

        with gr.Accordion("④ 锁定 / 别名", open=False):
            with gr.Row():
                v4r_lock_speaker = gr.Dropdown(label="选择角色", choices=[], scale=1)
                v4r_lock_btn = gr.Button("🔒 切换锁定状态", size="sm", scale=1)
                v4r_lock_msg = gr.Markdown("")
            with gr.Row():
                v4r_alias_speaker = gr.Dropdown(label="选择角色（修改别名）", choices=[], scale=1)
                v4r_alias = gr.Textbox(label="修改别名（逗号分隔，保存即生效）", scale=2)
                v4r_alias_btn = gr.Button("保存别名", size="sm", scale=1)
                v4r_alias_msg = gr.Markdown("")

    return {
        "group": grp_v4_role,
        "project": v4r_project,
        "refresh": v4r_refresh,
        "open": v4r_open,
        "summary": v4r_summary,
        "unresolved_table": v4r_unresolved_table,
        "route_btn": v4r_route_btn,
        "extract_btn": v4r_extract_btn,
        "route_msg": v4r_route_msg,
        "candidates_table": v4r_candidates_table,
        "candidate": v4r_candidate,
        "candidate_target": v4r_candidate_target,
        "confirm_candidate": v4r_confirm_candidate,
        "reject_candidate": v4r_reject_candidate,
        "merge_candidate": v4r_merge_candidate,
        "candidate_msg": v4r_candidate_msg,
        "assign_segs": v4r_assign_segs,
        "assign_speaker": v4r_assign_speaker,
        "assign_new": v4r_assign_new,
        "assign_lock": v4r_assign_lock,
        "assign_btn": v4r_assign_btn,
        "assign_msg": v4r_assign_msg,
        "merge_source": v4r_merge_source,
        "merge_target": v4r_merge_target,
        "merge_btn": v4r_merge_btn,
        "merge_msg": v4r_merge_msg,
        "lock_speaker": v4r_lock_speaker,
        "lock_btn": v4r_lock_btn,
        "lock_msg": v4r_lock_msg,
        "alias_speaker": v4r_alias_speaker,
        "alias": v4r_alias,
        "alias_btn": v4r_alias_btn,
        "alias_msg": v4r_alias_msg,
    }
