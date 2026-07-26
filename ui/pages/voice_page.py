"""角色与声音阶段 UI builder。"""
from __future__ import annotations

import gradio as gr


def create_voice_page() -> dict:
    """创建以角色绑定为主、声音资产管理为辅的页面。"""
    with gr.Group(visible=False, elem_id="grp-voices") as grp_voices:
        with gr.Row(equal_height=False, elem_classes=["voice-workspace"]):
            with gr.Column(scale=1, min_width=300, elem_classes=["role-list-panel"]):
                gr.Markdown("### 角色与声音")
                v_status = gr.Markdown("打开项目后显示角色绑定状态。")
                v_role_search = gr.Textbox(
                    label="搜索角色",
                    placeholder="按角色名称或描述搜索",
                    show_label=True,
                    elem_classes=["role-list-search"],
                )
                v_table = gr.Radio(
                    choices=[],
                    value=None,
                    label="",
                    show_label=False,
                    interactive=True,
                    elem_classes=["role-management-list"],
                )
                gr.Markdown("点击一行角色，在右侧加载当前配置。列表可滚动浏览大量角色。")

            with gr.Column(scale=2, min_width=420, elem_classes=["voice-config-panel"]):
                v_role_title = gr.Markdown("### 当前角色配置\n请从左侧角色列表选择角色。")
                with gr.Group(elem_classes=["binding-workspace"]):
                    gr.Markdown(
                        "<div class='voice-flow-steps'>"
                        "<span><b>①</b> 选择声音</span><span><b>②</b> 试听确认</span>"
                        "<span><b>③</b> 保存绑定</span>"
                        "</div>"
                    )
                    with gr.Column(elem_classes=["voice-config-steps"]):
                        with gr.Column(elem_classes=["voice-step-card", "voice-choice-card"]):
                            gr.Markdown("##### ① 选择声音")
                            gr.Markdown("先筛选音色分类，再选择、上传或录制参考音频。")
                            v_bind_category = gr.Dropdown(
                                label="音色分类", choices=[], value=None, interactive=True,
                                info="用于筛选下方音色列表",
                            )
                            v_lib = gr.Dropdown(
                                label="音色列表", choices=[], interactive=True,
                            )
                            v_audio = gr.Audio(
                                label="上传或录制参考音频",
                                type="filepath",
                                sources=["upload", "microphone"],
                                elem_classes=["voice-reference-upload"],
                            )
                        with gr.Row(equal_height=False, elem_classes=["voice-config-footer"]):
                            with gr.Column(elem_classes=["voice-step-card"]):
                                gr.Markdown("##### ② 试听确认")
                                gr.Markdown("试听当前选择的声音，确认音质和人设。")
                                v_preview_btn = gr.Button("试听当前声音", variant="secondary", size="sm")
                                v_preview_audio = gr.Audio(label="试听结果", type="filepath", interactive=False)
                            with gr.Column(elem_classes=["voice-step-card"]):
                                gr.Markdown("##### ③ 保存绑定")
                                gr.Markdown("确认无误后，保存到当前角色。")
                                v_bind = gr.Button("确认绑定", variant="primary")
                    v_bind_msg = gr.Markdown("")
                    v_current = gr.Markdown("当前参考音频：未选择")

        # 仅保存当前列表选中的角色，不提供第二个可见选择入口。
        v_role = gr.State(value=None)

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
        "v_role_search": v_role_search,
        "v_role_title": v_role_title,
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
