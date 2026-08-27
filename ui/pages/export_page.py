"""交付阶段 UI builder — 对齐 Pencil 交付·导出成品画板。"""
from __future__ import annotations

import gradio as gr


def create_export_page() -> dict:
    """创建成品与字幕交付页面。"""
    with gr.Group(visible=False, elem_id="grp-export") as grp_export:
        with gr.Group(elem_classes=["delivery-workspace"]):
            gr.Markdown("##### 导出有声书")
            with gr.Row():
                e_readiness = gr.Markdown(
                    "打开项目后检查生产完整性、音频、章节、FFmpeg 与 metadata。",
                    elem_classes=["review-status"],
                )
                e_readiness_refresh = gr.Button("刷新交付准备度", size="sm")
            with gr.Row():
                e_fmt = gr.Dropdown(label="格式", choices=["mp3", "m4b", "wav"], value="wav")
                e_br = gr.Dropdown(label="比特率", choices=["128k", "192k", "320k"], value="192k")
            e_save_dir = gr.Textbox(
                label="保存位置",
                placeholder="留空使用项目默认目录",
            )
            e_save_dir_hint = gr.Markdown(
                "项目默认目录在打开项目后显示。留空即使用该目录。",
                elem_classes=["export-default-hint"],
            )
            e_go = gr.Button("导出成品", variant="primary")
            e_out = gr.File(label="下载成品", interactive=False)
            e_path = gr.Textbox(label="导出状态", interactive=False)
            e_open = gr.Button("打开导出位置", variant="secondary", interactive=False)
            # Export status is backed by the durable task id, not a local
            # button/session flag.  The owning app wires these hidden states
            # to the Export-only polling timer.
            e_export_task_id = gr.State("")
            e_export_output_dir = gr.State("")

        with gr.Group(elem_classes=["delivery-workspace"]):
            gr.Markdown("##### 字幕文件")
            with gr.Row():
                e_subtitle = gr.Radio(
                    label="字幕格式",
                    choices=[("不生成", "none"), ("仅 SRT", "srt"), ("仅 LRC", "lrc"), ("SRT + LRC", "both")],
                    value="none",
                    interactive=True,
                    scale=3,
                )
                e_subtitle_btn = gr.Button("生成字幕", variant="secondary", scale=1)
            e_subtitle_out = gr.File(label="下载字幕", interactive=False)
            e_subtitle_msg = gr.Markdown("")

    return {
        "group": grp_export,
        "e_readiness": e_readiness,
        "e_readiness_refresh": e_readiness_refresh,
        "e_fmt": e_fmt,
        "e_br": e_br,
        "e_save_dir": e_save_dir,
        "e_save_dir_hint": e_save_dir_hint,
        "e_go": e_go,
        "e_out": e_out,
        "e_path": e_path,
        "e_open": e_open,
        "e_export_task_id": e_export_task_id,
        "e_export_output_dir": e_export_output_dir,
        "e_subtitle": e_subtitle,
        "e_subtitle_btn": e_subtitle_btn,
        "e_subtitle_out": e_subtitle_out,
        "e_subtitle_msg": e_subtitle_msg,
    }
