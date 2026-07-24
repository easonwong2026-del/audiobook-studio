"""合成页 UI builder。"""
from __future__ import annotations
import gradio as gr
from lib import progress as synth_progress


def create_synthesis_page() -> dict:
    """创建合成页组件。

    Returns:
        组件引用字典：所有合成页组件的引用。
    """
    with gr.Group(visible=False, elem_id="grp-synth") as grp_synth:
        gr.Markdown("> 💡 确认所有角色都已绑定了音色 → 点击「开始合成」→ 每段完成后自动显示 ✅。可随时点停止，下次继续（断点续跑）。")
        gr.Markdown("> 📌 试听与重合成请在「试听与质检」分类操作。")
        # O5：合成前分段预览（只读）+ 章节勾��范围（位于 O2 参数面板之上）
        gr.Markdown("#### 📋 合成前分段预览（勾选范围）")
        s_preview_df = gr.Dataframe(
            headers=synth_progress.PREVIEW_HEADERS,
            datatype=synth_progress.PREVIEW_DATATYPES,
            interactive=False,
            label="即将合成的段落（只读预览）",
            wrap=True,
        )
        s_chapters_sel = gr.CheckboxGroup(
            label="合成章节范围（默认全选；取消勾选的章节将被跳过）",
            choices=[],
            value=[],
            interactive=True,
        )
        with gr.Row():
            with gr.Column(scale=3):
                s_log = gr.Textbox(label="", lines=16, max_lines=16, interactive=False, autoscroll=True, show_label=False)
            with gr.Column(scale=1):
                with gr.Group():
                    gr.Markdown("#### 参数面板")
                    # O2 合成期情感 / 语速全局覆盖
                    s_emo = gr.Dropdown(
                        label="情感（合成期全局覆盖）",
                        choices=["(按剧本默认)", "neutral", "angry", "happy", "sad",
                                 "excited", "whisper", "sarcastic"],
                        value="(按剧本默认)",
                        interactive=True,
                    )
                    s_override = gr.Checkbox(
                        label="统一覆盖强度 / 语速（勾选后下方滑块生效）", value=False,
                    )
                    with gr.Row():
                        s_alpha = gr.Slider(label="情绪强度", minimum=0.0, maximum=1.0, value=1.0, step=0.1, scale=1)
                        s_rate = gr.Slider(label="语速", minimum=0.7, maximum=1.5, value=1.0, step=0.1, scale=1)
                    gr.Markdown(
                        "**合成质量 / 速度（beam search）**｜① **1**＝最快，但 GPT 候选少、句尾可能略平或偶有重复，需试听确认；② **2**＝默认平衡点；③ **3**＝质量优先、韵律最稳，但最慢"
                    )
                    s_beam = gr.Dropdown(
                        label="合成质量 / 速度（beam search）",
                        choices=[1, 2, 3],
                        value=2,
                        interactive=True,
                    )
                    s_start = gr.Button("▶ 开始合成", variant="primary")
                    s_cancel = gr.Button("■ 停止", variant="stop")
                gr.Markdown("<span style='color:#b3b3b3;font-size:12px'>合成中请勿操作其他页面</span>")

        # O3：结构化队列进度列表（状态图标 | 章节 | 段落 | 角色 | 文本预览 | 进度%）
        s_queue_list = gr.Dataframe(
            headers=synth_progress.QUEUE_HEADERS,
            datatype=synth_progress.QUEUE_DATATYPES,
            interactive=False,
            label="📋 队列进度",
            wrap=True,
        )

        # O12：段落级暂停 / 恢复（仅合成/暂停态可用）
        with gr.Row():
            s_pause = gr.Button("⏸ 暂停", size="sm")
            s_resume = gr.Button("▶ 恢复", size="sm")

        with gr.Row():
            s_open_btn = gr.Button("📂 打开音频文件夹", size="sm")
            s_open_msg = gr.Markdown("")
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
