"""生产与质检：合成控制台 UI builder — 对齐 Pencil 合成中心画板。"""
from __future__ import annotations

import gradio as gr

from lib import progress as synth_progress


def create_synthesis_page() -> dict:
    """创建以生产状态和队列追踪为中心的合成界面。"""
    with gr.Group(visible=False, elem_id="grp-synth") as grp_synth:
        s_engine_status = gr.Markdown(
            "生产引擎：读取 runtime 实际状态中…",
            elem_classes=["production-engine-status"],
        )
        s_task_status = gr.Markdown(
            "当前没有运行中的生产任务。",
            elem_classes=["production-task-status"],
        )
        with gr.Group(elem_classes=["production-command"]):
            with gr.Row(equal_height=True):
                with gr.Column(scale=3):
                    gr.Markdown("##### 开始生产")
                    gr.Markdown("默认使用剧本标注。已有结果自动断点续跑。")
                with gr.Column(scale=2):
                    with gr.Row():
                        s_start = gr.Button(
                            "开始合成", variant="primary", interactive=False,
                        )
                        s_pause = gr.Button("暂停", size="sm")
                        s_resume = gr.Button("恢复", size="sm")
                        s_cancel = gr.Button("停止", variant="stop", size="sm")

            with gr.Accordion("生产范围", open=False):
                s_scope_mode = gr.Radio(
                    label="生产范围模式",
                    choices=[
                        ("整本", "all"),
                        ("按章节", "chapters"),
                        ("自定义段落", "segments"),
                    ],
                    value="all",
                    interactive=True,
                )
                s_scope_readiness = gr.Markdown(
                    "选择范围后，这里会显示当前 scope 的生产准备状态。",
                    elem_classes=["production-scope-readiness"],
                )
                with gr.Group(visible=False) as s_chapter_scope_group:
                    s_chapters_sel = gr.CheckboxGroup(
                        label="章节范围", choices=[], value=[], interactive=True,
                    )
                with gr.Group(visible=False) as s_segment_scope_group:
                    with gr.Row(equal_height=True):
                        s_segment_chapter_filter = gr.Dropdown(
                            label="先按章节筛选段落",
                            choices=[],
                            value=None,
                            interactive=True,
                            scale=3,
                        )
                        s_select_scope_segments = gr.Button(
                            "全选当前范围", size="sm", scale=1,
                        )
                        s_clear_scope_segments = gr.Button(
                            "清空选择", size="sm", scale=1,
                        )
                    with gr.Row(equal_height=True):
                        s_select_pending_segments = gr.Button(
                            "仅未完成", size="sm",
                        )
                        s_select_failed_segments = gr.Button(
                            "仅失败", size="sm",
                        )
                    s_segments_sel = gr.CheckboxGroup(
                        label="自定义段落（可跨章节累计选择）",
                        choices=[],
                        value=[],
                        interactive=True,
                    )
                    gr.Markdown(
                        "已完成段不会由普通生产重做；需要重新生成请使用试听质检中的修复/重生成。",
                        elem_classes=["production-scope-note"],
                    )
                s_preview_df = gr.Dataframe(
                    headers=synth_progress.SCOPE_PREVIEW_HEADERS,
                    datatype=synth_progress.SCOPE_PREVIEW_DATATYPES,
                    interactive=False,
                    label="当前生产范围预览",
                    visible=False,
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

        gr.Markdown("##### 任务执行明细")
        s_queue_list = gr.Dataframe(
            headers=synth_progress.QUEUE_HEADERS,
            datatype=synth_progress.QUEUE_DATATYPES,
            interactive=False,
            label="段落执行状态",
            wrap=True,
        )
        with gr.Row():
            s_open_btn = gr.Button("打开音频文件夹", size="sm")
            s_open_msg = gr.Markdown("")

        with gr.Accordion("运行详情", open=False, elem_classes=["run-log"]):
            s_log = gr.Textbox(label="运行日志", lines=12, max_lines=12, interactive=False, autoscroll=True)

    return {
        "group": grp_synth,
        "s_task_status": s_task_status,
        "s_engine_status": s_engine_status,
        "s_preview_df": s_preview_df,
        "s_scope_mode": s_scope_mode,
        "s_scope_readiness": s_scope_readiness,
        "s_chapter_scope_group": s_chapter_scope_group,
        "s_segment_scope_group": s_segment_scope_group,
        "s_chapters_sel": s_chapters_sel,
        "s_segment_chapter_filter": s_segment_chapter_filter,
        "s_segments_sel": s_segments_sel,
        "s_select_scope_segments": s_select_scope_segments,
        "s_clear_scope_segments": s_clear_scope_segments,
        "s_select_pending_segments": s_select_pending_segments,
        "s_select_failed_segments": s_select_failed_segments,
        "s_segment_selection_state": gr.State([]),
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
