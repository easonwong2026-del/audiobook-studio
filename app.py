#!/usr/bin/env python3
"""Audiobook Studio UI -- 外部 Agent JSON 驱动的本地有声书生产工作台。"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import weakref
from html import escape
from typing import Any

import gradio as gr

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import (
    __version__,
    chapter_identity,
    config,
    project_paths,
    script_loader,
    segment_cache,
    voice_lib,
)
from lib import dataframe_style as df_style
from lib import progress as synth_progress
from lib import project_manager as _pm
from services import (
    ACTIVE_PRODUCTION_STATES,
    ExportService,
    ProductionJobError,
    ProductionJobService,
    ProjectBackupService,
    ProjectCatalogService,
    get_application_lifecycle,
    ProjectService,
    ProjectStorageService,
    QualityService,
    RepairError,
    RepairService,
    QuickTTSBusyError,
    QuickTTSService,
    RuntimeTTSService,
    SupplementService,
    SupplementTaskState,
    VoiceAssetService,
    VoiceCastError,
    VoiceCastResolver,
    WorkflowService,
)
from services.project_storage import format_size
from services.review_audio import ReviewAudioService
from services.session import SessionState
from services.synthesis import SynthesisState
from ui import create_project_handlers as create_ui
from ui import project_catalog_handlers as catalog_ui
from ui.components import (
    build_role_management_choices,
    create_production_navigation,
    empty_dashboard_html,
    format_bound_role_choices,
    format_role_label,
    format_role_management_summary,
    project_dashboard_html,
)
from ui.navigation import _GROUPS, _goto, create_nav_buttons
from ui.pages import (
    create_create_project_page,
    create_export_page,
    create_overview_page,
    create_project_page,
    create_review_page,
    create_settings_page,
    create_supplement_page,
    create_synthesis_page,
    create_voice_page,
)
from ui.shared import create_status_bar
from ui.theme import LIGHT_CSS, THEME
from ui.wiring.project_catalog_wiring import wire_project_catalog
from ui.wiring.settings_wiring import wire_settings_page
from ui.wiring.voice_wiring import wire_voice_page

BASE = os.path.dirname(os.path.abspath(__file__))
# 音色库外置于数据目录（默认 ~/AudiobookStudio/voice_library），与程序目录解耦。
# 注意：音色库路径必须在调用时动态解析（config.get_voice_library），
# 不得在此处模块级缓存，否则运行期切换数据目录后路径不会更新（见方案 §5.2）。


def _audio_pipeline():
    """按需加载音频后处理模块，仅在试听、修复或导出时付出开销。"""
    from lib import audio_pipeline
    return audio_pipeline




# ═══════════ callbacks (unchanged logic, 业务编排迁入 services) ═══════════

def create_project(name, script_file, ss):
    """Compatibility wrapper for the single structured-script import path."""
    if not name or not script_file:
        return name, None, "### ⚠ 请输入项目名称并上传 JSON 文件", gr.update()
    try:
        from services.project_creation import ProjectCreationService

        result = ProjectCreationService.create_from_structured_script(name, script_file)
        # 写入会话态（多标签各自独立，不共享全局可变 S）
        ss.set_project(result.project_name, None, {})
        return "", None, (
            f"### ✅ 项目「{result.project_name}」创建成功！"
            "下一步：前往角色与声音。"
        ), gr.update(choices=ProjectService.scan_projects(), value=result.project_name)
    except Exception as e:
        return name, None, f"### ❌ 创建失败: {e}", gr.update()


def _snap(ss):
    """读取（必要时重建）当前项目快照：优先用会话态快照，缺失时按项目名重建。"""
    s = ss.ensure_snapshot()
    if s is not None:
        return s
    if ss and ss.project:
        rebuilt = ProjectService.open_project_as_snapshot(ss.project)
        ss.set_snapshot(rebuilt)
        return rebuilt
    return None

def open_project(name, ss):
    if not name:
        return (
            "📖 等待打开项目", gr.update(choices=[], value=None), None,
            "### 当前角色配置\n请从左侧角色列表选择角色。",
            gr.update(choices=[]), "", "打开项目后显示角色绑定状态。",
        )
    try:
        # 业务委托 ProjectService.open_project_as_snapshot（包 pm.load_snapshot）
        snap = ProjectService.open_project_as_snapshot(name)
        ss.set_project(name, snap.script, snap.bindings)
        ss.set_snapshot(snap)
        roles = list(snap.script.get("voices",{}).keys())
        vcount = len(roles)
        bound = sum(1 for v in ss.bindings.values() if v)
        script_meta = snap.script.get("meta", {}) if isinstance(snap.script.get("meta"), dict) else {}
        title = str(script_meta.get("title") or name)
        author = str(script_meta.get("author") or "未填写")
        total_segments = int(getattr(snap.meta, "total_segments", 0) or 0)
        completed_segments = int(getattr(snap.meta, "completed_count", 0) or 0)
        progress = round((completed_segments / total_segments) * 100, 1) if total_segments else 0.0
        info = f"""### 🎧 {escape(title)}
- **项目名称**：`{escape(name)}`
- **书名**：{escape(title)}
- **作者**：{escape(author)}
- **章节数**：{snap.meta.total_chapters}
- **片段数**：{total_segments}
- **已完成片段**：{completed_segments}
- **合成完成比例**：{progress}%
- **角色与声音**：{vcount} 个角色（{bound} 已绑定）"""
        if snap.meta.failed_count: info += f"\n<span class='status-err'>⚠ {snap.meta.failed_count} 段失败</span>"

        seg_dir = project_paths.project_dir(ProjectService.get_project_dir(name), "segments")
        existing = scan_existing_raw(snap, seg_dir)
        log_init = "\n".join(existing[-15:]) if existing else "等待音色配置完成后开始合成..."
        # Production state is process-wide and durable; a fresh browser
        # session must discover an Agent/Web task instead of relying on its
        # own SessionState object.
        task_snapshot = _latest_production_task(name)
        if task_snapshot:
            runtime = ProductionJobService.get_runtime_state(task_snapshot.get("task_id"))
            ss.synthesis = runtime
            task_logs = task_snapshot.get("log_lines") or []
            if task_logs:
                log_init = "\n".join(task_logs[-15:])

        role_choices = build_role_management_choices(snap.script, ss.bindings)

        return (info,
        gr.update(choices=role_choices, value=None),
                None,
                "### 当前角色配置\n请从左侧角色列表选择角色。",
                gr.update(choices=_lib_voices(),value=None),
                log_init,
                _voice_cast_summary(snap))
    except Exception as e:
        return (
            f"### 打开失败\n{e}", gr.update(), None,
            "### 当前角色配置\n请从左侧角色列表选择角色。",
            gr.update(), "", "打开项目后显示角色绑定状态.",
        )




def refresh_top_status(ss):
    """O11：刷新顶部全局状态栏文本（项目 / 章节 / 进度 / 引擎状态）。

    生产引擎展示语义（P1）：顶部必须区分「项目生产引擎」（来自该项目生产
    task 的 frozen engine provenance）与「Runtime 当前引擎」（实际加载的
    引擎）。绝不把 runtime current 或 Settings default 伪装成项目使用过的引擎。
    """
    if not ss or not ss.project:
        return "*等待打开项目…*"
    try:
        snap = _snap(ss)
        if snap is None:
            meta, script, _ = ProjectService.open_project(ss.project)
        else:
            meta, script = snap.meta, snap.script
        chapters = len(script.get("chapters", []))
        done = getattr(meta, "completed_count", 0)
        total = getattr(meta, "total_segments", 0)
        title = script.get("meta", {}).get("title", ss.project)
        health = ProductionJobService.get_runtime_health()
        project_engine_text = _project_production_engine_text(ss.project)
        runtime_engine_text = _runtime_engine_text(health)
        return (f"📖 **{title}** · {chapters} 章 · {done}/{total} 段 · "
                f"{project_engine_text} · Runtime：{runtime_engine_text}")
    except Exception as exc:
        return f"📖 {ss.project}（状态读取失败：{exc}）"


def _production_status_label(status: str) -> str:
    return {
        "pending": "等待中",
        "running": "运行中",
        "pausing": "正在暂停",
        "paused": "已暂停",
        "recovering": "自动恢复中",
        "cancelling": "正在停止",
        "cancelled": "已停止",
        "done": "已完成",
        "error": "完成但有失败段",
        "interrupted": "上次运行已中断",
        "needs_attention": "需要处理",
    }.get(status, status or "未知")


def _production_source_label(source: str) -> str:
    return {
        "mcp": "Agent / MCP",
        "web": "网页",
        "recovery": "恢复任务",
        "system": "系统",
    }.get(source, source or "系统")


_STARTUP_PHASE_LABELS = {
    "task_submitted": "🚀 任务已提交，正在启动生产运行时…",
    "runtime_starting": "🚀 正在启动生产运行时…",
    "runtime_available": "🚀 生产运行时已启动",
    "task_claimed": "🚀 生产运行时已接管任务",
    "engine_loading": "⏳ 正在加载 IndexTTS2 模型…",
    "engine_ready": "✅ TTS 模型已就绪，正在准备第一段…",
    "preparing_first_segment": "✅ TTS 模型已就绪，正在准备第一段…",
    "synthesizing_first_segment": "🎧 正在合成第 1 段…",
    "running": "🎧 生产中",
    "engine_failed": "❌ TTS 引擎初始化失败",
}

# 面向用户的质量状态中文标签。后端枚举（not_started / needs_review /
# needs_fix / technical_warning / regenerating / passed）保持不变，仅展示层映射。
_QUALITY_STATUS_LABELS = {
    "not_started": "未生产",
    "needs_review": "待试听确认",
    "needs_fix": "需修复",
    "technical_warning": "技术警告",
    "regenerating": "重合成中",
    "passed": "通过",
}

_TECHNICAL_OUTCOME_LABELS = {
    "pass": "通过",
    "fail": "异常",
    "warning": "异常",
    "none": "未执行",
    "unreviewed": "未执行",
}

_REVIEW_STATUS_LABELS = {
    "passed": "已通过",
    "needs_fix": "需要修复",
    "unreviewed": "待确认",
    "not_started": "未生产",
    "regenerating": "重合成中",
}


def _quality_status_label(status: str) -> str:
    return _QUALITY_STATUS_LABELS.get(str(status or ""), str(status or "needs_review"))


def _technical_outcome_label(outcome) -> str:
    key = str(outcome or "")
    return _TECHNICAL_OUTCOME_LABELS.get(key, key or "未执行")


def _review_status_label(status) -> str:
    key = str(status or "")
    return _REVIEW_STATUS_LABELS.get(key, key or "待确认")


def _engine_name(value: Any) -> str:
    return {
        "indextts:2": "IndexTTS 2",
        "indextts:2.5": "IndexTTS 2.5",
    }.get(str(value or ""), str(value or ""))


def _global_default_engine() -> dict[str, Any]:
    try:
        profile = config.get_public_tts_profile()
        return profile if isinstance(profile, dict) else {}
    except (OSError, RuntimeError, TypeError, ValueError):
        return {}


def _project_production_engine_text(project_name: str) -> str:
    """Describe the project's *real historical* production engine(s).

    The source is production task provenance only — never the runtime current
    engine and never the Settings default.  A mixed-engine project shows every
    distinct engine it actually used (e.g. "IndexTTS 2 / 2.5") instead of
    claiming a single one.
    """
    try:
        engines = segment_cache.project_production_engines(project_name)
    except Exception:
        engines = []
    names = [
        name for name in (
            _engine_name(engine.get("engine_identity") or engine.get("engine_version"))
            for engine in engines
            if isinstance(engine, dict)
        ) if name
    ]
    if not names:
        return "项目引擎：尚无生产记录"
    return f"项目引擎：{' / '.join(names)}"


def _runtime_engine_text(health: dict[str, Any]) -> str:
    """Render only the actual runtime engine + state (never Settings/project)."""
    state = str(health.get("engine_state") or "unknown")
    actual = health if health.get("engine_identity") else {}
    identity = _engine_name(
        actual.get("engine_identity") or actual.get("engine_version") or ""
    )
    precision = str(actual.get("precision") or "")
    state_label = {
        "ready": "Ready", "loading": "Loading", "recovering": "Recovering",
        "error": "Error", "unknown": "Unknown",
    }.get(state, state)
    if not identity:
        return state_label
    return " · ".join(item for item in (identity, precision, state_label) if item)


def _engine_label(profile: Any) -> str:
    if not isinstance(profile, dict):
        return ""
    return " · ".join(
        item for item in (
            _engine_name(profile.get("engine_identity")),
            str(profile.get("precision") or ""),
        ) if item
    )



def _production_task_markdown(task: dict | None) -> str:
    """Render a task snapshot without exposing private filesystem paths."""
    if not task:
        return "当前没有运行中的生产任务。"
    progress = task.get("progress", {}) or {}
    total = int(progress.get("total", 0) or 0)
    completed = int(progress.get("completed", 0) or 0)
    failed = int(progress.get("failed", 0) or 0)
    percent = float(progress.get("percent", 0.0) or 0.0)
    scope = task.get("scope", {}) or {}
    if scope.get("all"):
        scope_text = "整书"
    elif scope.get("chapter_ids"):
        scope_text = "第 " + ", ".join(str(item) for item in scope["chapter_ids"]) + " 章"
    elif scope.get("segment_ids"):
        scope_text = "指定段落（" + str(len(scope["segment_ids"])) + " 段）"
    else:
        scope_text = "未指定"
    engine = task.get("engine_snapshot") or task.get("options", {}).get("engine_snapshot", {})
    runtime = ProductionJobService.get_runtime_health()
    engine_label = _engine_label(engine)
    global_label = _engine_label(
        runtime.get("global_default_engine") or _global_default_engine()
    )
    lines = [
        "### 当前生产任务",
        f"- **任务 ID**：`{task.get('task_id', '')}`",
        f"- **任务来源**：{_production_source_label(str(task.get('source') or ''))}",
        f"- **生产范围**：{scope_text}",
        f"- **状态**：{_production_status_label(str(task.get('status') or ''))}",
        f"- **进度**：{completed} / {total}（{percent:.1f}%）",
    ]
    if engine_label:
        lines.append(f"本任务引擎：**{engine_label}**")
    if global_label:
        lines.append(f"全局默认：**{global_label}**")
    runtime_state = str(runtime.get("engine_state") or "unknown")
    runtime_label = _engine_label(runtime)
    if runtime_label:
        lines.append(f"实际 runtime：**{runtime_label} · {runtime_state}**")
    startup = task.get("startup") if isinstance(task.get("startup"), dict) else {}
    phase = str(startup.get("startup_phase") or "")
    if phase:
        elapsed = startup.get("startup_phase_elapsed_seconds")
        elapsed_text = (
            f"（已持续 {float(elapsed):.0f} 秒）"
            if isinstance(elapsed, (int, float)) and elapsed is not None
            else ""
        )
        if phase == "engine_failed":
            error_code = str(startup.get("engine_error_code") or "TTS_ENGINE_INIT_FAILED")
            summary = str(startup.get("engine_error_summary") or "引擎初始化失败")
            lines.append(
                f"- ❌ **TTS 引擎初始化失败**（`{error_code}`）：{summary}"
            )
        elif phase in _STARTUP_PHASE_LABELS:
            lines.append(f"- {_STARTUP_PHASE_LABELS[phase]}{elapsed_text}")
        elif phase != "running":
            lines.append(f"- 🚀 启动阶段：`{phase}`{elapsed_text}")
        if startup.get("startup_slow") and phase != "running":
            lines.append(
                "- ⏱️ **启动耗时偏长，但运行时仍存活**：IndexTTS2 冷启动通常需要 1-3 分钟，"
                "请耐心等待（可稍后查看运行时健康状态）。"
            )
    current_chapter = progress.get("current_chapter")
    current_segment = progress.get("current_segment")
    if current_chapter or current_segment:
        lines.append(
            f"- **当前**：{current_chapter or '—'}"
            + (f" · `{current_segment}`" if current_segment else "")
        )
    if failed:
        lines.append(f"- **失败**：{failed}")
    if task.get("status") == "interrupted":
        lines.append("- **提示**：检测到上次中断任务，可点击“继续”恢复剩余段落。")
    recovery = task.get("recovery") if isinstance(task.get("recovery"), dict) else None
    if task.get("status") == "recovering" and recovery:
        lines.append(
            "- 🔄 **检测到 TTS 运行时异常，正在自动恢复 "
            f"{recovery.get('attempt', '?')}/{recovery.get('max_attempts', '?')}**"
            + (
                f"，将重试 `{recovery.get('retry_segment')}`"
                if recovery.get("retry_segment") else ""
            )
        )
    elif task.get("status") == "recovering":
        lines.append("- 🔄 **TTS 正在自动恢复，请稍候**")
    if task.get("status") == "needs_attention":
        reason = ""
        if recovery:
            reason = f"（{recovery.get('reason_code') or 'TTS 运行时异常'}）"
        lines.append(f"- ❌ **自动恢复失败，需要处理**{reason}")
        lines.append("- **建议**：查看运行时健康状态后重试，或取消当前任务。")
    return "\n".join(lines)


def _latest_production_task(project: str) -> dict | None:
    if not project:
        return None
    active = ProductionJobService.get_active_task(project)
    if active:
        return active
    tasks = ProductionJobService.list_tasks(project_name=project)
    return next(
        (
            task for task in tasks
            if task.get("status") in {"interrupted", "needs_attention"}
        ),
        None,
    )


def refresh_production_task(ss):
    """Refresh the shared task panel from ProductionJobService."""
    if not ss or not ss.project:
        return "当前没有运行中的生产任务。"
    try:
        task = _latest_production_task(ss.project)
        if task:
            runtime = ProductionJobService.get_runtime_state(task.get("task_id"))
            if runtime is not None:
                ss.synthesis = runtime
        return _production_task_markdown(task)
    except Exception as exc:
        logger.warning("刷新生产任务状态失败: %s", exc)
        return f"当前生产任务状态读取失败：{exc}"


def refresh_production_engine_status(_ss=None):
    """Render project production engine vs runtime current engine.

    The header must not claim the runtime current (or Settings default) is the
    project's production engine.  ``_ss`` is the Gradio session state; when a
    project is open its historical task provenance is shown.
    """
    health = ProductionJobService.get_runtime_health()
    project = getattr(_ss, "project", None) if _ss is not None else None
    project_text = (
        _project_production_engine_text(project)
        if project else "项目引擎：—"
    )
    runtime_text = _runtime_engine_text(health)
    return f"{project_text}\nRuntime：{runtime_text}"


def refresh_production_task_tick(ss):
    """Timer tick: refresh the panel and keep the timer alive while active."""
    markdown = refresh_production_task(ss)
    task = _latest_production_task(getattr(ss, "project", None)) if ss else None
    active = bool(
        task and str(task.get("status") or "") in ACTIVE_PRODUCTION_STATES
    )
    return markdown, gr.Timer(active=active)


def activate_production_timer():
    """Turn on the 1s polling timer after a start/cancel action."""
    return gr.Timer(active=True)

def delete_project(name, ss=None):
    """Archive a project by default; permanent deletion has a separate callback."""
    if not name:
        update = gr.update(choices=ProjectService.scan_projects(), value=None)
        return (update, "⚪ 请先选择项目。") if ss is not None else update
    try:
        target = ProjectStorageService.archive(name)
        if ss is not None and ss.project == name:
            ss.set_project(None, None, {})
            ss.set_snapshot(None)
            ss.synthesis = None
            message = f"✅ 项目已移入回收站，可从 `{os.path.dirname(target)}` 恢复。"
        else:
            message = f"✅ 项目已移入回收站：`{target}`"
    except Exception as exc:
        logger.exception("归档项目失败")
        message = f"❌ 归档项目失败：{exc}"
    update = gr.update(choices=ProjectService.scan_projects(), value=None)
    return (update, message) if ss is not None else update


def apply_data_dir(new_dir):
    """应用用户指定的数据保存位置（持久化到 config.json，本会话立即生效）。"""
    if not new_dir or not new_dir.strip():
        return "⚠ 请填写保存位置", config.get_data_dir()
    try:
        d = os.path.normpath(ProjectService.set_data_dir(new_dir.strip()))
        return f"✅ 数据目录已设置为：{d}（本会话立即生效）", d
    except Exception as e:
        return f"❌ 设置失败：{e}", config.get_data_dir()


def open_data_dir():
    """在资源管理器中打开当前数据目录。"""
    d = config.get_data_dir()
    os.makedirs(d, exist_ok=True)
    try:
        os.startfile(d)
    except OSError as exc:
        logger.warning("打开数据目录失败: %s", exc)
    return ""

def refresh_role_list(search, current_role, ss):
    """按搜索词刷新角色管理列表，同时保留仍可见的当前角色。"""
    if not ss or not ss.project:
        return gr.update(choices=[], value=None)
    snap = _snap(ss)
    if not snap:
        return gr.update(choices=[], value=None)
    choices = build_role_management_choices(snap.script, snap.bindings, search)
    selected = current_role if any(value == current_role for _, value in choices) else None
    return gr.update(choices=choices, value=selected)


def refresh_role_summary(ss):
    """Refresh the role binding count after a save without reloading the page."""
    if not ss or not ss.project:
        return "打开项目后显示角色绑定状态。"
    snap = _snap(ss)
    if not snap:
        return "打开项目后显示角色绑定状态。"
    return _voice_cast_summary(snap)


def _voice_cast_summary(snap):
    """Render shared Voice Cast state without exposing audio paths."""
    # Legacy/manual projects already have the complete snapshot required for
    # this display.  Avoid a second disk read during the normal open chain.
    if not os.path.isfile(os.path.join(snap.project_dir, "character_roster.json")):
        return format_role_management_summary(snap.script, snap.bindings)
    try:
        status = VoiceCastResolver.get_voice_binding_status(snap.name)
    except Exception:
        return format_role_management_summary(snap.script, snap.bindings)
    if status.get("mode") == "legacy_manual":
        return format_role_management_summary(snap.script, snap.bindings)
    state = "已锁定" if status.get("cast_locked") else "草稿"
    return (
        f"全书角色：**{status.get('roles_total', 0)}** · "
        f"已绑定：**{status.get('bound', 0)}** · "
        f"新增待处理：**{status.get('new_roles', 0)}** · 状态：**{state}**"
    )


def finalize_voice_cast_ui(ss):
    """Gradio callback for the one-click Voice Cast finalization."""
    if not ss or not ss.project:
        return "请先打开项目。"
    try:
        result = VoiceCastResolver.finalize_voice_cast(ss.project)
        ss.invalidate_snapshot()
        return f"✅ 全书声音方案已锁定（{len(result.get('cast', {}).get('roles', {}))} 个角色）。"
    except VoiceCastError as exc:
        return f"❌ {exc.code}：{exc}"
    except Exception as exc:
        return f"❌ 锁定失败：{exc}"


def _role_config_title(role, voice, binding):
    """生成右侧当前角色标题，避免再次提供角色选择控件。"""
    if not role:
        return "### 当前角色配置\n请从左侧角色列表选择角色。"
    description = str((voice or {}).get("description") or (voice or {}).get("name") or "").strip()
    detail = f"\n{description}" if description else ""
    status = "✅ 已绑定" if binding else "⚠ 待绑定"
    return f"### 当前角色：{role}{detail}\n{status}"


def select_role_from_list(role, ss):
    """选择角色列表项后加载该角色的绑定状态和右侧配置。"""
    if not ss or not ss.project or not role:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    role = str(role)
    snap = _snap(ss)
    if not snap or role not in (snap.script.get("voices", {}) or {}):
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    binding = snap.bindings.get(role)
    voice = snap.script.get("voices", {}).get(role, {})
    current = f"当前绑定音频：{os.path.basename(binding)}" if binding else "当前绑定音频：未选择"
    return (
        role,
        _role_config_title(role, voice, binding),
        gr.update(value=binding),
        gr.update(value=None),
        f"*{current}*",
        None,
        "",
    )

def _lib_voices():
    return voice_lib.voice_names()
def _lib_path(n):
    vlib = config.get_voice_library()
    return os.path.join(vlib, n) if n else None
def _safe_name(s):
    """Sanitize filename: replace filesystem-illegal chars (/ : * ? " < > |) with _"""
    import re
    return re.sub(r'[\\/:*?"<>|]', '_', s)

def bind_voice(role, audio_file, from_lib, ss):
    if not ss or not ss.project or not role:
        return "请先从左侧角色列表选择角色", gr.update(), gr.update(), role, gr.update(), gr.update()
    src = _lib_path(from_lib) if from_lib else audio_file
    if not src:
        return "请上传音频、录制或从音色库选择", gr.update(), gr.update(), role, gr.update(), gr.update()
    cat = voice_lib._category_of(os.path.basename(src)) if from_lib else "未分类"
    # A roster-backed project uses the same stable Voice Cast service as MCP
    # when the UI choice comes from the global library.  Direct uploads retain
    # the established manual-binding path for backwards compatibility.
    project_dir = ProjectService.get_project_dir(ss.project)
    if from_lib and os.path.isfile(
        project_paths.project_file(project_dir, "character_roster")
    ):
        try:
            resolved = VoiceCastResolver.resolve_role(ss.project, {"role": role})
            asset_id = VoiceAssetService.asset_id_for_path(src)
            VoiceCastResolver.bind_cast_role(ss.project, resolved["role_id"], asset_id)
            refreshed = ProjectService.open_project_as_snapshot(ss.project)
            dest = refreshed.bindings.get(role)
            if not dest:
                raise VoiceCastError("VOICE_BINDING_NOT_APPLIED", "演员表绑定未写入运行态绑定")
        except VoiceCastError:
            raise
    else:
        # 业务委托 ProjectService.bind_voice（拷贝 + 写 voice_bindings.json），返回 dest
        dest = ProjectService.bind_voice(ss.project, role, src, category=cat)
    # 原地 mutate 会话态绑定表（R1：多标签隔离，不靠返回值回传）
    ss.bindings[role] = dest
    # 写盘后重建快照并刷新会话态绑定表 / 分类映射
    snap = ProjectService.open_project_as_snapshot(ss.project)
    ss.set_snapshot(snap)
    ss.bindings = snap.bindings
    voice = snap.script.get("voices", {}).get(role, {})
    return (
        f"{format_role_label(role, voice)} 已绑定",
        gr.update(
            choices=build_role_management_choices(snap.script, ss.bindings),
            value=role,
        ),
        gr.update(),
        role,
        _role_config_title(role, voice, dest),
        f"*当前绑定音频：{os.path.basename(dest)}*",
    )

def preview_bound_voice(role, audio_file, from_lib, ss):
    """试听当前选择的声音，未选择候选声音时回退到已绑定声音。

    新绑定流程在保存前就提供试听，确保用户试听的是当前上传/音色库候选音频，
    而不是误把上一次已经绑定的声音当成待确认声音。
    """
    if not role or not ss:
        return None
    audio = _lib_path(from_lib) if from_lib else audio_file
    audio = audio or ss.bindings.get(role)
    if not audio or not os.path.isfile(audio):
        return None
    try:
        # test_voice_and_concat_wavs 在单例 runtime 中完成三句试听与拼接；
        # Web 进程只等待项目内持久任务，不加载第二份模型。
        return RuntimeTTSService.test_voice_and_concat_wavs(
            ss.project,
            role,
            audio,
        )
    except Exception:
        return None


def _string_list(values) -> list[str]:
    """Normalize UI multi-select values without changing their order."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _scope_from_ui(
    scope_mode,
    selected_chapters=None,
    selected_segment_ids=None,
) -> dict[str, object]:
    """Map the visible production-range controls to the shared scope API."""
    mode = str(scope_mode or "all").strip().lower()
    if mode in {"chapters", "chapter"}:
        return {
            "all": False,
            "chapter_ids": _string_list(selected_chapters),
            "segment_ids": [],
        }
    if mode in {"segments", "segment", "custom"}:
        return {
            "all": False,
            "chapter_ids": [],
            "segment_ids": _string_list(selected_segment_ids),
        }
    return {"all": True, "chapter_ids": [], "segment_ids": []}


def _scope_mode_label(scope_mode) -> str:
    return {
        "all": "整本",
        "chapters": "按章节",
        "segments": "自定义段落",
    }.get(str(scope_mode or "all"), "整本")


def _chapter_options(ss) -> tuple[list[tuple[str, str]], list[str]]:
    if not ss or not ss.project:
        return [], []
    snap = _snap(ss)
    chapters = snap.script.get("chapters", []) if snap else []
    options: list[tuple[str, str]] = []
    ids: list[str] = []
    for index, chapter in enumerate(chapters):
        chapter_id = str(chapter.get("id") or "").strip()
        if not chapter_id:
            continue
        ids.append(chapter_id)
        options.append((
            chapter_identity.chapter_label(chapter, index, len(chapters)),
            chapter_id,
        ))
    return options, ids


def _segment_records(ss, chapter_filter=None) -> list[dict]:
    """Return compact segment records for the current custom-scope filter."""
    if not ss or not ss.project:
        return []
    snap = _snap(ss)
    if snap is None:
        return []
    chapters = snap.script.get("chapters", [])
    selected_filter = str(chapter_filter or "").strip()
    records: list[dict] = []
    for index, chapter in enumerate(chapters):
        chapter_id = str(chapter.get("id") or "").strip()
        if selected_filter and selected_filter != "__all__" and chapter_id != selected_filter:
            continue
        chapter_label = chapter_identity.chapter_label(chapter, index, len(chapters))
        for segment in chapter.get("segments", []):
            if not isinstance(segment, dict):
                continue
            segment_id = str(segment.get("id") or "").strip()
            if not segment_id:
                continue
            text = " ".join(str(segment.get("text") or "").split())
            status = str(snap.meta.segments_status.get(segment_id, "pending"))
            status_label = {
                "done": "✅ 已完成",
                "failed": "❌ 失败",
                "running": "⏳ 合成中",
            }.get(status, "⬜ 待合成")
            records.append({
                "id": segment_id,
                "chapter_id": chapter_id,
                "chapter_label": chapter_label,
                "role": str(segment.get("role") or segment.get("speaker") or ""),
                "text": text,
                "status": status,
                "status_label": status_label,
            })
    return records


def _segment_choices(ss, chapter_filter=None) -> tuple[list[tuple[str, str]], list[str]]:
    records = _segment_records(ss, chapter_filter)
    choices: list[tuple[str, str]] = []
    ids: list[str] = []
    for record in records:
        text = record["text"][:36] + ("…" if len(record["text"]) > 36 else "")
        choices.append((
            f"{record['id']} · {record['role']} · {record['status_label']} · {text}",
            record["id"],
        ))
        ids.append(record["id"])
    return choices, ids


def _scope_preview_rows(ss, scope_mode, selected_chapters=None, selected_segment_ids=None):
    if not ss or not ss.project:
        return []
    snap = _snap(ss)
    if snap is None:
        return []
    mode = str(scope_mode or "all")
    if mode == "segments":
        selected_segments = _string_list(selected_segment_ids)
        selected_chapter_ids = None
    elif mode == "chapters":
        selected_segments = None
        selected_chapter_ids = _string_list(selected_chapters)
    else:
        selected_segments = None
        selected_chapter_ids = None
    return synth_progress.build_scope_preview_rows_from_script(
        snap.script,
        selected_chapters=selected_chapter_ids,
        selected_segment_ids=selected_segments,
        status_by_id=getattr(snap.meta, "segments_status", {}),
    )


def _format_scope_plan(plan: dict | None, scope_mode="all") -> str:
    if not plan:
        return "当前没有可用的生产范围计划。"
    if plan.get("project_name") == "" and plan.get("blockers"):
        return "⚠ " + str(plan["blockers"][0].get("message") or "无法读取生产计划")
    voice = plan.get("voice_cast", {}) or {}
    required = int(voice.get("required_role_count", 0) or 0)
    bound = int(voice.get("bound_role_count", 0) or 0)
    lines = [
        f"### {_scope_mode_label(scope_mode)} · "
        + ("✅ 当前选择可以开始生产" if plan.get("ready") else "⚠ 当前选择暂不可生产"),
        f"准备生产：{plan.get('segments', 0)} 段 · {plan.get('chapters', 0)} 章",
        f"需要角色：{required} · 角色已准备：{bound}/{required}",
        (
            f"已完成：{plan.get('already_completed', 0)} · "
            f"待合成：{plan.get('remaining', 0)} · "
            f"失败待重试：{plan.get('failed', 0)}"
        ),
    ]
    blockers = plan.get("blockers", []) or []
    if blockers:
        lines.append("\n**阻塞原因**")
        lines.extend(
            f"- {escape(str(item.get('message') or item.get('code') or '未就绪'))}"
            for item in blockers[:6]
            if isinstance(item, dict)
        )
    warnings = plan.get("warnings", []) or []
    if warnings:
        lines.append("\n**提示**")
        lines.extend(
            f"- {escape(str(item.get('message') or item.get('code') or ''))}"
            for item in warnings[:3]
            if isinstance(item, dict)
        )
    return "\n".join(lines)


def render_scope_controls(ss):
    """Restore scope controls, including legacy chapter-only selections."""
    options, chapter_ids = _chapter_options(ss)
    saved = _pm.get_synthesis_selections(ss.project) if ss and ss.project else {}
    saved_chapters = _string_list(saved.get("chapters")) if isinstance(saved, dict) else []
    saved_segments = _string_list(
        saved.get("segment_ids") if isinstance(saved, dict) else []
    )
    mode = str(saved.get("mode") or "") if isinstance(saved, dict) else ""
    if mode not in {"all", "chapters", "segments"}:
        if saved_segments:
            mode = "segments"
        elif saved_chapters and set(saved_chapters) != set(chapter_ids):
            mode = "chapters"
        else:
            mode = "all"
    chapters_value = [item for item in saved_chapters if item in chapter_ids]
    if mode == "chapters" and not chapters_value:
        chapters_value = list(chapter_ids)
    filter_value = str(saved.get("segment_chapter") or "") if isinstance(saved, dict) else ""
    if filter_value not in chapter_ids:
        filter_value = chapter_ids[0] if chapter_ids else "__all__"
    segment_choices, visible_ids = _segment_choices(ss, filter_value)
    segment_value = [item for item in saved_segments if item in visible_ids]
    full_segment_value = [
        item for item in saved_segments
        if item in {record["id"] for record in _segment_records(ss, "__all__")}
    ]
    plan = ProductionJobService.plan(
        ss.project if ss and ss.project else "",
        _scope_from_ui(mode, chapters_value, full_segment_value),
    )
    return (
        gr.update(value=mode),
        gr.update(visible=mode == "chapters"),
        gr.update(choices=options, value=chapters_value),
        gr.update(visible=mode == "segments"),
        gr.update(choices=[("全部章节", "__all__"), *options], value=filter_value),
        gr.update(choices=segment_choices, value=segment_value),
        full_segment_value,
        df_style.style_dataframe(
            _scope_preview_rows(ss, mode, chapters_value, full_segment_value),
            synth_progress.SCOPE_PREVIEW_HEADERS,
            status_col=5,
            status_color_map=df_style.ICON_COLORS,
        ),
        _format_scope_plan(plan, mode),
    )


def refresh_scope_preview(ss, scope_mode, selected_chapters, _chapter_filter, selected_segment_ids):
    """Plan the current UI scope without creating a task or locking roles."""
    if not ss or not ss.project:
        return [], "请先打开项目。"
    mode = str(scope_mode or "all")
    scope = _scope_from_ui(mode, selected_chapters, selected_segment_ids)
    plan = ProductionJobService.plan(ss.project, scope)
    return (
        df_style.style_dataframe(
            _scope_preview_rows(ss, mode, selected_chapters, selected_segment_ids),
            synth_progress.SCOPE_PREVIEW_HEADERS,
            status_col=5,
            status_color_map=df_style.ICON_COLORS,
        ),
        _format_scope_plan(plan, mode),
    )


def update_scope_visibility(scope_mode):
    mode = str(scope_mode or "all")
    return (
        gr.update(visible=mode == "chapters"),
        gr.update(visible=mode == "segments"),
    )


def refresh_segment_filter(ss, chapter_filter, selected_segment_ids):
    choices, visible_ids = _segment_choices(ss, chapter_filter)
    selected = [item for item in _string_list(selected_segment_ids) if item in visible_ids]
    return gr.update(choices=choices, value=selected)


def merge_segment_selection(visible_selection, selected_segment_ids, chapter_filter, ss):
    all_visible = _segment_choices(ss, chapter_filter)[1]
    stored = _string_list(selected_segment_ids)
    stored = [item for item in stored if item not in all_visible]
    visible = [item for item in _string_list(visible_selection) if item in all_visible]
    merged = stored + visible
    return gr.update(value=visible), merged


def apply_scope_segment_action(action, ss, chapter_filter, selected_segment_ids):
    all_visible = _segment_choices(ss, chapter_filter)[1]
    stored = [item for item in _string_list(selected_segment_ids) if item not in all_visible]
    status_by_id = {
        record["id"]: record["status"]
        for record in _segment_records(ss, chapter_filter)
    }
    action = str(action or "").strip()
    if action == "all":
        target = list(all_visible)
    elif action == "clear":
        target = []
    elif action == "pending":
        target = [item for item in all_visible if status_by_id.get(item) != "done"]
    else:  # failed
        target = [item for item in all_visible if status_by_id.get(item) == "failed"]
    merged = stored + target
    return gr.update(value=target), merged


def select_scope_segments(chapter_filter, selected_segment_ids, ss):
    return apply_scope_segment_action("all", ss, chapter_filter, selected_segment_ids)


def clear_scope_segments(chapter_filter, selected_segment_ids, ss):
    return apply_scope_segment_action("clear", ss, chapter_filter, selected_segment_ids)


def select_pending_scope_segments(chapter_filter, selected_segment_ids, ss):
    return apply_scope_segment_action("pending", ss, chapter_filter, selected_segment_ids)


def select_failed_scope_segments(chapter_filter, selected_segment_ids, ss):
    return apply_scope_segment_action("failed", ss, chapter_filter, selected_segment_ids)


def do_synthesis(ss, num_beams=2, progress=gr.Progress(),
                emotion="(按剧本默认)", s_override=False, emo_alpha=1.0, speech_rate=1.0,
                selected_chapters=None, scope_mode="all", selected_segment_ids=None):
    """Start production through the shared Web/MCP task kernel."""
    proj = ss.project
    if not proj:
        yield ("请先在项目管理中打开项目", [])
        return
    existing = ProductionJobService.get_active_task(proj)
    if existing is not None:
        yield (
            f"项目已有生产任务 `{existing['task_id']}`（{existing['status']}），"
            "请先继续监控或控制该任务。",
            [],
        )
        return
    # 2.3 O2：解析覆盖并持久化，保证预览 / 导出一致
    emotion_override = None if emotion == "(按剧本默认)" else emotion
    overrides = {
        "emotion": emotion_override,
        "override": bool(s_override),
        "emo_alpha": float(emo_alpha),
        "speech_rate": float(speech_rate),
    }
    try:
        _pm.set_synthesis_overrides(proj, overrides)
    except Exception as exc:
        logger.warning("保存合成覆盖参数失败: %s", exc)
    chapter_ids = _string_list(selected_chapters)
    segment_ids = _string_list(selected_segment_ids)
    scope = _scope_from_ui(scope_mode, chapter_ids, segment_ids)
    try:
        _pm.set_synthesis_selections(
            proj,
            {
                "mode": str(scope_mode or "all"),
                "chapters": chapter_ids,
                "segment_ids": segment_ids,
            },
        )
    except Exception as exc:
        logger.warning("保存合成勾选失败: %s", exc)
    try:
        started = ProductionJobService.start(
            proj,
            scope,
            {
                "num_beams": num_beams,
                "emotion": emotion_override,
                "emo_alpha": emo_alpha if s_override else None,
                "speech_rate": speech_rate if s_override else None,
            },
            source="web",
        )
        ss.synthesis = ProductionJobService.get_runtime_state(started["task_id"])
        if ss.synthesis is None:
            # A task may finish before the UI gets its first callback; the
            # durable snapshot remains the source of truth.
            ss.synthesis = SynthesisState(
                task_id=started["task_id"], project=proj, status=started.get("status", "pending")
            )
    except ProductionJobError as exc:
        yield (f"❌ {exc}", [])
        return
    except Exception as exc:
        logger.exception("网页启动生产失败")
        yield (f"❌ 启动生产失败: {exc}", [])
        return
    # 轮询直到终态，~0.5s 刷新一次日志 + 进度条 + 队列列表
    while True:
        snapshot = ProductionJobService.get_task_snapshot(started["task_id"])
        state = ss.synthesis
        status = str(snapshot.get("status") or getattr(state, "status", "pending"))
        if state is not None:
            state.status = status
        log_lines = snapshot.get("log_lines") or (
            state.log_lines[-50:] if state is not None else []
        )
        log_text = "\n".join(log_lines)
        rows = (
            synth_progress.to_queue_rows(state.segment_states)
            if state is not None and state.segment_states
            else synth_progress.to_queue_rows(
                synth_progress.build_segment_states(
                    proj,
                    snapshot.get("scope", {}).get("chapter_ids") or None,
                    snapshot.get("scope", {}).get("segment_ids") or None,
                )
            )
        )
        time.sleep(0.5)
        try:
            progress(
                float(snapshot.get("progress", {}).get("percent", 0.0) or 0.0) / 100,
                f"{snapshot.get('progress', {}).get('completed', 0)}/{snapshot.get('progress', {}).get('total', 0)}",
            )
        except Exception as exc:
            logger.debug("进度回调异常（进行中）: %s", exc)
        yield (
            log_text or _production_task_markdown(snapshot),
            df_style.style_dataframe(
                rows,
                synth_progress.QUEUE_HEADERS,
                status_col=0,
                status_color_map=df_style.ICON_COLORS,
            ),
        )
        if status in ("done", "cancelled", "error", "interrupted"):
            break
    # 终态再刷一次
    try:
        final_progress = snapshot.get("progress", {})
        progress(
            float(final_progress.get("percent", 0.0) or 0.0) / 100,
            f"{final_progress.get('completed', 0)}/{final_progress.get('total', 0)}",
        )
    except Exception as exc:
        logger.debug("进度回调异常（终态）: %s", exc)
    yield (
        "\n".join(snapshot.get("log_lines") or []) or _production_task_markdown(snapshot),
        df_style.style_dataframe(
            rows,
            synth_progress.QUEUE_HEADERS,
            status_col=0,
            status_color_map=df_style.ICON_COLORS,
        ),
    )

def cancel(ss):
    """Request cooperative cancellation through the shared task service."""
    if not ss or not ss.project:
        return "当前没有生产任务。"
    task = _latest_production_task(ss.project)
    task_id = task.get("task_id") if task else getattr(ss.synthesis, "task_id", None)
    if not task_id:
        return "当前没有生产任务。"
    try:
        result = ProductionJobService.cancel(task_id)
        return f"任务 {task_id}：{_production_status_label(result.get('status', ''))}"
    except Exception as exc:
        return f"停止任务失败：{exc}"

def pause_synthesis(ss):
    """O12：暂停合成（协作暂停，段边界挂起，不杀进行中进程）。

    仅在 ``ss.synthesis`` 存在且 ``status in (running, paused)`` 时生效；否则返回提示不报错。
    返回 (队列列表, 暂停按钮, 恢复按钮) 的更新三元组。
    """
    task = _latest_production_task(ss.project) if ss and ss.project else None
    if not task:
        return (gr.update(), gr.update(), gr.update())
    try:
        result = ProductionJobService.pause(task["task_id"])
    except Exception:
        return (gr.update(), gr.update(), gr.update())
    runtime = ProductionJobService.get_runtime_state(task["task_id"])
    if runtime is not None:
        ss.synthesis = runtime
    rows = df_style.style_dataframe(
        synth_progress.to_queue_rows(runtime.segment_states if runtime else []),
        synth_progress.QUEUE_HEADERS,
        status_col=0,
        status_color_map=df_style.ICON_COLORS,
    )
    return (
        rows,
        gr.update(value=f"⏸ {_production_status_label(result.get('status', 'paused'))}", interactive=False),
        gr.update(interactive=True),
    )

def resume_synthesis(ss):
    """O12：恢复合成（paused -> running，worker 退出段边界挂起继续提交新段）。

    仅在 ``ss.synthesis`` 存在且 ``status == 'paused'`` 时生效；否则返回提示不报错。
    返回 (队列列表, 暂停按钮, 恢复按钮) 的更新三元组。
    """
    task = _latest_production_task(ss.project) if ss and ss.project else None
    if not task or task.get("status") not in ("paused", "interrupted"):
        return (gr.update(), gr.update(), gr.update())
    try:
        ProductionJobService.resume(task["task_id"])
    except Exception:
        return (gr.update(), gr.update(), gr.update())
    runtime = ProductionJobService.get_runtime_state(task["task_id"])
    if runtime is not None:
        ss.synthesis = runtime
    rows = df_style.style_dataframe(
        synth_progress.to_queue_rows(runtime.segment_states if runtime else []),
        synth_progress.QUEUE_HEADERS,
        status_col=0,
        status_color_map=df_style.ICON_COLORS,
    )
    return (
        rows,
        gr.update(value="⏸ 暂停", interactive=True),
        gr.update(interactive=False),
    )

def refresh_queue_list(ss):
    """O3：空闲/打开项目时填充队列进度列表（读内存段态或据项目重建）。

    与 O11 ``refresh_top_status`` 共享状态源约定：O11 读 meta（粗粒度），本函数读
    ``state.segment_states``（细粒度）；不互相写、不反向写 meta。
    """
    task = _latest_production_task(ss.project) if ss and ss.project else None
    if task:
        runtime = ProductionJobService.get_runtime_state(task.get("task_id"))
        if runtime is not None:
            ss.synthesis = runtime
        if runtime is not None and runtime.segment_states:
            return df_style.style_dataframe(
                synth_progress.to_queue_rows(runtime.segment_states),
                synth_progress.QUEUE_HEADERS,
                status_col=0,
                status_color_map=df_style.ICON_COLORS,
            )
        scope = task.get("scope", {}) or {}
        selected = scope.get("chapter_ids") or None
        selected_segments = scope.get("segment_ids") or None
        try:
            return df_style.style_dataframe(
                synth_progress.to_queue_rows(
                    synth_progress.build_segment_states(
                        ss.project,
                        selected,
                        selected_segments,
                    )
                ),
                synth_progress.QUEUE_HEADERS,
                status_col=0,
                status_color_map=df_style.ICON_COLORS,
            )
        except Exception:
            pass
    if ss and ss.synthesis is not None and ss.synthesis.segment_states:
        return df_style.style_dataframe(
            synth_progress.to_queue_rows(ss.synthesis.segment_states),
            synth_progress.QUEUE_HEADERS,
            status_col=0,
            status_color_map=df_style.ICON_COLORS,
        )
    if ss and ss.project:
        try:
            return df_style.style_dataframe(
                synth_progress.to_queue_rows(synth_progress.build_segment_states(ss.project)),
                synth_progress.QUEUE_HEADERS,
                status_col=0,
                status_color_map=df_style.ICON_COLORS,
            )
        except Exception:
            return df_style.style_dataframe([], synth_progress.QUEUE_HEADERS, status_col=0, status_color_map=df_style.ICON_COLORS)
    return df_style.style_dataframe([], synth_progress.QUEUE_HEADERS, status_col=0, status_color_map=df_style.ICON_COLORS)

def scan_existing_raw(snap, seg_dir):
    # 阶段三：直接读快照的 meta.segments_status + script，不再重复读盘。
    meta = snap.meta
    script = snap.script
    lines=[]
    for ch in script.get("chapters",[]):
        for seg in ch.get("segments",[]):
            if meta.segments_status.get(seg['id'])=="done":
                lines.append(f"✅ {seg['id']} {seg['role']}")
    return lines

def _safe_path_for_file_component(path):
    """确保返回给 gr.File 的路径位于 Gradio allowed_paths 内（data_dir 子树或 tempdir）。

    导出目录若设在数据目录（app.launch 的 allowed_paths）之外，gr.File 会因
    InvalidPathError 报错。用户的目标文件已落在其指定目录，这里仅给应用内下载
    链接返回一份 data_dir / tempdir 内的副本，原文件不动。
    """
    if not path or not os.path.isfile(path):
        return path
    data_dir = config.get_data_dir()
    if data_dir:
        try:
            if os.path.commonpath([os.path.abspath(path), os.path.abspath(data_dir)]) == os.path.abspath(data_dir):
                return path  # 已在白名单内，原样返回
        except ValueError:
            pass
    # 落到 tempdir（Gradio 默认允许 serve），复制一份副本供下载
    tmp_dir = tempfile.gettempdir()
    base = os.path.basename(path)
    dst = os.path.join(tmp_dir, f"audiobook_export_{base}")
    if os.path.exists(dst):
        dst = os.path.join(tmp_dir, f"audiobook_export_{int(time.time() * 1000)}_{base}")
    try:
        shutil.copy2(path, dst)
    except Exception:
        return path  # 复制失败就退回原路径，不阻断导出结果
    return dst


_EXPORT_ACTIVE_STATUSES = frozenset({
    "pending", "running", "cancelling", "pausing", "paused", "recovering",
})
_EXPORT_TERMINAL_STATUSES = frozenset({
    "done", "error", "cancelled", "interrupted", "needs_attention",
})
_EXPORT_STATUS_LABELS = {
    "pending": "等待导出",
    "running": "正在导出",
    "cancelling": "正在取消",
    "pausing": "正在暂停",
    "paused": "已暂停，等待恢复",
    "recovering": "正在恢复",
}


def _remember_export_ui_state(ss, task_id: str, output_dir: str, project: str = ""):
    """Keep only the UI tracking pointer on the per-session state object.

    The durable task repository remains authoritative; these fields only let
    the existing five-input Export click guard the task it already tracks.
    """
    if ss is None:
        return
    ss._export_ui_task_id = str(task_id or "")
    ss._export_ui_output_dir = str(output_dir or "")
    ss._export_ui_project = str(project or getattr(ss, "project", None) or "")


def _export_ui_reset(message: str, *, task_id: str = "", output_dir: str = ""):
    """Return the complete Export UI state and stop its polling timer."""
    return (
        None,
        message,
        task_id,
        output_dir,
        gr.update(interactive=False),
        gr.Timer(active=False),
        gr.update(interactive=True),
    )


def _resolve_export_ui_artifact(
    project_name: str,
    task_id: str,
    task: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Resolve the primary ready artifact from the durable delivery manifest.

    The task row tells us which manifest belongs to this export.  The manifest
    is then re-read from persistent history and its relative path is resolved
    through ``project_paths``.  A task status of ``done`` alone is never enough
    to make the UI claim that a file is ready.
    """
    manifest_id = str(task.get("manifest_id") or task_id or "")
    if not manifest_id:
        return None, "任务没有关联的最终 manifest。"
    try:
        manifest = ExportService.get_delivery_manifest(project_name, manifest_id)
    except Exception as exc:  # noqa: BLE001 - UI must render persistent-read errors
        return None, f"最终 manifest 读取失败：{exc}"
    if not manifest or manifest.get("ready") is not True:
        return None, "最终 manifest 尚未 ready。"
    if str(manifest.get("export_id") or task_id) != str(task_id):
        return None, "最终 manifest 与当前导出任务不匹配。"
    outputs = manifest.get("outputs") or []
    primary = next(
        (item for item in outputs if isinstance(item, dict) and item.get("relative_path")),
        None,
    )
    if not primary:
        return None, "最终 manifest 没有可用 artifact。"
    relative_path = str(primary.get("relative_path") or "")
    try:
        project_dir = ProjectService.get_project_dir(project_name)
        artifact_path = project_paths.resolve_relative(project_dir, relative_path)
    except (OSError, KeyError, ValueError) as exc:
        return None, f"最终 artifact 路径无法解析：{exc}"
    try:
        if not os.path.isfile(artifact_path) or os.path.getsize(artifact_path) <= 0:
            return None, "最终 artifact 尚未发布或文件为空。"
    except OSError as exc:
        return None, f"最终 artifact 尚未发布：{exc}"
    return {
        "path": os.path.normpath(artifact_path),
        "filename": os.path.basename(artifact_path),
        "manifest": manifest,
        "output": primary,
    }, ""


def _copy_export_ui_artifact(artifact_path: str, output_dir: str) -> tuple[str, str]:
    """Preserve the existing optional user copy without changing the backend artifact."""
    requested = str(output_dir or "").strip()
    if not requested:
        return artifact_path, ""
    try:
        destination_dir = os.path.abspath(os.path.expanduser(requested))
        os.makedirs(destination_dir, exist_ok=True)
        destination = os.path.join(destination_dir, os.path.basename(artifact_path))
        if os.path.abspath(destination) != os.path.abspath(artifact_path):
            shutil.copy2(artifact_path, destination)
        return destination, ""
    except (OSError, shutil.Error) as exc:
        # The durable official artifact is still valid.  Keep success truthful
        # while making the optional copy failure visible to the user.
        return artifact_path, f"另存到指定位置失败：{exc}"


def _export_ui_values(task_id: str, output_dir: str, ss):
    """Read one durable export task and render all Export-only UI outputs."""
    identifier = str(task_id or "").strip()
    requested_dir = str(output_dir or "").strip()
    session_project = str(getattr(ss, "project", None) or "") if ss else ""
    tracked_project = str(getattr(ss, "_export_ui_project", "") or "") if ss else ""
    if session_project and tracked_project and tracked_project != session_project:
        _remember_export_ui_state(ss, "", "", session_project)
        return _export_ui_reset(
            "当前项目没有已提交的导出任务。",
            output_dir="",
        )
    _remember_export_ui_state(ss, identifier, requested_dir, session_project)
    if not identifier:
        return _export_ui_reset("当前没有已提交的导出任务。", output_dir=requested_dir)
    try:
        task = ExportService.get_export_task(
            getattr(ss, "project", None) if ss else "",
            identifier,
        )
    except Exception as exc:  # noqa: BLE001 - UI must surface any read failure
        return _export_ui_reset(
            f"❌ 导出状态读取失败：{exc}",
            task_id=identifier,
            output_dir=requested_dir,
        )

    status = str(task.get("status") or "unknown").lower()
    if status in _EXPORT_ACTIVE_STATUSES:
        label = _EXPORT_STATUS_LABELS.get(status, status)
        return (
            None,
            f"⏳ 导出状态：{label}\n任务 ID：{identifier}",
            identifier,
            requested_dir,
            gr.update(interactive=False),
            gr.Timer(active=True),
            gr.update(interactive=False),
        )

    project_name = str(task.get("project") or session_project or "")
    if session_project and project_name and project_name != session_project:
        _remember_export_ui_state(ss, "", "", session_project)
        return _export_ui_reset(
            "当前项目没有已提交的导出任务。",
            output_dir="",
        )
    _remember_export_ui_state(ss, identifier, requested_dir, project_name)
    if status == "done":
        artifact, reason = _resolve_export_ui_artifact(project_name, identifier, task)
        if artifact is None:
            return _export_ui_reset(
                f"⚠ 导出任务已完成，但最终成品尚未就绪。\n{reason}",
                task_id=identifier,
                output_dir=requested_dir,
            )
        artifact_path, copy_warning = _copy_export_ui_artifact(
            artifact["path"], requested_dir
        )
        lines = [
            "✅ 导出成功",
            f"文件：{os.path.basename(artifact_path)}",
            f"位置：{artifact_path}",
        ]
        if os.path.abspath(artifact_path) != os.path.abspath(artifact["path"]):
            lines.append(f"正式 artifact：{artifact['path']}")
        if copy_warning:
            lines.append(f"⚠ {copy_warning}")
        return (
            _safe_path_for_file_component(artifact_path),
            "\n".join(lines),
            identifier,
            requested_dir,
            gr.update(interactive=True),
            gr.Timer(active=False),
            gr.update(interactive=True),
        )

    if status == "cancelled":
        return _export_ui_reset(
            "🚫 导出已取消\n未生成最终成品。",
            task_id=identifier,
            output_dir=requested_dir,
        )
    if status == "error":
        error = task.get("error") if isinstance(task.get("error"), dict) else {}
        message = str(error.get("message") or "导出任务失败。")
        return _export_ui_reset(
            f"❌ 导出失败\n{message}",
            task_id=identifier,
            output_dir=requested_dir,
        )
    if status == "interrupted":
        return _export_ui_reset(
            "⚠ 导出已中断\n未生成最终成品，请重新发起导出。",
            task_id=identifier,
            output_dir=requested_dir,
        )
    if status == "needs_attention":
        return _export_ui_reset(
            "⚠ 导出需要处理\n未生成最终成品，请检查运行时状态后重试。",
            task_id=identifier,
            output_dir=requested_dir,
        )
    if status in _EXPORT_TERMINAL_STATUSES:
        return _export_ui_reset(
            f"⚠ 导出任务已结束（{status}），未生成最终成品。",
            task_id=identifier,
            output_dir=requested_dir,
        )

    # Unknown non-terminal states remain observable and are polled, but never
    # become a false success.
    return (
        None,
        f"⏳ 正在同步导出状态：{status}\n任务 ID：{identifier}",
        identifier,
        requested_dir,
        gr.update(interactive=False),
        gr.Timer(active=True),
        gr.update(interactive=False),
    )


def refresh_export_status(task_id: str, output_dir: str, ss):
    """Export-only timer callback backed by the durable task repository."""
    return _export_ui_values(task_id, output_dir, ss)


def open_export_location(task_id: str, output_dir: str, ss):
    """Open the directory containing the ready primary export artifact."""
    identifier = str(task_id or "").strip()
    if not identifier:
        return "⚪ 尚未完成任何导出，暂无可打开的位置。"
    try:
        task = ExportService.get_export_task(
            getattr(ss, "project", None) if ss else "",
            identifier,
        )
        if str(task.get("status") or "").lower() != "done":
            return "⚪ 导出尚未完成，暂时不能打开导出位置。"
        project_name = str(task.get("project") or getattr(ss, "project", None) or "")
        artifact, reason = _resolve_export_ui_artifact(project_name, identifier, task)
        if artifact is None:
            return f"⚠ 最终成品尚未就绪，不能打开位置：{reason}"
        target = artifact["path"]
        requested = str(output_dir or "").strip()
        if requested:
            candidate = os.path.join(
                os.path.abspath(os.path.expanduser(requested)),
                os.path.basename(target),
            )
            if os.path.isfile(candidate):
                target = candidate
        from lib.procutil import open_in_folder

        directory = os.path.dirname(target)
        if not open_in_folder(directory):
            return f"❌ 打开导出位置失败：{directory}"
        return f"✅ 已打开导出位置：{directory}"
    except Exception as exc:  # noqa: BLE001 - opening is a best-effort UI action
        return f"❌ 打开导出位置失败：{exc}"


def do_export(fmt, bitrate, output_dir, *args):
    """Start a durable export and immediately render its real task status."""
    qa_policy = "require_passed"
    ss = None
    active_task_id = ""
    active_output_dir = ""
    if len(args) >= 2:
        qa_policy, ss = args[0], args[1]
        if len(args) >= 3:
            active_task_id = str(args[2] or "").strip()
        if len(args) >= 4:
            active_output_dir = str(args[3] or "").strip()
    elif args:
        ss = args[0]
    requested_dir = str(output_dir or "").strip()
    if not ss or not ss.project:
        return _export_ui_reset("请先打开项目", output_dir=requested_dir)
    if not active_task_id:
        tracked_project = str(getattr(ss, "_export_ui_project", "") or "")
        if tracked_project == ss.project:
            active_task_id = str(getattr(ss, "_export_ui_task_id", "") or "").strip()
            active_output_dir = str(
                getattr(ss, "_export_ui_output_dir", "") or ""
            ).strip()
    try:
        # A second click while the current durable export is active must not
        # clear the UI's only tracking id after the backend rejects a second
        # export for the same project.
        if active_task_id:
            current = ExportService.get_export_task(ss.project, active_task_id)
            if str(current.get("status") or "").lower() in _EXPORT_ACTIVE_STATUSES:
                return _export_ui_values(
                    active_task_id,
                    active_output_dir or requested_dir,
                    ss,
                )
        result = ExportService.start_export(
            ss.project,
            fmt,
            bitrate=bitrate,
            qa_policy=str(qa_policy or "require_passed"),
        )
        export_id = str(result.get("task_id") or result.get("export_id") or "")
        if not export_id:
            return _export_ui_reset(
                "❌ 导出启动失败：服务没有返回 durable task_id。",
                output_dir=requested_dir,
            )
        # Re-read the durable task immediately.  This also handles an
        # idempotent replay that is already done without trusting a stale
        # local/UI result payload.
        return _export_ui_values(export_id, requested_dir, ss)
    except Exception as e:  # noqa: BLE001 - start errors must become UI state
        # The durable task may have become active between the guard read and
        # start_export().  Preserve that task instead of stopping its polling.
        candidate_task_id = active_task_id
        if not candidate_task_id:
            plan = getattr(e, "plan", None)
            blockers = plan.get("blockers", []) if isinstance(plan, dict) else []
            for blocker in blockers:
                if not isinstance(blocker, dict):
                    continue
                if (
                    str(blocker.get("code") or "") == "EXPORT_ACTIVE"
                    and str(blocker.get("status") or "").lower()
                    in _EXPORT_ACTIVE_STATUSES
                ):
                    candidate_task_id = str(blocker.get("task_id") or "").strip()
                    break
        if candidate_task_id:
            try:
                current = ExportService.get_export_task(ss.project, candidate_task_id)
                if str(current.get("status") or "").lower() in _EXPORT_ACTIVE_STATUSES:
                    return _export_ui_values(
                        candidate_task_id,
                        active_output_dir or requested_dir,
                        ss,
                    )
            except Exception as lookup_error:  # noqa: BLE001 - retain the original start error
                logger.debug("active export lookup after start failure failed: %s", lookup_error)
        return _export_ui_reset(
            f"❌ 导出启动失败：{e}",
            output_dir=requested_dir,
        )


def refresh_export_readiness(fmt, qa_policy, ss):
    """Render the formal delivery gate used by Web and MCP."""
    if not ss or not ss.project:
        return "#### 交付准备度\n请先打开项目。"
    try:
        plan = ExportService.plan_export(
            ss.project,
            fmt or "wav",
            qa_policy=qa_policy or "require_passed",
        )
        summary = plan.get("summary", {})
        metadata = summary.get("metadata", {})
        lines = [
            "#### 交付准备度",
            f"- 合成音频：{summary.get('active_revisions', 0)}/{summary.get('segments', 0)}",
            f"- 生产失败：{summary.get('failed_segments', 0)}",
            f"- 章节：{summary.get('chapters', 0)}",
            f"- FFmpeg：{'正常' if summary.get('ffmpeg_ready') else '不可用'}",
            f"- Metadata：{'正常' if metadata.get('title') else '缺少书名'}",
        ]
        exports = ExportService.list_exports(ss.project)
        if exports:
            latest_export = exports[0]
            lines.append(
                f"- Export：{latest_export.get('status', 'unknown')} · "
                f"{latest_export.get('export_id', '')}"
            )
        try:
            workflow = WorkflowService.get_state(ss.project)
            lines.append(
                "- Delivery："
                + ("current" if workflow["summary"].get("delivered") else "stale/missing")
            )
        except Exception:
            lines.append("- Delivery：状态暂不可用")
        if plan.get("ready"):
            lines.append("\n✅ 已满足当前 QA 策略，可以导出成品。")
        else:
            lines.append("\n**尚未就绪：**")
            lines.extend(
                f"- {item.get('message', item.get('code', '未知问题'))}"
                for item in plan.get("blockers", [])[:12]
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"#### 交付准备度\n❌ 检查失败：{exc}"

def do_export_subtitles(ss, sub_choice):
    """O1：生成字幕（srt / lrc），走全新 handler，绝不改 do_export 三参签名与接线。

    Args:
        ss: 会话态（首参，满足 AST 红线 handler 必接 ss）。
        sub_choice: 字幕格式选择，"none" / "srt" / "lrc" / "both"。
    """
    if not ss or not ss.project:
        return None, "请先打开项目"
    if not sub_choice or sub_choice == "none":
        return None, "未选择字幕格式"
    fmts = ("srt", "lrc") if sub_choice == "both" else (sub_choice,)
    try:
        report = QualityService.get_quality_report(ss.project)
        segment_paths = {}
        for item in report.get("segments", []):
            revision = item.get("audio_revision") or {}
            relative_path = str(revision.get("relative_path") or "")
            if relative_path:
                segment_paths[str(item.get("segment_id") or "")] = os.path.join(
                    ProjectService.get_project_dir(ss.project),
                    *relative_path.split("/"),
                )
        paths = ExportService.export_subtitles(
            ProjectService.get_project_dir(ss.project),
            formats=fmts,
            segment_paths=segment_paths,
            require_complete=True,
        )
        if not paths:
            return None, "未找到已合成段落，无法生成字幕（请先合成）"
        return paths, "字幕已生成"
    except Exception as e:
        return None, str(e)

def _selected_segment_id(choice, script=None):
    """Normalize a Gradio label/value choice without parsing display text."""
    return ReviewAudioService.normalize_segment_id(choice, script or {})


def _audio_update(path: str | None):
    """Keep an Audio component visible while changing only its value."""
    return gr.update(value=path, visible=True)


_STARTUP_PREP_PHASES = frozenset({
    "task_submitted", "runtime_starting", "runtime_available",
    "task_claimed", "engine_loading",
})


def _active_startup_phase(project_name):
    """Return the active production task's startup phase ('' if none)."""
    if not project_name:
        return ""
    try:
        task = ProductionJobService.get_active_task(project_name)
        startup = task.get("startup") if isinstance(task, dict) else None
        return str(startup.get("startup_phase") or "") if isinstance(startup, dict) else ""
    except Exception:
        return ""


def _quality_summary_markdown(report, project_name=None):
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    production_status = str(summary.get("production_status") or "")
    phase = _active_startup_phase(project_name)
    if production_status == "not_started" or summary.get("not_started", 0) == summary.get("segments", 0):
        preparing = " · 生产准备中…" if phase in _STARTUP_PREP_PHASES else ""
        return (
            "#### 质量状态\n"
            f"🟡 **尚未开始生产**{preparing}\n"
            "- 当前项目尚未产出可用的音频，质量检查暂不可用（`quality_status=not_available`）。\n"
            "- 点击「开始合成」后，此处会随生产进度自动更新。"
        )
    return (
        "#### 质量状态\n"
        f"通过 **{summary.get('passed', 0)}** · "
        f"待试听确认 **{summary.get('needs_review', 0)}** · "
        f"需修复 **{summary.get('needs_fix', 0)}** · "
        f"未生产 **{summary.get('not_started', 0)}** · "
        f"技术警告 **{summary.get('technical_warning', 0)}** · "
        f"重合成中 **{summary.get('regenerating', 0)}**"
    )


def _segment_quality_markdown(item):
    if not isinstance(item, dict):
        return "选择段落后显示技术 QA 与人工 Review。"
    technical = item.get("technical_qa") or {}
    human = item.get("human_review") or {}
    revision = item.get("audio_revision") or {}
    checks = technical.get("checks") or []
    quality_status = str(item.get("quality_status") or "needs_review")
    lines = [
        f"#### 段落 {item.get('segment_id', '')}",
        f"- Audio revision：{revision.get('audio_revision', '—')}",
        f"- 技术检查：{_technical_outcome_label(item.get('technical_outcome'))}",
        f"- 人工试听：{_review_status_label(item.get('review_status'))}",
        f"- 综合状态：{_quality_status_label(quality_status)}",
    ]
    if checks:
        lines.append("- 技术问题：" + "、".join(
            str(check.get("code") or "") for check in checks[:8]
        ))
    if human.get("review_note"):
        lines.append(f"- 备注：{human['review_note']}")
    return "\n".join(lines)


def _review_segment_choices(ss, status_filter="all", chapter_id=None, *, include_missing=False):
    if not ss or not ss.project:
        return [], {}, {}
    snapshot = _snap(ss)
    report = QualityService.get_quality_report(ss.project)
    quality_by_id = {
        str(item.get("segment_id")): item for item in report.get("segments", [])
    }
    project_dir = ProjectService.get_project_dir(ss.project)
    choices = []
    selected_chapter = str(chapter_id or "").strip()
    for chapter in snapshot.script.get("chapters", []):
        if selected_chapter and str(chapter.get("id") or "") != selected_chapter:
            continue
        for segment in chapter.get("segments", []):
            segment_id = str(segment.get("id") or "")
            quality = quality_by_id.get(segment_id, {})
            if status_filter not in (None, "", "all"):
                if quality.get("quality_status") != status_filter:
                    continue
            revision = quality.get("audio_revision") or {}
            relative_path = str(revision.get("relative_path") or "")
            audio_path = (
                os.path.join(project_dir, *relative_path.split("/"))
                if relative_path else ""
            )
            if not include_missing and (not audio_path or not os.path.isfile(audio_path)):
                continue
            text = " ".join(str(segment.get("text") or "").split())
            label = (
                f"{segment_id} · {segment.get('role') or segment.get('speaker') or ''} · "
                f"{text[:36]}{'…' if len(text) > 36 else ''}"
            )
            choices.append((label, segment_id))
    return choices, quality_by_id, report


_REVIEW_REPAIR_ACTIVE_STATES = frozenset({
    "pending", "running", "pausing", "paused", "recovering", "cancelling",
})
_REVIEW_REPAIR_TERMINAL_STATES = frozenset({
    "done", "error", "cancelled", "interrupted", "needs_attention", "partial",
})
_REVIEW_REPAIR_STATUS_LABELS = {
    "pending": "等待运行",
    "running": "重合成中",
    "pausing": "正在暂停",
    "paused": "已暂停",
    "recovering": "运行时恢复中",
    "cancelling": "正在取消",
}
_REVIEW_REPAIR_OUTPUT_COUNT = 11
_REVIEW_REPAIR_RECOVERY_OUTPUT_COUNT = 4

# Gradio event inputs are invocation-time snapshots.  Keep a tiny server-side
# fence per SessionState object so a late timer callback can compare itself
# with the newest review repair even while another callback is in flight.
_REVIEW_REPAIR_FENCE_LOCK = threading.RLock()
_REVIEW_REPAIR_FENCES: dict[
    int, tuple[Any, int, str, str, str]
] = {}


def _review_repair_fence_cleanup(key, owner_ref):
    with _REVIEW_REPAIR_FENCE_LOCK:
        current = _REVIEW_REPAIR_FENCES.get(key)
        if current is not None and current[0] is owner_ref:
            _REVIEW_REPAIR_FENCES.pop(key, None)


def _review_repair_fence_owner(owner_ref):
    return owner_ref() if isinstance(owner_ref, weakref.ReferenceType) else owner_ref


def _review_repair_fence_entry_locked(ss):
    if ss is None:
        return None
    key = id(ss)
    current = _REVIEW_REPAIR_FENCES.get(key)
    if current is None:
        return None
    if _review_repair_fence_owner(current[0]) is not ss:
        _REVIEW_REPAIR_FENCES.pop(key, None)
        return None
    return current[1:]


def _review_repair_fence_store_locked(ss, generation, project, repair_id, task_id):
    key = id(ss)
    current = _REVIEW_REPAIR_FENCES.get(key)
    owner_ref = (
        current[0]
        if current is not None and _review_repair_fence_owner(current[0]) is ss
        else None
    )
    if owner_ref is None:
        try:
            owner_ref = weakref.ref(
                ss,
                lambda ref, fence_key=key: _review_repair_fence_cleanup(fence_key, ref),
            )
        except TypeError:
            # SimpleNamespace-style direct-call test doubles cannot be weakly
            # referenced; production SessionState uses the weak-ref path.
            owner_ref = ss
    entry = (
        owner_ref,
        int(generation),
        str(project or ""),
        str(repair_id or ""),
        str(task_id or ""),
    )
    _REVIEW_REPAIR_FENCES[key] = entry
    return entry[1:]


def _review_repair_fence_set(
    ss,
    project,
    repair_id,
    task_id,
    *,
    force=False,
):
    """Set the current session's UI identity and advance only on change."""
    if ss is None:
        return None
    identity = (
        str(project or ""),
        str(repair_id or ""),
        str(task_id or ""),
    )
    with _REVIEW_REPAIR_FENCE_LOCK:
        current = _review_repair_fence_entry_locked(ss)
        if current is not None and not force and current[1:] == identity:
            return current
        generation = (current[0] + 1) if current is not None else 1
        return _review_repair_fence_store_locked(
            ss, generation, project, repair_id, task_id
        )


def _review_repair_fence_reserve(ss, project):
    """Reserve a new observer generation before submitting a replacement."""
    return _review_repair_fence_set(
        ss, project, "", "", force=True
    )


def _review_repair_fence_transition(
    ss,
    expected,
    project,
    repair_id,
    task_id,
):
    """Advance identity only when no newer callback has claimed the session."""
    if ss is None or expected is None:
        return None
    with _REVIEW_REPAIR_FENCE_LOCK:
        current = _review_repair_fence_entry_locked(ss)
        if current != expected:
            return None
        return _review_repair_fence_store_locked(
            ss,
            current[0] + 1,
            project,
            repair_id,
            task_id,
        )


def _review_repair_fence_for_callback(ss, project, repair_id, task_id):
    """Capture a callback fence, bootstrapping only isolated test/direct calls."""
    if ss is None:
        return None
    identity = (
        str(project or ""),
        str(repair_id or ""),
        str(task_id or ""),
    )
    with _REVIEW_REPAIR_FENCE_LOCK:
        current = _review_repair_fence_entry_locked(ss)
        if current is None:
            return _review_repair_fence_store_locked(
                ss, 1, project, repair_id, task_id
            )
        return current if current[1:] == identity else None


def _review_repair_fence_snapshot(ss):
    """Capture the complete current fence for a slow recovery callback."""
    if ss is None:
        return None
    with _REVIEW_REPAIR_FENCE_LOCK:
        return _review_repair_fence_entry_locked(ss)


def _review_repair_fence_compare_and_set(
    ss,
    expected,
    project,
    repair_id,
    task_id,
):
    """Write recovery identity only when the full captured fence is unchanged."""
    if ss is None:
        return None
    identity = (
        str(project or ""),
        str(repair_id or ""),
        str(task_id or ""),
    )
    with _REVIEW_REPAIR_FENCE_LOCK:
        current_project = str(getattr(ss, "project", None) or "")
        if current_project != identity[0]:
            return None
        current = _review_repair_fence_entry_locked(ss)
        if current != expected:
            return None
        if current is not None and current[1:] == identity:
            return current
        generation = (current[0] + 1) if current is not None else 1
        return _review_repair_fence_store_locked(
            ss, generation, project, repair_id, task_id
        )


def _review_repair_fence_is_current(ss, expected):
    if ss is None or expected is None:
        return False
    with _REVIEW_REPAIR_FENCE_LOCK:
        return _review_repair_fence_entry_locked(ss) == expected


def _review_repair_stale_outputs():
    """Leave every Gradio output untouched when a callback is stale."""
    return (gr.skip(),) * _REVIEW_REPAIR_OUTPUT_COUNT


def _review_repair_stale_recovery_outputs():
    """Leave recovery outputs untouched when a slower recovery is stale."""
    return (gr.skip(),) * _REVIEW_REPAIR_RECOVERY_OUTPUT_COUNT


def _review_selection_ids(selected, script):
    """Return valid current-project ids plus invalid selections, preserving order."""
    values = selected if isinstance(selected, list) else [selected]
    known = {
        str(segment.get("id") or "")
        for chapter in (script or {}).get("chapters", [])
        if isinstance(chapter, dict)
        for segment in chapter.get("segments", [])
        if isinstance(segment, dict) and str(segment.get("id") or "")
    }
    ids: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for choice in values:
        segment_id = _selected_segment_id(choice, script or {})
        if not segment_id:
            continue
        if segment_id not in known:
            invalid.append(segment_id)
        elif segment_id not in seen:
            ids.append(segment_id)
            seen.add(segment_id)
    return ids, invalid


def _review_workspace_values(ss, status_filter="all", chapter_id=None, preferred=None):
    """Build the four review workspace outputs from the current project only."""
    choices, quality_by_id, report = _review_segment_choices(
        ss, status_filter or "all", chapter_id
    )
    batch_choices, _batch_quality, _batch_report = _review_segment_choices(
        ss, status_filter or "all", chapter_id, include_missing=True
    )
    preview_values = [value for _label, value in choices]
    batch_values = [value for _label, value in batch_choices]
    preferred_ids = [str(item) for item in (preferred or []) if str(item)]
    selected = next(
        (item for item in preferred_ids if item in preview_values),
        preview_values[0] if preview_values else None,
    )
    selected_for_batch = [item for item in preferred_ids if item in batch_values]
    return (
        _quality_summary_markdown(report, ss.project if ss else None),
        gr.update(choices=choices, value=selected),
        gr.update(choices=batch_choices, value=selected_for_batch),
        _segment_quality_markdown(quality_by_id.get(str(selected)) if selected else None),
    )


def _batch_qa_line(result):
    """Render one durable technical-QA result with an actionable outcome."""
    segment_id = str(result.get("segment_id") or "?")
    outcome = str(result.get("outcome") or "error")
    checks = [item for item in (result.get("checks") or []) if isinstance(item, dict)]
    first_error = next((item for item in checks if item.get("severity") == "error"), None)
    if outcome == "pass":
        return f"{segment_id}  technical pass"
    if outcome == "warning":
        codes = "、".join(str(item.get("code") or "warning") for item in checks[:3])
        return f"{segment_id}  technical warning" + (f": {codes}" if codes else "")
    if first_error:
        code = str(first_error.get("code") or "technical failure")
        detail = {
            "AUDIO_MISSING": "audio missing",
            "AUDIO_EMPTY": "audio empty",
            "QA_ITEM_ERROR": str(result.get("error") or first_error.get("message") or "analysis error"),
        }.get(code, str(first_error.get("message") or code))
        return f"{segment_id}  error: {detail}"
    return f"{segment_id}  technical fail"


def _review_batch_error(message, ss, status_filter="all", chapter_id=None):
    try:
        workspace = _review_workspace_values(ss, status_filter, chapter_id)
    except Exception:
        workspace = (
            "#### 质量状态",
            gr.update(),
            gr.update(),
            "选择段落后显示技术 QA 与人工 Review。",
        )
    return (message, *workspace)


def refresh_quality_workspace(status_filter, chapter_id, ss):
    """Refresh QA filters from one project snapshot and one quality report."""
    try:
        choices, quality_by_id, report = _review_segment_choices(
            ss, status_filter or "all", chapter_id
        )
        batch_choices, _batch_quality_by_id, _batch_report = _review_segment_choices(
            ss,
            status_filter or "all",
            chapter_id,
            include_missing=True,
        )
        selected = choices[0][1] if choices else None
        item = quality_by_id.get(str(selected)) if selected else None
        return (
            _quality_summary_markdown(report, ss.project),
            gr.update(choices=choices, value=selected),
            gr.update(choices=batch_choices, value=[]),
            _segment_quality_markdown(item),
        )
    except Exception as exc:
        return (
            f"#### 质量状态\n❌ 刷新失败：{exc}",
            gr.update(choices=[], value=None),
            gr.update(choices=[], value=[]),
            "无法读取段落质量状态。",
        )


def show_segment_quality(choice, ss):
    if not ss or not ss.project or not choice:
        return "选择段落后显示技术 QA 与人工 Review。"
    segment_id = _selected_segment_id(choice, _snap(ss).script)
    return _segment_quality_markdown(
        QualityService.get_segment_quality(ss.project, segment_id)
    )


def run_selected_technical_qa(choice, ss):
    if not ss or not ss.project or not choice:
        return "请选择段落后运行技术 QA。", "#### 质量状态"
    try:
        segment_id = _selected_segment_id(choice, _snap(ss).script)
        QualityService.run_technical_qa(ss.project, segment_id)
        item = QualityService.get_segment_quality(ss.project, segment_id)
        report = QualityService.get_quality_report(ss.project)
        return _segment_quality_markdown(item), _quality_summary_markdown(report, ss.project)
    except Exception as exc:
        return f"❌ 技术 QA 失败：{exc}", "#### 质量状态"


def select_review_segments(mode, status_filter, chapter_id, ss):
    """Select the current chapter or current filtered review result."""
    if not ss or not ss.project:
        return gr.update(choices=[], value=[])
    if mode == "chapter" and not chapter_id:
        return gr.update(choices=[], value=[])
    effective_filter = "all" if mode == "chapter" else (status_filter or "all")
    choices, _quality_by_id, _report = _review_segment_choices(
        ss, effective_filter, chapter_id, include_missing=True
    )
    return gr.update(choices=choices, value=[value for _label, value in choices])


def clear_review_segment_selection(status_filter, chapter_id, ss):
    """Clear only the current project's review selection."""
    if not ss or not ss.project:
        return gr.update(choices=[], value=[])
    choices, _quality_by_id, _report = _review_segment_choices(
        ss, status_filter or "all", chapter_id, include_missing=True
    )
    return gr.update(choices=choices, value=[])


def batch_technical_qa(selected, status_filter, chapter_id, ss):
    """Run the existing batch QA API and render one result line per segment."""
    if not ss or not ss.project:
        return _review_batch_error("❌ 请先打开项目。", ss, status_filter, chapter_id)
    script = _snap(ss).script
    segment_ids, invalid = _review_selection_ids(selected, script)
    if invalid:
        details = "、".join(f"{item}: 段落不存在" for item in invalid)
        return _review_batch_error(
            f"❌ 批量 technical QA 未提交：{details}",
            ss,
            status_filter,
            chapter_id,
        )
    if not segment_ids:
        return _review_batch_error(
            "请选择至少一个段落后运行批量 technical QA。",
            ss,
            status_filter,
            chapter_id,
        )
    try:
        results = QualityService.run_technical_qa_batch(ss.project, segment_ids)
        lines = [f"### 批量 technical QA（{len(results)} 项）"]
        lines.extend(f"- {_batch_qa_line(result)}" for result in results)
        workspace = _review_workspace_values(
            ss,
            status_filter or "all",
            chapter_id,
            preferred=segment_ids,
        )
        return ("\n".join(lines), *workspace)
    except Exception as exc:
        return _review_batch_error(
            f"❌ 批量 technical QA 失败：{exc}",
            ss,
            status_filter,
            chapter_id,
        )


def _next_review_value(choices, current, quality_by_id):
    values = [value for _label, value in choices]
    if not values:
        return None
    start = values.index(str(current)) + 1 if str(current) in values else 0
    ordered = values[start:] + values[:start]
    return next(
        (
            value for value in ordered
            if quality_by_id.get(value, {}).get("quality_status") != "passed"
        ),
        values[min(start, len(values) - 1)],
    )


def mark_selected_review(
    choice, review_status, issue_type, review_note, status_filter, chapter_id, ss,
    auto_next=False,
):
    if not ss or not ss.project or not choice:
        return "请选择段落。", "#### 质量状态", gr.update()
    try:
        segment_id = _selected_segment_id(choice, _snap(ss).script)
        QualityService.mark_review(
            ss.project,
            segment_id,
            review_status,
            issue_type=issue_type or "",
            review_note=review_note or "",
            reviewed_by="web",
        )
        choices, quality_by_id, report = _review_segment_choices(
            ss, status_filter or "all", chapter_id
        )
        next_value = (
            _next_review_value(choices, segment_id, quality_by_id)
            if auto_next else segment_id
        )
        current = quality_by_id.get(str(next_value)) or QualityService.get_segment_quality(
            ss.project, segment_id
        )
        return (
            _segment_quality_markdown(current),
            _quality_summary_markdown(report, ss.project),
            gr.update(choices=choices, value=next_value),
        )
    except Exception as exc:
        return f"❌ 保存质检失败：{exc}", "#### 质量状态", gr.update()


def mark_selected_passed(choice, issue_type, note, status_filter, chapter_id, ss):
    return mark_selected_review(
        choice,
        "passed",
        issue_type,
        note,
        status_filter,
        chapter_id,
        ss,
        auto_next=True,
    )


def navigate_review_segment(direction, choice, status_filter, chapter_id, ss):
    choices, quality_by_id, _report = _review_segment_choices(
        ss, status_filter or "all", chapter_id
    )
    values = [value for _label, value in choices]
    if not values:
        return gr.update(value=None), "当前筛选没有可试听段落。"
    current = str(choice or "")
    index = values.index(current) if current in values else 0
    index = (index + (1 if direction == "next" else -1)) % len(values)
    selected = values[index]
    return (
        gr.update(choices=choices, value=selected),
        _segment_quality_markdown(quality_by_id.get(selected)),
    )


def bulk_pass_technical_qa(chapter_id, status_filter, ss, selected=None):
    """Pass selected/current-chapter segments whose technical QA is ``pass``."""
    if not ss or not ss.project:
        return _review_batch_error("❌ 请先打开项目。", ss, status_filter, chapter_id)
    snapshot = _snap(ss)
    if selected:
        segment_ids, invalid = _review_selection_ids(selected, snapshot.script)
        if invalid:
            details = "、".join(f"{item}: 段落不存在" for item in invalid)
            return _review_batch_error(
                f"❌ 批量通过未提交：{details}",
                ss,
                status_filter,
                chapter_id,
            )
    else:
        if not chapter_id:
            return _review_batch_error(
                "请选择章节或先选择段落后再批量通过。",
                ss,
                status_filter,
                chapter_id,
            )
        segment_ids = [
            str(segment.get("id") or "")
            for chapter in snapshot.script.get("chapters", [])
            if str(chapter.get("id") or "") == str(chapter_id)
            for segment in chapter.get("segments", [])
            if isinstance(segment, dict)
        ]
    try:
        result = QualityService.pass_technically_clean(
            ss.project,
            segment_ids,
            reviewed_by="web_bulk",
        )
        skipped_ids = result.get("skipped_segment_ids") or []
        scope_label = "所选" if selected else "本章"
        message = (
            f"✅ 已批量通过 {scope_label} **{result['passed']}** 段；"
            f"跳过 **{result['skipped']}** 段（技术 QA 未 pass 或已通过）。"
        )
        if skipped_ids:
            message += "\n- 跳过：" + "、".join(skipped_ids)
        workspace = _review_workspace_values(
            ss,
            status_filter or "all",
            chapter_id,
            preferred=result.get("segment_ids") or segment_ids,
        )
        return (message, *workspace)
    except Exception as exc:
        return _review_batch_error(
            f"❌ 批量通过失败：{exc}",
            ss,
            status_filter,
            chapter_id,
        )


def initialize_review_page(ss):
    """Initialize all review controls and proactively load the first previews."""
    if not ss or not ss.project:
        state = ReviewAudioService.initialize(None, None, None)
    else:
        snapshot = _snap(ss)
        state = ReviewAudioService.initialize(
            ss.project,
            ProjectService.get_project_dir(ss.project),
            snapshot.script if snapshot else None,
            snapshot.meta if snapshot else None,
        )
    return (
        state.chapter_table,
        gr.update(choices=state.chapter_choices, value=state.selected_chapter),
        _audio_update(state.chapter_audio),
        state.chapter_status,
        gr.update(choices=state.segment_choices, value=state.selected_segment),
        gr.update(choices=state.segment_choices, value=[]),
        _audio_update(state.segment_audio),
        state.segment_status,
        gr.update(value=""),
    )


def preview_chapters(ss):
    """Use the unified initializer for every review-page entry point."""
    return initialize_review_page(ss)

def play_segment(choices, ss):
    if not ss or not ss.project or not choices:
        yield _audio_update(None), "⚪ 未选择段落。"
        return
    snapshot = _snap(ss)
    if not snapshot:
        yield _audio_update(None), "⚪ 请先打开项目。"
        return
    sid = _selected_segment_id(choices, snapshot.script)
    yield _audio_update(None), f"⏳ 正在加载段落 {sid} 的试听音频…"
    result = ReviewAudioService.play_segment(
        ss.project,
        ProjectService.get_project_dir(ss.project),
        snapshot.script,
        sid,
    )
    yield _audio_update(result.path), result.status

def _review_repair_message(repair, snapshot=None):
    repair_id = str((repair or {}).get("repair_id") or "")
    status = str((repair or {}).get("status") or (snapshot or {}).get("status") or "unknown")
    label = _REVIEW_REPAIR_STATUS_LABELS.get(status, status)
    progress = (repair or {}).get("result", {}).get("progress", {})
    if not isinstance(progress, dict):
        progress = (snapshot or {}).get("progress", {}) or {}
    completed = progress.get("completed")
    total = progress.get("total")
    suffix = f"（{completed}/{total}）" if completed is not None and total else ""
    if status in _REVIEW_REPAIR_ACTIVE_STATES or status in {"preparing", "submitting"}:
        return f"⏳ Repair {repair_id}：{label}{suffix}"
    if status == "done":
        return f"✅ Repair {repair_id} 完成"
    if status == "partial":
        return f"⚠ Repair {repair_id} 部分完成"
    return f"⚠ Repair {repair_id}：{status}"


def _review_repair_clear_outputs(
    message,
    ss,
    status_filter="all",
    chapter_id=None,
    *,
    fence=None,
):
    settled_fence = fence
    if fence is not None:
        settled_fence = _review_repair_fence_transition(
            ss,
            fence,
            getattr(ss, "project", None) if ss else "",
            "",
            "",
        )
        if settled_fence is None:
            return _review_repair_stale_outputs()
    try:
        workspace = _review_workspace_values(ss, status_filter, chapter_id)
    except Exception as exc:
        workspace = (
            f"#### 质量状态\n❌ 刷新失败：{exc}",
            gr.update(choices=[], value=None),
            gr.update(choices=[], value=[]),
            "无法读取段落质量状态。",
        )
    if settled_fence is not None and not _review_repair_fence_is_current(
        ss, settled_fence
    ):
        return _review_repair_stale_outputs()
    return (
        *workspace,
        _audio_update(None),
        "当前段落试听将在 repair 终态后刷新。",
        message,
        "",
        "",
        "",
        gr.Timer(active=False),
    )


def _review_repair_active_outputs(
    repair,
    ss,
    status_filter,
    chapter_id,
    *,
    fence,
    message,
):
    if not _review_repair_fence_is_current(ss, fence):
        return _review_repair_stale_outputs()
    project = str(getattr(ss, "project", None) or "") if ss else ""
    repair_identifier = str((repair or {}).get("repair_id") or "")
    task_identifier = str((repair or {}).get("task_id") or "")
    preferred = (repair or {}).get("segment_ids") or []
    workspace = _review_workspace_values(
        ss, status_filter or "all", chapter_id, preferred=preferred
    )
    if not _review_repair_fence_is_current(ss, fence):
        return _review_repair_stale_outputs()
    return (
        *workspace,
        _audio_update(None),
        "重合成进行中，终态后将刷新段落试听。",
        message,
        repair_identifier,
        task_identifier,
        project,
        gr.Timer(active=True),
    )


def _review_repair_terminal_outputs(
    repair,
    snapshot,
    preview_choice,
    ss,
    status_filter,
    chapter_id,
    *,
    fence,
):
    """Reconcile one terminal repair for both timer and submit callbacks."""
    project = str(getattr(ss, "project", None) or "") if ss else ""
    settled_fence = _review_repair_fence_transition(
        ss, fence, project, "", ""
    )
    if settled_fence is None:
        return _review_repair_stale_outputs()
    if ss:
        ss.invalidate_snapshot()
    preferred = (repair or {}).get("segment_ids") or []
    workspace = _review_workspace_values(
        ss, status_filter or "all", chapter_id, preferred=preferred
    )
    audio, audio_status = _review_repair_audio(preview_choice, ss)
    if not _review_repair_fence_is_current(ss, settled_fence):
        return _review_repair_stale_outputs()
    detail = str(
        (repair or {}).get("error")
        or "新 audio revision 已进入技术 QA，旧 revision 已保留。"
    )
    return (
        *workspace,
        audio,
        audio_status,
        _review_repair_message(repair, snapshot) + f"\n{detail}",
        "",
        "",
        "",
        gr.Timer(active=False),
    )


def _review_repair_audio(choice, ss):
    if not ss or not ss.project or not choice:
        return _audio_update(None), "请选择已生成音频的段落。"
    try:
        snapshot = _snap(ss)
        result = ReviewAudioService.play_segment(
            ss.project,
            ProjectService.get_project_dir(ss.project),
            snapshot.script,
            _selected_segment_id(choice, snapshot.script),
        )
        return _audio_update(result.path), result.status
    except Exception as exc:
        return _audio_update(None), f"⚠ 刷新段落试听失败：{exc}"


def recover_review_repair(ss):
    """Restore an active repair observer from durable history on page entry."""
    recovery_fence = _review_repair_fence_snapshot(ss)
    project = str(getattr(ss, "project", None) or "") if ss else ""
    if not project:
        settled = _review_repair_fence_compare_and_set(
            ss, recovery_fence, "", "", ""
        )
        if ss is not None and settled is None:
            return _review_repair_stale_recovery_outputs()
        return "", "", "", gr.Timer(active=False)
    try:
        active = RepairService.find_active(project)
    except Exception as exc:
        logger.warning("恢复 review repair observer 失败: %s", exc)
        active = None
    if not active:
        settled = _review_repair_fence_compare_and_set(
            ss, recovery_fence, project, "", ""
        )
        if ss is not None and settled is None:
            return _review_repair_stale_recovery_outputs()
        return "", "", "", gr.Timer(active=False)
    repair_id = str(active.get("repair_id") or "")
    task_id = str(active.get("task_id") or "")
    settled = _review_repair_fence_compare_and_set(
        ss, recovery_fence, project, repair_id, task_id
    )
    if ss is not None and settled is None:
        return _review_repair_stale_recovery_outputs()
    return (
        repair_id,
        task_id,
        project,
        gr.Timer(active=True),
    )


def refresh_review_repair_tick(
    repair_id,
    task_id,
    tracked_project,
    preview_choice,
    status_filter,
    chapter_id,
    ss,
):
    """Observe one durable repair task and stop exactly at its terminal state."""
    current_project = str(getattr(ss, "project", None) or "") if ss else ""
    tracked = str(tracked_project or "")
    repair_identifier = str(repair_id or "")
    task_identifier = str(task_id or "")
    fence_project = tracked or current_project
    fence = _review_repair_fence_for_callback(
        ss,
        fence_project,
        repair_identifier,
        task_identifier,
    )
    if fence is None:
        return _review_repair_stale_outputs()
    if tracked and tracked != current_project:
        return _review_repair_stale_outputs()
    if not current_project:
        return _review_repair_clear_outputs(
            "⚪ 请先打开项目。", ss, status_filter, chapter_id, fence=fence
        )

    if not task_identifier:
        try:
            active = RepairService.find_active(current_project)
        except Exception:
            active = None
        if active:
            resolved_repair_id = str(active.get("repair_id") or "")
            resolved_task_id = str(active.get("task_id") or "")
            resolved_fence = _review_repair_fence_transition(
                ss,
                fence,
                current_project,
                resolved_repair_id,
                resolved_task_id,
            )
            if resolved_fence is None:
                return _review_repair_stale_outputs()
            fence = resolved_fence
            repair_identifier = resolved_repair_id
            task_identifier = resolved_task_id
            tracked = current_project
        else:
            return _review_repair_clear_outputs(
                "⚪ 当前没有活动的 repair task。",
                ss,
                status_filter,
                chapter_id,
                fence=fence,
            )
    if not _review_repair_fence_is_current(ss, fence):
        return _review_repair_stale_outputs()
    try:
        snapshot = ProductionJobService.get_task_snapshot(task_identifier)
    except Exception as exc:
        return _review_repair_clear_outputs(
            f"❌ Repair task 状态读取失败：{exc}",
            ss,
            status_filter,
            chapter_id,
            fence=fence,
        )
    if str(snapshot.get("project") or "") != current_project:
        return _review_repair_clear_outputs(
            "⚪ 已阻止跨项目显示 repair task。",
            ss,
            status_filter,
            chapter_id,
            fence=fence,
        )
    if not repair_identifier:
        linked = RepairService.find_by_task(current_project, task_identifier)
        linked_repair_id = str((linked or {}).get("repair_id") or "")
        resolved_fence = _review_repair_fence_transition(
            ss,
            fence,
            current_project,
            linked_repair_id,
            task_identifier,
        )
        if resolved_fence is None:
            return _review_repair_stale_outputs()
        fence = resolved_fence
        repair_identifier = linked_repair_id
    if not repair_identifier:
        return _review_repair_clear_outputs(
            "⚠ 找不到该 task 对应的 repair history，已停止观察。",
            ss,
            status_filter,
            chapter_id,
            fence=fence,
        )
    if not _review_repair_fence_is_current(ss, fence):
        return _review_repair_stale_outputs()
    try:
        current = RepairService.refresh(current_project, repair_identifier)
    except Exception as exc:
        return _review_repair_clear_outputs(
            f"❌ Repair 状态刷新失败：{exc}",
            ss,
            status_filter,
            chapter_id,
            fence=fence,
        )

    if not _review_repair_fence_is_current(ss, fence):
        return _review_repair_stale_outputs()
    task_status = str(snapshot.get("status") or "")
    repair_status = str(current.get("status") or task_status)
    is_terminal = (
        task_status in _REVIEW_REPAIR_TERMINAL_STATES
        or repair_status in _REVIEW_REPAIR_TERMINAL_STATES
    )
    if not is_terminal:
        return _review_repair_active_outputs(
            current,
            ss,
            status_filter,
            chapter_id,
            fence=fence,
            message=(
                "重合成由唯一 Production Runtime 执行，退出页面不会中断任务。"
            ),
        )
    return _review_repair_terminal_outputs(
        current,
        snapshot,
        preview_choice,
        ss,
        status_filter,
        chapter_id,
        fence=fence,
    )


def regenerate_segment(
    choices,
    emotion,
    emo_alpha,
    speech_rate,
    voice_choice,
    ss,
    tracked_repair_id="",
    tracked_task_id="",
    tracked_project="",
    status_filter="all",
    chapter_id=None,
):
    """Submit repair and share the timer's terminal reconciliation path."""
    if not ss or not ss.project or not choices:
        return _review_repair_clear_outputs(
            "请选择段落",
            ss,
            status_filter,
            chapter_id,
        )
    script = _snap(ss).script
    segment_ids, invalid = _review_selection_ids(choices, script)
    if invalid:
        details = "、".join(f"{item}: 段落不存在" for item in invalid)
        return _review_repair_clear_outputs(
            f"❌ 选择无效：{details}",
            ss,
            status_filter,
            chapter_id,
        )
    if not segment_ids:
        return _review_repair_clear_outputs(
            "请选择至少一个段落。",
            ss,
            status_filter,
            chapter_id,
        )
    try:
        active = RepairService.find_active(ss.project)
        if active:
            repair_identifier = str(active.get("repair_id") or tracked_repair_id or "")
            task_identifier = str(active.get("task_id") or tracked_task_id or "")
            fence = _review_repair_fence_set(
                ss, ss.project, repair_identifier, task_identifier
            )
            return _review_repair_active_outputs(
                active,
                ss,
                status_filter,
                chapter_id,
                fence=fence,
                message="已有活动 repair task，本次点击未创建重复任务。",
            )
        reservation = _review_repair_fence_reserve(ss, ss.project)
        started = RepairService.start(
            ss.project,
            segment_ids,
            emotion=emotion,
            emo_alpha=emo_alpha,
            speech_rate=speech_rate,
            voice_override=_lib_path(voice_choice) if voice_choice else None,
            source="web",
            requested_by="web",
        )
        repair_identifier = str(started.get("repair_id") or "")
        task_identifier = str(started.get("task_id") or "")
        fence = _review_repair_fence_transition(
            ss,
            reservation,
            ss.project,
            repair_identifier,
            task_identifier,
        )
        if fence is None:
            return _review_repair_stale_outputs()
        status = str(started.get("status") or "pending")
        if status in _REVIEW_REPAIR_ACTIVE_STATES or status in {"preparing", "submitting"}:
            return _review_repair_active_outputs(
                started,
                ss,
                status_filter,
                chapter_id,
                fence=fence,
                message=(
                    "重合成由唯一 Production Runtime 执行，退出页面不会中断任务。"
                ),
            )
        return _review_repair_terminal_outputs(
            started,
            started,
            segment_ids[0],
            ss,
            status_filter,
            chapter_id,
            fence=fence,
        )
    except (RepairError, ProductionJobError) as exc:
        _review_repair_fence_set(ss, getattr(ss, "project", None), "", "", force=True)
        return _review_repair_clear_outputs(
            f"❌ {exc}", ss, status_filter, chapter_id
        )
    except Exception as exc:
        logger.exception("段落 Repair 提交失败")
        _review_repair_fence_set(ss, getattr(ss, "project", None), "", "", force=True)
        return _review_repair_clear_outputs(
            f"❌ {exc}", ss, status_filter, chapter_id
        )

# ═══════════ 角色单独补录 / 补合成导出（T1-T4） ═══════════

def refresh_supplement_roles(ss):
    """补录角色下拉懒刷新：仅列已绑定音色的角色（未开项目/未绑定时禁用并提示）。

    约定：补录角色下拉 ``sup_role`` 只列「已绑定音色角色」，用
    ``project_manager.build_bound_role_choices(script, bindings)``；刷新时机为
    进入“生产与质检”阶段时懒刷新；刷新时机与打开项目链路解耦（阶段三重构后已无 22 元组契约）。
    """
    if not ss or not ss.project or not ss.script:
        return gr.update(interactive=False, choices=[], value=None,
                         info="请先打开项目并绑定角色音色")
    choices = format_bound_role_choices(ss.script, ss.bindings)
    if not choices:
        return gr.update(interactive=False, choices=[], value=None,
                         info="请先打开项目并绑定角色音色")
    return gr.update(interactive=True, choices=choices, value=choices[0][1])


def _fmt_elapsed(seconds: int) -> str:
    """Format elapsed seconds as ``MM:SS`` / ``H:MM:SS`` (progress display)."""
    seconds = max(int(seconds or 0), 0)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}时{minutes}分{sec}秒"
    if minutes:
        return f"{minutes}分{sec}秒"
    return f"{sec}秒"


def _infer_percent(message: str) -> float | None:
    """Extract ``进度 P%`` from an infer progress message.

    ``_latest_progress_phase`` renders ``… 进度 25.0%``; ``gr.Progress``
    expects a 0..1 fraction.  Returns ``None`` when the message has no
    progress token (caller falls back to indeterminate).
    """
    try:
        match = re.search(r"进度\s*([\d.]+)\s*%", str(message or ""))
        if not match:
            return None
        value = float(match.group(1))
        return max(0.0, min(value / 100.0, 1.0))
    except (ValueError, TypeError, AttributeError):
        return None


def do_supplement_parse_json(sup_json, ss):
    """解析上传的小 JSON：校验角色命中 + 至少一句文本，回填角色下拉与状态 state。

    Returns:
        ``(sup_role 更新, sup_json_role state, sup_json_lines state, 状态 markdown)``。
        失败时不改变 state（保持原角色 / 文本），仅给出诊断 markdown。
    """
    if not ss or not ss.project or not ss.script:
        return (gr.update(interactive=False, choices=[], value=None,
                          info="请先打开项目并绑定角色音色"),
                "", [], "❌ 请先打开项目")
    if not sup_json:
        return (gr.update(), "", [], "❌ 请先上传小 JSON 文件")
    # gr.File 在 4.x 返回 FileData；兼容 str 与 .name
    path = sup_json if isinstance(sup_json, str) else getattr(sup_json, "name", None)
    if not path or not os.path.isfile(path):
        return (gr.update(), "", [], "❌ 小 JSON 文件无效或不存在")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        return (gr.update(), "", [], f"❌ 小 JSON 不是合法 JSON：{e}")
    except Exception as e:
        return (gr.update(), "", [], f"❌ 读取小 JSON 失败：{e}")
    try:
        role, lines = SupplementService.parse_input_json(raw, ss.script)
    except ValueError as e:
        return (gr.update(), "", [], "❌ 小 JSON 解析失败：\n" + str(e))
    except Exception as e:
        return (gr.update(), "", [], f"❌ 小 JSON 解析异常：{e}")
    preview = "### ✅ 小 JSON 解析成功\n"
    preview += f"- **角色**：{role}\n"
    preview += f"- **句数**：{len(lines)}\n"
    preview += "\n" + "\n".join(f"{i + 1}. {ln[:50]}" for i, ln in enumerate(lines[:20]))
    if len(lines) > 20:
        preview += f"\n… 其余 {len(lines) - 20} 句"
    return (gr.update(value=role), role, lines, preview)


def do_utility_parse_json(utility_json, ss):
    """Parse project supplement JSON and feed role/text into shared UI state."""
    role_update, role, lines, message = do_supplement_parse_json(utility_json, ss)
    if message.startswith("### ✅"):
        return role_update, "\n".join(lines), gr.update(value=False), message
    return role_update, gr.update(), gr.update(), message


def _utility_progress(progress: "gr.Progress" = None):
    """Build the single progress adapter shared by project and utility TTS."""
    progress_phases: list[str] = []
    started_at = time.monotonic()

    def _callback(phase: str, message: str) -> None:
        progress_phases.append(str(message or phase or ""))
        if progress is None:
            return
        try:
            elapsed = int(time.monotonic() - started_at)
            if phase in {"submitted", "runtime_ensure_requested"}:
                progress(None, desc=str(message or "正在处理…"))
            elif phase == "engine_loading":
                progress(
                    None,
                    desc=f"{str(message or '正在加载引擎…')} 已等待 {_fmt_elapsed(elapsed)}",
                )
            elif phase == "engine_ready":
                progress(0.0, desc=str(message or "引擎就绪"))
            elif phase == "infer":
                percent = _infer_percent(message)
                progress(
                    0.0 if percent is None else percent,
                    desc=str(message or "正在合成…"),
                )
            elif phase in {"done", "error"}:
                progress(
                    1.0,
                    desc=str(message or ("✅ 任务完成" if phase == "done" else "❌ 任务失败")),
                )
        except Exception:  # pragma: no cover - progress is best effort
            pass

    return progress_phases, _callback


def _synthesize_project_utility(sup_role, sup_mode, sup_text, sup_json_role, sup_json_lines,
                                sup_emotion, sup_emo_alpha, sup_rate, sup_quality,
                                sup_split_punct, sup_voice, ss,
                                progress: "gr.Progress" = None):
    """逐句补合成：按模式取（角色, 文本）→ 逐句 synthesize → 收集 wav + 逐句状态。

    输入模式（``sup_mode``）：
      - ``"paste"``：角色=``sup_role`` 下拉，文本=``sup_text`` 按行拆分（可选按标点切长段）；
      - ``"json"``：角色/文本来自解析小 JSON 的 state（``sup_json_role`` / ``sup_json_lines``）。

    引擎加载/切换期间（IndexTTS 2.5 冷加载或 profile 切换可达数分钟）通过
    ``progress`` 上报「已提交补录任务 / 正在加载引擎 / 正在生成第 X/N 句」，
    避免点击后长时间无反馈。

    Returns:
        ``(sup_wavs state, 状态 markdown)``；状态 markdown 含逐句 ✅ / ❌ 句N 反馈。
    """
    if not ss or not ss.project or not ss.script:
        return [], "❌ 请先打开项目并绑定角色音色"
    # 决定角色与文本
    if sup_mode == "json":
        role = sup_json_role
        lines = list(sup_json_lines or [])
    else:
        role = sup_role
        lines = SupplementService.split_lines(sup_text or "", split_long=bool(sup_split_punct))
    if not role:
        return [], "❌ 未选择角色（请先刷新并选择已绑定音色的角色）"
    if not lines:
        return [], "❌ 没有可合成的文本（请粘贴内容，或先解析小 JSON）"

    # 音色真相源：参考音频唯一来自 ss.bindings[role]；P1 换音色仅本次覆盖、不回写 ss.bindings。
    override_voice = _lib_path(sup_voice) if sup_voice else None
    speaker = override_voice or ss.bindings.get(role)
    if not speaker:
        return [], f"❌ 角色「{role}」未绑定音色，且未选择替换音色"

    # 全局覆盖参数（P1 透传 synthesize_lines(overrides)）；(按默认) 时走引擎默认。
    use_override = sup_emotion not in (None, "(按默认)")
    overrides = {
        "emotion": (sup_emotion if use_override else None),
        "emo_alpha": (float(sup_emo_alpha) if use_override else None),
        "speech_rate": (float(sup_rate) if use_override else None),
    }
    num_beams = int(sup_quality) if sup_quality else 2

    # 5.3：任务隔离——每次补录独立目录（task_id 用 uuid，非秒级时间戳），互不覆盖。
    # v3 产物落在 02_生成音频/补录音频/<task_id>/，随项目备份与迁移；
    # v2/v1 项目由 resolver 解析到 cache/supplement_tasks/<task_id>/（历史行为）。
    import uuid as _uuid

    from lib import audio_format as _af
    task_id = _uuid.uuid4().hex
    project_dir = ProjectService.get_project_dir(ss.project)
    task_dir = os.path.join(
        project_paths.project_dir(project_dir, "supplement_audio", create=True),
        task_id,
    )
    os.makedirs(task_dir, exist_ok=True)
    task = SupplementTaskState(
        task_id=task_id, project=ss.project, role=role,
        status="running",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        task_dir=task_dir,
    )
    # 引擎加载/切换是多分钟级阻塞操作；项目补录和 Quick TTS 共用同一套
    # authoritative progress 适配器，不伪造百分比。
    progress_phases, _supplement_progress = _utility_progress(progress)

    try:
        results = SupplementService.synthesize_lines(
            role=role, lines=lines, speaker_audio=speaker,
            overrides=overrides, num_beams=num_beams, task=task,
            progress_cb=_supplement_progress,
        )
    except Exception as e:
        task.status = "error"
        hint = ""
        if progress_phases:
            hint = f"（阶段：{' → '.join(progress_phases[-3:])}）"
        if progress is not None:
            try:
                # P1-B：失败也必须把残留的「正在加载…」进度替换为明确终态。
                progress(1.0, desc="❌ 补合成失败")
            except Exception:  # pragma: no cover - progress is best effort
                pass
        return [], f"❌ 补合成异常：{str(e)[:200]}{hint}"

    # 写 manifest.json（任务隔离产物清单，便于回放 / 调试）
    try:
        manifest = {
            "task_id": task.task_id,
            "project": task.project,
            "role": task.role,
            "created_at": task.created_at,
            "items": [
                {"index": it.index, "text": it.text,
                 "wav_path": it.wav_path, "status": it.status, "error": it.error}
                for it in task.items
            ],
        }
        with open(os.path.join(task_dir, "manifest.json"), "w", encoding="utf-8") as mf:
            json.dump(manifest, mf, ensure_ascii=False, indent=2)
    except Exception:  # pylint: disable=broad-except
        pass

    ok_items = [it for it in task.items if it.status == "ok"]
    task.status = "done" if ok_items else "error"

    # 生成预览合并音频 preview.wav（任务目录内，供试听）
    wav_paths = [it.wav_path for it in task.items if it.wav_path]
    preview_path = os.path.join(task_dir, "preview.wav")
    if wav_paths:
        try:
            combined, rate, _ = _af.concatenate_normalized(
                wav_paths, target_rate=None, target_channels=1,
                target_dtype=_af.DEFAULT_TARGET_DTYPE,
            )
            _af.write_wav(preview_path, combined, rate)
        except Exception:  # pylint: disable=broad-except
            preview_path = None

    engine_note = ""
    for message in reversed(progress_phases):
        if "引擎" in str(message) or "加载" in str(message):
            note = str(message)
            # P1-B MEDIUM（QA）：终态 markdown 不得残留「正在加载…」进行时——
            # 实机「上面显示补合成完成，下面仍残留 正在加载 IndexTTS 2.5…」
            # 的“下面”就是这一行。成功返回时把进行时改写为过去时/中性表述，
            # 去掉尾部省略号；非进行时（如「引擎 … 就绪」）保持原样。
            if "正在加载" in note:
                note = note.replace("正在加载", "本次已加载")
            engine_note = f"\n> ⏳ {note.rstrip('…')}"
            break
    ok_count = len(wav_paths)
    total_count = len(results)
    if ok_count > 0:
        title = f"### 🎙 补合成完成（{ok_count}/{total_count} 成功）"
        done_desc = f"✅ 补录完成（{ok_count}/{total_count} 成功）"
    else:
        # LOW（QA）：0 成功时不得用 ✅ 误导文案，改中性/失败表述。
        title = f"### 🎙 补合成完成（0/{total_count} 成功，全部失败）"
        done_desc = f"❌ 补录完成（0/{total_count} 成功）"
    md = [title]
    for r in results:
        txt = (r.get("text") or "")[:30]
        if r["status"] == "ok":
            md.append(f"- ✅ 句{r['index'] + 1}: {txt}")
        else:
            md.append(f"- {r['error']}")
    md.append(f"\n> 任务 ID：`{task_id}`｜产物目录：`{task_dir}`")
    if engine_note:
        md.append(engine_note)
    if progress is not None:
        try:
            # P1-B：任务完成后把残留的「正在加载模型…」进度替换为明确终态，
            # 避免同时显示「补合成完成」与「正在加载 IndexTTS 2.5…」。
            progress(1.0, desc=done_desc)
        except Exception:  # pragma: no cover - progress is best effort
            pass
    return wav_paths, "\n".join(md)


def do_utility_tts_synth(
    utility_mode,
    utility_role,
    utility_voice,
    utility_text,
    utility_emotion,
    utility_emo_alpha,
    utility_rate,
    utility_quality,
    utility_split_punct,
    utility_override_voice,
    ss,
    progress: "gr.Progress" = None,
):
    """Shared synth entrypoint dispatching to the two authoritative services."""
    mode = str(utility_mode or "project_role")
    if mode == "project_role":
        wavs, message = _synthesize_project_utility(
            utility_role,
            "paste",
            utility_text,
            "",
            [],
            utility_emotion,
            utility_emo_alpha,
            utility_rate,
            utility_quality,
            utility_split_punct,
            utility_override_voice,
            ss,
            progress=progress,
        )
        return wavs, message, mode if wavs else "", str(ss.project) if wavs and ss else ""

    if mode != "library_voice":
        return [], "❌ 未知声音来源，请重新选择", "", ""
    if not utility_voice:
        return [], "❌ 请选择声音（全局声音库）", "", ""
    text = str(utility_text or "").strip()
    if not text:
        return [], "❌ 请输入台词", "", ""
    speaker = _lib_path(utility_voice)
    if not speaker or not os.path.isfile(speaker):
        return [], f"❌ 声音文件不存在：{utility_voice}", "", ""

    use_override = utility_emotion not in (None, "(按默认)")
    overrides = {
        "emotion": utility_emotion if use_override else None,
        "emo_alpha": float(utility_emo_alpha) if use_override else None,
        "speech_rate": float(utility_rate) if use_override else None,
    }
    progress_phases, utility_progress = _utility_progress(progress)
    try:
        wav = QuickTTSService.synthesize(
            text=text,
            speaker_audio=speaker,
            num_beams=int(utility_quality) if utility_quality else 2,
            overrides=overrides,
            progress_cb=utility_progress,
        )
    except QuickTTSBusyError as exc:
        if progress is not None:
            try:
                progress(1.0, desc="❌ 临时配音繁忙")
            except Exception:  # pragma: no cover - progress is best effort
                pass
        return [], f"❌ {exc}", "", ""
    except Exception as exc:
        hint = ""
        if progress_phases:
            hint = f"（阶段：{' → '.join(progress_phases[-3:])}）"
        if progress is not None:
            try:
                progress(1.0, desc="❌ 临时配音失败")
            except Exception:  # pragma: no cover - progress is best effort
                pass
        return [], f"❌ 临时配音失败：{str(exc)[:200]}{hint}", "", ""
    if progress is not None:
        try:
            progress(1.0, desc="✅ 临时配音完成")
        except Exception:  # pragma: no cover - progress is best effort
            pass
    return [wav], f"### 🎙 临时配音完成\n- ✅ 已生成：`{wav}`\n> 临时配音不走项目书架；试听或导出后产物位于 Quick TTS 目录。", mode, ""


def do_supplement_synth(sup_role, sup_mode, sup_text, sup_json_role, sup_json_lines,
                        sup_emotion, sup_emo_alpha, sup_rate, sup_quality,
                        sup_split_punct, sup_voice, ss,
                        progress: "gr.Progress" = None):
    """Compatibility API for existing callers; UI uses ``do_utility_tts_synth``."""
    return _synthesize_project_utility(
        sup_role,
        sup_mode,
        sup_text,
        sup_json_role,
        sup_json_lines,
        sup_emotion,
        sup_emo_alpha,
        sup_rate,
        sup_quality,
        sup_split_punct,
        sup_voice,
        ss,
        progress=progress,
    )


def do_supplement_export(sup_format, sup_bitrate, sup_export_name, sup_wavs, sup_role, ss):
    """把已合成的补录 wav 导出为独立音频（不进整本拼接），经白名单后返回下载路径。

    PR B 修复 4：支持自定义导出名称（非法字符清洗 / 扩展名归一 / 重名后缀）；
    导出前显示保存位置，导出后显示最终文件路径。

    Returns:
        ``(_safe_path, msg)``；``_safe_path`` 经 ``_safe_path_for_file_component``
        确保落在 Gradio allowed_paths 内，再交给 ``gr.File`` 下载。
    """
    if not ss or not ss.project:
        return None, "请先打开项目"
    wavs = [w for w in (sup_wavs or []) if w and os.path.isfile(w)]
    if not wavs:
        return None, "❌ 没有可导出的补录音频（请先逐句补合成）"
    role = sup_role or "角色"
    project_dir = ProjectService.get_project_dir(ss.project)
    out_dir = project_paths.project_dir(project_dir, "delivery_supplement", create=True)
    name = sup_export_name or f"supplement_{_safe_name(role)}"
    from services.export_naming import build_export_path, unique_path

    out_path = unique_path(build_export_path(out_dir, name, sup_format, fallback=f"supplement_{_safe_name(role)}"))
    meta = ss.script.get("meta", {}) if isinstance(ss.script, dict) else {}
    title = f"{meta.get('title', 'audiobook')} - {role} 补录" if meta else None
    artist = meta.get("author") if meta else None
    try:
        final = _audio_pipeline().export_supplement(
            paths=wavs, out_path=out_path, format=sup_format, bitrate=sup_bitrate,
            title=title, artist=artist,
        )
        msg = (
            f"✅ 导出完成：`{os.path.basename(final)}`\n\n"
            f"**保存位置：** `{out_dir}`\n\n"
            f"**最终文件：** `{final}`"
        )
        return _safe_path_for_file_component(final), msg
    except Exception as e:
        return None, str(e)


def refresh_supplement_export_hint(ss):
    """导出前显示项目补录的保存位置（v3 → <project_dir>/03_导出成品/补录）。"""
    if not ss or not ss.project:
        return "打开项目后将在此显示导出保存位置。"
    project_dir = ProjectService.get_project_dir(ss.project)
    out_dir = project_paths.project_dir(project_dir, "delivery_supplement", create=True)
    return f"**保存位置：** `{out_dir}`"


def open_supplement_folder(sup_wavs, ss):
    """打开项目补录导出目录（no-window，不弹黑框）。"""
    if not ss or not ss.project:
        return "请先打开项目"
    project_dir = ProjectService.get_project_dir(ss.project)
    out_dir = project_paths.project_dir(project_dir, "delivery_supplement", create=True)
    from lib.procutil import open_in_folder

    ok = open_in_folder(out_dir)
    return f"✅ 已打开导出目录：`{out_dir}`" if ok else f"❌ 打开目录失败：`{out_dir}`"


def refresh_quick_tts_engine_info():
    """显示临时配音将使用的 effective engine（复用既有 utility 选择规则）。"""
    try:
        from services.runtime_tts import _select_utility_engine

        profile, source = _select_utility_engine(None)
        label = _engine_display_label(profile)
        source_label = {
            "explicit": "显式指定",
            "runtime_switch_target": "切换目标",
            "runtime_current": "运行时已加载",
            "global_default": "Settings 默认",
        }.get(source, source)
        return f"**当前引擎：** {label}（{source_label}）"
    except Exception:
        return "**当前引擎：** 读取失败"


def _engine_display_label(profile):
    version = str((profile or {}).get("engine_version") or "")
    label = "IndexTTS 2.5" if version == "2.5" else "IndexTTS 2"
    return f"{label}（v{version or '?'}）"


def do_quick_tts_synth(qt_voice, qt_text, ss, progress: "gr.Progress" = None):
    """Compatibility API for existing callers; UI uses the shared entrypoint."""
    wavs, message, _, _ = do_utility_tts_synth(
        "library_voice", None, qt_voice, qt_text,
        "(按默认)", 1.0, 1.0, 2, True, None, ss,
        progress=progress,
    )
    return wavs, message


def play_quick_tts(qt_wavs):
    """试听临时配音音频。"""
    wavs = [w for w in (qt_wavs or []) if w and os.path.isfile(w)]
    if not wavs:
        return None
    return wavs[0]


def do_quick_tts_export(qt_format, qt_bitrate, qt_export_name, qt_wavs):
    """导出临时配音（Quick TTS exports 目录，自定义名称 / 重名后缀）。"""
    wavs = [w for w in (qt_wavs or []) if w and os.path.isfile(w)]
    if not wavs:
        return None, "❌ 没有可导出的临时配音音频（请先生成）"
    try:
        final = QuickTTSService.export(
            wav_path=wavs[0],
            name=qt_export_name or "quick_tts",
            fmt=qt_format,
            bitrate=qt_bitrate,
        )
        out_dir = QuickTTSService.exports_root()
        msg = (
            f"✅ 导出完成：`{os.path.basename(final)}`\n\n"
            f"**保存位置：** `{out_dir}`\n\n"
            f"**最终文件：** `{final}`"
        )
        return _safe_path_for_file_component(final), msg
    except Exception as e:
        return None, str(e)


def open_quick_tts_folder():
    """打开临时配音导出目录（no-window，不弹黑框）。"""
    out_dir = QuickTTSService.exports_root()
    from lib.procutil import open_in_folder

    ok = open_in_folder(out_dir)
    return f"✅ 已打开导出目录：`{out_dir}`" if ok else f"❌ 打开目录失败：`{out_dir}`"


def refresh_utility_export_hint(utility_mode, ss):
    """Show the authoritative destination for the selected utility mode."""
    if str(utility_mode or "project_role") == "library_voice":
        out_dir = QuickTTSService.exports_root()
        return f"**保存位置：** `{out_dir}`"
    return refresh_supplement_export_hint(ss)


def reset_utility_mode(utility_mode, ss):
    """Clear result state when switching business modes."""
    mode = str(utility_mode or "project_role")
    if mode not in {"project_role", "library_voice"}:
        mode = "project_role"
    label = "项目补录" if mode == "project_role" else "临时配音"
    return (
        gr.update(visible=mode == "project_role"),
        gr.update(visible=mode == "library_voice"),
        [],
        "",
        "",
        None,
        None,
        "",
        f"已切换到「{label}」，请重新生成音频。",
        refresh_utility_export_hint(mode, ss),
    )


def play_utility_preview(utility_mode, utility_result_mode, utility_result_project, utility_wavs, ss):
    """Preview the current mode's artifact through the single Audio component."""
    mode = str(utility_mode or "project_role")
    if mode != str(utility_result_mode or ""):
        return None
    if mode == "library_voice":
        return play_quick_tts(utility_wavs)
    if not ss or not ss.project or str(ss.project) != str(utility_result_project or ""):
        return None
    return play_supplement_preview("all", utility_wavs, ss)


def do_utility_export(
    utility_mode,
    utility_result_mode,
    utility_result_project,
    utility_format,
    utility_bitrate,
    utility_export_name,
    utility_wavs,
    utility_role,
    ss,
):
    """Dispatch shared export presentation to the mode-specific destination."""
    mode = str(utility_mode or "project_role")
    if mode != str(utility_result_mode or ""):
        return None, "❌ 当前音频来自另一种声音来源，请先重新生成"
    if mode == "library_voice":
        return do_quick_tts_export(
            utility_format, utility_bitrate, utility_export_name, utility_wavs
        )
    if not ss or not ss.project or str(ss.project) != str(utility_result_project or ""):
        return None, "❌ 当前项目已变化，请重新生成"
    return do_supplement_export(
        utility_format,
        utility_bitrate,
        utility_export_name,
        utility_wavs,
        utility_role,
        ss,
    )


def open_utility_folder(utility_mode, utility_result_mode, utility_result_project, utility_wavs, ss):
    """Open the selected mode's real export directory with the shared button."""
    mode = str(utility_mode or "project_role")
    if mode != str(utility_result_mode or ""):
        return "❌ 当前音频来自另一种声音来源，请先重新生成"
    if mode == "library_voice":
        return open_quick_tts_folder()
    if not ss or not ss.project or str(ss.project) != str(utility_result_project or ""):
        return "❌ 当前项目已变化，请重新生成"
    return open_supplement_folder(utility_wavs, ss)


def _on_ui_ready_prewarm():
    """Gradio ``app.load`` callback: one-shot background prewarm (fast).

    Gradio fires ``app.load`` only after the UI/server is confirmed usable,
    so prewarm can never race a launch failure (port busy / server startup
    error).  The callback only registers the one-shot request and spawns a
    daemon worker -- it returns immediately and never blocks on the model
    load (the multi-minute load stays inside the runtime process).  A second
    shutdown guard inside the worker guarantees prewarm is skipped when the
    application lifecycle already moved to shutting_down / stopped
    (``prewarm_skipped=application_shutdown``).
    """
    from services.prewarm import PrewarmService

    try:
        message = PrewarmService.request_ui_prewarm()
    except Exception:  # pragma: no cover - prewarm is best effort
        logger.exception("prewarm_event=request_failed")
        return None
    logger.info("prewarm_event=%s", message)
    return None


def play_supplement_preview(which, sup_wavs, ss):
    """P1 试听：which='all' 拼接整段试听；which='seg' 试听首段（逐句入口）。

    Returns:
        音频文件路径（gr.Audio type=filepath）或 None。
    """
    wavs = [w for w in (sup_wavs or []) if w and os.path.isfile(w)]
    if not wavs:
        return None
    if which == "all":
        out = os.path.join(config.get_preview_dir(),
                           f"supplement_preview_{int(time.time() * 1000)}.wav")
        try:
            return _audio_pipeline().export_supplement(paths=wavs, out_path=out, format="wav")
        except Exception:
            return wavs[0]
    # 逐句：返回第一段
    return wavs[0]


def play_lib_voice(choice):
    fp=_lib_path(choice) if choice else None
    return fp if fp and os.path.isfile(fp) else None

def _save_category_choices(cats: list[str] | None) -> list[str]:
    """构建保存分类下拉的可选值：真实分类 ∪ {未分类} + “— 新建 —”占位。

    “未分类”是合法业务值（``voice_lib._category_of`` 对无下划线前缀文件的
    默认分类，同时也是保存时的默认分类）。当音色库只有带前缀文件时
    ``voice_lib.list_categories()`` 不含“未分类”，若此时 value 仍为
    “未分类”，Gradio 会告警 value-not-in-choices，因此必须始终把“未分类”
    放进 choices（去重，不改变已有分类语义）。
    """
    base = [str(c) for c in (cats or []) if str(c)]
    if "未分类" not in base:
        base.insert(0, "未分类")
    return base + ["— 新建 —"]


def save_to_lib(recorded, uploaded, name, category, ss):
    """保存到音色库（业务委托 ProjectService.save_to_lib，支持分类前缀）。"""
    try:
        dest = ProjectService.save_to_lib(recorded, uploaded, name, category=category or "")
    except ValueError as e:
        return str(e), gr.update(), gr.update(), gr.update()
    # 刷新所有依赖音色列表的组件（绑定下拉 + 浏览器 + 试听页换音色 + 分类下拉）
    cats = voice_lib.list_categories()
    return (f"已保存至音色库: {os.path.basename(dest)}",
            gr.update(choices=_lib_voices()),
            gr.update(choices=_lib_voices()),
            gr.update(choices=_save_category_choices(cats), value=category or "未分类"))


def filter_vlib_by_category(category):
    """按分类筛选音色库 → 返回可选音色列表（供绑定区 v_lib 使用）。"""
    return gr.update(choices=voice_lib.voice_names(category or None), value=None)

def open_segments_folder(ss):
    if not ss.project: return "请先打开项目"
    d = ProjectService.get_project_dir(ss.project)
    sd = project_paths.project_dir(d, "segments", create=True)
    from lib.procutil import open_in_folder

    ok = open_in_folder(sd)
    return f"✅ 已打开分段音频目录：`{sd}`" if ok else f"❌ 打开目录失败：`{sd}`"


def refresh_project_storage(ss):
    """Show the active project root and the recursive storage summary."""
    if not ss or not ss.project:
        return "项目目录、存储占用和完整性状态会显示在这里。"
    try:
        return ProjectStorageService.format_summary(ss.project)
    except Exception as exc:
        logger.warning("读取项目存储信息失败: %s", exc)
        return f"#### 项目存储\n❌ 无法读取项目目录：{exc}"


def clear_project_view():
    return (
        "选择一个项目并点击“打开项目”后显示书名、作者、章节、片段和合成进度。",
        "打开项目后显示数据占用和最近修改时间。",
        "<div class='inline-empty'>打开项目后在这里查看章节结构。</div>",
    )


def open_project_folder(ss):
    if not ss or not ss.project:
        return "⚪ 请先打开项目。"
    _ok, message = ProjectStorageService.open_directory(ss.project)
    return ("✅ " if _ok else "❌ ") + message


def clear_project_cache(ss):
    """Clear only the external preview cache; never touch source or audio."""
    if not ss or not ss.project:
        return "⚪ 请先打开项目。"
    try:
        result = ProjectStorageService.clear_preview_cache(ss.project)
        return f"✅ 已清理试听缓存 {result['files']} 个文件（{format_size(result['bytes'])}）；原始文件和音频未受影响。"
    except Exception as exc:
        return f"❌ 清理试听缓存失败：{exc}"


def hide_project_from_list(name, ss):
    """Hide a project from the bookshelf without touching its local files."""
    if not name:
        return gr.update(), "⚪ 请先选择项目。"
    try:
        ProjectStorageService.remove_from_list(name)
        if ss and ss.project == name:
            ss.set_project(None, None, {})
            ss.set_snapshot(None)
            ss.synthesis = None
        return gr.update(choices=ProjectService.scan_projects(), value=None), f"✅ 已仅从项目列表移除「{name}」，本地文件仍保留。"
    except Exception as exc:
        return gr.update(), f"❌ 从项目列表移除失败：{exc}"


def restore_project_to_list(name):
    if not name or not str(name).strip():
        return gr.update(), "⚪ 请输入需要恢复的项目名称。"
    try:
        ProjectStorageService.restore_to_list(str(name).strip())
        return gr.update(choices=ProjectService.scan_projects(), value=str(name).strip()), "✅ 项目已恢复到项目列表。"
    except Exception as exc:
        return gr.update(), f"❌ 恢复项目列表显示失败：{exc}"


def scan_project_cleanup(ss):
    if not ss or not ss.project:
        return "⚪ 请先打开项目。", "", gr.update(visible=False)
    try:
        plan = ProjectStorageService.scan_cleanup(ss.project)
        if not plan["candidates"]:
            return (
                "✅ 当前没有可安全清理的缓存或临时文件。\n\n"
                "不会删除 structured_script.json、原始文件、有效音频或导出文件。",
                plan["token"],
                gr.update(visible=False),
            )
        from collections import Counter

        categories = Counter(item["reason"] for item in plan["candidates"])
        lines = [
            f"### 预计可释放 {format_size(plan['total_bytes'])}",
            f"共 {len(plan['candidates'])} 个文件，确认后才会删除。",
            "",
        ]
        lines.extend(f"- **{reason}**：{count} 个" for reason, count in categories.items())
        lines.extend([
            "",
            "**不会删除**：structured_script.json、原始文件、已生成有效音频、用户手工音频和导出文件。",
        ])
        lines.append("")
        lines.extend(f"- `{item['relative_path']}`：{item['reason']}" for item in plan["candidates"][:30])
        if len(plan["candidates"]) > 30:
            lines.append(f"- … 其余 {len(plan['candidates']) - 30} 项已纳入同一确认令牌")
        return "\n".join(lines), plan["token"], gr.update(visible=True)
    except Exception as exc:
        return f"❌ 扫描失败：{exc}", "", gr.update(visible=False)


def execute_project_cleanup(ss, token):
    if not ss or not ss.project:
        return "⚪ 请先打开项目。", "", gr.update(visible=False)
    try:
        result = ProjectStorageService.execute_cleanup(ss.project, token)
        if result.get("stale"):
            plan = result.get("plan", {})
            if plan.get("candidates"):
                return (
                    "⚠ 文件在确认前发生了变化，已重新扫描。请重新确认这次清理。",
                    plan.get("token", ""),
                    gr.update(visible=True),
                )
            return "✅ 文件已发生变化，当前没有可安全清理的内容。", "", gr.update(visible=False)
        return (
            f"✅ 已清理 {result['removed_files']} 个安全文件，释放 {format_size(result['removed_bytes'])}。",
            "",
            gr.update(visible=False),
        )
    except Exception as exc:
        return f"❌ 执行清理失败：{exc}", "", gr.update(visible=False)


def cancel_project_cleanup():
    return "已取消清理。项目文件没有改变。", "", gr.update(visible=False)


def check_project_integrity(ss):
    if not ss or not ss.project:
        return "⚪ 请先打开项目。", gr.update(visible=False)
    try:
        report = ProjectStorageService.check_integrity(ss.project)
        if report["ok"]:
            return "✅ 项目正常，未发现需要处理的问题。", gr.update(visible=False)
        repairable = sum(1 for issue in report["issues"] if issue.get("repairable"))
        manual = report["issue_count"] - repairable
        lines = [
            f"### 项目存在 {report['issue_count']} 项问题",
            f"- 可自动安全修复：{repairable} 项",
            f"- 需要人工处理：{manual} 项",
        ]
        lines.extend(
            f"- **{issue['severity']} / {issue['code']}**：{issue['message']}"
            for issue in report["issues"][:30]
        )
        lines.extend([
            "",
            "安全修复不会修改 structured_script.json、正常音频、用户手工音频、角色绑定决定或有效导出成品。",
        ])
        return "\n".join(lines), gr.update(visible=bool(repairable))
    except Exception as exc:
        return f"❌ 完整性检查失败：{exc}", gr.update(visible=False)


def repair_project_integrity(ss):
    if not ss or not ss.project:
        return "⚪ 请先打开项目。", gr.update(visible=False)
    try:
        report = ProjectStorageService.repair_integrity(ss.project)
        repaired = report.get("repaired", [])
        if report["ok"]:
            return (
                "✅ 已完成安全修复。" + ("\n" + "\n".join(f"- {item}" for item in repaired) if repaired else ""),
                gr.update(visible=False),
            )
        repairable = any(issue.get("repairable") for issue in report.get("issues", []))
        return (
            f"⚠ 已修复 {len(repaired)} 项；仍有 {report['issue_count']} 项问题，请人工处理剩余项目。",
            gr.update(visible=repairable),
        )
    except Exception as exc:
        return f"❌ 修复失败：{exc}", gr.update(visible=False)


def create_project_backup(ss, target_dir):
    if not ss or not ss.project:
        return "⚪ 请先打开项目。"
    try:
        path = ProjectBackupService.create_backup(ss.project, target_dir or None)
        return f"✅ 项目备份已创建：`{path}`"
    except Exception as exc:
        return f"❌ 创建备份失败：{exc}"


def restore_project_backup(archive_path):
    if not archive_path:
        return "⚪ 请选择项目备份 ZIP。"
    try:
        path = ProjectBackupService.restore_backup(archive_path)
        return f"✅ 项目备份已恢复到：`{path}`；请刷新项目列表后打开。"
    except Exception as exc:
        return f"❌ 恢复备份失败：{exc}"


def refresh_archived_projects():
    """Render the recoverable project list and its stable archive IDs."""
    from datetime import datetime

    archived = ProjectStorageService.list_archived()
    rows = []
    choices = []
    for item in archived:
        timestamp = item.get("archived_at")
        archived_at = (
            datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            if timestamp
            else "未知"
        )
        rows.append([
            item.get("original_name", ""),
            archived_at,
            format_size(item.get("storage_bytes", 0)),
            item.get("archive_id", ""),
        ])
        choices.append((
            f"{item.get('original_name', '')} · {archived_at}",
            item.get("archive_id", ""),
        ))
    return rows, gr.update(choices=choices, value=None), (
        "回收站为空。" if not rows else f"回收站共有 {len(rows)} 个项目。"
    )


def restore_archived_project(archive_id, _ss):
    if not archive_id:
        return gr.update(), "⚪ 请先选择回收站项目。", gr.update(), gr.update()
    try:
        result = ProjectStorageService.restore_archived(archive_id)
        name = result["project_name"]
        rows, choices, _status = refresh_archived_projects()
        return (
            gr.update(choices=ProjectService.scan_projects(), value=name),
            f"✅ 已恢复「{name}」，完整性检查通过；请点击“打开项目”。",
            rows,
            choices,
        )
    except Exception as exc:
        return gr.update(), f"❌ 恢复失败：{exc}", gr.update(), gr.update()


def permanently_delete_archived_project(archive_id, confirmed):
    if not confirmed:
        return gr.update(), gr.update(), "⚠ 永久删除前请勾选二次确认。"
    if not archive_id:
        return gr.update(), gr.update(), "⚪ 请先选择回收站项目。"
    try:
        ProjectStorageService.permanently_delete_archived(archive_id)
        rows, choices, status = refresh_archived_projects()
        return rows, choices, f"✅ 已永久删除回收站项目。{status}"
    except Exception as exc:
        return gr.update(), gr.update(), f"❌ 永久删除失败：{exc}"


def migrate_project_copy(ss, target_root):
    if not ss or not ss.project:
        return "⚪ 请先打开项目。"
    if not target_root or not target_root.strip():
        return "⚠ 请输入迁移目标项目根目录。"
    try:
        path = ProjectStorageService.migrate_to_projects_root(ss.project, target_root.strip())
        return f"✅ 项目已复制并校验：`{path}`；源项目仍保留。"
    except Exception as exc:
        return f"❌ 迁移失败：{exc}"

# ═══════════ O4/O5/O9/O13 新增 handler（仅追加，不触碰既有红线接线） ═══════════

# ── O4：书架 + 章节树 ──
def refresh_bookshelf():
    """刷新书架 Dataframe（返回着色契约 dict，列：项目|章|段进度|状态）。"""
    projects = ProjectService.list_projects()
    rows = [[p["name"], p["chapters"], f"{p['done']}/{p['total']}", p["status"]] for p in projects]
    return df_style.style_dataframe(
        rows,
        df_style.BOOKSHELF_HEADERS,
        status_col=3,
        status_color_map=df_style.STATUS_WORD_COLORS,
    )


def select_project_from_bookshelf(rows, evt: gr.SelectData):
    """点选书架某行 → 回填 p_sel（项目页 Dropdown，唯一项目选择真相源）。"""
    if evt is None or evt.index is None:
        return gr.update()
    try:
        rows = rows["data"] if isinstance(rows, dict) else rows
        name = rows[evt.index[0]][0]
    except Exception:
        return gr.update()
    return gr.update(value=name)


def render_chapter_tree(project):
    """渲染章节折叠树 HTML（O4 右栏）。project 为空返回提示。"""
    if not project:
        return "<i>未打开项目</i>"
    return _pm.build_chapter_tree(project)


def refresh_projects_full():
    """p_refresh 全量刷新：仅刷新 p_sel 选项（书架入口已统一到概览页）。"""
    choices = ProjectService.scan_projects()
    return gr.update(choices=choices)


# ── O5：合成前分段预览 / 勾选 ──
def render_preview(ss):
    """渲染合成前预览 Dataframe + 章节勾选（回填已持久化选择）。

    返回 (预览行, gr.update(章节选项+勾选值))。
    """
    if not ss or not ss.project:
        return [], gr.update(choices=[], value=[])
    snap = _snap(ss)
    script = snap.script
    chapters = script.get("chapters", [])
    chapter_options = [str(ch.get("id")) for ch in chapters]
    chapter_labels = {
        str(ch.get("id")): chapter_identity.chapter_label(ch, index, len(chapters))
        for index, ch in enumerate(chapters)
    }
    # 回填勾选：读 synthesis_selections.json
    sel = _pm.get_synthesis_selections(ss.project)
    saved = sel.get("chapters")
    if saved is not None:
        chosen = [c for c in saved if c in chapter_options]
    else:
        chosen = list(chapter_options)
    mode = str(sel.get("mode") or "all")
    segment_ids = _string_list(sel.get("segment_ids"))
    if mode not in {"all", "chapters", "segments"}:
        mode = "segments" if segment_ids else ("chapters" if saved is not None and set(chosen) != set(chapter_options) else "all")
    rows = _scope_preview_rows(ss, mode, chosen, segment_ids)
    return df_style.style_dataframe(
        rows,
        synth_progress.SCOPE_PREVIEW_HEADERS,
        status_col=5,
        status_color_map=df_style.ICON_COLORS,
    ), gr.update(
        choices=[(chapter_labels.get(c, c), c) for c in chapter_options],
        value=chosen,
    )


# ── O9：音色库浏览 / 搜索 ──
def refresh_voice_lib(search, category):
    """刷新音色库浏览器（Dataframe 行 + 分类下拉选项）。"""
    voices = voice_lib.scan_voice_library(search=search or "", category=category)
    rows = []
    for v in voices:
        rows.append([v["name"], v["category"], v["size_kb"], v["path"]])
    cats = voice_lib.list_categories()
    return df_style.style_dataframe(rows, df_style.VOICE_HEADERS, status_col=None), gr.update(choices=cats, value=category)


def select_voice_from_browser(rows, evt: gr.SelectData):
    """点选音色库某行 → 回填 v_lib（触发既有 v_lib.change 自动试听）+ 喂共享试听器。"""
    if evt is None or evt.index is None:
        return gr.update(), None
    try:
        rows = rows["data"] if isinstance(rows, dict) else rows
        name = rows[evt.index[0]][0]
    except Exception:
        return gr.update(), None
    path = _lib_path(name)
    return gr.update(value=name), (path if path and os.path.isfile(path) else None)


# ── O13：章节级合并试听 ──
def preview_chapter_options(ss):
    """刷新章节合并试听下拉选项。"""
    if not ss or not ss.project:
        return gr.update(choices=[], value=None)
    script = _snap(ss).script
    chapters = script.get("chapters", [])
    opts = [
        (chapter_identity.chapter_label(ch, index, len(chapters)), str(ch.get("id")))
        for index, ch in enumerate(chapters)
    ]
    return gr.update(choices=opts, value=opts[0][1] if opts else None)


def preview_chapter(ss, chapter_id):
    """合并试听单章，并始终返回播放器状态说明。"""
    if not ss or not ss.project or not chapter_id:
        yield _audio_update(None), "⚪ 请先打开项目并选择章节。"
        return
    snapshot = _snap(ss)
    if not snapshot:
        yield _audio_update(None), "⚪ 请先打开项目。"
        return
    yield _audio_update(None), "⏳ 正在生成章节合并试听，请稍候…"
    result = ReviewAudioService.render_chapter_preview(
        ss.project,
        ProjectService.get_project_dir(ss.project),
        snapshot.script,
        chapter_id,
    )
    yield _audio_update(result.path), result.status


# ═══════════ UI ═══════════

# ═══════════ 页面级刷新辅助（打开项目统一链路复用） ═══════════

def refresh_categories():
    """刷新绑定/保存分类下拉（v_bind_category / v_save_category）。"""
    cats = voice_lib.list_categories()
    return (
        gr.update(choices=cats or ["未分类"]),
        gr.update(
            choices=_save_category_choices(cats),
            value="未分类",
        ),
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


def refresh_production_voice_choices():
    """进入生产区时按需刷新临时替换声音，避免启动时重复扫描音色目录。"""
    choices = voice_lib.voice_names()
    return gr.update(choices=choices, value=None), gr.update(choices=choices, value=None)


def refresh_production_check(ss):
    """进入生产阶段时主动展示剧本和角色绑定检查（只提示，不阻断）。"""
    if not ss or not ss.project:
        return "#### 生产检查\n请先打开项目，系统会在这里显示剧本和角色声音状态。"
    try:
        snap = _snap(ss)
        if snap is None:
            return "#### 生产检查\n请先打开项目。"
        # ProjectSnapshot stores the raw structured_script dict; validation
        # expects the parsed Script model used by the loader/service layer.
        errors = script_loader.validate_script(script_loader.from_dict(snap.script))
        roles = snap.script.get("voices", {}) or {}
        missing = [role for role in roles if not snap.bindings.get(role)]
        lines = ["#### 生产检查"]
        if errors:
            lines.append(f"⚠ 剧本需要检查（{len(errors)} 项提示），请先回到项目页确认书稿。")
        else:
            lines.append("✅ 剧本有效")
        try:
            cast_status = VoiceCastResolver.get_voice_binding_status(ss.project)
        except Exception:
            # Snapshot-only callers (including older UI tests and an opening
            # project that has not been persisted yet) still use the raw
            # binding summary below.
            cast_status = {"mode": "legacy_manual"}
        if cast_status.get("mode") == "voice_cast":
            total = int(cast_status.get("roles_total", 0) or 0)
            bound = int(cast_status.get("bound", 0) or 0)
            locked = int(cast_status.get("locked", 0) or 0)
            cast_label = "已锁定" if cast_status.get("cast_locked") else "未完成"
            lines.append(
                f"全书演员表：{cast_label} · 已绑定 {bound}/{total} · "
                f"已锁定正式角色 {locked}"
            )
            if cast_status.get("unbound"):
                lines.append("后续未绑定角色不会阻止当前 scope；请在开始生产前检查所选范围。")
            else:
                lines.append("✅ 全书角色均已绑定；合成中心仍会按当前 scope 显示局部 readiness。")
        else:
            if missing:
                lines.append(
                    f"⚠ {len(missing)} 个角色未绑定声音：{', '.join(format_role_label(r, roles.get(r)) for r in missing)}。"
                )
                lines.append("建议先完成角色声音配置；这里不会阻断你查看队列或质检。")
            else:
                lines.append("✅ 所有角色已绑定声音，可以开始生产。")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("刷新生产检查失败: %s", exc)
        return f"#### 生产检查\n⚠ 状态读取失败：{exc}"


def refresh_export_default_dir(ss):
    """显示当前项目的动态默认导出目录，避免用户猜路径。"""
    if not ss or not ss.project:
        return "项目默认目录：打开项目后显示。留空保存位置即可使用该目录。"
    try:
        project_dir = os.path.normpath(ProjectService.get_project_dir(ss.project))
        output_dir = os.path.normpath(project_paths.project_dir(project_dir, "exports"))
        return f"项目默认目录：`{output_dir}`\n留空保存位置即可导出到该目录。"
    except Exception as exc:
        logger.warning("读取默认导出目录失败: %s", exc)
        return "项目默认目录：暂时无法读取，请打开项目后重试。"


def _dashboard_snapshot(ss):
    """将现有项目快照整理为工作台展示数据。

    这里只读取 ``SessionState`` / ``ProjectSnapshot`` 并决定下一步 UI 文案，不改变
    项目、队列或任何持久化状态；业务操作仍由既有 Service 和 handler 负责。
    """
    if not ss or not ss.project:
        return empty_dashboard_html()

    try:
        snap = _snap(ss)
        if snap is None:
            return empty_dashboard_html()
        script, meta = snap.script, snap.meta
        title = script.get("meta", {}).get("title", ss.project)
        chapters = script.get("chapters", [])
        total_chapters = len(chapters)
        total_segments = getattr(meta, "total_segments", 0)
        completed_segments = getattr(meta, "completed_count", 0)
        statuses = getattr(meta, "segments_status", {}) or {}
        completed_chapters = sum(
            1 for chapter in chapters
            if chapter.get("segments")
            and all(statuses.get(segment.get("id")) == "done" for segment in chapter["segments"])
        )
        roles = script.get("voices", {}) or {}
        role_total = len(roles)
        roles_bound = sum(1 for role in roles if ss.bindings.get(role))

        workflow = WorkflowService.get_state(ss.project)
        stage = str(workflow.get("stage") or "prepared")
        stage_labels = {
            "prepared": "项目已准备",
            "cast_pending": "等待角色声音",
            "ready_for_production": "可以开始生产",
            "producing": "生产进行中",
            "quality_check": "进入质量检查",
            "needs_fix": "需要修复",
            "quality_passed": "质量已通过",
            "exporting": "正在导出",
            "delivered": "已经交付",
        }
        actions = workflow.get("next_actions") or []
        next_action = actions[0] if actions else {}
        next_step = stage_labels.get(stage, stage)
        next_detail = str(
            next_action.get("reason") or "按工作流状态继续下一步。"
        )
        issues: list[tuple[str, str]] = [
            (
                "error" if blocker.get("code") in {
                    "SYNTHESIS_FAILED", "QUALITY_FIX_REQUIRED"
                } else "warning",
                str(blocker.get("message") or blocker.get("code") or ""),
            )
            for blocker in workflow.get("blockers", [])
        ]
        active_task = workflow.get("summary", {}).get("active_production_task")
        quality = workflow.get("summary", {}).get("quality", {})
        task_label = (
            f"生产任务 · {active_task}"
            if active_task else f"工作流 · {stage_labels.get(stage, stage)}"
        )
        task_detail = (
            f"合成 {completed_segments}/{total_segments} 段；"
            f"QA 通过 {quality.get('passed', 0)}，"
            f"待试听确认 {quality.get('needs_review', 0)}，"
            f"需修复 {quality.get('needs_fix', 0)}。"
        )

        return project_dashboard_html(
            title=title,
            project_name=ss.project,
            chapters_done=completed_chapters,
            chapters_total=total_chapters,
            segments_done=completed_segments,
            segments_total=total_segments,
            roles_bound=roles_bound,
            roles_total=role_total,
            task_label=task_label,
            task_detail=task_detail,
            next_step=next_step,
            next_detail=next_detail,
            issues=issues,
        )
    except Exception as exc:
        logger.warning("刷新工作台状态失败: %s", exc)
        return empty_dashboard_html()


def refresh_overview(ss):
    """刷新工作台的项目状态、生产摘要、待办和项目书架。

    书架输出走 **catalog 数据源 + ``ss.catalog_query`` 过滤**（单一状态来源），
    绝不使用 legacy ``refresh_bookshelf`` 覆盖——否则导航离开/返回时会把
    搜索结果刷成全部项目（幽灵状态回归）。
    """
    query = (ss.catalog_query if ss is not None else "") or ""
    return (*_dashboard_snapshot(ss), catalog_ui.render_bookshelf_rows(query))


def refresh_p_sel(name):
    """刷新项目下拉选项（catalog 数据源；选中项不在新 catalog 中则清空）。"""
    choices = [s.project_name for s in ProjectCatalogService.scan()]
    value = name if name in choices else None
    return gr.update(choices=choices, value=value)


def _review_outputs():
    """Return the complete review-page callback contract in one stable order."""
    return [
        e_chapter_table,
        e_chapter_sel,
        e_chapter_audio,
        e_chapter_audio_status,
        e_seg_preview_sel,
        e_seg_regen_sel,
        e_seg_audio,
        e_seg_audio_status,
        e_regenerate_msg,
    ]


def _open_chain_rest(event):
    """把打开项目后的统一刷新接到 event 的 .then 链上（3 入口复用）。

    顺序与原 22 元组全量刷新契约一致，覆盖：顶栏 / 章节表 / 章节试听
    选项 / 队列列表 / 章节树 / 合成预览 / 音色库 / 分类下拉 / 生产检查 /
    默认导出目录 / 概览 / 项目下拉。
    """
    e = event
    e = e.then(refresh_top_status, [ss], [top_status])
    e = e.then(preview_chapters, [ss], _review_outputs())
    e = e.then(preview_chapter_options, [ss], [e_chapter_sel])
    e = e.then(
        refresh_quality_workspace,
        [e_quality_filter, e_chapter_sel, ss],
        [e_quality_summary, e_seg_preview_sel, e_seg_regen_sel, e_segment_quality],
    )
    e = e.then(
        recover_review_repair,
        [ss],
        [e_review_repair_id, e_review_repair_task_id, e_review_repair_project, review_repair_timer],
    )
    e = e.then(refresh_queue_list, [ss], [s_queue_list])
    e = e.then(refresh_production_task, [ss], [s_task_status])
    e = e.then(render_chapter_tree, [p_sel], [p_chapter_tree])
    e = e.then(refresh_project_storage, [ss], [p_storage])
    e = e.then(render_preview, [ss], [s_preview_df, s_chapters_sel])
    e = e.then(
        render_scope_controls,
        [ss],
        [
            s_scope_mode,
            s_chapter_scope_group,
            s_chapters_sel,
            s_segment_scope_group,
            s_segment_chapter_filter,
            s_segments_sel,
            s_segment_selection_state,
            s_preview_df,
            s_scope_readiness,
        ],
    )
    e = e.then(refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category])
    e = e.then(refresh_categories, [], [v_bind_category, v_save_category])
    e = e.then(refresh_production_voice_choices, [], [e_voice, utility_override_voice])
    e = e.then(refresh_production_check, [ss], [production_check])
    e = e.then(refresh_export_default_dir, [ss], [e_save_dir_hint])
    e = e.then(
        refresh_export_readiness,
        [e_fmt, e_qa_policy, ss],
        [e_readiness],
    )
    e = e.then(
        refresh_overview, [ss],
        [ov_status, ov_progress, ov_task, ov_issues, ov_bookshelf],
    )
    e = e.then(refresh_p_sel, [p_sel], [p_sel])
    return e


with gr.Blocks(theme=THEME, title=f"Audiobook Studio v{__version__}") as app:
    # 每会话独立的真相源（取代全局可变 S，多标签不再互相踩状态）
    ss = gr.State(SessionState())

    gr.HTML(LIGHT_CSS)

    # 顶部状态栏（从 ui/shared 抽离）
    shared_components = create_status_bar()
    top_status = shared_components["top_status"]

    with gr.Row():
        # 侧边栏导航按钮（从 ui/navigation 抽离）
        nav = create_nav_buttons()
        # 解包导航按钮（保持变量名兼容接线代码）
        nav_overview = nav["nav_overview"]
        nav_project = nav["nav_project"]
        nav_voices = nav["nav_voices"]
        nav_synth = nav["nav_synth"]
        nav_export = nav["nav_export"]
        nav_create_project = nav["nav_create_project"]
        nav_settings = nav["nav_settings"]

        # ═══ 右侧主工作区 ═══
        with gr.Column(scale=1, elem_classes=["main-area"]) as main_col:

            # ───────── 概览 ─────────
            ov_page = create_overview_page()
            grp_overview = ov_page["group"]
            ov_status = ov_page["ov_status"]
            ov_progress = ov_page["ov_progress"]
            ov_task = ov_page["ov_task"]
            ov_issues = ov_page["ov_issues"]
            ov_bookshelf = ov_page["ov_bookshelf"]
            ov_open = ov_page["ov_open"]
            ov_voices = ov_page["ov_voices"]
            ov_synth = ov_page["ov_synth"]
            ov_export = ov_page["ov_export"]
            bookshelf_search = ov_page["bookshelf_search"]
            bookshelf_selected_proj = ov_page["bookshelf_selected_proj"]
            bookshelf_selected = ov_page["bookshelf_selected"]
            bookshelf_open = ov_page["bookshelf_open"]
            bookshelf_open_dir = ov_page["bookshelf_open_dir"]
            bookshelf_open_audio = ov_page["bookshelf_open_audio"]
            bookshelf_open_delivery = ov_page["bookshelf_open_delivery"]
            bookshelf_backup = ov_page["bookshelf_backup"]
            bookshelf_backup_dir = ov_page["bookshelf_backup_dir"]
            bookshelf_cleanup = ov_page["bookshelf_cleanup"]
            bookshelf_cleanup_confirm = ov_page["bookshelf_cleanup_confirm"]
            bookshelf_cleanup_cancel = ov_page["bookshelf_cleanup_cancel"]
            bookshelf_cleanup_token = ov_page["bookshelf_cleanup_token"]
            bookshelf_storage = ov_page["bookshelf_storage"]
            bookshelf_storage_token = ov_page["bookshelf_storage_token"]
            bookshelf_storage_confirm = ov_page["bookshelf_storage_confirm"]
            bookshelf_storage_cancel = ov_page["bookshelf_storage_cancel"]
            bookshelf_integrity = ov_page["bookshelf_integrity"]
            bookshelf_integrity_repair = ov_page["bookshelf_integrity_repair"]
            bookshelf_archive = ov_page["bookshelf_archive"]
            bookshelf_archive_confirm = ov_page["bookshelf_archive_confirm"]
            bookshelf_msg = ov_page["bookshelf_msg"]
            bookshelf_restore_file = ov_page["bookshelf_restore_file"]
            bookshelf_restore = ov_page["bookshelf_restore"]
            bookshelf_trash_table = ov_page["bookshelf_trash_table"]
            bookshelf_trash_sel = ov_page["bookshelf_trash_sel"]
            bookshelf_trash_refresh = ov_page["bookshelf_trash_refresh"]
            bookshelf_trash_restore = ov_page["bookshelf_trash_restore"]
            bookshelf_trash_confirm = ov_page["bookshelf_trash_confirm"]
            bookshelf_trash_delete = ov_page["bookshelf_trash_delete"]
            bookshelf_trash_status = ov_page["bookshelf_trash_status"]

            # ───────── 新建项目 ─────────
            cr_page = create_create_project_page()
            grp_create_project = cr_page["group"]
            cp_json_name = cr_page["cp_json_name"]
            cp_json_file = cr_page["cp_json_file"]
            cp_json_slot_status = cr_page["cp_json_slot_status"]
            cp_json_cleanup = cr_page["cp_json_cleanup"]
            cp_json_preview = cr_page["cp_json_preview"]
            cp_json_check = cr_page["cp_json_check"]
            cp_json_create = cr_page["cp_json_create"]
            cp_json_result = cr_page["cp_json_result"]

            # ───────── 项目 ─────────
            prj_page = create_project_page()
            grp_project = prj_page["group"]
            p_sel = prj_page["p_sel"]
            p_refresh = prj_page["p_refresh"]
            p_open = prj_page["p_open"]
            p_open_msg = prj_page["p_open_msg"]
            p_summary = prj_page["p_summary"]
            p_chapter_tree = prj_page["p_chapter_tree"]
            p_storage = prj_page["p_storage"]

            # ───────── 音色资产 ─────────
            vce_page = create_voice_page()
            grp_voices = vce_page["group"]
            v_status = vce_page["v_status"]
            v_table = vce_page["v_table"]
            v_role_search = vce_page["v_role_search"]
            v_role_title = vce_page["v_role_title"]
            v_bind_category = vce_page["v_bind_category"]
            v_audio = vce_page["v_audio"]
            v_role = vce_page["v_role"]
            v_lib = vce_page["v_lib"]
            v_current = vce_page["v_current"]
            v_bind = vce_page["v_bind"]
            v_bind_msg = vce_page["v_bind_msg"]
            v_preview_btn = vce_page["v_preview_btn"]
            v_preview_audio = vce_page["v_preview_audio"]
            v_record = vce_page["v_record"]
            v_upload_clone = vce_page["v_upload_clone"]
            v_save_name = vce_page["v_save_name"]
            v_save_category = vce_page["v_save_category"]
            v_save_btn = vce_page["v_save_btn"]
            v_save_msg = vce_page["v_save_msg"]
            v_lib_search = vce_page["v_lib_search"]
            v_lib_category = vce_page["v_lib_category"]
            v_lib_browser = vce_page["v_lib_browser"]

            # ───────── 生产阶段内部导航 ─────────
            production_nav = create_production_navigation()
            grp_production_nav = production_nav["group"]
            production_stage = production_nav["stage"]
            production_check = production_nav["production_check"]

            # ───────── 合成 ─────────
            syn_page = create_synthesis_page()
            grp_synth = syn_page["group"]
            s_task_status = syn_page["s_task_status"]
            s_engine_status = syn_page["s_engine_status"]
            s_preview_df = syn_page["s_preview_df"]
            s_scope_mode = syn_page["s_scope_mode"]
            s_scope_readiness = syn_page["s_scope_readiness"]
            s_chapter_scope_group = syn_page["s_chapter_scope_group"]
            s_segment_scope_group = syn_page["s_segment_scope_group"]
            s_chapters_sel = syn_page["s_chapters_sel"]
            s_segment_chapter_filter = syn_page["s_segment_chapter_filter"]
            s_segments_sel = syn_page["s_segments_sel"]
            s_select_scope_segments = syn_page["s_select_scope_segments"]
            s_clear_scope_segments = syn_page["s_clear_scope_segments"]
            s_select_pending_segments = syn_page["s_select_pending_segments"]
            s_select_failed_segments = syn_page["s_select_failed_segments"]
            s_segment_selection_state = syn_page["s_segment_selection_state"]
            s_log = syn_page["s_log"]
            s_emo = syn_page["s_emo"]
            s_override = syn_page["s_override"]
            s_alpha = syn_page["s_alpha"]
            s_rate = syn_page["s_rate"]
            s_beam = syn_page["s_beam"]
            s_start = syn_page["s_start"]
            s_cancel = syn_page["s_cancel"]
            s_queue_list = syn_page["s_queue_list"]
            s_pause = syn_page["s_pause"]
            s_resume = syn_page["s_resume"]
            s_open_btn = syn_page["s_open_btn"]
            s_open_msg = syn_page["s_open_msg"]

            # 生产启动阶段 1s 轮询：点击「开始合成」后 1 秒内显示真实 startup phase。
            # active 由 tick 自动开关（有活动任务即保持轮询，终态后自动停）。
            s_start_timer = gr.Timer(1.0, active=False)
            s_start_timer.tick(
                refresh_production_task_tick,
                [ss],
                [s_task_status, s_start_timer],
            )
            s_start_timer.tick(refresh_production_engine_status, [ss], [s_engine_status])

            # ───────── 试听与质检 ─────────
            review_page = create_review_page()
            grp_review = review_page["group"]
            e_review_refresh = review_page["e_review_refresh"]
            e_quality_summary = review_page["e_quality_summary"]
            e_chapter_table = review_page["e_chapter_table"]
            e_chapter_sel = review_page["e_chapter_sel"]
            e_chapter_reload = review_page["e_chapter_reload"]
            e_chapter_audio = review_page["e_chapter_audio"]
            e_chapter_audio_status = review_page["e_chapter_audio_status"]
            e_chapter_status = review_page["e_chapter_status"]
            e_seg_preview_sel = review_page["e_seg_preview_sel"]
            e_quality_filter = review_page["e_quality_filter"]
            e_prev = review_page["e_prev"]
            e_next = review_page["e_next"]
            e_seg_regen_sel = review_page["e_seg_regen_sel"]
            e_select_chapter_segments = review_page["e_select_chapter_segments"]
            e_select_filtered_segments = review_page["e_select_filtered_segments"]
            e_clear_segment_selection = review_page["e_clear_segment_selection"]
            e_batch_qa = review_page["e_batch_qa"]
            e_batch_repair = review_page["e_batch_repair"]
            e_seg_sel = review_page["e_seg_sel"]
            e_emo = review_page["e_emo"]
            e_alpha = review_page["e_alpha"]
            e_rate = review_page["e_rate"]
            e_voice = review_page["e_voice"]
            e_regenerate = review_page["e_regenerate"]
            e_seg_audio = review_page["e_seg_audio"]
            e_seg_audio_status = review_page["e_seg_audio_status"]
            e_segment_quality = review_page["e_segment_quality"]
            e_run_qa = review_page["e_run_qa"]
            e_review_status = review_page["e_review_status"]
            e_issue_type = review_page["e_issue_type"]
            e_review_note = review_page["e_review_note"]
            e_mark_review = review_page["e_mark_review"]
            e_mark_passed = review_page["e_mark_passed"]
            e_bulk_pass = review_page["e_bulk_pass"]
            e_bulk_pass_msg = review_page["e_bulk_pass_msg"]
            e_seg_status = review_page["e_seg_status"]
            e_regenerate_msg = review_page["e_regenerate_msg"]
            e_review_repair_id = review_page["e_review_repair_id"]
            e_review_repair_task_id = review_page["e_review_repair_task_id"]
            e_review_repair_project = review_page["e_review_repair_project"]
            review_repair_timer = gr.Timer(1.0, active=False)

            # ───────── 导出 ─────────
            export_page = create_export_page()
            grp_export = export_page["group"]
            e_readiness = export_page["e_readiness"]
            e_readiness_refresh = export_page["e_readiness_refresh"]
            e_fmt = export_page["e_fmt"]
            e_br = export_page["e_br"]
            e_qa_policy = export_page["e_qa_policy"]
            e_save_dir = export_page["e_save_dir"]
            e_save_dir_hint = export_page["e_save_dir_hint"]
            e_go = export_page["e_go"]
            e_out = export_page["e_out"]
            e_path = export_page["e_path"]
            e_open = export_page["e_open"]
            e_export_task_id = export_page["e_export_task_id"]
            e_export_output_dir = export_page["e_export_output_dir"]
            e_subtitle = export_page["e_subtitle"]
            e_subtitle_btn = export_page["e_subtitle_btn"]
            e_subtitle_out = export_page["e_subtitle_out"]
            e_subtitle_msg = export_page["e_subtitle_msg"]

            # ───────── 角色单独补录 / 补合成导出 / 临时配音 ─────────
            supplement_page = create_supplement_page()
            grp_supplement = supplement_page["group"]
            utility_mode = supplement_page["utility_mode"]
            utility_project_group = supplement_page["utility_project_group"]
            utility_role = supplement_page["utility_role"]
            utility_role_refresh = supplement_page["utility_role_refresh"]
            utility_json = supplement_page["utility_json"]
            utility_json_parse = supplement_page["utility_json_parse"]
            utility_split_punct = supplement_page["utility_split_punct"]
            utility_override_voice = supplement_page["utility_override_voice"]
            utility_library_group = supplement_page["utility_library_group"]
            utility_voice = supplement_page["utility_voice"]
            utility_engine = supplement_page["utility_engine"]
            utility_text = supplement_page["utility_text"]
            utility_emotion = supplement_page["utility_emotion"]
            utility_emo_alpha = supplement_page["utility_emo_alpha"]
            utility_rate = supplement_page["utility_rate"]
            utility_quality = supplement_page["utility_quality"]
            utility_synth = supplement_page["utility_synth"]
            utility_status = supplement_page["utility_status"]
            utility_wavs = supplement_page["utility_wavs"]
            utility_result_mode = supplement_page["utility_result_mode"]
            utility_result_project = supplement_page["utility_result_project"]
            utility_preview = supplement_page["utility_preview"]
            utility_audio = supplement_page["utility_audio"]
            utility_export_name = supplement_page["utility_export_name"]
            utility_format = supplement_page["utility_format"]
            utility_bitrate = supplement_page["utility_bitrate"]
            utility_export = supplement_page["utility_export"]
            utility_open_folder = supplement_page["utility_open_folder"]
            utility_save_loc = supplement_page["utility_save_loc"]
            utility_out = supplement_page["utility_out"]
            utility_path = supplement_page["utility_path"]
            # ───────── 设置 ─────────
            set_page = create_settings_page()
            grp_settings = set_page["group"]

    # 填充 _GROUPS（运行时装载，供 navigation._goto 使用）
    _GROUPS[:] = [
        grp_overview,
        grp_create_project,
        grp_project,
        grp_voices,
        grp_production_nav,
        grp_synth,
        grp_review,
        grp_export,
        grp_supplement,
        grp_settings,
    ]

    # The browser is an observer/controller.  A lightweight timer keeps the
    # shared durable task state visible after MCP actions without owning the
    # worker lifecycle.
    s_task_timer = gr.Timer(1.5)

    # ═══════════ 侧边栏导航切换 ═══════════

    s_task_timer.tick(
        refresh_production_task, [ss], [s_task_status]
    ).then(
        refresh_queue_list, [ss], [s_queue_list]
    )
    s_task_timer.tick(refresh_production_engine_status, [ss], [s_engine_status])

    # Export has its own lightweight observer timer.  It only stays active for
    # the task id created by the Export handler and stops on a terminal result.
    e_export_timer = gr.Timer(1.0, active=False)
    e_export_timer.tick(
        refresh_export_status,
        [e_export_task_id, e_export_output_dir, ss],
        [
            e_out,
            e_path,
            e_export_task_id,
            e_export_output_dir,
            e_open,
            e_export_timer,
            e_go,
        ],
    )

    # Review repair observes only the selected durable repair task.  It is
    # deliberately separate from the Production observer and stops on every
    # terminal state, including interrupted/needs_attention.
    review_repair_timer.tick(
        refresh_review_repair_tick,
        [
            e_review_repair_id,
            e_review_repair_task_id,
            e_review_repair_project,
            e_seg_preview_sel,
            e_quality_filter,
            e_chapter_sel,
            ss,
        ],
        [
            e_quality_summary,
            e_seg_preview_sel,
            e_seg_regen_sel,
            e_segment_quality,
            e_seg_audio,
            e_seg_audio_status,
            e_regenerate_msg,
            e_review_repair_id,
            e_review_repair_task_id,
            e_review_repair_project,
            review_repair_timer,
        ],
    )

    # 旧的全量刷新契约（22 元组）已移除（阶段三：open_project 首步 + _open_chain_rest 打开链）

    nav_overview.click(
        lambda: _goto("overview"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-overview')?.classList.add('active'); }").then(
        refresh_overview, [ss], [ov_status, ov_progress, ov_task, ov_issues, ov_bookshelf])
    nav_project.click(
        lambda: _goto("project"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-project')?.classList.add('active'); }")
    nav_create_project.click(
        lambda: _goto("create_project"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-create-project')?.classList.add('active'); }")
    nav_settings.click(
        lambda: _goto("settings"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-settings')?.classList.add('active'); }")
    nav_voices.click(
        lambda: _goto("voices"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-voices')?.classList.add('active'); }").then(
        refresh_role_list,
        [v_role_search, v_role, ss], [v_table]).then(
        refresh_voice_filters,
        [], [v_bind_category, v_lib_category, v_save_category]).then(
        refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category])
    nav_synth.click(
        lambda: _goto("synth"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-synth')?.classList.add('active'); }").then(
        lambda: gr.update(value="synth"), None, [production_stage]).then(
        refresh_production_voice_choices, [], [e_voice, utility_override_voice]).then(
        refresh_production_check, [ss], [production_check]).then(
        preview_chapters, [ss], _review_outputs()).then(
        preview_chapter_options, [ss], [e_chapter_sel]).then(
        refresh_quality_workspace,
        [e_quality_filter, e_chapter_sel, ss],
        [e_quality_summary, e_seg_preview_sel, e_seg_regen_sel, e_segment_quality]).then(
        recover_review_repair,
        [ss],
        [e_review_repair_id, e_review_repair_task_id, e_review_repair_project, review_repair_timer]).then(
        refresh_queue_list, [ss], [s_queue_list]).then(
        refresh_production_task, [ss], [s_task_status]).then(
        refresh_production_engine_status, [ss], [s_engine_status]).then(
        refresh_supplement_roles, [ss], [utility_role]).then(
        refresh_utility_export_hint, [utility_mode, ss], [utility_save_loc]).then(
        lambda: gr.update(choices=_lib_voices(), value=None), None, [utility_voice]).then(
        refresh_quick_tts_engine_info, None, [utility_engine])
    nav_export.click(
        lambda: _goto("export"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-export')?.classList.add('active'); }").then(
        refresh_export_default_dir, [ss], [e_save_dir_hint]).then(
        refresh_export_readiness, [e_fmt, e_qa_policy, ss], [e_readiness]).then(
        refresh_export_status,
        [e_export_task_id, e_export_output_dir, ss],
        [
            e_out,
            e_path,
            e_export_task_id,
            e_export_output_dir,
            e_open,
            e_export_timer,
            e_go,
        ],
    )

    # ── 生产阶段内部导航：合成中心 / 试听质检 / 角色补录 ──
    production_stage.change(_goto, [production_stage], _GROUPS).then(
        refresh_production_check, [ss], [production_check]
    ).then(
        refresh_production_task, [ss], [s_task_status]
    ).then(
        preview_chapters, [ss],
        _review_outputs(),
    ).then(
        preview_chapter_options, [ss], [e_chapter_sel]
    ).then(
        refresh_quality_workspace,
        [e_quality_filter, e_chapter_sel, ss],
        [e_quality_summary, e_seg_preview_sel, e_seg_regen_sel, e_segment_quality],
    ).then(
        recover_review_repair,
        [ss],
        [e_review_repair_id, e_review_repair_task_id, e_review_repair_project, review_repair_timer],
    )

    # ── 概览页：书架点选 → 只设 ss.selected_project（选择≠打开；打开需点按钮） ──
    ov_bookshelf.select(
        catalog_ui.select_bookshelf_row,
        [ov_bookshelf, ss],
        [bookshelf_selected_proj, bookshelf_selected, p_sel],
    )

    # ── 概览页快捷操作：「打开项目」切页 → open_project 首步 → 打开链刷新 ──
    chain = ov_open.click(
        lambda: _goto("project"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-project')?.classList.add('active'); }"    ).then(open_project, [p_sel, ss], [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status])
    _open_chain_rest(chain)
    ov_voices.click(
        lambda: _goto("voices"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-voices')?.classList.add('active'); }").then(
        refresh_role_list,
        [v_role_search, v_role, ss], [v_table]).then(
        refresh_voice_filters,
        [], [v_bind_category, v_lib_category, v_save_category]).then(
        refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category])
    ov_synth.click(
        lambda: _goto("synth"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-synth')?.classList.add('active'); }").then(
        lambda: gr.update(value="synth"), None, [production_stage]).then(
        refresh_production_voice_choices, [], [e_voice, utility_override_voice]).then(
        refresh_production_check, [ss], [production_check]).then(
        preview_chapters, [ss], _review_outputs()).then(
        preview_chapter_options, [ss], [e_chapter_sel]).then(
        refresh_quality_workspace,
        [e_quality_filter, e_chapter_sel, ss],
        [e_quality_summary, e_seg_preview_sel, e_seg_regen_sel, e_segment_quality]).then(
        recover_review_repair,
        [ss],
        [e_review_repair_id, e_review_repair_task_id, e_review_repair_project, review_repair_timer]).then(
        refresh_queue_list, [ss], [s_queue_list]).then(
        refresh_production_task, [ss], [s_task_status]).then(
        refresh_supplement_roles, [ss], [utility_role]).then(
        refresh_utility_export_hint, [utility_mode, ss], [utility_save_loc]).then(
        lambda: gr.update(choices=_lib_voices(), value=None), None, [utility_voice]).then(
        refresh_quick_tts_engine_info, None, [utility_engine])
    ov_export.click(
        lambda: _goto("export"), None, _GROUPS,
        js="(x) => { document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active')); document.getElementById('nav-export')?.classList.add('active'); }").then(
        refresh_export_default_dir, [ss], [e_save_dir_hint]).then(
        refresh_export_readiness, [e_fmt, e_qa_policy, ss], [e_readiness]).then(
        refresh_export_status,
        [e_export_task_id, e_export_output_dir, ss],
        [
            e_out,
            e_path,
            e_export_task_id,
            e_export_output_dir,
            e_open,
            e_export_timer,
            e_go,
        ],
    )

    # ═══════════ events（业务接线，沿用 v2） ═══════════

    # ═══════════ 新建项目页面 ═══════════
    cp_json_file.change(
        create_ui.derive_json_project_name,
        [cp_json_file, cp_json_name],
        [cp_json_name],
    ).then(
        create_ui.inspect_json,
        [cp_json_file, cp_json_name],
        [cp_json_preview, cp_json_slot_status, cp_json_cleanup, cp_json_create],
    )
    cp_json_name.change(
        create_ui.inspect_json,
        [cp_json_file, cp_json_name],
        [cp_json_preview, cp_json_slot_status, cp_json_cleanup, cp_json_create],
    )
    cp_json_check.click(
        create_ui.inspect_json,
        [cp_json_file, cp_json_name],
        [cp_json_preview, cp_json_slot_status, cp_json_cleanup, cp_json_create],
    )
    cp_json_cleanup.click(
        create_ui.archive_orphan_and_recheck,
        [cp_json_name],
        [cp_json_slot_status, cp_json_cleanup],
    ).then(
        create_ui.inspect_json,
        [cp_json_file, cp_json_name],
        [cp_json_preview, cp_json_slot_status, cp_json_cleanup, cp_json_create],
    )
    voice_create_chain = cp_json_create.click(
        create_ui.create_from_json,
        [cp_json_name, cp_json_file, ss],
        [cp_json_result, p_sel],
        concurrency_limit=1,
        concurrency_id="project-creation",
    ).then(
        open_project,
        [p_sel, ss],
        [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status],
    ).then(
        lambda: _goto("voices"), None, _GROUPS,
    )
    voice_create_chain = _open_chain_rest(voice_create_chain)
    voice_create_chain.then(
        refresh_role_list, [v_role_search, v_role, ss], [v_table],
    )
    # 创建项目成功后统一刷新目录类组件（书架 / p_sel / 回收站）
    voice_create_chain.then(
        catalog_ui.refresh_project_catalog,
        [bookshelf_search, p_sel],
        [ov_bookshelf, p_sel, bookshelf_trash_table, bookshelf_trash_sel, bookshelf_trash_status],
    )

    # ═══════════ 设置页面 ═══════════
    wire_settings_page(
        set_page,
        catalog_refresh=(
            catalog_ui.refresh_project_catalog,
            [bookshelf_search, p_sel],
            [ov_bookshelf, p_sel, bookshelf_trash_table, bookshelf_trash_sel, bookshelf_trash_status],
        ),
    )

    # ═══════════ 角色与声音页面 ═══════════
    wire_voice_page(
        vce_page,
        {
            "project": p_sel,
            "session": ss,
            "production_voice": e_voice,
            "callbacks": {
                "select_role_from_list": select_role_from_list,
                "refresh_role_list": refresh_role_list,
                "bind_voice": bind_voice,
                "refresh_role_summary": refresh_role_summary,
                "finalize_voice_cast": finalize_voice_cast_ui,
                "play_lib_voice": play_lib_voice,
                "save_to_lib": save_to_lib,
                "filter_vlib_by_category": filter_vlib_by_category,
                "refresh_voice_lib": refresh_voice_lib,
                "select_voice_from_browser": select_voice_from_browser,
                "preview_bound_voice": preview_bound_voice,
            },
        },
    )

    # ═══════════ 项目书架（概览页）：搜索 / 选择隔离 / 管理动作 / 全局恢复与回收站 ═══════════
    # 书架点选只写 ss.selected_project；「打开项目」是唯一打开入口（委托 app.open_project）。
    catalog_ui.bind_open_project(open_project)
    wire_project_catalog(
        ov_page,
        {
            "session": ss,
            "project_sel": p_sel,
            "groups": _GROUPS,
            "catalog_outputs": [
                ov_bookshelf,
                p_sel,
                bookshelf_trash_table,
                bookshelf_trash_sel,
                bookshelf_trash_status,
            ],
            "callbacks": {
                "open_project": open_project,
                "open_project_outputs": [
                    p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status,
                ],
                "open_chain_rest": _open_chain_rest,
                "goto_project": lambda: _goto("project"),
            },
        },
    )

    p_refresh.click(refresh_projects_full, [], [p_sel])
    chain = p_open.click(open_project, [p_sel, ss], [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status])
    _open_chain_rest(chain)
    s_scope_mode.change(
        update_scope_visibility,
        [s_scope_mode],
        [s_chapter_scope_group, s_segment_scope_group],
    ).then(
        refresh_scope_preview,
        [ss, s_scope_mode, s_chapters_sel, s_segment_chapter_filter, s_segment_selection_state],
        [s_preview_df, s_scope_readiness],
    )
    s_chapters_sel.change(
        refresh_scope_preview,
        [ss, s_scope_mode, s_chapters_sel, s_segment_chapter_filter, s_segment_selection_state],
        [s_preview_df, s_scope_readiness],
    )
    s_segment_chapter_filter.change(
        refresh_segment_filter,
        [ss, s_segment_chapter_filter, s_segment_selection_state],
        [s_segments_sel],
    ).then(
        refresh_scope_preview,
        [ss, s_scope_mode, s_chapters_sel, s_segment_chapter_filter, s_segment_selection_state],
        [s_preview_df, s_scope_readiness],
    )
    s_segments_sel.change(
        merge_segment_selection,
        [s_segments_sel, s_segment_selection_state, s_segment_chapter_filter, ss],
        [s_segments_sel, s_segment_selection_state],
    ).then(
        refresh_scope_preview,
        [ss, s_scope_mode, s_chapters_sel, s_segment_chapter_filter, s_segment_selection_state],
        [s_preview_df, s_scope_readiness],
    )
    for button, handler in (
        (s_select_scope_segments, select_scope_segments),
        (s_clear_scope_segments, clear_scope_segments),
        (s_select_pending_segments, select_pending_scope_segments),
        (s_select_failed_segments, select_failed_scope_segments),
    ):
        button.click(
            handler,
            [s_segment_chapter_filter, s_segment_selection_state, ss],
            [s_segments_sel, s_segment_selection_state],
        ).then(
            refresh_scope_preview,
            [ss, s_scope_mode, s_chapters_sel, s_segment_chapter_filter, s_segment_selection_state],
            [s_preview_df, s_scope_readiness],
        )
    s_start.click(do_synthesis, [ss, s_beam, s_emo, s_override, s_alpha, s_rate, s_chapters_sel, s_scope_mode, s_segment_selection_state], outputs=[s_log, s_queue_list]).then(
        refresh_production_task, [ss], [s_task_status]).then(
        refresh_top_status, [ss], [top_status]).then(
        activate_production_timer, None, s_start_timer)
    s_cancel.click(cancel, [ss], outputs=s_log).then(
        refresh_production_task, [ss], [s_task_status]).then(
        refresh_top_status, [ss], [top_status]).then(
        activate_production_timer, None, s_start_timer)
    s_pause.click(pause_synthesis, [ss], [s_queue_list, s_pause, s_resume]).then(
        refresh_production_task, [ss], [s_task_status])
    s_resume.click(resume_synthesis, [ss], [s_queue_list, s_pause, s_resume]).then(
        refresh_production_task, [ss], [s_task_status])
    s_open_btn.click(open_segments_folder, [ss], s_open_msg)
    e_chapter_sel.change(preview_chapter, [ss, e_chapter_sel],
                         [e_chapter_audio, e_chapter_audio_status]).then(
        refresh_quality_workspace,
        [e_quality_filter, e_chapter_sel, ss],
        [e_quality_summary, e_seg_preview_sel, e_seg_regen_sel, e_segment_quality],
    )
    e_chapter_reload.click(preview_chapter, [ss, e_chapter_sel], [e_chapter_audio, e_chapter_audio_status])
    e_seg_preview_sel.change(
        play_segment, [e_seg_preview_sel, ss], [e_seg_audio, e_seg_audio_status]
    ).then(
        show_segment_quality, [e_seg_preview_sel, ss], [e_segment_quality]
    )
    e_quality_filter.change(
        refresh_quality_workspace,
        [e_quality_filter, e_chapter_sel, ss],
        [e_quality_summary, e_seg_preview_sel, e_seg_regen_sel, e_segment_quality],
    )
    e_select_chapter_segments.click(
        lambda status, chapter, state: select_review_segments(
            "chapter", status, chapter, state
        ),
        [e_quality_filter, e_chapter_sel, ss],
        [e_seg_regen_sel],
    )
    e_select_filtered_segments.click(
        lambda status, chapter, state: select_review_segments(
            "filtered", status, chapter, state
        ),
        [e_quality_filter, e_chapter_sel, ss],
        [e_seg_regen_sel],
    )
    e_clear_segment_selection.click(
        clear_review_segment_selection,
        [e_quality_filter, e_chapter_sel, ss],
        [e_seg_regen_sel],
    )
    e_prev.click(
        lambda choice, status, chapter, state: navigate_review_segment(
            "previous", choice, status, chapter, state
        ),
        [e_seg_preview_sel, e_quality_filter, e_chapter_sel, ss],
        [e_seg_preview_sel, e_segment_quality],
    )
    e_next.click(
        lambda choice, status, chapter, state: navigate_review_segment(
            "next", choice, status, chapter, state
        ),
        [e_seg_preview_sel, e_quality_filter, e_chapter_sel, ss],
        [e_seg_preview_sel, e_segment_quality],
    )
    e_run_qa.click(
        run_selected_technical_qa,
        [e_seg_preview_sel, ss],
        [e_segment_quality, e_quality_summary],
    )
    e_mark_review.click(
        mark_selected_review,
        [
            e_seg_preview_sel, e_review_status, e_issue_type,
            e_review_note, e_quality_filter, e_chapter_sel, ss,
        ],
        [e_segment_quality, e_quality_summary, e_seg_preview_sel],
    )
    e_mark_passed.click(
        mark_selected_passed,
        [
            e_seg_preview_sel, e_issue_type, e_review_note,
            e_quality_filter, e_chapter_sel, ss,
        ],
        [e_segment_quality, e_quality_summary, e_seg_preview_sel],
    )
    e_bulk_pass.click(
        bulk_pass_technical_qa,
        [e_chapter_sel, e_quality_filter, ss, e_seg_regen_sel],
        [
            e_bulk_pass_msg,
            e_quality_summary,
            e_seg_preview_sel,
            e_seg_regen_sel,
            e_segment_quality,
        ],
    )
    e_batch_qa.click(
        batch_technical_qa,
        [e_seg_regen_sel, e_quality_filter, e_chapter_sel, ss],
        [
            e_bulk_pass_msg,
            e_quality_summary,
            e_seg_preview_sel,
            e_seg_regen_sel,
            e_segment_quality,
        ],
    )
    for repair_button in (e_regenerate, e_batch_repair):
        repair_button.click(
            regenerate_segment,
            [
                e_seg_regen_sel, e_emo, e_alpha, e_rate, e_voice, ss,
                e_review_repair_id, e_review_repair_task_id, e_review_repair_project,
                e_quality_filter, e_chapter_sel,
            ],
            [
                e_quality_summary,
                e_seg_preview_sel,
                e_seg_regen_sel,
                e_segment_quality,
                e_seg_audio,
                e_seg_audio_status,
                e_regenerate_msg,
                e_review_repair_id,
                e_review_repair_task_id,
                e_review_repair_project,
                review_repair_timer,
            ],
        )
    e_review_refresh.click(
        preview_chapters, [ss], _review_outputs(),
    ).then(preview_chapter_options, [ss], [e_chapter_sel]).then(
        refresh_quality_workspace,
        [e_quality_filter, e_chapter_sel, ss],
        [e_quality_summary, e_seg_preview_sel, e_seg_regen_sel, e_segment_quality],
    ).then(
        recover_review_repair,
        [ss],
        [e_review_repair_id, e_review_repair_task_id, e_review_repair_project, review_repair_timer],
    )
    e_readiness_refresh.click(
        refresh_export_readiness,
        [e_fmt, e_qa_policy, ss],
        [e_readiness],
    )
    e_fmt.change(
        refresh_export_readiness,
        [e_fmt, e_qa_policy, ss],
        [e_readiness],
    )
    e_qa_policy.change(
        refresh_export_readiness,
        [e_fmt, e_qa_policy, ss],
        [e_readiness],
    )
    e_go.click(
        do_export,
        [e_fmt, e_br, e_save_dir, e_qa_policy, ss],
        [
            e_out,
            e_path,
            e_export_task_id,
            e_export_output_dir,
            e_open,
            e_export_timer,
            e_go,
        ],
    ).then(
        refresh_export_readiness,
        [e_fmt, e_qa_policy, ss],
        [e_readiness],
    )
    e_open.click(
        open_export_location,
        [e_export_task_id, e_export_output_dir, ss],
        [e_path],
    )
    e_subtitle_btn.click(do_export_subtitles, [ss, e_subtitle], [e_subtitle_out, e_subtitle_msg])

    # ── 统一补录 / Quick TTS 操作区 ──
    utility_mode.change(
        reset_utility_mode,
        [utility_mode, ss],
        [
            utility_project_group,
            utility_library_group,
            utility_wavs,
            utility_result_mode,
            utility_result_project,
            utility_audio,
            utility_out,
            utility_path,
            utility_status,
            utility_save_loc,
        ],
    )
    utility_role_refresh.click(
        refresh_supplement_roles, [ss], [utility_role]
    ).then(
        refresh_utility_export_hint,
        [utility_mode, ss],
        [utility_save_loc],
    )
    utility_json_parse.click(
        do_utility_parse_json,
        [utility_json, ss],
        [utility_role, utility_text, utility_split_punct, utility_status],
    )
    utility_synth.click(
        do_utility_tts_synth,
        [
            utility_mode,
            utility_role,
            utility_voice,
            utility_text,
            utility_emotion,
            utility_emo_alpha,
            utility_rate,
            utility_quality,
            utility_split_punct,
            utility_override_voice,
            ss,
        ],
        [utility_wavs, utility_status, utility_result_mode, utility_result_project],
    )
    utility_preview.click(
        play_utility_preview,
        [utility_mode, utility_result_mode, utility_result_project, utility_wavs, ss],
        [utility_audio],
    )
    utility_export.click(
        do_utility_export,
        [
            utility_mode,
            utility_result_mode,
            utility_result_project,
            utility_format,
            utility_bitrate,
            utility_export_name,
            utility_wavs,
            utility_role,
            ss,
        ],
        [utility_out, utility_path],
    ).then(
        refresh_utility_export_hint,
        [utility_mode, ss],
        [utility_save_loc],
    )
    utility_open_folder.click(
        open_utility_folder,
        [utility_mode, utility_result_mode, utility_result_project, utility_wavs, ss],
        [utility_path],
    )

    # ── 后台预热：UI-ready 一次性事件（Gradio app.load）──
    # 只有 Gradio 确认 UI/server 可用后才触发，杜绝「线程+sleep(2s) 猜 UI Ready」
    # 与 launch 快速失败竞态；callback 只做 single-flight 登记 + 启动后台 daemon
    # worker 并立即返回（不阻塞首屏、不加载模型）。worker 执行前再查 application
    # lifecycle guard，确保 shutting_down / stopped 后绝不重启 detached runtime。
    app.load(_on_ui_ready_prewarm)

if __name__ == "__main__":
    os.chdir(BASE)
    from lib.logging_setup import setup_logging
    setup_logging(log_dir=os.path.join(BASE, "logs"))
    # 数据目录外置后，首次启动把程序目录内的旧克隆音色迁移到外置 voice_library（一次性、安全拷贝）。
    config.migrate_legacy_voice_library()

    # ── 应用生命周期：把「应用退出」接通到「Runtime 优雅停机」这一缺失 edge ──
    # 所有退出触发源（Gradio server close / SIGINT / SIGTERM / atexit）统一汇入
    # ApplicationLifecycleService，由它 single-flight 编排 Runtime 关机，避免孤儿进程。
    _lifecycle = get_application_lifecycle()
    _lifecycle.install_process_exit_hooks()

    # Gradio 默认只允许 serve 当前 cwd 与 tempdir 下的文件。数据目录（音色库、预览、
    # 合成产物、导出）已全部外置到 config.get_data_dir()（如 D:\AudiobookStudio），
    # 不在 cwd 内，返回其下音频路径给 Audio/File 组件会在序列化阶段触发 InvalidPathError
    # 导致前端显示「错误」。将其加入 allowed_paths 白名单，递归放行其下所有子目录。
    try:
        app.queue().launch(server_name="0.0.0.0", server_port=7862, share=False, inbrowser=True,
                           allowed_paths=[config.get_data_dir()])
    finally:
        # The Gradio server has stopped (normal close / Ctrl+C / exception):
        # guarantee the production runtime is shut down before the process
        # exits. 这是最可靠的一条 edge；single-flight 使其与上面的
        # signal / atexit 触发相互幂等。
        _lifecycle.request_application_shutdown("gradio_server_stop")
