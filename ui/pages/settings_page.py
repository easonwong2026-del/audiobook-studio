"""设置页面 — AI 模型、数据存储、系统信息。"""
from __future__ import annotations

import gradio as gr


def create_settings_page() -> dict:
    with gr.Group(
        visible=False,
        elem_id="grp-settings",
        elem_classes=["settings-page"] + ["page-shell"],
    ) as grp:
        gr.Markdown("### 设置")

        with gr.Tabs(elem_classes=["settings-tabs"]):
            with gr.Tab("AI 模型"):
                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown("##### 默认 Provider")
                    with gr.Row(elem_classes=["settings-provider-row"]):
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
                        s_model = gr.Dropdown(
                            label="模型",
                            choices=[],
                            allow_custom_value=True,
                            info="可从账户模型列表选择，也可输入兼容代理的自定义模型 ID",
                            scale=2,
                        )
                    with gr.Row(elem_classes=["settings-model-actions"]):
                        s_models_refresh = gr.Button("刷新模型列表", size="sm")
                        s_model_default = gr.Button("恢复 Provider 默认模型", size="sm")
                    s_model_source = gr.Markdown("当前模型来源：Provider 默认")
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

                    with gr.Row(elem_classes=["settings-actions"]):
                        s_save = gr.Button("保存配置", variant="primary")
                        s_test = gr.Button("测试当前配置", variant="secondary")
                        s_clear_key = gr.Button("清除已保存密钥", variant="stop", size="sm", visible=False)

                    s_status = gr.Markdown("")

            with gr.Tab("AI 剧本分析"):
                with gr.Group(elem_classes=["settings-card", "analysis-settings-card"]):
                    gr.Markdown(
                        "##### 章节分析链路\n"
                        "这里的配置独立于 Provider/API Key。当前章节先做结构分析；"
                        "标准/深度模式再进入演绎导演阶段。"
                    )
                    s_analysis_provider_info = gr.Markdown(
                        "Provider：本地离线基线 · 模型：未配置 · Base URL：—"
                    )
                    with gr.Row(elem_classes=["settings-provider-row"]):
                        s_analysis_depth = gr.Dropdown(
                            label="分析深度",
                            choices=[
                                ("快速：仅章节结构", "quick"),
                                ("标准：结构 + 演绎导演", "standard"),
                                ("深度：结构 + 演绎导演 + 安全复查", "deep"),
                            ],
                            value="quick",
                        )
                        s_analysis_reasoning = gr.Dropdown(
                            label="推理模式",
                            choices=[
                                ("关闭", "off"),
                                ("高", "high"),
                                ("最大", "max"),
                            ],
                            value="high",
                            info="DeepSeek 使用 Off / High / Max；不根据模型名称猜测。",
                        )
                    s_analysis_auto_upgrade = gr.Checkbox(
                        label="复杂修复/深度复查自动升级到最大推理",
                        value=True,
                    )
                    s_analysis_capability = gr.Markdown("")
                    gr.Markdown(
                        "##### 内置核心提示词（只读）\n"
                        "核心协议由版本控制；自定义补充只会追加在核心提示词之后。"
                    )
                    s_analysis_prompt_version = gr.Markdown(
                        "核心提示词版本：chapter-analysis-core-v1 · 协议：chapter-analysis-protocol-v1 · 更新：随协议版本发布"
                    )
                    s_analysis_prompt_core = gr.Textbox(
                        label="核心提示词",
                        value="",
                        lines=5,
                        interactive=False,
                        show_copy_button=True,
                    )
                    s_analysis_prompt_supplement = gr.Textbox(
                        label="自定义补充（可选）",
                        placeholder="只填写希望追加的偏好，不要重写核心协议。",
                        lines=5,
                        show_copy_button=True,
                    )
                    s_analysis_prompt_preview = gr.Textbox(
                        label="最终提示词预览",
                        value="",
                        lines=10,
                        interactive=False,
                        show_copy_button=True,
                    )
                    with gr.Row(elem_classes=["settings-actions"]):
                        s_analysis_save = gr.Button("保存分析设置", variant="primary")
                        s_analysis_reset = gr.Button("恢复默认补充", variant="secondary")
                    s_analysis_status = gr.Markdown("")

            with gr.Tab("数据与存储"):
                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown("##### 数据保存位置")
                    from lib import config
                    s_data_dir = gr.Textbox(
                        label="数据目录",
                        value=config.get_data_dir(),
                    )
                    with gr.Row(elem_classes=["settings-data-actions"]):
                        s_data_apply = gr.Button("应用", variant="primary")
                        s_data_open = gr.Button("打开数据文件夹")
                    s_data_msg = gr.Markdown("")
                with gr.Group(elem_classes=["settings-card"]):
                    gr.Markdown("##### 异常与残留项目")
                    gr.Markdown(
                        "这里只列出不完整、损坏或临时目录。归档会移动到数据目录的 "
                        "`.trash/projects`，不会永久删除。"
                    )
                    s_orphan_table = gr.Dataframe(
                        headers=["项目名称", "状态", "路径", "缺失/损坏文件", "最后修改时间"],
                        datatype=["str", "str", "str", "str", "str"],
                        interactive=False,
                        wrap=True,
                    )
                    s_orphan_name = gr.Dropdown(
                        label="选择要处理的异常项目",
                        choices=[],
                    )
                    with gr.Row():
                        s_orphan_refresh = gr.Button("刷新")
                        s_orphan_open = gr.Button("打开目录")
                        s_orphan_archive = gr.Button("移动到回收站", variant="stop")
                    s_orphan_status = gr.Markdown("")

            with gr.Tab("系统信息"):
                with gr.Group(elem_classes=["settings-card"]):
                    from lib import __version__
                    s_version = gr.Markdown(f"**版本**：v{__version__}")
                    import platform
                    s_python = gr.Markdown(f"**Python**：{platform.python_version()}")
                    s_status_info = gr.Markdown("")
                    gr.Markdown("##### 环境诊断")
                    gr.Markdown(
                        "只读取环境状态，不安装 CUDA、Torch、模型，也不会执行 GPU 推理。"
                    )
                    s_diagnostics_run = gr.Button("运行环境诊断", variant="primary")
                    s_diagnostics_status = gr.Markdown("")
                    s_diagnostics_table = gr.Dataframe(
                        headers=["检查项", "状态", "结果", "修复建议"],
                        datatype=["str", "str", "str", "str"],
                        interactive=False,
                        wrap=True,
                        elem_classes=["diagnostics-table"],
                    )
                    s_diagnostics_report = gr.Textbox(
                        label="可复制诊断报告（Markdown）",
                        lines=12,
                        show_copy_button=True,
                        interactive=False,
                        elem_classes=["diagnostics-report"],
                    )
                    gr.Markdown("##### 服务管理")
                    gr.Markdown(
                        "关闭服务会停止当前 AI 分析和音频任务，保存可恢复状态，释放端口，"
                        "并卸载 TTS/CUDA 资源。不会影响其他 Python 或 WorkBuddy 进程。"
                    )
                    with gr.Row():
                        s_shutdown = gr.Button("关闭服务", variant="stop")
                        s_shutdown_status = gr.Markdown("")

    return {
        "group": grp,
        "s_provider": s_provider,
        "s_model": s_model,
        "s_models_refresh": s_models_refresh,
        "s_model_default": s_model_default,
        "s_model_source": s_model_source,
        "s_provider_config": s_provider_config,
        "s_api_key": s_api_key,
        "s_base_url": s_base_url,
        "s_timeout": s_timeout,
        "s_save": s_save,
        "s_test": s_test,
        "s_clear_key": s_clear_key,
        "s_status": s_status,
        "s_analysis_provider_info": s_analysis_provider_info,
        "s_analysis_depth": s_analysis_depth,
        "s_analysis_reasoning": s_analysis_reasoning,
        "s_analysis_auto_upgrade": s_analysis_auto_upgrade,
        "s_analysis_capability": s_analysis_capability,
        "s_analysis_prompt_version": s_analysis_prompt_version,
        "s_analysis_prompt_core": s_analysis_prompt_core,
        "s_analysis_prompt_supplement": s_analysis_prompt_supplement,
        "s_analysis_prompt_preview": s_analysis_prompt_preview,
        "s_analysis_save": s_analysis_save,
        "s_analysis_reset": s_analysis_reset,
        "s_analysis_status": s_analysis_status,
        "s_data_dir": s_data_dir,
        "s_data_apply": s_data_apply,
        "s_data_open": s_data_open,
        "s_data_msg": s_data_msg,
        "s_orphan_table": s_orphan_table,
        "s_orphan_name": s_orphan_name,
        "s_orphan_refresh": s_orphan_refresh,
        "s_orphan_open": s_orphan_open,
        "s_orphan_archive": s_orphan_archive,
        "s_orphan_status": s_orphan_status,
        "s_version": s_version,
        "s_python": s_python,
        "s_status_info": s_status_info,
        "s_diagnostics_run": s_diagnostics_run,
        "s_diagnostics_status": s_diagnostics_status,
        "s_diagnostics_table": s_diagnostics_table,
        "s_diagnostics_report": s_diagnostics_report,
        "s_shutdown": s_shutdown,
        "s_shutdown_status": s_shutdown_status,
    }
