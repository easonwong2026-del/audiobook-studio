"""设置页面回调。

「高级：剧本导演校正」功能已按用户反馈移除，导演编辑相关回调随之删除。
本模块保留 v3.3.1 设置页面回调：Provider 配置、模型列表、连接测试、
密钥管理、数据目录与异常项目处理。此模块只做 Gradio 输入输出适配，
领域逻辑仍在 services。
"""
from __future__ import annotations

import html
import logging
import os

import gradio as gr

from lib import config
from services.ai_analysis_settings import (
    ANALYSIS_DEPTHS,
    ANALYSIS_PROTOCOL_VERSION,
    CORE_PROMPT,
    CORE_PROMPT_VERSION,
    AiAnalysisSettingsService,
)
from services.ai_settings import AiSettingsService

logger = logging.getLogger(__name__)


def _analysis_reasoning_choices(provider: str) -> list[tuple[str, str]]:
    normalized = str(provider or "custom").strip().lower()
    if normalized == "deepseek":
        return [("关闭", "off"), ("高", "high"), ("最大", "max")]
    if normalized == "openai":
        return [
            ("自动", "auto"),
            ("关闭", "off"),
            ("低", "low"),
            ("中", "medium"),
            ("高", "high"),
        ]
    return [("自动", "auto"), ("关闭", "off")]


def _analysis_provider_info(provider: str) -> str:
    values = AiSettingsService.get_effective_provider_config(str(provider or "local"))
    name = str(values.get("provider") or provider or "local")
    model = str(values.get("model") or AiSettingsService.get_default_model(name) or "未配置")
    base_url = str(values.get("base_url") or AiSettingsService.get_default_base_url(name) or "—")
    return f"Provider：**{html.escape(name)}** · 模型：`{html.escape(model)}` · Base URL：`{html.escape(base_url)}`"


def load_ai_analysis_settings(provider: str = "") -> tuple:
    selected = str(provider or AiSettingsService.get_effective_provider_config().get("provider", "local"))
    values = AiAnalysisSettingsService.for_provider(selected)
    modes = _analysis_reasoning_choices(selected)
    capability = values.get("capabilities") or {}
    mode_text = "、".join(label for label, _value in modes)
    return (
        _analysis_provider_info(selected),
        values.get("depth", "quick"),
        gr.update(choices=modes, value=values.get("reasoning_mode", "auto")),
        bool(values.get("auto_upgrade_max", True)),
        f"当前 Provider 支持：{html.escape(str(capability.get('label', '未注册')))}；可用模式：{mode_text}",
        CORE_PROMPT,
        values.get("prompt_supplement", ""),
        AiAnalysisSettingsService.prompt_preview(values.get("prompt_supplement", "")),
        f"核心提示词版本：{CORE_PROMPT_VERSION} · 协议：{ANALYSIS_PROTOCOL_VERSION} · 更新：随协议版本发布",
    )


def preview_ai_analysis_prompt(supplement: str) -> str:
    return AiAnalysisSettingsService.prompt_preview(supplement)


def save_ai_analysis_settings(
    provider: str,
    depth: str,
    reasoning_mode: str,
    auto_upgrade_max: bool,
    prompt_supplement: str,
) -> tuple:
    try:
        normalized = str(provider or "local").strip().lower()
        values = AiAnalysisSettingsService.get_config()
        values.update(
            {
                "depth": depth if depth in ANALYSIS_DEPTHS else "quick",
                "auto_upgrade_max": bool(auto_upgrade_max),
                "prompt_supplement": str(prompt_supplement or "").strip(),
            }
        )
        if normalized == "deepseek":
            values["deepseek_reasoning_mode"] = reasoning_mode
        elif normalized == "openai":
            values["openai_reasoning_mode"] = reasoning_mode
        AiAnalysisSettingsService.save_config(values)
        return (
            "✅ AI 剧本分析设置已保存；下次章节分析生效。",
            _analysis_provider_info(normalized),
            AiAnalysisSettingsService.prompt_preview(values["prompt_supplement"]),
        )
    except Exception as exc:  # noqa: BLE001 - user configuration feedback
        return (
            f"❌ 分析设置保存失败：{html.escape(str(exc)[:300])}",
            _analysis_provider_info(provider),
            AiAnalysisSettingsService.prompt_preview(prompt_supplement),
        )


def reset_ai_analysis_prompt() -> tuple:
    AiAnalysisSettingsService.save_config({"prompt_supplement": ""})
    return "", CORE_PROMPT, "✅ 已恢复默认自定义补充；核心提示词未修改。"


# ═══════════════════════════════════════════════════════════════
# v3.3.1：设置页面回调
# ═══════════════════════════════════════════════════════════════


def update_provider_config_fields(provider: str) -> tuple:
    """切换 Provider 时更新可见字段。"""
    provider = str(provider or "local")
    if provider == "local":
        return (
            "<p>本地离线基线无需配置 API Key。</p>",
            gr.update(
                choices=AiSettingsService.list_models("local"),
                value="",
            ),
            gr.update(visible=False, value=""),
            gr.update(visible=False, value=""),
            gr.update(visible=False, value="清除已保存密钥"),
        )
    default_base = AiSettingsService.get_default_base_url(provider)
    config_values = AiSettingsService.get_provider_config()
    model = (
        config_values.get(f"{provider}_model", "")
        or AiSettingsService.get_default_model(provider)
    )
    choices = list(dict.fromkeys([
        AiSettingsService.get_default_model(provider),
        model,
    ]))
    saved_base = config_values.get(f"{provider}_base_url", "")
    return (
        AiSettingsService.api_key_status(provider),
        gr.update(choices=[item for item in choices if item], value=model),
        gr.update(visible=True, value=""),
        gr.update(visible=True, value=saved_base or default_base),
        gr.update(visible=AiSettingsService.has_stored_api_key(provider), value="清除已保存密钥"),
    )


def load_ai_settings() -> tuple:
    """Load saved non-sensitive settings without returning the API key."""
    cfg = AiSettingsService.get_provider_config()
    provider = str(cfg.get("default_provider", "local"))
    model = (
        cfg.get(f"{provider}_model", "")
        or AiSettingsService.get_default_model(provider)
    )
    base = cfg.get(f"{provider}_base_url", "")
    timeout = cfg.get("timeout", 180)
    if provider == "local":
        status = "<p>本地离线基线无需配置密钥。</p>"
        return provider, model, gr.update(visible=False, value=""), timeout, status, gr.update(visible=False, value=""), gr.update(visible=False, value="清除已保存密钥")
    default_base = AiSettingsService.get_default_base_url(provider)
    return provider, model, gr.update(visible=True, value=base or default_base), timeout, AiSettingsService.api_key_status(provider), gr.update(visible=True, value=""), gr.update(visible=AiSettingsService.has_stored_api_key(provider), value="清除已保存密钥")


def save_ai_settings(provider, model, api_key, base_url, timeout) -> tuple:
    """保存 AI 配置和密钥。"""
    try:
        provider = str(provider or "local")
        config_values = AiSettingsService.get_provider_config()
        config_values["default_provider"] = provider
        if model and model.strip():
            config_values[f"{provider}_model"] = model.strip()
        elif f"{provider}_model" in config_values:
            del config_values[f"{provider}_model"]
        if base_url and base_url.strip():
            config_values[f"{provider}_base_url"] = base_url.strip()
        elif f"{provider}_base_url" in config_values:
            del config_values[f"{provider}_base_url"]
        config_values["timeout"] = int(timeout) if timeout else 180
        AiSettingsService.save_provider_config(config_values)

        # 保存密钥到 Keyring（非空时）
        if api_key and api_key.strip():
            try:
                AiSettingsService.set_api_key(provider, api_key.strip())
            except Exception as keyring_err:
                return (f"⚠ 配置已保存，但密钥保存失败：{html.escape(str(keyring_err))}", AiSettingsService.api_key_status(provider), gr.update(value=""), gr.update(visible=AiSettingsService.has_stored_api_key(provider), value="清除已保存密钥"))
        return (f"✅ **{provider.title()}** 配置已保存。", AiSettingsService.api_key_status(provider), gr.update(value=""), gr.update(visible=AiSettingsService.has_stored_api_key(provider), value="清除已保存密钥"))
    except Exception as exc:
        logger.exception("保存 AI 配置失败")
        return (f"❌ 保存失败：{html.escape(str(exc))}", AiSettingsService.api_key_status(provider), gr.update(value=""), gr.update(visible=AiSettingsService.has_stored_api_key(provider), value="清除已保存密钥"))


def test_ai_connection(provider: str, model: str = "", api_key: str = "", base_url: str = "", timeout: float = 30) -> str:
    """测试 Provider 连接。"""
    try:
        return AiSettingsService.check_connection(
            str(provider or "local"),
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            model=model,
        )
    except Exception as exc:
        logger.exception("测试 AI 连接失败")
        return f"❌ 连接测试异常：{html.escape(str(exc))}"


def clear_ai_api_key(provider: str) -> tuple:
    provider = str(provider or "local")
    try:
        AiSettingsService.delete_api_key(provider)
        return (AiSettingsService.api_key_status(provider), gr.update(value=""), gr.update(visible=False, value="清除已保存密钥"), "✅ 已清除系统密钥环中保存的 API Key。")
    except Exception:
        return (AiSettingsService.api_key_status(provider), gr.update(value=""), gr.update(visible=AiSettingsService.has_stored_api_key(provider), value="清除已保存密钥"), "❌ 清除 API Key 失败，请检查密钥环权限。")


def refresh_ai_models(
    provider: str,
    current_model: str = "",
    api_key: str = "",
    base_url: str = "",
    timeout: float = 30,
) -> tuple:
    """Refresh dropdown choices while preserving the current custom selection."""
    provider = str(provider or "local")
    current = str(current_model or "").strip()
    default = AiSettingsService.get_default_model(provider)
    try:
        models = AiSettingsService.list_models(
            provider,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        choices = list(dict.fromkeys([
            *([default] if default else []),
            *models,
            *([current] if current else []),
        ]))
        selected = current or default
        source = AiSettingsService.model_source(provider, selected, models)
        return (
            gr.update(choices=choices, value=selected),
            f"✅ 已读取 {len(models)} 个模型；当前选择不会因刷新被清空。",
            f"当前模型来源：{source}",
        )
    except Exception as exc:
        choices = [item for item in (default, current) if item]
        safe = html.escape(str(exc)[:200])
        return (
            gr.update(choices=list(dict.fromkeys(choices)), value=current or default),
            f"⚠ 模型列表读取失败，已保留当前选择：{safe}",
            f"当前模型来源：{AiSettingsService.model_source(provider, current or default)}",
        )


def restore_default_ai_model(provider: str) -> tuple:
    provider = str(provider or "local")
    default = AiSettingsService.get_default_model(provider)
    choices = [default] if default else AiSettingsService.list_models("local")
    return (
        gr.update(choices=choices, value=default),
        "当前模型来源：Provider 默认",
        "✅ 已恢复 Provider 默认模型；保存配置后生效。",
    )


def describe_ai_model_source(provider: str, model: str) -> str:
    return f"当前模型来源：{AiSettingsService.model_source(str(provider or 'local'), model)}"


def refresh_abnormal_projects() -> tuple:
    from datetime import datetime

    from repositories.project_repo import ProjectRepository

    inspections = ProjectRepository.list_abnormal_projects()
    rows = []
    for item in inspections:
        details = "、".join([*item.missing_files, *item.invalid_files])
        modified = (
            datetime.fromtimestamp(item.modified_at).strftime("%Y-%m-%d %H:%M:%S")
            if item.modified_at
            else ""
        )
        rows.append([item.name, item.status, item.path, details, modified])
    choices = [item.name for item in inspections]
    return (
        rows,
        gr.update(choices=choices, value=choices[0] if choices else None),
        f"共发现 {len(choices)} 个异常或残留项目目录。",
    )


def refresh_abnormal_project_data() -> tuple:
    rows, selection, _status = refresh_abnormal_projects()
    return rows, selection


def open_abnormal_project(project_name: str) -> str:
    from repositories.project_repo import ProjectRepository

    name = str(project_name or "").strip()
    if not name:
        return "⚠ 请先选择异常项目"
    inspection = ProjectRepository.inspect_project_slot(name)
    if inspection.status not in {"incomplete", "corrupted", "temporary"}:
        return "⚠ 该项目不属于可处理的工作区残留"
    try:
        import subprocess
        import sys

        if sys.platform == "win32":
            os.startfile(inspection.path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", inspection.path])
        else:
            subprocess.Popen(["xdg-open", inspection.path])
        return f"✅ 已打开：`{inspection.path}`"
    except Exception as exc:
        return f"❌ 打开目录失败：{html.escape(str(exc))}"


def archive_abnormal_project(project_name: str) -> str:
    from repositories.project_repo import ProjectRepository

    name = str(project_name or "").strip()
    if not name:
        return "⚠ 请先选择异常项目"
    try:
        target = ProjectRepository.archive_orphan_project(name)
        return f"✅ 已移动到回收站：`{target}`"
    except Exception as exc:
        return f"❌ 归档失败：{html.escape(str(exc))}"


def apply_data_dir(new_dir: str) -> tuple:
    """应用数据目录变更。"""
    if not new_dir or not new_dir.strip():
        return "⚠ 请填写保存位置", ""
    try:
        from services import ProjectService
        d = os.path.normpath(ProjectService.set_data_dir(new_dir.strip()))
        return f"✅ 数据目录已设置为：{d}（本会话立即生效）", d
    except Exception as e:
        return f"❌ 设置失败：{e}", ""


def open_data_dir() -> str:
    """打开数据目录。"""
    import sys
    d = config.get_data_dir()
    os.makedirs(d, exist_ok=True)
    try:
        import subprocess
        if sys.platform == "win32":
            os.startfile(d)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])
    except Exception as exc:
        return f"❌ 打开数据目录失败：{html.escape(str(exc))}"
    return f"✅ 已打开数据目录：`{d}`"


def shutdown_service() -> str:
    """Request shutdown of this owned Audiobook Studio instance only."""
    from services.service_lifecycle import ServiceLifecycle

    return ServiceLifecycle.request_shutdown()
