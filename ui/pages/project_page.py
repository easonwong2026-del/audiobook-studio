"""项目管理页 UI builder。"""
from __future__ import annotations
import gradio as gr
from services import ProjectService
from lib import config


def create_project_page() -> dict:
    """创建项目管理页组件。

    Returns:
        组件引用字典：所有项目页组件的引用。
    """
    with gr.Group(visible=False, elem_id="grp-project") as grp_project:
        gr.Markdown("> ℹ️ **使用流程**: 上传书稿（txt/docx）给 WorkBuddy → 我分析后输出命名好的 JSON → 在此页上传 JSON 创建项目 → 切换到「音色配置」绑定角色音频 → 合成 → 导出")
        with gr.Row(equal_height=True):
            with gr.Column(scale=1):
                gr.Markdown("#### ✨ 新建项目")
                p_name = gr.Textbox(label="项目名称", placeholder="如: 甲方来了")
                p_script = gr.File(label="上传 structured_script.json", file_types=[".json"])
                with gr.Row():
                    p_create = gr.Button("创建项目", variant="primary")
                p_create_msg = gr.Markdown("")

            with gr.Column(scale=1):
                gr.Markdown("#### 📂 打开已有项目")
                with gr.Row():
                    p_sel = gr.Dropdown(label="已有项目", choices=ProjectService.scan_projects(), scale=4)
                    p_refresh = gr.Button("🔄", size="sm", scale=1)
                with gr.Row():
                    p_open = gr.Button("打开项目", variant="primary")
                    p_del = gr.Button("删除", variant="stop", size="sm")
                p_open_msg = gr.Markdown("")

        p_summary = gr.Markdown("")

        # O4：章节树（打开项目后显示；书架入口统一在概览页）
        gr.Markdown("#### 🌲 章节树")
        p_chapter_tree = gr.HTML(value="*打开项目后显示章节树*")

        # 数据保存位置（项目 / 产物外置，用户可自选）
        gr.Markdown("---")
        gr.Markdown("#### 💾 数据保存位置")
        gr.Markdown(
            "> 项目与合成产物默认保存在用户目录下的 `AudiobookStudio/`，与程序目录分离。"
            "你可在此更改保存位置：**立即对本会话生效**；重启工作台后对所有项目生效。"
            "旧版存放在程序目录内的历史项目会自动在上方「打开已有项目」中列出。"
        )
        data_dir_box = gr.Textbox(
            label="数据保存位置",
            value=config.get_data_dir(),
            placeholder="如: D:\\AudiobookStudio",
        )
        with gr.Row():
            data_apply = gr.Button("📁 应用保存位置", variant="primary")
            data_open = gr.Button("📂 打开数据文件夹")
        data_dir_msg = gr.Markdown("")
    return {
        "group": grp_project,
        "p_name": p_name,
        "p_script": p_script,
        "p_create": p_create,
        "p_create_msg": p_create_msg,
        "p_sel": p_sel,
        "p_refresh": p_refresh,
        "p_open": p_open,
        "p_del": p_del,
        "p_open_msg": p_open_msg,
        "p_summary": p_summary,
        "p_chapter_tree": p_chapter_tree,
        "data_dir_box": data_dir_box,
        "data_apply": data_apply,
        "data_open": data_open,
        "data_dir_msg": data_dir_msg,
    }
