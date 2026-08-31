"""Low-risk Voice Asset UI callbacks.

This module owns role-list presentation, voice-library browsing, category
filters, and the save/preview adapters used by the Voice page.  Voice Cast,
runtime audition, and production/utility choice refreshes intentionally stay
in ``app.py`` because they cross higher-level workflow boundaries.
"""
from __future__ import annotations

import os

import gradio as gr

from lib import config, voice_lib
from lib import dataframe_style as df_style
from services.project import ProjectService
from services.voice_assets import VoiceAssetError, VoiceAssetService
from ui.components import format_role_config_title
from ui.components.voice_binding import build_role_management_choices


def _snapshot(ss):
    """Read the current session snapshot without importing ``app``."""
    ensure_snapshot = getattr(ss, "ensure_snapshot", None)
    if not callable(ensure_snapshot):
        return None
    snapshot = ensure_snapshot()
    if snapshot is not None:
        return snapshot
    project = getattr(ss, "project", None)
    apply_snapshot = getattr(ss, "apply_project_snapshot", None)
    if project and callable(apply_snapshot):
        rebuilt = ProjectService.open_project_as_snapshot(project)
        return apply_snapshot(rebuilt, project=project)
    return None


def refresh_role_list(search, current_role, ss):
    """按搜索词刷新角色管理列表，同时保留仍可见的当前角色。"""
    if not ss or not ss.project:
        return gr.update(choices=[], value=None)
    snapshot = _snapshot(ss)
    if not snapshot:
        return gr.update(choices=[], value=None)
    choices = build_role_management_choices(snapshot.script, snapshot.bindings, search)
    selected = current_role if any(value == current_role for _, value in choices) else None
    return gr.update(choices=choices, value=selected)


def select_role_from_list(role, ss):
    """选择角色列表项后加载该角色的绑定状态和右侧配置。"""
    empty = (
        gr.update(), gr.update(), gr.update(), gr.update(),
        gr.update(), gr.update(), gr.update(),
    )
    if not ss or not ss.project or not role:
        return empty
    role = str(role)
    snapshot = _snapshot(ss)
    if not snapshot or role not in (snapshot.script.get("voices", {}) or {}):
        return empty
    binding = snapshot.bindings.get(role)
    voice = snapshot.script.get("voices", {}).get(role, {})
    current = f"当前绑定音频：{os.path.basename(binding)}" if binding else "当前绑定音频：未选择"
    return (
        role,
        format_role_config_title(role, voice, binding),
        gr.update(value=binding),
        gr.update(value=None),
        f"*{current}*",
        None,
        "",
    )


def _library_path(name):
    root = config.get_voice_library()
    return os.path.join(root, name) if name else None


def play_lib_voice(choice):
    path = _library_path(choice) if choice else None
    return path if path and os.path.isfile(path) else None


def _save_category_choices(cats: list[str] | None) -> list[str]:
    """构建保存分类 choices，始终保留合法的“未分类”与新建占位。"""
    base = [str(category) for category in (cats or []) if str(category)]
    if "未分类" not in base:
        base.insert(0, "未分类")
    return base + ["— 新建 —"]


def save_to_lib(recorded, uploaded, name, category, ss):
    """保存到音色库并刷新 Voice Asset 页面依赖的下拉选项。"""
    try:
        dest = ProjectService.save_to_lib(recorded, uploaded, name, category=category or "")
    except ValueError as exc:
        return str(exc), gr.update(), gr.update(), gr.update()
    cats = voice_lib.list_categories()
    return (
        f"已保存至音色库: {os.path.basename(dest)}",
        gr.update(choices=voice_lib.voice_names()),
        gr.update(choices=voice_lib.voice_names()),
        gr.update(choices=_save_category_choices(cats), value=category or "未分类"),
    )


def filter_vlib_by_category(category):
    """按分类筛选绑定区音色列表。"""
    return gr.update(choices=voice_lib.voice_names(category or None), value=None)


def refresh_voice_lib(search, category):
    """刷新音色库浏览器（Dataframe 行 + 分类下拉选项）。"""
    voices = voice_lib.scan_voice_library(search=search or "", category=category)
    rows = [[voice["name"], voice["category"], voice["size_kb"], voice["path"]] for voice in voices]
    cats = voice_lib.list_categories()
    return (
        df_style.style_dataframe(rows, df_style.VOICE_HEADERS, status_col=None),
        gr.update(choices=cats, value=category),
    )


def select_voice_from_browser(rows, evt: gr.SelectData):
    """点选音色库某行，回填下拉并提供真实存在的试听路径。"""
    if evt is None or evt.index is None:
        return gr.update(), None
    try:
        rows = rows["data"] if isinstance(rows, dict) else rows
        name = rows[evt.index[0]][0]
    except (AttributeError, IndexError, KeyError, TypeError):
        return gr.update(), None
    path = _library_path(name)
    return gr.update(value=name), (path if path and os.path.isfile(path) else None)


def refresh_categories():
    """刷新绑定/保存分类下拉。"""
    cats = voice_lib.list_categories()
    return (
        gr.update(choices=cats or ["未分类"]),
        gr.update(choices=_save_category_choices(cats), value="未分类"),
    )


def refresh_voice_filters():
    """一次扫描结果刷新绑定筛选、资产筛选和新声音分类。"""
    cats = voice_lib.list_categories()
    filter_choices = cats or ["未分类"]
    save_choices = _save_category_choices(cats)
    return (
        gr.update(choices=filter_choices, value=None),
        gr.update(choices=filter_choices, value=None),
        gr.update(choices=save_choices, value="未分类"),
    )


def _selected_voice_path(role, ss) -> str | None:
    if not role or not ss or not getattr(ss, "project", None):
        return None
    snapshot = _snapshot(ss)
    if not snapshot:
        return None
    path = str((snapshot.bindings or {}).get(str(role)) or "").strip()
    if not path:
        return None
    if os.path.isabs(path):
        return path
    try:
        from lib import project_paths

        return project_paths.resolve_relative(snapshot.project_dir, path)
    except ValueError:
        return os.path.join(snapshot.project_dir, path)


def _reference_status_text(status: dict) -> str:
    labels = {
        "ready": "✅ Reference ready",
        "needs_reference": "⚠ 需要生成参考音频",
        "manual_required": "❌ 需要人工提供参考",
        "error": "❌ 参考音频处理失败",
    }
    original = status.get("original_audio") or "未选择"
    original_duration = status.get("original_duration")
    reference = status.get("reference_audio") or "待生成"
    reference_duration = status.get("reference_duration")
    source_line = f"原始声音：{original}\n原始时长：{float(original_duration):.1f} 秒" if original_duration is not None else f"原始声音：{original}\n原始时长：读取失败"
    reference_line = f"TTS 参考：{reference}\n参考时长：{float(reference_duration):.1f} 秒" if reference_duration is not None else f"TTS 参考：{reference}\n参考时长：待生成"
    return f"{source_line}\n\n{reference_line}\n\n状态：{labels.get(status.get('reference_status'), '⚠ 状态未知')}"


def refresh_reference_status(role, ss):
    source = _selected_voice_path(role, ss)
    if not source or not os.path.isfile(source):
        return "TTS 参考：未选择", None
    try:
        status = VoiceAssetService.status_for_path(source)
    except VoiceAssetError as exc:
        return f"❌ 参考音频不可用：{exc}", None
    return _reference_status_text(status), None


def preview_reference(role, ss):
    source = _selected_voice_path(role, ss)
    if not source or not os.path.isfile(source):
        return None
    try:
        reference = VoiceAssetService.resolve_tts_reference(source_path=source)
    except VoiceAssetError:
        return None
    return reference if os.path.isfile(reference) else None


def regenerate_reference(role, ss):
    source = _selected_voice_path(role, ss)
    if not source or not os.path.isfile(source):
        return "请先选择或绑定原始声音。", None
    try:
        VoiceAssetService.ensure_reference(source_path=source, force=True)
    except VoiceAssetError as exc:
        try:
            status = VoiceAssetService.status_for_path(source)
            return f"{_reference_status_text(status)}\n\n❌ {exc.code}：{exc}", None
        except VoiceAssetError:
            return f"❌ {exc.code}：{exc}", None
    status = VoiceAssetService.status_for_path(source)
    reference = status.get("_reference_path")
    return _reference_status_text(status), reference if reference and os.path.isfile(reference) else None


def _library_check_text(result: dict) -> str:
    return (
        f"总资产：{result.get('total', 0)} · "
        f"ready：{result.get('ready', 0)} · "
        f"needs_reference：{result.get('needs_reference', 0)} · "
        f"manual_required：{result.get('manual_required', 0)} · "
        f"error：{result.get('error', 0)}"
    )


def check_voice_library():
    return _library_check_text(VoiceAssetService.check_library())


def batch_generate_references():
    return _library_check_text(VoiceAssetService.generate_missing_references())
