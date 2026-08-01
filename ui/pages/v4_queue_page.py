"""Isolated v4 synthesis queue controls for the future v4 project shell."""
from __future__ import annotations

import gradio as gr


def create_v4_queue_page() -> dict:
    with gr.Group(visible=False, elem_id="grp-v4-synthesis-queue") as group:
        gr.Markdown("### 合成队列")
        summary = gr.Markdown("尚未加载任务")
        table = gr.Dataframe(
            headers=[
                "task_id", "chapter", "speaker", "状态", "长度",
                "尝试", "拆分深度", "缓存",
            ],
            interactive=False,
            row_count=12,
        )
        with gr.Row():
            start = gr.Button("开始/继续合成", variant="primary")
            cancel = gr.Button("停止", variant="stop")
            refresh = gr.Button("刷新")
        status = gr.Markdown("")
    return {
        "group": group,
        "summary": summary,
        "table": table,
        "start": start,
        "cancel": cancel,
        "refresh": refresh,
        "status": status,
    }
