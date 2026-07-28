"""AI 剧本导演面板。"""
from __future__ import annotations

import gradio as gr


def create_director_panel() -> dict:
    """创建 TXT → structured_script v3 的导演入口。"""
    with gr.Accordion(
        "AI 剧本导演 · TXT 智能预处理",
        open=True,
        elem_classes=["director-panel"],
    ):
        gr.Markdown(
            "上传原始小说，系统会设计角色、情绪、节奏、停顿与呼吸。"
            "分析完成后会自动回填到下方“新建项目”，但不会自动创建项目。"
        )
        with gr.Row(equal_height=True):
            d_txt = gr.File(
                label="原始小说",
                file_types=[".txt", ".docx", ".epub"],
                type="filepath",
                scale=3,
            )
            d_provider = gr.Dropdown(
                label="AI Provider",
                choices=[
                    ("本地离线基线", "local"),
                    ("OpenAI", "openai"),
                    ("DeepSeek", "deepseek"),
                ],
                value="local",
                scale=2,
            )
            d_model = gr.Textbox(
                label="模型（可选）",
                placeholder="留空使用 Provider 默认模型",
                scale=2,
            )
        with gr.Row():
            d_title = gr.Textbox(label="作品名（可选）", placeholder="默认使用 TXT 文件名")
            d_author = gr.Textbox(label="作者（可选）")
            d_analyze = gr.Button("开始导演分析", variant="primary")
        gr.Markdown(
            "OpenAI 读取 `OPENAI_API_KEY`；DeepSeek 读取 `DEEPSEEK_API_KEY`。"
            "密钥不会写入项目文件。"
        )
        d_status = gr.Markdown("")
        d_preview = gr.HTML(
            value="<div class='inline-empty'>完成分析后显示角色和片段预览。</div>"
        )
        d_output = gr.File(label="生成的 structured_script.json", interactive=False)
        gr.Markdown("#### 人工导演校正")
        gr.Markdown(
            "可直接修改角色、文本、情绪、速度、强度、呼吸和前后停顿。"
            "保存时会重新执行质量守卫；速度范围为 0.85–1.15，停顿单位为毫秒。"
        )
        d_edit_chapter = gr.Dropdown(
            label="编辑章节",
            choices=[],
            info="按章节加载 Segment，避免长篇小说一次传输整本表格。",
        )
        d_editor = gr.Dataframe(
            headers=[
                "id",
                "speaker",
                "text",
                "emotion",
                "speed",
                "intensity",
                "breath",
                "pause_before",
                "pause_after",
            ],
            datatype=[
                "str",
                "str",
                "str",
                "str",
                "number",
                "number",
                "str",
                "number",
                "number",
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
        gr.Markdown("#### 声音推荐与 AI 导演试听")
        gr.Markdown(
            "推荐只读取角色描述、表演状态和音色文件标签，不会自动绑定声音。"
            "试听会使用已保存 segment 的情绪、强度、速度和停顿设计；"
            "表格有修改时请先点击“保存人工调整”。"
        )
        with gr.Row():
            d_voice_role = gr.Dropdown(label="选择角色", choices=[])
            d_recommend = gr.Button("推荐声音", variant="secondary")
            d_voice = gr.Dropdown(label="试听声音", choices=[])
        d_recommendations = gr.Dataframe(
            headers=["voice_name", "category", "score", "reasons"],
            datatype=["str", "str", "number", "str"],
            row_count=(0, "dynamic"),
            col_count=(4, "fixed"),
            interactive=False,
            wrap=True,
            label="推荐结果",
        )
        with gr.Row():
            d_segment = gr.Dropdown(label="试听 Segment", choices=[], scale=4)
            d_audition = gr.Button("生成导演试听", variant="primary", scale=1)
        d_audition_audio = gr.Audio(label="导演试听", type="filepath")
        d_audition_status = gr.Markdown("")
        with gr.Row():
            d_feedback = gr.Dropdown(
                label="试听反馈",
                choices=[
                    ("语速太快 → 放慢", "slower"),
                    ("语速太慢 → 加快", "faster"),
                    ("表达太弱 → 加强", "stronger"),
                    ("表达太强 → 减弱", "softer"),
                    ("停顿太短 → 延长", "longer_pauses"),
                    ("停顿太长 → 缩短", "shorter_pauses"),
                    ("呼吸感不足 → 增加", "more_breath"),
                    ("呼吸感太重 → 减少", "less_breath"),
                ],
            )
            d_feedback_apply = gr.Button("应用反馈到当前 Segment", variant="secondary")

    return {
        "d_txt": d_txt,
        "d_provider": d_provider,
        "d_model": d_model,
        "d_title": d_title,
        "d_author": d_author,
        "d_analyze": d_analyze,
        "d_status": d_status,
        "d_preview": d_preview,
        "d_output": d_output,
        "d_edit_chapter": d_edit_chapter,
        "d_editor": d_editor,
        "d_apply": d_apply,
        "d_undo": d_undo,
        "d_history": d_history,
        "d_voice_role": d_voice_role,
        "d_recommend": d_recommend,
        "d_voice": d_voice,
        "d_recommendations": d_recommendations,
        "d_segment": d_segment,
        "d_audition": d_audition,
        "d_audition_audio": d_audition_audio,
        "d_audition_status": d_audition_status,
        "d_feedback": d_feedback,
        "d_feedback_apply": d_feedback_apply,
    }
