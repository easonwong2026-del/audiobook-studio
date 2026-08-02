"""Integrated v4 source-first project shell."""
from __future__ import annotations

import gradio as gr

from ui.pages.v4_plan_page import create_v4_plan_page
from ui.pages.v4_queue_page import create_v4_queue_page
from ui.pages.v4_speaker_review_page import create_v4_speaker_review_page


def create_v4_workspace_page() -> dict:
    with gr.Group(visible=False, elem_id="grp-v4-workspace") as group:
        gr.Markdown("## v4 Source-first 工作流")
        with gr.Row():
            project = gr.Dropdown(label="v4 项目", choices=[])
            refresh_projects = gr.Button("刷新")
            open_project = gr.Button("打开", variant="primary")
        summary = gr.Markdown("请选择项目")
        with gr.Tabs():
            with gr.Tab("1 角色识别与确认"):
                review = create_v4_speaker_review_page(visible=True)
                segment_ids = gr.Textbox(
                    label="要修改的 segment IDs（逗号分隔）"
                )
            with gr.Tab("2 角色与声音"):
                gr.Markdown(
                    "角色与声音统一使用主导航「③ 角色与声音」中的卡片式绑定入口；"
                    "这里不再重复显示角色选择器。"
                )
                voice_speaker = gr.Dropdown(label="角色", choices=[], visible=False)
                voice_file = gr.Audio(label="参考音频", type="filepath", visible=False)
                bind_voice = gr.Button("绑定声音", visible=False)
                voice_status = gr.Markdown("", visible=False)
            with gr.Tab("3 TTS 设置与计划"):
                profile = gr.Markdown("")
                plan = create_v4_plan_page(visible=True)
                plan_status = gr.Markdown("")
            with gr.Tab("4 合成队列"):
                queue = create_v4_queue_page(visible=True)
            with gr.Tab("5 试听质检"):
                chapter = gr.Dropdown(label="章节", choices=[])
                chapter_audio = gr.Audio(label="章节音频")
            with gr.Tab("6 导出"):
                export_format = gr.Dropdown(
                    ["wav", "mp3", "m4b"], value="wav", label="格式"
                )
                bitrate = gr.Dropdown(
                    ["128k", "192k", "256k"], value="192k", label="码率"
                )
                export = gr.Button("导出", variant="primary")
                export_file = gr.File(label="成品")
                export_status = gr.Markdown("")
            with gr.Tab("迁移旧项目"):
                v3_project = gr.Dropdown(label="v3 项目", choices=[])
                migrate = gr.Button("复制迁移（保留并备份 v3）")
                migration_status = gr.Markdown("")
    return {
        "group": group,
        "project": project,
        "refresh_projects": refresh_projects,
        "open_project": open_project,
        "summary": summary,
        "review": review,
        "segment_ids": segment_ids,
        "voice_speaker": voice_speaker,
        "voice_file": voice_file,
        "bind_voice": bind_voice,
        "voice_status": voice_status,
        "profile": profile,
        "plan": plan,
        "plan_status": plan_status,
        "queue": queue,
        "chapter": chapter,
        "chapter_audio": chapter_audio,
        "export_format": export_format,
        "bitrate": bitrate,
        "export": export,
        "export_file": export_file,
        "export_status": export_status,
        "v3_project": v3_project,
        "migrate": migrate,
        "migration_status": migration_status,
    }
