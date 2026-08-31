"""角色与声音阶段 UI builder — 角色绑定与音色管理。"""
from __future__ import annotations

import gradio as gr


def create_voice_page() -> dict:
    with gr.Group(visible=False, elem_id="grp-voices") as grp_voices:
        with gr.Row(equal_height=False, elem_classes=["voice-workspace"]):
            with gr.Column(scale=0, min_width=277, elem_classes=["role-list-panel"]):
                gr.Markdown("### 角色与声音")
                v_status = gr.Markdown("打开项目后显示角色绑定状态。")
                v_cast_finalize = gr.Button(
                    "确认并锁定全书声音方案",
                    variant="secondary",
                    size="sm",
                    visible=False,
                    interactive=False,
                )
                v_role_search = gr.Textbox(
                    label="搜索角色",
                    placeholder="按名称或描述搜索",
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

            with gr.Column(scale=1, min_width=0, elem_classes=["voice-config-panel"]):
                v_role_title = gr.Markdown("### 当前角色配置\n请从左侧角色列表选择角色。")
                with gr.Group(elem_classes=["binding-workspace"]):
                    gr.Markdown(
                        "<div class='voice-flow-steps'>"
                        "<span class='is-active'>① 选择声音</span>"
                        "<span>② 确认绑定</span>"
                        "</div>"
                    )
                    with gr.Row(equal_height=False, elem_classes=["voice-binding-layout"]), gr.Column(elem_classes=["voice-step-card", "voice-choice-card"]):
                            gr.Markdown("##### 选择声音")
                            gr.Markdown("先筛选音色分类，再选择、上传或录制参考音频。")
                            v_bind_category = gr.Dropdown(
                                label="音色分类",
                                choices=[],
                                value=None,
                                interactive=True,
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
                        v_current = gr.Markdown("当前参考音频：未选择")
                        v_bind = gr.Button("确认绑定", variant="primary", elem_classes=["voice-bind-action"])
                    v_bind_msg = gr.Markdown("")
                    v_reference_status = gr.Markdown("TTS 参考：未选择")
                    with gr.Row():
                        v_reference_preview_btn = gr.Button("试听参考", size="sm")
                        v_reference_regenerate_btn = gr.Button("重新生成", size="sm")
                    v_reference_audio = gr.Audio(
                        label="TTS 参考试听", type="filepath", interactive=False,
                    )

        v_role = gr.State(value=None)

        # 隐藏兼容组件
        v_preview_btn = gr.Button("试听当前声音", visible=False)
        v_preview_audio = gr.Audio(
            label="试听结果", type="filepath", interactive=False, visible=False,
        )

        with gr.Accordion("管理声音资产", open=False, elem_classes=["asset-accordion"]):
            with gr.Row():
                v_lib_search = gr.Textbox(label="搜索声音", placeholder="按名称或分类搜索", scale=3)
                v_lib_category = gr.Dropdown(label="分类", choices=[], value=None, interactive=True, scale=1)
            v_lib_browser = gr.Dataframe(
                headers=["名称", "分类", "大小(KB)", "试听"],
                datatype=["str", "str", "str", "str"],
                interactive=False,
                label="选择一行带回上方绑定区",
                wrap=True,
            )

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
            with gr.Row():
                v_library_check_btn = gr.Button("检查音色库", size="sm")
                v_library_batch_btn = gr.Button("批量生成缺失参考", size="sm")
            v_library_status = gr.Markdown("")

    return {
        "group": grp_voices,
        "v_status": v_status,
        "v_cast_finalize": v_cast_finalize,
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
        "v_reference_status": v_reference_status,
        "v_reference_preview_btn": v_reference_preview_btn,
        "v_reference_regenerate_btn": v_reference_regenerate_btn,
        "v_reference_audio": v_reference_audio,
        "v_preview_btn": v_preview_btn,
        "v_preview_audio": v_preview_audio,
        "v_record": v_record,
        "v_upload_clone": v_upload_clone,
        "v_save_name": v_save_name,
        "v_save_category": v_save_category,
        "v_save_btn": v_save_btn,
        "v_save_msg": v_save_msg,
        "v_library_check_btn": v_library_check_btn,
        "v_library_batch_btn": v_library_batch_btn,
        "v_library_status": v_library_status,
        "v_lib_search": v_lib_search,
        "v_lib_category": v_lib_category,
        "v_lib_browser": v_lib_browser,
    }
