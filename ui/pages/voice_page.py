"""角色与声音阶段 UI builder。"""
from __future__ import annotations

import os

import gradio as gr

from lib import config


def _builder_lib_voices() -> list[str]:
    """扫描音色库目录，返回文件名列表（替代 app.py 的 _lib_voices）。"""
    vlib = config.get_voice_library()
    os.makedirs(vlib, exist_ok=True)
    return [f for f in os.listdir(vlib) if f.endswith((".wav", ".mp3"))]


def create_voice_page() -> dict:
    """创建以角色绑定为主、声音资产管理为辅的页面。"""
    with gr.Group(visible=False, elem_id="grp-voices") as grp_voices:
        gr.Markdown("### 角色与声音")
        gr.Markdown("先为每个角色确认声音。所有角色完成绑定后，即可进入生产。")
        v_status = gr.Markdown("")
        v_table = gr.Markdown("<div class='inline-empty'>打开项目后显示角色绑定清单。</div>")

        with gr.Group(elem_classes=["binding-workspace"]):
            gr.Markdown("#### 为角色配置声音")
            gr.Markdown("从音色库选择、上传文件或直接录制；试听无误后确认绑定。")
            with gr.Row(equal_height=True):
                with gr.Column(scale=3):
                    v_audio = gr.Audio(
                        label="上传或录制参考音频",
                        type="filepath",
                        sources=["upload", "microphone"],
                    )
                    v_current = gr.Markdown("当前参考音频：未选择")
                with gr.Column(scale=2):
                    v_role = gr.Dropdown(label="角色", choices=[], interactive=True)
                    v_bind_category = gr.Dropdown(
                        label="音色分类（可选）", choices=[], value=None, interactive=True,
                    )
                    v_lib = gr.Dropdown(
                        label="从音色库选择", choices=_builder_lib_voices(), interactive=True,
                    )
                    with gr.Row():
                        v_preview_btn = gr.Button("试听已绑定声音", size="sm")
                        v_bind = gr.Button("确认绑定", variant="primary")
            v_bind_msg = gr.Markdown("")
            v_preview_audio = gr.Audio(label="声音试听", type="filepath", interactive=False)

        with gr.Accordion("管理声音资产", open=False, elem_classes=["asset-accordion"]):
            gr.Markdown("上传、录制和整理可复用的参考音频；这些操作不会改变当前项目的绑定，直到你点击“确认绑定”。")
            gr.Markdown("#### 浏览音色库")
            with gr.Row():
                v_lib_search = gr.Textbox(label="搜索声音", placeholder="按名称或分类搜索", scale=3)
                v_lib_category = gr.Dropdown(label="分类", choices=[], value=None, interactive=True, scale=1)
            v_lib_browser = gr.Dataframe(
                headers=["名称", "分类", "大小(KB)", "试听"],
                datatype=["str", "str", "str", "str"],
                interactive=False,
                label="选择一行即可带回上方绑定区",
                wrap=True,
            )

            gr.Markdown("#### 保存新声音")
            with gr.Row():
                v_record = gr.Microphone(label="录制声音", type="filepath", scale=1)
                v_upload_clone = gr.Audio(label="或上传文件", type="filepath", sources=["upload"], scale=1)
            with gr.Row():
                v_save_name = gr.Textbox(label="声音名称", placeholder="例如：温柔女声 01", scale=2)
                v_save_category = gr.Dropdown(
                    label="分类", choices=["未分类"], value="未分类", interactive=True, scale=1,
                )
            v_save_btn = gr.Button("保存到音色库", variant="secondary")
            v_save_msg = gr.Markdown("")

    return {
        "group": grp_voices,
        "v_status": v_status,
        "v_table": v_table,
        "v_bind_category": v_bind_category,
        "v_audio": v_audio,
        "v_role": v_role,
        "v_lib": v_lib,
        "v_current": v_current,
        "v_bind": v_bind,
        "v_bind_msg": v_bind_msg,
        "v_preview_btn": v_preview_btn,
        "v_preview_audio": v_preview_audio,
        "v_record": v_record,
        "v_upload_clone": v_upload_clone,
        "v_save_name": v_save_name,
        "v_save_category": v_save_category,
        "v_save_btn": v_save_btn,
        "v_save_msg": v_save_msg,
        "v_lib_search": v_lib_search,
        "v_lib_category": v_lib_category,
        "v_lib_browser": v_lib_browser,
    }
