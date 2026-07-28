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
                "AI 导演分析后的精细调整工具。逐章查看每个说话单元（Segment），"
                "手动修改角色分配、情绪标注、语速、表达强度、呼吸感和前后停顿。"
                "\n\n**适用场景**：AI 自动分析后个别段情绪不准、语速不合适、"
                "停顿太长或太短。非必选步骤——AI 分析结果可直接用于后续生产。"
                "\n\n**操作**：选用章节 → 在表格中修改 → 点「保存人工调整」。"
                "每次保存会生成撤销快照，可用「撤销上次保存」恢复。"
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
