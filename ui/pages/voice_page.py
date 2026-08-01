"""角色与声音阶段 UI builder — 角色绑定、音色管理与 AI 声音推荐。"""
from __future__ import annotations

import gradio as gr


def create_voice_page() -> dict:
    with gr.Group(visible=False, elem_id="grp-voices") as grp_voices:
        with gr.Row(equal_height=False, elem_classes=["voice-workspace"]):
            with gr.Column(scale=0, min_width=277, elem_classes=["role-list-panel"]):
                gr.Markdown("### 角色与声音")
                v_status = gr.Markdown("打开项目后显示角色绑定状态。")
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
                    with gr.Row(equal_height=False, elem_classes=["voice-binding-layout"]):
                        with gr.Column(elem_classes=["voice-step-card", "voice-choice-card"]):
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

                            # AI 声音建议
                            with gr.Accordion("AI 声音建议", open=False):
                                v_recommend = gr.Button("刷新推荐", variant="secondary", size="sm")
                                v_recommendations = gr.Dataframe(
                                    headers=["voice_name", "category", "score", "reasons"],
                                    datatype=["str", "str", "number", "str"],
                                    row_count=(0, "dynamic"),
                                    col_count=(4, "fixed"),
                                    interactive=False,
                                    wrap=True,
                                    label="推荐候选",
                                )
                                v_recommend_status = gr.Markdown("选择角色后点击「刷新推荐」")

                    with gr.Row(equal_height=False, elem_classes=["voice-config-footer"]):
                        v_current = gr.Markdown("当前参考音频：未选择")
                        v_bind = gr.Button("确认绑定", variant="primary", elem_classes=["voice-bind-action"])
                    v_bind_msg = gr.Markdown("")

        v_role = gr.State(value=None)

        # ── V4 项目专用：AI 角色识别 / 人工指派 / 合并 / 锁定 / 别名 ──
        with gr.Group(visible=False, elem_id="v4-role-manager") as v4_role_grp:
            with gr.Accordion(
                "V4 角色管理：AI 识别 / 人工指派 / 合并 / 锁定 / 别名",
                open=True,
                elem_classes=["v4-role-manager-accordion"],
            ):
                gr.Markdown(
                    "V4 项目使用稳定角色 ID。未确认片段可先由 AI 自动识别，"
                    "再人工指派、合并（旧角色保留为别名）、锁定或修改别名。"
                )
                with gr.Row():
                    v4_unresolved_table = gr.Dataframe(
                        headers=["segment_id", "chapter", "text"],
                        datatype=["str", "str", "str"],
                        row_count=(0, "dynamic"),
                        col_count=(3, "fixed"),
                        interactive=False,
                        wrap=True,
                        label="待确认片段（unresolved）",
                    )
                with gr.Row():
                    v4_route_btn = gr.Button("🤖 AI 自动识别角色", variant="primary", size="sm")
                    v4_route_msg = gr.Markdown("")
                with gr.Row():
                    v4_assign_segs = gr.Textbox(
                        label="要指派的片段 ID（逗号分隔）", scale=2,
                        placeholder="例如：segment_000012, segment_000013",
                    )
                    v4_assign_speaker = gr.Dropdown(
                        label="指定现有角色", choices=[], scale=1,
                    )
                    v4_assign_new = gr.Textbox(
                        label="或新建角色名", scale=1,
                    )
                    v4_assign_lock = gr.Checkbox(label="锁定该角色", value=False, scale=1)
                with gr.Row():
                    v4_assign_btn = gr.Button("指派片段", variant="secondary", size="sm")
                    v4_assign_msg = gr.Markdown("")
                with gr.Row():
                    v4_merge_source = gr.Dropdown(label="合并来源角色", choices=[], scale=1)
                    v4_merge_target = gr.Dropdown(label="合并到角色", choices=[], scale=1)
                    v4_merge_btn = gr.Button("合并角色", variant="secondary", size="sm")
                    v4_merge_msg = gr.Markdown("")
                with gr.Row():
                    v4_lock_btn = gr.Button("🔒 切换锁定当前角色", size="sm")
                    v4_alias = gr.Textbox(label="修改别名（逗号分隔，保存即生效）", scale=2)
                    v4_alias_btn = gr.Button("保存别名", size="sm")
                    v4_lock_alias_msg = gr.Markdown("")

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

    return {
        "group": grp_voices,
        "v_status": v_status,
        "v_table": v_table,
        "v_role_search": v_role_search,
        "v_role_title": v_role_title,
        "v_bind_category": v_bind_category,
        "v_audio": v_audio,
        "v_role": v_role,
        "v4_role_grp": v4_role_grp,
        "v4_unresolved_table": v4_unresolved_table,
        "v4_route_btn": v4_route_btn,
        "v4_route_msg": v4_route_msg,
        "v4_assign_segs": v4_assign_segs,
        "v4_assign_speaker": v4_assign_speaker,
        "v4_assign_new": v4_assign_new,
        "v4_assign_lock": v4_assign_lock,
        "v4_assign_btn": v4_assign_btn,
        "v4_assign_msg": v4_assign_msg,
        "v4_merge_source": v4_merge_source,
        "v4_merge_target": v4_merge_target,
        "v4_merge_btn": v4_merge_btn,
        "v4_merge_msg": v4_merge_msg,
        "v4_lock_btn": v4_lock_btn,
        "v4_alias": v4_alias,
        "v4_alias_btn": v4_alias_btn,
        "v4_lock_alias_msg": v4_lock_alias_msg,
        "v_lib": v_lib,
        "v_current": v_current,
        "v_bind": v_bind,
        "v_bind_msg": v_bind_msg,
        "v_preview_btn": v_preview_btn,
        "v_preview_audio": v_preview_audio,
        "v_recommend": v_recommend,
        "v_recommendations": v_recommendations,
        "v_recommend_status": v_recommend_status,
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
