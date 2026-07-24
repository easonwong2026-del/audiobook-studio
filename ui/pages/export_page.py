"""导出页 UI builder。"""
from __future__ import annotations
import gradio as gr


def create_export_page() -> dict:
    """创建导出页组件。"""
    with gr.Group(visible=False, elem_id="grp-export") as grp_export:
        gr.Markdown("> 💡 确认全部 OK 后点击「一键导出」。WAV 格式无需转换即可播放。")
        gr.Markdown("---")
        gr.Markdown("#### 💾 导出设置")
        with gr.Row():
            e_fmt = gr.Dropdown(label="导出格式", choices=["mp3","m4b","wav"], value="wav", scale=1)
            e_br = gr.Dropdown(label="比特率", choices=["128k","192k","320k"], value="192k", scale=1)
        e_save_dir = gr.Textbox(label="保存到（留空 = 默认项目目录）", placeholder="如: D:\\有声书\\输出")
        with gr.Row():
            e_go = gr.Button("📦 一键导出有声书", variant="primary")
        e_out = gr.File(label="下载导出文件", interactive=False)
        e_path = gr.Textbox(label="文件路径", interactive=False)

        gr.Markdown("---")
        gr.Markdown("#### 📝 字幕（srt / lrc）")
        with gr.Row():
            e_subtitle = gr.Radio(
                label="生成字幕",
                choices=[("不生成", "none"), ("仅 srt", "srt"), ("仅 lrc", "lrc"), ("srt + lrc", "both")],
                value="none",
                interactive=True,
            )
            e_subtitle_btn = gr.Button("📝 生成字幕", variant="secondary")
        e_subtitle_out = gr.File(label="字幕文件下载", interactive=False)
        e_subtitle_msg = gr.Markdown("")
    return {
        "group": grp_export,
        "e_fmt": e_fmt,
        "e_br": e_br,
        "e_save_dir": e_save_dir,
        "e_go": e_go,
        "e_out": e_out,
        "e_path": e_path,
        "e_subtitle": e_subtitle,
        "e_subtitle_btn": e_subtitle_btn,
        "e_subtitle_out": e_subtitle_out,
        "e_subtitle_msg": e_subtitle_msg,
    }
