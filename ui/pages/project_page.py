"""项目管理阶段 UI builder — 项目切换、查看与高级剧本校正。"""
from __future__ import annotations

import gradio as gr

from services import ProjectService


def create_project_page() -> dict:
    """创建项目管理页面（不含新建项目入口）。"""
    with gr.Group(visible=False, elem_id="grp-project") as grp_project:
        with gr.Row(equal_height=True, elem_classes=["stage-row"]):
            with gr.Column(scale=1, elem_classes=["stage-card"]):
                gr.Markdown("#### 选择项目")
                with gr.Row():
                    p_sel = gr.Dropdown(
                        label="项目",
                        choices=ProjectService.scan_projects(),
                        scale=4,
                    )
                    p_refresh = gr.Button("刷新", size="sm", scale=1)
                with gr.Row():
                    p_open = gr.Button("打开项目", variant="primary")
                    p_del = gr.Button("删除项目", variant="stop", size="sm")
                p_open_msg = gr.Markdown("")

        gr.Markdown("#### 书稿结构")
        p_summary = gr.Markdown("打开项目后显示书名、角色与合成概览。")
        p_chapter_tree = gr.HTML(value="<div class='inline-empty'>打开项目后在这里查看章节结构。</div>")

        # 高级：剧本导演校正（默认折叠）
        with gr.Accordion("高级：剧本导演校正", open=False, elem_classes=["director-editor-accordion"]):
            gr.Markdown(
                "按章节加载 Segment，可直接修改角色、文本、情绪、速度、强度、"
                "呼吸和前后停顿。保存时重新执行质量守卫。"
            )
            d_edit_chapter = gr.Dropdown(
                label="编辑章节",
                choices=[],
                info="按章节加载 Segment，避免长篇小说一次传输整本表格。",
            )
            d_editor = gr.Dataframe(
                headers=[
                    "id", "speaker", "text", "emotion",
                    "speed", "intensity", "breath",
                    "pause_before", "pause_after",
                ],
                datatype=[
                    "str", "str", "str", "str",
                    "number", "number", "str",
                    "number", "number",
                ],
                row_count=(0, "dynamic"),
                col_count=(9, "fixed"),
                interactive=True,
                wrap=True,
                label="Segment 导演表",
            )
            with gr.Row():
                d_apply = gr.Button("保存人工调整", variant="primary")
                d_undo = gr.Button("撤销上次保存", variant="secondary")
            d_history = gr.State("")

    return {
        "group": grp_project,
        "p_sel": p_sel,
        "p_refresh": p_refresh,
        "p_open": p_open,
        "p_del": p_del,
        "p_open_msg": p_open_msg,
        "p_summary": p_summary,
        "p_chapter_tree": p_chapter_tree,
        "d_edit_chapter": d_edit_chapter,
        "d_editor": d_editor,
        "d_apply": d_apply,
        "d_undo": d_undo,
        "d_history": d_history,
    }
