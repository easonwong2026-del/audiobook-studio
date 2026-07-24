"""音色资产页 UI builder。"""
from __future__ import annotations
import os
import gradio as gr
from lib import config
from lib import voice_lib as _vl


def _builder_lib_voices() -> list[str]:
    """扫描音色库目录，返回文件名列表（替代 app.py 的 _lib_voices）。"""
    vlib = config.get_voice_library()
    os.makedirs(vlib, exist_ok=True)
    return [f for f in os.listdir(vlib) if f.endswith(('.wav', '.mp3'))]


def create_voice_page() -> dict:
    """创建音色资产页组件。

    Returns:
        组件引用字典：所有音色资产页组件的引用。
    """
    with gr.Group(visible=False, elem_id="grp-voices") as grp_voices:
        gr.Markdown("> 💡 **步骤**: ① 上传/录制参考音频 或 从音色库选择 → 用播放器试听确认 → ② 选角色 → ③ 点击绑定。每绑定一个角色后下方表格会更新。")
        v_status = gr.Markdown("")
        v_table = gr.Markdown("")

        gr.Markdown("---")
        gr.Markdown("#### 🎙 绑定音色")
        gr.Markdown("> 💡 可先按分类筛选音色库，再从下方下拉中选择，快速定位目标音色。")

        with gr.Row():
            v_bind_category = gr.Dropdown(label="按分类筛选音色库", choices=[], value=None, interactive=True,
                                           scale=1, info="选中后右侧「或从音色库选择」仅显示该分类")
        with gr.Row():
            with gr.Column(scale=2):
                v_audio = gr.Audio(label="上传 / 录制参考音频", type="filepath", sources=["upload","microphone"])
            with gr.Column(scale=1):
                v_role = gr.Dropdown(label="选择角色", choices=[], interactive=True)
                v_lib = gr.Dropdown(label="或从音色库选择", choices=_builder_lib_voices(), interactive=True,
                                    info="下方可按分类筛选音色库")

        v_current = gr.Markdown("*当前参考音频: 未选择*")

        with gr.Row():
            v_bind = gr.Button("✅ 确认绑定", variant="primary")

        v_bind_msg = gr.Textbox(label="", visible=False)

        gr.Markdown("---")
        gr.Markdown("#### 🎧 试听已绑定角色音色")
        v_preview_btn = gr.Button("🎧 试听所选角色音色")
        v_preview_audio = gr.Audio(label="试听参考音色", type="filepath", interactive=False)

        gr.Markdown("---")
        gr.Markdown("#### 💾 保存克隆声音到音色库")
        gr.Markdown("*先用下方组件录制声音 → 填名称 → 选分类 → 点保存*")
        with gr.Row():
            v_record = gr.Microphone(label="🎙 录制克隆声音", type="filepath", scale=1)
            v_upload_clone = gr.Audio(label="📂 或上传文件", type="filepath", sources=["upload"], scale=1)
        with gr.Row():
            v_save_name = gr.Textbox(label="音频名称", placeholder="如: 温柔女声_001", scale=2)
            v_save_category = gr.Dropdown(label="分类", choices=[], value="未分类", interactive=True, scale=1,
                                          info="有分类时文件名自动前缀 {分类}_")
        with gr.Row():
            v_save_btn = gr.Button("💾 保存到音色库", variant="primary", scale=1)
        v_save_msg = gr.Textbox(label="")

        # O9：音色库浏览 / 搜索 / 分类（点选行→自动填 v_lib 并试听）
        gr.Markdown("---")
        gr.Markdown("#### 🔍 音色库浏览 / 搜索")
        with gr.Row():
            v_lib_search = gr.Textbox(label="搜索（名称 / 分类）", placeholder="如 温柔", scale=3)
            v_lib_category = gr.Dropdown(label="分类", choices=[], value=None, interactive=True, scale=1)
        v_lib_browser = gr.Dataframe(
            headers=["名称", "分类", "大小(KB)", "试听"],
            datatype=["str", "str", "str", "str"],
            interactive=False,
            label="音色库（点选行→自动填入上方下拉并试听）",
            wrap=True,
        )
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
