"""新建项目页面的 UI 回调。

处理从原始书稿创建项目和从 JSON 创建项目的 Gradio 事件。
不负责导演编辑、声音推荐和试听回调（保留在 director_handlers.py）。
"""
from __future__ import annotations

import html
import logging
import os

import gradio as gr

from services.ai_settings import AiSettingsService
from services.project_creation import ProjectCreationService

logger = logging.getLogger(__name__)


def _file_value_path(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("path") or value.get("name")
    return getattr(value, "path", None) or getattr(value, "name", None)


def _config_summary_text() -> str:
    """返回当前 AI 配置摘要的 Markdown 文本。"""
    try:
        config = AiSettingsService.get_provider_config()
        provider = config.get("default_provider", "local")
        model = config.get(f"{provider}_model", "")
        api_key = AiSettingsService.get_api_key(provider)
        key_status = "已配置" if api_key else "使用环境变量或本地分析"

        lines = [
            "##### 当前 AI 配置",
            f"- **Provider**：`{provider}`",
        ]
        if model:
            lines.append(f"- **模型**：`{model}`")
        is_local = provider == "local"
        if is_local:
            lines.append("- 本地离线基线无需密钥")
        else:
            lines.append(f"- **密钥状态**：{key_status}")
            lines.append("- 项目创建前建议先前往 **设置 → AI 模型** 测试连接和保存密钥")

        return "\n".join(lines)
    except Exception:
        return "##### 当前 AI 配置\n默认 Provider：**Local**（离线分析）"


def refresh_config_summary() -> str:
    return _config_summary_text()


def format_creation_warnings(warnings: list[str], limit: int = 10) -> str:
    """统一格式化创建质量提示，限制长篇项目的 UI 输出。"""
    if not warnings:
        return ""
    safe = [html.escape(str(item)) for item in warnings[:limit]]
    lines = [
        f"\n**质量检查**：共 {len(warnings)} 项 warning，不阻止创建\n",
        *(f"- {item}" for item in safe),
    ]
    hidden = len(warnings) - len(safe)
    if hidden:
        lines.append(f"\n另有 {hidden} 条未展示，可在项目管理或验收工具中查看。")
    return "\n".join(lines)


def create_from_source(
    project_name, source_file, title, author
) -> tuple:
    """从原始书稿创建项目的主入口。"""
    source = _file_value_path(source_file)
    name = (project_name or "").strip()

    if not name:
        return ("", "### ⚠ 请输入项目名称", "", gr.update())
    if not source or not os.path.isfile(source):
        return ("", "### ⚠ 请先上传小说文件", "", gr.update())

    try:
        result = ProjectCreationService.create_from_source(
            project_name=name,
            source_path=source,
            title=(title or "").strip() or None,
            author=(author or "").strip() or None,
        )

        msg = (
            f"### ✅ 项目创建成功\n\n"
            f"- **项目名称**：`{result.project_name}`\n"
            f"- **作品名**：{result.title}\n"
            f"- **章节数**：{result.chapter_count}\n"
            f"- **段落数**：{result.segment_count}\n"
            f"- **角色数**：{result.role_count}\n"
        )
        msg += format_creation_warnings(result.warnings)
        msg += "\n\n👉 **下一步**：进入「角色与声音」页面配置角色音色。"

        return (
            f"✅ 项目 `{result.project_name}` 已创建",
            msg,
            result.project_name,
            gr.update(choices=[], value=None),  # 通知顶部刷新
        )
    except ValueError as e:
        return ("", f"### ❌ 创建失败\n{html.escape(str(e))}", "", gr.update())
    except Exception as e:
        logger.exception("项目创建失败")
        return ("", f"### ❌ 创建异常\n{html.escape(str(e)[:500])}", "", gr.update())


def create_from_json(project_name, json_file) -> tuple:
    """从结构化 JSON 创建项目。"""
    source = _file_value_path(json_file)
    name = (project_name or "").strip()

    if not name:
        return ("", "### ⚠ 请输入项目名称", gr.update())
    if not source or not os.path.isfile(source):
        return ("", "### ⚠ 请上传 structured_script.json", gr.update())

    try:
        result = ProjectCreationService.create_from_structured_script(
            project_name=name,
            script_path=source,
        )

        msg = (
            f"### ✅ 项目创建成功\n\n"
            f"- **项目名称**：`{result.project_name}`\n"
            f"- **作品名**：{result.title}\n"
            f"- **章节数**：{result.chapter_count}\n"
            f"- **段落数**：{result.segment_count}\n"
            f"- **角色数**：{result.role_count}\n"
        )
        msg += format_creation_warnings(result.warnings)
        msg += "\n\n👉 **下一步**：进入「角色与声音」页面配置角色音色。"

        return (
            msg,
            result.project_name,
            gr.update(choices=[], value=None),
        )
    except ValueError as e:
        return (f"### ❌ 创建失败\n{html.escape(str(e))}", "", gr.update())
    except Exception as e:
        logger.exception("JSON 创建失败")
        return (f"### ❌ 创建异常\n{html.escape(str(e)[:500])}", "", gr.update())
