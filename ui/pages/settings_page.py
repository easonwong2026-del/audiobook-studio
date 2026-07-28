"""设置页面 — AI 模型、数据存储、系统信息。"""
from __future__ import annotations

import gradio as gr


def create_settings_page() -> dict:
    with gr.Group(visible=False, elem_id="grp-settings") as grp:
        gr.Markdown("### 设置")

        with gr.Tabs():
            with gr.Tab("AI 模型"):
                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown("##### 默认 Provider")
                    with gr.Row():
                        s_provider = gr.Dropdown(
                            label="Provider",
                            choices=[
                                ("本地离线基线", "local"),
                                ("OpenAI", "openai"),
                                ("DeepSeek", "deepseek"),
                            ],
                            value="local",
                            scale=2,
                        )
                        s_model = gr.Textbox(
                            label="默认模型（可选）",
                            placeholder="留空使用 Provider 默认模型",
                            scale=2,
                        )
                    s_provider_config = gr.HTML(value="<p>当前 Provider 无需配置密钥。</p>")

                    s_api_key = gr.Textbox(
                        label="API Key",
                        type="password",
                        placeholder="输入新密钥以替换已有密钥，留空保留已有密钥",
                        visible=False,
                    )
                    s_base_url = gr.Textbox(
                        label="Base URL（可选）",
                        placeholder="自定义 API 地址，留空使用默认地址",
                        visible=False,
                    )

                    s_timeout = gr.Slider(
                        label="请求超时（秒）",
                        minimum=30,
                        maximum=600,
                        value=180,
                        step=10,
                    )

                    with gr.Row():
                        s_save = gr.Button("保存配置", variant="primary")
                        s_test = gr.Button("测试当前配置", variant="secondary")
                        s_clear_key = gr.Button("清除已保存密钥", variant="stop", size="sm", visible=False)

                    s_status = gr.Markdown("")

            with gr.Tab("数据与存储"):
                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown("##### 数据保存位置")
                    from lib import config
                    s_data_dir = gr.Textbox(
                        label="数据目录",
                        value=config.get_data_dir(),
                    )
                    with gr.Row():
                        s_data_apply = gr.Button("应用", variant="primary")
                        s_data_open = gr.Button("打开数据文件夹")
                    s_data_msg = gr.Markdown("")

            with gr.Tab("系统信息"):
                with gr.Group(elem_classes=["settings-card"]):
                    from lib import __version__
                    s_version = gr.Markdown(f"**版本**：v{__version__}")
                    s_python = gr.Markdown("**Python**：3.10+")
                    s_status_info = gr.Markdown("")

    return {
        "group": grp,
        "s_provider": s_provider,
        "s_model": s_model,
        "s_provider_config": s_provider_config,
        "s_api_key": s_api_key,
        "s_base_url": s_base_url,
        "s_timeout": s_timeout,
        "s_save": s_save,
        "s_test": s_test,
        "s_clear_key": s_clear_key,
        "s_status": s_status,
        "s_data_dir": s_data_dir,
        "s_data_apply": s_data_apply,
        "s_data_open": s_data_open,
        "s_data_msg": s_data_msg,
        "s_version": s_version,
        "s_python": s_python,
        "s_status_info": s_status_info,
    }
