"""生产与质检：合成控制台 UI builder — 对齐 Pencil 合成中心画板。"""
from __future__ import annotations

import gradio as gr

from lib import progress as synth_progress


def create_synthesis_page() -> dict:
    """创建以生产状态和队列追踪为中心的合成界面。"""
    with gr.Group(visible=False, elem_id="grp-synth") as grp_synth:
        with gr.Group(elem_classes=["production-command"]):
            with gr.Row(equal_height=True):
                with gr.Column(scale=3):
                    gr.Markdown("##### 开始生产")
                    gr.Markdown("默认使用剧本标注。已有结果自动断点续跑。")
                with gr.Column(scale=2):
                    with gr.Row():
                        s_start = gr.Button("开始合成", variant="primary")
                        s_pause = gr.Button("暂停", size="sm")
                        s_resume = gr.Button("恢复", size="sm")
                        s_cancel = gr.Button("停止", variant="stop", size="sm")

            with gr.Accordion("生产范围", open=False):
                s_chapters_sel = gr.CheckboxGroup(
                    label="章节范围", choices=[], value=[], interactive=True,
                )
                s_preview_df = gr.Dataframe(
                    headers=synth_progress.PREVIEW_HEADERS,
                    datatype=synth_progress.PREVIEW_DATATYPES,
                    interactive=False,
                    label="待合成段落",
                    wrap=True,
                )

            with gr.Accordion("高级设置", open=False, elem_classes=["advanced-settings"]):
                with gr.Row():
                    s_emo = gr.Dropdown(
                        label="统一情感",
                        choices=["(按剧本默认)", "neutral", "angry", "happy", "sad", "excited", "whisper", "sarcastic"],
                        value="(按剧本默认)", interactive=True, scale=2,
                    )
                    s_override = gr.Checkbox(label="统一覆盖", value=False, scale=1)
                with gr.Row():
                    s_alpha = gr.Slider(label="情绪强度", minimum=0.0, maximum=1.0, value=1.0, step=0.1)
                    s_rate = gr.Slider(label="语速", minimum=0.7, maximum=1.5, value=1.0, step=0.1)
                    s_beam = gr.Dropdown(
                        label="合成质量",
                        choices=[("快速", 1), ("标准", 2), ("高质量", 3)],
                        value=2, interactive=True,
                    )

        gr.Markdown("##### 生产队列")
        s_queue_list = gr.Dataframe(
            headers=synth_progress.QUEUE_HEADERS,
            datatype=synth_progress.QUEUE_DATATYPES,
            interactive=False,
            label="当前任务状态",
            wrap=True,
        )
        with gr.Row():
            s_open_btn = gr.Button("打开音频文件夹", size="sm")
            s_open_msg = gr.Markdown("")

        with gr.Accordion("运行详情", open=False, elem_classes=["run-log"]):
            s_log = gr.Textbox(label="运行日志", lines=12, max_lines=12, interactive=False, autoscroll=True)

    return {
        "group": grp_synth,
        "s_preview_df": s_preview_df,
        "s_chapters_sel": s_chapters_sel,
        "s_log": s_log,
        "s_emo": s_emo,
        "s_override": s_override,
        "s_alpha": s_alpha,
        "s_rate": s_rate,
        "s_beam": s_beam,
        "s_start": s_start,
        "s_cancel": s_cancel,
        "s_queue_list": s_queue_list,
        "s_pause": s_pause,
        "s_resume": s_resume,
        "s_open_btn": s_open_btn,
        "s_open_msg": s_open_msg,
    }
