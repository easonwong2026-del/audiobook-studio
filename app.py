#!/usr/bin/env python3
"""Audiobook Studio UI -- 有声书生产工作台。

本次重构把模块式导航改为「工作台 → 项目 → 角色与声音 → 生产与质检 → 交付」
的生产流程。页面 Builder 负责布局，既有 handler 继续委托给 Service；不改变 TTS、
队列、持久化或数据协议。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import gradio as gr

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import __version__, config, script_loader, segment_cache, voice_lib
from lib import dataframe_style as df_style
from lib import progress as synth_progress
from lib import project_manager as _pm
from services import (
    ExportService,
    ProjectService,
    SupplementService,
    SupplementTaskState,
    SynthesisService,
)
from services.session import SessionState
from services.speaker_review_service import SpeakerReviewService
from services.synthesis import SynthesisState
from services.v4_export import V4ExportService
from services.v4_project_service import V4ProjectService
from services.v4_quality_service import V4QualityService
from services.v4_synthesis_service import V4SynthesisService
from services.v4_voice_service import V4VoiceService
from ui import create_project_handlers as create_ui
from ui import settings_handlers as settings_ui
from ui import v4_workspace_handlers as v4_ui
from ui.components import (
    build_role_management_choices,
    build_v4_role_management_choices,
    create_production_navigation,
    empty_dashboard_html,
    format_bound_role_choices,
    format_role_label,
    format_role_management_summary,
    project_dashboard_html,
)
from ui.navigation import _GROUPS, _goto, activate_js, create_nav_buttons
from ui.pages import (
    create_create_project_page,
    create_export_page,
    create_overview_page,
    create_project_page,
    create_review_page,
    create_settings_page,
    create_supplement_page,
    create_synthesis_page,
    create_v4_role_page,
    create_v4_workspace_page,
    create_voice_page,
)
from ui.shared import create_status_bar
from ui.theme import LIGHT_CSS, THEME
from ui.wiring.settings_wiring import wire_settings_page
from ui.wiring.voice_wiring import wire_voice_page

BASE = os.path.dirname(os.path.abspath(__file__))
# 音色库外置于数据目录（默认 ~/AudiobookStudio/voice_library），与程序目录解耦。
# 注意：音色库路径必须在调用时动态解析（config.get_voice_library），
# 不得在此处模块级缓存，否则运行期切换数据目录后路径不会更新（见方案 §5.2）。


def _tts_engine():
    """按需加载数值计算与 TTS 适配层，缩短 UI 首次构建路径。"""
    from lib import tts_engine
    return tts_engine


def _audio_pipeline():
    """按需加载音频后处理模块，仅在试听、修复或导出时付出开销。"""
    from lib import audio_pipeline
    return audio_pipeline




# ═══════════ callbacks (unchanged logic, 业务编排迁入 services) ═══════════

def create_project(name, script_file, ss):
    import json as _json
    if not name or not script_file:
        return name, None, "### ⚠ 请输入项目名称并上传 JSON 文件", gr.update()
    try:
        # B12: 先在导入阶段校验剧本，避免非法剧本在合成中途 KeyError 崩溃
        script = script_loader.load_script(script_file)
        errors = script_loader.validate_script(script)
        if errors:
            err_msg = "### ❌ 剧本校验失败：\n" + "\n".join(f"- {e}" for e in errors)
            return name, None, err_msg, gr.update()
        # 业务委托 ProjectService（写 workspace + 写 project.json）
        ProjectService.create_project(name, script_file)
        # 写入会话态（多标签各自独立，不共享全局可变 S）
        ss.set_project(name, None, {})
        return "", None, f"### ✅ 项目「{name}」创建成功！请在右侧下拉框选中它，点击「打开项目」", gr.update(choices=project_choices())
    except _json.JSONDecodeError:
        # 文件不是合法 JSON（如用户传了 TXT 改名）：给出明确、可操作的提示
        return name, None, (
            "### ❌ 创建失败：上传的文件不是合法 JSON。\n"
            "请确认上传的是由 WorkBuddy 生成的 `structured_script.json`，"
            "而非 .txt / .md 等文本文件改名而来。"
        ), gr.update()
    except Exception as e:
        return name, None, f"### ❌ 创建失败: {e}", gr.update()


def _snap(ss):
    """读取（必要时重建）当前项目快照：优先用会话态快照，缺失时按项目名重建。

    V4 项目没有 V3 快照，直接返回 None（各页面按 ``getattr(ss, "is_v4", False)`` 走 V4 分支）。
    """
    if ss is not None and getattr(ss, "is_v4", False):
        return None
    s = ss.ensure_snapshot() if ss is not None else None
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
    # V4 项目：走统一服务（V4ProjectService），其余逻辑与 V3 一致由页面处理
    if V4ProjectService.detect_format(name) == "v4":
        return _open_v4_project(name, ss)
    try:
        # 业务委托 ProjectService.open_project_as_snapshot（包 pm.load_snapshot）
        snap = ProjectService.open_project_as_snapshot(name)
        ss.set_project(name, snap.script, snap.bindings)
        ss.set_snapshot(snap)
        roles = list(snap.script.get("voices",{}).keys())
        vcount = len(roles)
        bound = sum(1 for v in ss.bindings.values() if v)

        info = f"""### 🎧 {snap.script['meta'].get('title',name)}
<div style="display:flex;gap:20px;margin-top:8px">
<span>📄 **{snap.meta.total_chapters}** 章</span>
<span>🎯 **{vcount}** 角色（{bound} 已绑定）</span>
<span>✅ **{snap.meta.completed_count}** 段已合成</span>
</div>"""
        if snap.meta.failed_count: info += f"\n<span class='status-err'>⚠ {snap.meta.failed_count} 段失败</span>"

        seg_dir = os.path.join(ProjectService.get_project_dir(name),"segments")
        existing = scan_existing_raw(snap, seg_dir)
        log_init = "\n".join(existing[-15:]) if existing else "等待音色配置完成后开始合成..."

        role_choices = build_role_management_choices(snap.script, ss.bindings)

        return (info,
        gr.update(choices=role_choices, value=None),
                None,
                "### 当前角色配置\n请从左侧角色列表选择角色。",
                gr.update(choices=_lib_voices(),value=None),
                log_init,
                format_role_management_summary(snap.script, ss.bindings))
    except Exception as e:
        return (
            f"### 打开失败\n{e}", gr.update(), None,
            "### 当前角色配置\n请从左侧角色列表选择角色。",
            gr.update(), "", "打开项目后显示角色绑定状态.",
        )


def _open_v4_project(name, ss):
    """打开 V4 项目：统一上下文入会话，返回与 V3 打开一致的角色管理 7 元组。"""
    try:
        context = V4ProjectService.open_project(name)
        if context is None or not context.is_v4:
            raise ValueError("项目不存在或不是 V4 格式")
        ss.set_v4_project(name, context.script, context.speakers)
        from services.v4_progress import V4ProgressService

        script = context.script
        source = (context.project_path / "source/source.txt").read_text(
            encoding="utf-8"
        )
        unresolved_rows = SpeakerReviewService.unresolved_rows(source, script)
        unresolved = len(unresolved_rows)
        plan = context.production.load_plan()
        progress = V4ProgressService.from_project(
            context.project_path,
            list(script.chapters),
            plan_revision=plan.revision if plan else None,
        )
        segment_total = progress.segments_total
        title = (
            context.manifest.title if context.manifest is not None else name
        )
        completed = progress.segments_done
        info = f"""### 📚 {title}
<div style="display:flex;gap:20px;margin-top:8px">
<span>📄 **{len(script.chapters)}** 章</span>
<span>🧩 **{segment_total}** 片段</span>
<span>🔎 **{unresolved}** 待确认角色</span>
<span>✅ **{completed}** 段已合成</span>
</div>"""
        speaker_choices = _v4_role_choices(ss)
        log_init = (
            "V4 项目：请在「③ 角色与声音」确认角色与音色，"
            "然后在「④ 生产与质检」生成计划并合成。"
        )
        return (
            info,
            gr.update(choices=speaker_choices, value=None),
            None,
            "### 当前角色配置\n请从左侧角色列表选择角色。",
            gr.update(choices=_lib_voices(), value=None),
            log_init,
            format_v4_role_summary(ss),
        )
    except Exception as exc:  # noqa: BLE001 - 打开失败转为用户可读消息
        logger.warning("打开 V4 项目失败: %s", exc)
        return (
            f"### 打开失败\n{exc}", gr.update(), None,
            "### 当前角色配置\n请从左侧角色列表选择角色。",
            gr.update(), "", "打开项目后显示角色绑定状态。",
        )


def migrate_v3_to_v4(name):
    if not name:
        return "请先选择要迁移的 V3 项目。", gr.update()
    if V4ProjectService.detect_format(name) != "v3":
        return f"「{name}」不是 V3 项目（V4 项目无需迁移）。", gr.update()
    try:
        result = V4ProjectService.migrate_to_v4(name)
        msg = (
            f"✅ 已复制迁移到 `{result.project_path.name}`（V3 原项目保持不变）"
        )
        if result.reused_existing:
            msg += "（复用上次迁移结果）"
        msg += f"\n备份：`{result.backup_path}`"
        return msg, gr.update(
            choices=project_choices(), value=result.project_path.name
        )
    except Exception as exc:  # noqa: BLE001 - 用户可读错误
        logger.warning("V3 → V4 迁移失败: %s", exc)
        return f"❌ 迁移失败：{exc}", gr.update()


def format_v4_role_summary(ss):
    """V4 角色绑定计数；具体状态只显示在同一张角色卡片中。"""
    if ss is None or not getattr(ss, "is_v4", False) or ss.speakers_v4 is None:
        return "打开 V4 项目后显示角色状态。"
    from repositories.production_repository import ProductionRepository
    from services.v4_analysis_validity import (
        DIALOGUE_COVERAGE_UNKNOWN_LABEL,
        REASON_MESSAGES,
        ReasonCode,
    )

    project_path = V4ProjectService.root() / ss.project
    try:
        production = ProductionRepository(project_path)
        voices, _p, _pr, _profile = production.load_inputs()
    except (OSError, KeyError, TypeError, ValueError):
        voices = None
    total = len(ss.speakers_v4.speakers)
    bound = sum(
        1
        for item in ss.speakers_v4.speakers
        if voices is not None and item.speaker_id in voices.bindings
    )
    text = f"共 **{total}** 个角色 · **{bound}** 已绑定 · **{total - bound}** 待绑定"
    try:
        from repositories.v4_analysis_repository import V4AnalysisRepository

        state = V4AnalysisRepository(project_path).load(ss.script.source_sha256)
        summary = (state or {}).get("summary") or {}
        if state and state.get("status") == "waiting_for_ai":
            text += "\n\n⚠ AI 尚未配置。配置后点击「继续 AI 分析」。"
        elif summary:
            status = state.get("status") if state else ""
            status_label = (
                "AI 分析完成" if status == "completed" else f"AI 分析：{status or '未知'}"
            )
            coverage = summary.get("dialogue_coverage")
            coverage_text = (
                f"{coverage * 100:.0f}%"
                if coverage is not None
                else DIALOGUE_COVERAGE_UNKNOWN_LABEL
            )
            text += (
                f"\n\n{status_label} · "
                f"识别角色：{summary.get('identified_characters', total)} · "
                f"自动确认：{summary.get('auto_confirmed_characters', 0)} · "
                f"需要确认：{summary.get('needs_review_characters', 0) + summary.get('dialogue_unresolved', 0)} · "
                f"已过滤噪音：{summary.get('filtered_noise', 0)} · "
                f"对白自动归属：{coverage_text}"
            )
            reason_codes = ((state or {}).get("validity") or {}).get("reason_codes") or []
            reason_lines: list[str] = []
            for code_value in reason_codes:
                code = ReasonCode.from_value(code_value)
                if code is None or code == ReasonCode.OK:
                    continue
                reason_lines.append(REASON_MESSAGES.get(code, ""))
            if reason_lines:
                text += "\n\n" + "\n".join(
                    f"⚠ {line}" for line in reason_lines if line
                )
    except Exception:  # noqa: BLE001 - summary is supplementary UI
        pass
    return text


def format_role_summary(ss):
    """Return the single shared binding summary for either project format."""
    if ss is None or not getattr(ss, "project", None):
        return "打开项目后显示角色绑定状态。"
    if getattr(ss, "is_v4", False):
        return format_v4_role_summary(ss)
    snapshot = _snap(ss)
    if snapshot is None:
        return "打开项目后显示角色绑定状态。"
    return format_role_management_summary(snapshot.script, snapshot.bindings)




def refresh_top_status(ss):
    """O11：刷新顶部全局状态栏文本（项目 / 章节 / 进度 / 引擎加载状态）。"""
    if not ss or not ss.project:
        return "*等待打开项目…*"
    try:
        if getattr(ss, "is_v4", False):
            from repositories.production_repository import ProductionRepository
            from services.v4_progress import V4ProgressService

            script = ss.script
            try:
                with (V4ProjectService.root() / ss.project / "project.json").open(
                    "r", encoding="utf-8"
                ) as handle:
                    title = json.load(handle).get("title") or ss.project
            except (OSError, json.JSONDecodeError):
                title = ss.project
            project_path = V4ProjectService.root() / ss.project
            plan = ProductionRepository(project_path).load_plan()
            progress = V4ProgressService.from_project(
                project_path,
                list(script.chapters),
                plan_revision=plan.revision if plan else None,
            )
            return (
                f"📖 **{title}** · {progress.chapters_total} 章 · "
                f"{progress.segments_done}/{progress.segments_total} 段 · "
                f"引擎: {'已加载' if getattr(sys.modules.get('lib.tts_engine'), '_tts', None) is not None else '未加载'}"
            )
        snap = _snap(ss)
        if snap is None:
            meta, script, _ = ProjectService.open_project(ss.project)
        else:
            meta, script = snap.meta, snap.script
        chapters = len(script.get("chapters", []))
        done = getattr(meta, "completed_count", 0)
        total = getattr(meta, "total_segments", 0)
        title = script.get("meta", {}).get("title", ss.project)
        engine_module = sys.modules.get("lib.tts_engine")
        engine_state = "已加载" if getattr(engine_module, "_tts", None) is not None else "未加载"
        return (f"📖 **{title}** · {chapters} 章 · {done}/{total} 段 · "
                f"引擎: {engine_state}")
    except Exception as exc:
        return f"📖 {ss.project}（状态读取失败：{exc}）"

def delete_project(name):
    if name: ProjectService.delete_project(name)
    return gr.update(choices=project_choices())


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


def _project_path(name):
    """根据项目名解析项目目录（V3 用 ProjectService，V4 用统一服务 root）。"""
    if V4ProjectService.detect_format(name) == "v4":
        return V4ProjectService.root() / name
    return Path(ProjectService.get_project_dir(name))


def project_dir_markup(name):
    """已打开项目时显示项目目录路径与「打开项目目录」按钮可见性。"""
    if not name:
        return "项目目录：未打开项目", gr.update(visible=False)
    path = _project_path(name)
    return f"项目目录：`{path}`", gr.update(visible=True)


def open_project_dir(name):
    """在资源管理器中打开当前项目目录。"""
    if not name:
        return "请先打开项目。"
    path = _project_path(name)
    os.makedirs(path, exist_ok=True)
    try:
        os.startfile(str(path))
    except OSError as exc:
        logger.warning("打开项目目录失败: %s", exc)
        return f"❌ 打开项目目录失败：{exc}"
    return f"✅ 已打开项目目录：`{path}`"

def refresh_role_list(search, current_role, ss):
    """按搜索词刷新角色管理列表，同时保留仍可见的当前角色。"""
    if not ss or not ss.project:
        return gr.update(choices=[], value=None)
    if getattr(ss, "is_v4", False):
        choices = _v4_role_choices(ss)
        if search:
            choices = [(label, value) for label, value in choices if search in label]
        selected = (
            current_role
            if current_role in {value for _, value in choices}
            else None
        )
        return gr.update(choices=choices, value=selected)
    snap = _snap(ss)
    if not snap:
        return gr.update(choices=[], value=None)
    choices = build_role_management_choices(snap.script, snap.bindings, search)
    selected = current_role if any(value == current_role for _, value in choices) else None
    return gr.update(choices=choices, value=selected)


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
    if getattr(ss, "is_v4", False):
        bound_audio = None
        try:
            from repositories.production_repository import ProductionRepository

            production = ProductionRepository(
                V4ProjectService.root() / ss.project
            )
            voices, _p, _pr, _profile = production.load_inputs()
            binding = voices.bindings.get(role)
            if binding is not None:
                bound_audio = (
                    V4ProjectService.root() / ss.project / binding.voice_id
                )
        except Exception:  # noqa: BLE001
            bound_audio = None
        current = (
            f"当前绑定音频：{os.path.basename(str(bound_audio))}"
            if bound_audio and bound_audio.is_file()
            else "当前绑定音频：未选择"
        )
        return (
            role,
            _v4_role_config_title(ss, role),
            gr.update(value=str(bound_audio) if bound_audio and bound_audio.is_file() else None),
            gr.update(value=None),
            f"*{current}*",
            None,
            "",
        )
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
        return (
            "请先从左侧角色列表选择角色", gr.update(), gr.update(), role,
            gr.update(), gr.update(), format_role_summary(ss),
        )
    src = _lib_path(from_lib) if from_lib else audio_file
    if not src:
        return (
            "请上传音频、录制或从音色库选择", gr.update(), gr.update(), role,
            gr.update(), gr.update(), format_role_summary(ss),
        )
    if getattr(ss, "is_v4", False):
        project_path = V4ProjectService.root() / ss.project
        ok, message = V4VoiceService.bind_voice(project_path, role, src)
        if not ok:
            return (
                message, gr.update(), gr.update(), role, gr.update(), gr.update(),
                format_role_summary(ss),
            )
        # 刷新会话角色文档（绑定后 voices.json 已更新）
        context = V4ProjectService.open_project(ss.project)
        if context is not None:
            ss.set_v4_project(ss.project, context.script, context.speakers)
        speaker_name = _v4_speaker_name(ss, role)
        return (
            f"✅ {speaker_name or role} 已绑定",
            gr.update(
                choices=_v4_role_choices(ss), value=role
            ),
            gr.update(),
            role,
            _v4_role_config_title(ss, role),
            f"*当前绑定音频：{os.path.basename(str(src))}*",
            format_role_summary(ss),
        )
    # 业务委托 ProjectService.bind_voice（拷贝 + 写 voice_bindings.json），返回 dest
    cat = voice_lib._category_of(os.path.basename(src)) if from_lib else "未分类"
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
        format_role_summary(ss),
    )


def unbind_voice(role, ss):
    """Remove the selected role's binding while retaining the source audio asset."""
    if not ss or not ss.project or not role:
        return (
            "请先从左侧角色列表选择角色", gr.update(), gr.update(), role,
            gr.update(), gr.update(), format_role_summary(ss),
        )
    if getattr(ss, "is_v4", False):
        ok, message = V4VoiceService.unbind_voice(
            V4ProjectService.root() / ss.project, role
        )
        if not ok:
            return (
                message, gr.update(), gr.update(), role, gr.update(), gr.update(),
                format_role_summary(ss),
            )
        context = V4ProjectService.open_project(ss.project)
        if context is not None:
            ss.set_v4_project(ss.project, context.script, context.speakers)
        return (
            f"✅ {_v4_speaker_name(ss, role) or role} 已解除绑定",
            gr.update(choices=_v4_role_choices(ss), value=role),
            gr.update(), role, _v4_role_config_title(ss, role),
            "*当前绑定音频：未选择*", format_role_summary(ss),
        )
    removed = ProjectService.unbind_voice(ss.project, role)
    if not removed:
        return (
            "该角色当前没有绑定音色", gr.update(), gr.update(), role,
            gr.update(), gr.update(), format_role_summary(ss),
        )
    snapshot = ProjectService.open_project_as_snapshot(ss.project)
    ss.set_snapshot(snapshot)
    ss.bindings = snapshot.bindings
    voice = snapshot.script.get("voices", {}).get(role, {})
    return (
        f"{format_role_label(role, voice)} 已解除绑定",
        gr.update(
            choices=build_role_management_choices(snapshot.script, ss.bindings),
            value=role,
        ),
        gr.update(), role, _role_config_title(role, voice, None),
        "*当前绑定音频：未选择*", format_role_summary(ss),
    )


def _v4_role_choices(ss):
    """V4 角色卡片选项；显示名只展示，稳定 speaker_id 作为真实值。"""
    if ss is None or ss.speakers_v4 is None:
        return []
    from repositories.production_repository import ProductionRepository

    try:
        production = ProductionRepository(V4ProjectService.root() / ss.project)
        voices, _p, _pr, _profile = production.load_inputs()
        bindings = voices.bindings
    except Exception:  # noqa: BLE001 - 卡片刷新不能阻断页面
        bindings = {}
    stats = _v4_speaker_stats(ss)
    return build_v4_role_management_choices(ss.speakers_v4.speakers, bindings, stats)


def _v4_speaker_stats(ss):
    """Build the compact role-card facts without changing the V4 data model."""
    if ss is None or not getattr(ss, "is_v4", False) or ss.script is None:
        return {}
    counts = {item.speaker_id: 0 for item in ss.speakers_v4.speakers}
    for chapter in ss.script.chapters:
        for segment in chapter.segments:
            if segment.kind == "dialogue" and segment.speaker_id in counts:
                counts[segment.speaker_id] += 1
    confidence = {}
    persisted_cards = {}
    try:
        from repositories.character_candidates_repository import (
            CharacterCandidatesRepository,
        )
        from repositories.v4_analysis_repository import V4AnalysisRepository

        state = V4AnalysisRepository(
            V4ProjectService.root() / ss.project
        ).load(ss.script.source_sha256)
        persisted_cards = ((state or {}).get("summary") or {}).get("character_cards") or {}
        candidates = CharacterCandidatesRepository(
            V4ProjectService.root() / ss.project
        ).load(ss.script.source_sha256)
        for candidate in candidates.candidates:
            if getattr(candidate, "status", "confirmed") != "confirmed":
                continue
            for speaker in ss.speakers_v4.speakers:
                if candidate.display_name in [speaker.display_name, *speaker.aliases]:
                    confidence[speaker.speaker_id] = max(
                        confidence.get(speaker.speaker_id, 0.0), candidate.confidence
                    )
    except Exception:  # noqa: BLE001 - cards should still render
        pass
    return {
        speaker.speaker_id: {
            **persisted_cards.get(speaker.speaker_id, {}),
            "importance": persisted_cards.get(speaker.speaker_id, {}).get(
                "importance",
                "主要角色" if counts.get(speaker.speaker_id, 0) >= 3 else "次要角色",
            ),
            "dialogue_count": persisted_cards.get(speaker.speaker_id, {}).get(
                "dialogue_count", counts.get(speaker.speaker_id, 0)
            ),
            "confidence": persisted_cards.get(speaker.speaker_id, {}).get(
                "confidence",
                speaker.confidence
                if speaker.confidence is not None
                else confidence.get(speaker.speaker_id, 1.0),
            ),
            "status": (
                "unknown"
                if speaker.status != "confirmed"
                and getattr(speaker, "review_status", "confirmed") == "confirmed"
                else getattr(speaker, "review_status", "confirmed")
            ),
            "candidate_reason": getattr(speaker, "candidate_reason", None),
        }
        for speaker in ss.speakers_v4.speakers
    }


def _v4_speaker_name(ss, speaker_id):
    if ss is None or ss.speakers_v4 is None:
        return None
    for item in ss.speakers_v4.speakers:
        if item.speaker_id == speaker_id:
            return item.display_name
    return None


def _v4_role_config_title(ss, speaker_id):
    """V4 右侧当前角色标题（含绑定状态）。"""
    name = _v4_speaker_name(ss, speaker_id)
    if not name:
        return "### 当前角色配置\n请从左侧角色列表选择角色。"
    from repositories.production_repository import ProductionRepository

    try:
        production = ProductionRepository(V4ProjectService.root() / ss.project)
        voices, _p, _pr, _profile = production.load_inputs()
        bound = speaker_id in voices.bindings
    except Exception:  # noqa: BLE001
        bound = False
    speaker = next(
        (item for item in ss.speakers_v4.speakers if item.speaker_id == speaker_id),
        None,
    )
    review_status = getattr(speaker, "review_status", "confirmed") if speaker else "confirmed"
    if speaker and speaker.status != "confirmed" and review_status == "confirmed":
        review_status = "unknown"
    review_label = {
        "confirmed": "✅ 已确认",
        "candidate": "🟡 AI 候选 · 需要确认",
        "rejected": "⛔ 已拒绝",
        "unknown": "⚪ 未知说话人 · 待确认",
    }.get(review_status, "⚠ 待确认")
    binding_label = "✅ 已绑定" if bound else "⚠ 待绑定"
    reason = (
        f"\n候选理由：{speaker.candidate_reason}"
        if speaker and speaker.candidate_reason
        else ""
    )
    return f"### 当前角色：{name}\n{review_label} · {binding_label}{reason}"


def _wire_v4_role_controls(page: dict) -> None:
    """Wire one advanced-role panel, whether embedded or debug-only."""
    outputs = [
        page["summary"], page["unresolved_table"], page["assign_speaker"],
        page["merge_source"], page["merge_target"], page["lock_speaker"],
        page["alias_speaker"], page["candidates_table"], page["candidate"],
        page["candidate_target"],
    ]

    page["refresh"].click(
        lambda: gr.update(choices=v4_ui.scan_v4_projects()),
        None,
        [page["project"]],
    )
    page["open"].click(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    ).then(
        v4_ui.v4_chapter_choices,
        [page["project"]],
        [page["chapter"]],
    )
    page["analyze_chapter_btn"].click(
        v4_ui.analyze_v4_chapter,
        [page["project"], page["chapter"]],
        [page["chapter_msg"]],
        concurrency_limit=1,
        concurrency_id="v4-analysis",
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    ).then(
        v4_ui.v4_chapter_choices,
        [page["project"]],
        [page["chapter"]],
    )
    page["reanalyze_chapter_btn"].click(
        v4_ui.reanalyze_v4_chapter,
        [page["project"], page["chapter"]],
        [page["chapter_msg"]],
        concurrency_limit=1,
        concurrency_id="v4-analysis",
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    ).then(
        v4_ui.v4_chapter_choices,
        [page["project"]],
        [page["chapter"]],
    )
    page["view_chapter_btn"].click(
        v4_ui.view_v4_chapter_script,
        [page["project"], page["chapter"]],
        [page["script_view"]],
    )
    page["chapter_plan_btn"].click(
        v4_ui.generate_v4_chapter_plan_message,
        [page["project"], page["chapter"]],
        [page["chapter_plan_msg"]],
    )
    page["synthesize_chapter_btn"].click(
        v4_ui.synthesize_v4_chapter,
        [page["project"], page["chapter"]],
        [page["chapter_plan_msg"]],
        concurrency_limit=1,
        concurrency_id="v4-synthesis",
    )
    page["reanalyze_btn"].click(
        v4_ui.reanalyze_v4_project,
        [page["project"]],
        [page["reanalyze_msg"]],
        concurrency_limit=1,
        concurrency_id="v4-analysis",
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["extract_btn"].click(
        v4_ui.extract_v4_characters,
        [page["project"]],
        [page["route_msg"]],
        concurrency_limit=1,
        concurrency_id="v4-character-extraction",
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["route_btn"].click(
        v4_ui.route_v4_speakers,
        [page["project"]],
        [page["route_msg"]],
        concurrency_limit=1,
        concurrency_id="v4-routing",
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["assign_btn"].click(
        v4_ui.assign_v4_speaker,
        [
            page["project"], page["assign_segs"], page["assign_speaker"],
            page["assign_new"], page["assign_lock"],
        ],
        [page["assign_msg"]],
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["merge_btn"].click(
        v4_ui.merge_v4_speakers,
        [page["project"], page["merge_source"], page["merge_target"]],
        [page["merge_msg"]],
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["lock_btn"].click(
        v4_ui.set_v4_speaker_lock,
        [page["project"], page["lock_speaker"]],
        [page["lock_msg"]],
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["alias_btn"].click(
        v4_ui.set_v4_speaker_alias,
        [page["project"], page["alias_speaker"], page["alias"]],
        [page["alias_msg"]],
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["confirm_candidate"].click(
        v4_ui.confirm_v4_candidate,
        [page["project"], page["candidate"]],
        [page["candidate_msg"]],
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["reject_candidate"].click(
        v4_ui.reject_v4_candidate,
        [page["project"], page["candidate"]],
        [page["candidate_msg"]],
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )
    page["merge_candidate"].click(
        v4_ui.merge_v4_candidate,
        [page["project"], page["candidate"], page["candidate_target"]],
        [page["candidate_msg"]],
    ).then(
        v4_ui.open_v4_role_project,
        [page["project"]],
        outputs,
    )


def _refresh_embedded_v4_role_project(name):
    """Keep the embedded advanced panel aligned with the currently open project."""
    choices = v4_ui.scan_v4_projects()
    value = name if name in choices else None
    return gr.update(choices=choices, value=value)

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
        tts = _tts_engine()
        tts.init_engine()
        parts = tts.test_voice(audio)
        if not parts or not all(os.path.isfile(p) for p in parts):
            return None
        # 把三句测试句拼接为一段连续音频，供单一 gr.Audio 播放
        out_dir = config.get_preview_dir()
        out = os.path.join(out_dir, f"preview_{_safe_name(role)}.wav")
        tts._concat_wavs(parts, out)
        return out if os.path.isfile(out) else None
    except Exception:
        return None

def do_synthesis(ss, num_beams=2, progress=gr.Progress(),
                emotion="(按剧本默认)", s_override=False, emo_alpha=1.0, speech_rate=1.0,
                selected_chapters=None):
    """开始合成：提交后台队列并轮询进度（R1 后台化，不再阻塞 UI）。

    2.3 O2：接收合成期情感 / 语速全局覆盖，持久化到项目 ``synthesis_overrides.json``
    并透传至 ``SynthesisService.start``，保证预览 / 导出缓存键一致。
    """
    proj = ss.project
    if ss and getattr(ss, "is_v4", False):
        yield from _do_synthesis_v4(ss, progress)
        return
    bindings = ss.bindings
    script = ss.script or {}
    if not proj:
        yield ("请先在项目管理中打开项目", [])
        return
    missing = [n for n in (script.get("voices", {}) or {}) if n not in bindings or not bindings[n]]
    if missing:
        yield (f"以下角色未绑定: {', '.join(missing)}", [])
        return
    try:
        _tts_engine().init_engine()
    except Exception as e:
        yield (f"模型加载失败: {e}", [])
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
    # 5.4：已有合成任务进行中（pending/running/pausing/paused/cancelling）时禁止开启新任务，
    # 避免第二个整本任务覆盖 state 引用导致第一个任务失控。
    if ss.synthesis is not None and ss.synthesis.status in (
        "pending", "running", "pausing", "paused", "cancelling"
    ):
        yield ("⚠ 已有合成任务进行中（状态：" + ss.synthesis.status
               + "），请先停止当前任务再开始新的合成。", [])
        return
    # 准备本次合成任务态（每会话独立），提交后台
    ss.synthesis = SynthesisState(task_id=f"task_{int(time.time()*1000)}", project=proj)
    # O3：初始化内存段态列表（与 O11 共享真相，绝不反向写 meta.segments_status）
    # O5：传入 selected_chapters，使未选中段在内存态标 skipped（⏭）
    ss.synthesis.segment_states = synth_progress.build_segment_states(proj, selected_chapters)
    # O5：持久化本次勾选（非破坏性，与 synthesis_overrides.json 同构）
    try:
        _pm.set_synthesis_selections(proj, {"chapters": selected_chapters or []})
    except Exception as exc:
        logger.warning("保存合成勾选失败: %s", exc)
    SynthesisService.start(
        ss.synthesis, proj, bindings, num_beams=num_beams,
        emotion=emotion_override,
        emo_alpha=emo_alpha if s_override else None,
        speech_rate=speech_rate if s_override else None,
        selected_chapters=selected_chapters,
    )
    state = ss.synthesis
    # 轮询直到终态，~0.5s 刷新一次日志 + 进度条 + 队列列表
    while state.status not in ("done", "cancelled", "error"):
        time.sleep(0.5)
        try:
            progress(state.progress, f"{state.completed}/{state.total}")
        except Exception as exc:
            logger.debug("进度回调异常（进行中）: %s", exc)
        yield (state.snapshot_text(), df_style.style_dataframe(synth_progress.to_queue_rows(state.segment_states), synth_progress.QUEUE_HEADERS, status_col=0, status_color_map=df_style.ICON_COLORS))
    # 终态再刷一次
    try:
        progress(state.progress, f"{state.completed}/{state.total}")
    except Exception as exc:
        logger.debug("进度回调异常（终态）: %s", exc)
    yield (state.snapshot_text(), df_style.style_dataframe(synth_progress.to_queue_rows(state.segment_states), synth_progress.QUEUE_HEADERS, status_col=0, status_color_map=df_style.ICON_COLORS))


def _do_synthesis_v4(ss, progress=gr.Progress()):
    """V4 合成：确保计划 → 启动统一队列（后台线程）→ 轮询 runtime.db 进度。

    语义：pause 协作暂停（当前任务完成后挂起）；resume 跳过已完成；
    cancel 保留已完成缓存；中断恢复由 executor 在下次启动时自动处理。
    """
    proj = ss.project
    if not proj:
        yield ("请先在项目管理中打开项目", [])
        return
    project_path = V4ProjectService.root() / proj
    plan_ok, plan_msg = V4SynthesisService.ensure_plan(project_path)
    if not plan_ok:
        yield (plan_msg, [])
        return
    if "unresolved" in plan_msg or "未绑定" in plan_msg:
        yield (plan_msg + "\n\n未确认角色 / 未绑定音色的片段将跳过，可在③继续处理后重试。", _v4_queue_styled(proj))
    ok, message = V4SynthesisService.start(proj)
    if not ok:
        yield (message, _v4_queue_styled(proj))
        return
    yield (message, _v4_queue_styled(proj))
    while True:
        time.sleep(0.5)
        snapshot = V4SynthesisService.snapshot(proj)
        status = snapshot["run_status"]
        text = snapshot.get("text") or snapshot.get("error") or status
        try:
            counts = snapshot["counts"]
            done = counts.get("completed", 0)
            total = sum(counts.values())
            progress(done / total if total else 0, f"{done}/{total}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("V4 进度回调异常: %s", exc)
        yield (text, _v4_queue_styled(proj))
        if status in ("done", "cancelled", "error"):
            break
    final = V4SynthesisService.snapshot(proj)
    yield (
        final.get("text") or final.get("error") or "已完成",
        _v4_queue_styled(proj),
    )


def _v4_queue_styled(project_name):
    """V4 队列表格样式化行。"""
    rows = V4SynthesisService.queue_rows(project_name)
    return df_style.style_dataframe(
        rows,
        ["task", "chapter", "speaker", "status", "len", "try", "split", "out"],
        status_col=3,
        status_color_map=df_style.ICON_COLORS,
    )


def cancel(ss):
    """停止合成：置协作取消标志（worker 在下一段前检查 -> 段边界生效）。"""
    if ss and getattr(ss, "is_v4", False):
        ok, message = V4SynthesisService.cancel(ss.project)
        return message
    if ss.synthesis is not None:
        SynthesisService.cancel(ss.synthesis)
        return "停止中..."
    return "当前没有运行中的合成任务。"

def pause_synthesis(ss):
    """O12：暂停合成（协作暂停，段边界挂起，不杀进行中进程）。

    仅在 ``ss.synthesis`` 存在且 ``status in (running, paused)`` 时生效；否则返回提示不报错。
    返回 (队列列表, 暂停按钮, 恢复按钮) 的更新三元组。
    """
    if ss and getattr(ss, "is_v4", False):
        ok, message = V4SynthesisService.pause(ss.project)
        rows = _v4_queue_styled(ss.project)
        if ok:
            return (
                rows,
                gr.update(value="⏸ 已暂停", interactive=False),
                gr.update(interactive=True),
            )
        return (rows, gr.update(), gr.update())
    if ss.synthesis is None or ss.synthesis.status not in ("running", "paused"):
        return (gr.update(), gr.update(), gr.update())
    SynthesisService.pause(ss.synthesis)
    rows = df_style.style_dataframe(
        synth_progress.to_queue_rows(ss.synthesis.segment_states),
        synth_progress.QUEUE_HEADERS,
        status_col=0,
        status_color_map=df_style.ICON_COLORS,
    )
    return (
        rows,
        gr.update(value="⏸ 已暂停", interactive=False),
        gr.update(interactive=True),
    )

def resume_synthesis(ss):
    """O12：恢复合成（paused -> running，worker 退出段边界挂起继续提交新段）。

    仅在 ``ss.synthesis`` 存在且 ``status == 'paused'`` 时生效；否则返回提示不报错。
    返回 (队列列表, 暂停按钮, 恢复按钮) 的更新三元组。
    """
    if ss and getattr(ss, "is_v4", False):
        ok, message = V4SynthesisService.resume(ss.project)
        rows = _v4_queue_styled(ss.project)
        if ok:
            return (
                rows,
                gr.update(value="⏸ 暂停", interactive=True),
                gr.update(interactive=False),
            )
        return (rows, gr.update(), gr.update())
    if ss.synthesis is None or ss.synthesis.status != "paused":
        return (gr.update(), gr.update(), gr.update())
    SynthesisService.resume(ss.synthesis)
    rows = df_style.style_dataframe(
        synth_progress.to_queue_rows(ss.synthesis.segment_states),
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
    if ss and getattr(ss, "is_v4", False) and ss.project:
        rows = V4SynthesisService.queue_rows(ss.project)
        return df_style.style_dataframe(
            rows,
            ["task", "chapter", "speaker", "status", "len", "try", "split", "out"],
            status_col=3,
            status_color_map=df_style.ICON_COLORS,
        )
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


def do_export(fmt, bitrate, output_dir, *args):
    """一键导出（D1：用 *args 吸收 ss，零改动过 glue 测试）。"""
    ss = args[0] if args else None
    if not ss or not ss.project:
        return None, "请先打开项目"
    if getattr(ss, "is_v4", False):
        try:
            project_path = V4ProjectService.root() / ss.project
            # 导出前检查章节是否已拼接
            chapters = V4QualityService.available_chapters(project_path)
            if not chapters:
                return None, (
                    "尚未生成可导出的章节音频：请先在「④ 生产与质检」"
                    "生成计划并完成合成。"
                )
            path = V4ExportService.export(
                project_path,
                output_format=fmt,
                bitrate=bitrate,
                output_dir=output_dir or None,
            )
            return (
                _safe_path_for_file_component(path),
                f"✅ 导出完成：`{path}`",
            )
        except Exception as e:
            return None, str(e)
    try:
        out = ExportService.export(ProjectService.get_project_dir(ss.project), fmt, bitrate, output_dir)
        return _safe_path_for_file_component(out), "导出完成"
    except Exception as e:
        # R2：显式报错（含中间 WAV 路径 / ffmpeg 安装链接 / 可改 WAV 建议）
        return None, str(e)

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
        if getattr(ss, "is_v4", False):
            paths = V4ExportService.generate_subtitles(
                V4ProjectService.root() / ss.project, formats=fmts
            )
            if not paths:
                return None, "未找到已合成段落，无法生成字幕（请先合成）"
            return [str(item) for item in paths], "字幕已生成"
        paths = ExportService.export_subtitles(
            ProjectService.get_project_dir(ss.project), formats=fmts
        )
        if not paths:
            return None, "未找到已合成段落，无法生成字幕（请先合成）"
        return paths, "字幕已生成"
    except Exception as e:
        return None, str(e)

def preview_chapters(ss):
    if not ss.project: return "*请先在项目管理中打开项目*",None,gr.update(choices=[])
    if getattr(ss, "is_v4", False):
        return _preview_chapters_v4(ss)
    # 阶段三：复用会话态快照的剧本 dict，不再直接读盘。
    snap = _snap(ss); script = snap.script
    proj_dir=ProjectService.get_project_dir(ss.project)
    seg_dir=os.path.join(proj_dir,"segments")
    def _f(sid,t,r,e,ea=1.0,sr=1.0,ph=None,dm=None):
        # B7：参数感知缓存键优先，旧版裸文件回退
        return segment_cache.find_segment_wav(
            seg_dir, sid, t, r, e, ea, sr, ph, dm
        )
    lines=["| 章节 | 完成 | 详情 |","|------|------|------|"]
    chapter_rows=[]; first_audio=None; seg_choices=[]; td=0; ta=0
    for ch in script.get("chapters",[]):
        segs=ch.get("segments",[]); ta+=len(segs)
        done=[]; miss=[]
        for seg in segs:
            fp=_f(seg['id'],seg['text'],seg['role'],seg.get('emotion','neutral'),seg.get('emo_alpha',1.0),seg.get('speech_rate',1.0),seg.get('pinyin_hints'),segment_cache.director_metadata_for(seg))
            if fp: done.append(seg['id']); seg_choices.append(f"{seg['id']} {seg['role']}")
            else: miss.append(seg['id'])
            if first_audio is None and fp: first_audio=fp
        td+=len(done)
        d=f"{len(done)}/{len(segs)}"
        if done: d+=f" ✅ {', '.join(done[:4])}"+(f" +{len(done)-4}" if len(done)>4 else "")
        if miss and len(miss)<=2: d+=f" ❌ {', '.join(miss)}"
        chapter_rows.append(f"| 第{ch['id']}章 {ch['title']} | {len(done)}/{len(segs)} | {d} |")
    # T5：仅截断「展示用 summary 文本」（按章上限，超出加说明）；
    #     严禁截断 seg_choices（e_seg_sel 下拉需完整，供长书导出页选段试听/重合成）。
    MAX_CHAPTER_ROWS=100
    if len(chapter_rows)>MAX_CHAPTER_ROWS:
        lines+=chapter_rows[:MAX_CHAPTER_ROWS]
        lines.append(f"| … | … | 其余 {len(chapter_rows)-MAX_CHAPTER_ROWS} 章（详情见导出页） |")
    else:
        lines+=chapter_rows
    summary=f"### 📊 {td}/{ta} 段已完成\n\n"+"\n".join(lines)
    if td==0: summary+="\n\n⚠ 未检测到合成段落"
    return summary,first_audio,gr.update(choices=seg_choices,value=seg_choices[0] if seg_choices else None)

def _preview_chapters_v4(ss):
    """V4 项目章节试听表：从 runtime.db 读取每章已完成片段。"""
    project_path = V4ProjectService.root() / ss.project
    script = ss.script
    lines = ["| 章节 | 完成 | 详情 |", "|------|------|------|"]
    chapter_rows = []
    seg_choices = []
    first_audio = None
    td = ta = 0
    for chapter in script.chapters:
        segs = chapter.segments
        ta += len(segs)
        done = []
        for seg in segs:
            audio = V4QualityService.segment_audio(project_path, seg.segment_id)
            if audio:
                done.append(seg.segment_id)
                seg_choices.append(f"{seg.segment_id} {seg.speaker_id or '?'}")
                if first_audio is None:
                    first_audio = audio
        td += len(done)
        chapter_rows.append(
            f"| {chapter.title} | {len(done)}/{len(segs)} | "
            + (", ".join(done[:4]) if done else "等待合成")
            + (" +…" if len(done) > 4 else "") + " |"
        )
    summary = f"### 📊 {td}/{ta} 段已完成\n\n" + "\n".join(lines + chapter_rows)
    if td == 0:
        summary += "\n\n⚠ 尚未合成段落：请先在「④ 生产与质检」生成计划并合成。"
    return summary, first_audio, gr.update(
        choices=seg_choices, value=seg_choices[0] if seg_choices else None
    )


def play_segment(choices, ss):
    if not ss.project or not choices: return None
    if isinstance(choices,list): choices=choices[0] if choices else None
    if not choices: return None
    if getattr(ss, "is_v4", False):
        segment_id = choices.split(" ")[0]
        return V4QualityService.segment_audio(
            V4ProjectService.root() / ss.project, segment_id
        )
    # 阶段三：复用会话态快照的剧本 dict。
    script = _snap(ss).script
    sid=choices.split(" ")[0]; proj_dir=ProjectService.get_project_dir(ss.project)
    seg_dir=os.path.join(proj_dir,"segments")
    # B7：参数感知缓存键优先，旧版裸文件回退
    for ch in script.get("chapters",[]):
        for seg in ch.get("segments",[]):
            if seg["id"]==sid:
                return segment_cache.find_segment_wav(
                    seg_dir, sid, seg["text"], seg["role"],
                    seg.get("emotion","neutral"),
                    seg.get("emo_alpha",1.0),
                    seg.get("speech_rate",1.0),
                    seg.get("pinyin_hints"),
                    segment_cache.director_metadata_for(seg),
                )
    return None

def regenerate_segment(choices, emotion, emo_alpha, speech_rate, voice_choice, ss):
    if not ss.project or not choices: return None,"请选择段落"
    if isinstance(choices,str): choices=[choices]
    if getattr(ss, "is_v4", False):
        return _regenerate_segment_v4(choices, ss)
    bindings=ss.bindings
    # 阶段三：复用会话态快照的剧本 dict。
    script = _snap(ss).script
    proj_dir=ProjectService.get_project_dir(ss.project)
    tts = _tts_engine()
    tts.init_engine(); seg_dir=os.path.join(proj_dir,"segments")
    os.makedirs(seg_dir,exist_ok=True); results=[]
    # 如果选了音色库音频，覆盖绑定
    override_voice = _lib_path(voice_choice) if voice_choice else None
    for choice in choices:
        sid=choice.split(" ")[0]
        for ch in script.get("chapters",[]):
            for seg in ch.get("segments",[]):
                if seg["id"]!=sid: continue
                speaker = override_voice or bindings.get(seg["role"])
                if not speaker: results.append(f"❌ {sid}: 角色未绑定"); break
                try:
                    # B7：重合成写入参数感知缓存键路径，与批量链路命名一致
                    director_meta = segment_cache.director_metadata_for(seg)
                    out=segment_cache.segment_wav_path(
                        seg_dir, sid, emotion, emo_alpha, speech_rate,
                        seg.get("pinyin_hints"), director_meta,
                    )
                    from lib import directed_synthesis
                    directed_synthesis.synthesize(
                        segment=seg, speaker_audio=speaker,
                        emotion=emotion, emo_alpha=emo_alpha, speech_rate=speech_rate,
                        pinyin_hints=seg.get("pinyin_hints"), output_path=out,
                        engine=tts,
                    )
                    ProjectService.update_segment_status(ss.project,sid,"done"); results.append(f"✅ {sid}")
                except Exception as e: results.append(f"❌ {sid}: {str(e)[:40]}")
                break
    first_sid=choices[0].split(" ")[0]; first_fp=None
    for ch in script.get("chapters",[]):
        for seg in ch.get("segments",[]):
            if seg["id"]==first_sid:
                # B7：用同一缓存键推导真实 wav 名
                first_fp=segment_cache.find_segment_wav(seg_dir, first_sid, seg["text"], seg["role"],
                    emotion, emo_alpha, speech_rate, seg.get("pinyin_hints"),
                    segment_cache.director_metadata_for(seg))
                break
        if first_fp: break
    # 2.4 M-3：批量重合成结束后释放碎片化显存（不卸载模型）
    tts.empty_cache()
    # 段状态已写盘（ProjectService.update_segment_status），使快照失效以便下次读取重载
    ss.invalidate_snapshot()
    return (first_fp, "\n".join(results))


def _regenerate_segment_v4(choices, ss):
    """V4 重新生成：失效该 segment 对应任务缓存并置回 pending（不影响其他缓存）。"""
    project_path = V4ProjectService.root() / ss.project
    results = []
    first_audio = None
    for choice in choices:
        segment_id = choice.split(" ")[0]
        ok, message = V4QualityService.regenerate_segment(project_path, segment_id)
        results.append(("✅ " if ok else "❌ ") + message if ok else message)
        if first_audio is None:
            first_audio = V4QualityService.segment_audio(project_path, segment_id)
    return first_audio, "\n".join(results)

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
    if getattr(ss, "is_v4", False):
        from repositories.production_repository import ProductionRepository

        try:
            production = ProductionRepository(
                V4ProjectService.root() / ss.project
            )
            voices, _p, _pr, _profile = production.load_inputs()
        except Exception:  # noqa: BLE001
            return gr.update(interactive=False, choices=[], value=None,
                             info="请先打开项目并绑定角色音色")
        choices = [
            (item.display_name, item.speaker_id)
            for item in ss.speakers_v4.speakers
            if item.speaker_id in voices.bindings
        ]
        if not choices:
            return gr.update(interactive=False, choices=[], value=None,
                             info="V4 项目：请先绑定角色音色")
        return gr.update(interactive=True, choices=choices, value=choices[0][1])
    choices = format_bound_role_choices(ss.script, ss.bindings)
    if not choices:
        return gr.update(interactive=False, choices=[], value=None,
                         info="请先打开项目并绑定角色音色")
    return gr.update(interactive=True, choices=choices, value=choices[0][1])


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


def do_supplement_synth(sup_role, sup_mode, sup_text, sup_json_role, sup_json_lines,
                        sup_emotion, sup_emo_alpha, sup_rate, sup_quality,
                        sup_split_punct, sup_voice, ss):
    """逐句补合成：按模式取（角色, 文本）→ 逐句 synthesize → 收集 wav + 逐句状态。

    输入模式（``sup_mode``）：
      - ``"paste"``：角色=``sup_role`` 下拉，文本=``sup_text`` 按行拆分（可选按标点切长段）；
      - ``"json"``：角色/文本来自解析小 JSON 的 state（``sup_json_role`` / ``sup_json_lines``）。

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
    if getattr(ss, "is_v4", False):
        from repositories.production_repository import ProductionRepository

        try:
            production = ProductionRepository(
                V4ProjectService.root() / ss.project
            )
            voices, _p, _pr, _profile = production.load_inputs()
            binding = voices.bindings.get(role)
            bound = (
                str(V4ProjectService.root() / ss.project / binding.voice_id)
                if binding is not None
                else None
            )
        except Exception:  # noqa: BLE001
            bound = None
        speaker = override_voice or bound
    else:
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
    # 产物落在 <data_dir>/preview/supplement_tasks/<task_id>/（001.wav... + manifest.json + preview.wav）。
    import uuid as _uuid

    from lib import audio_format as _af
    task_id = _uuid.uuid4().hex
    task_dir = os.path.join(config.get_workspace_paths().task_cache_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    task = SupplementTaskState(
        task_id=task_id, project=ss.project, role=role,
        status="running",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        task_dir=task_dir,
    )
    # 清理过期任务（>7 天），避免 supplement_tasks 无限增长
    try:
        SupplementService.cleanup_old_tasks(max_age_days=7)
    except Exception:  # pylint: disable=broad-except
        pass

    tts = _tts_engine()
    tts.init_engine()
    try:
        results = SupplementService.synthesize_lines(
            role=role, lines=lines, speaker_audio=speaker,
            overrides=overrides, num_beams=num_beams, task=task,
        )
    except Exception as e:
        tts.empty_cache()
        task.status = "error"
        return [], f"❌ 补合成异常：{str(e)[:200]}"
    finally:
        # 2.4 M-3：补合成结束后释放碎片化显存（不卸载模型，与 regenerate_segment 同策略）。
        tts.empty_cache()

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

    md = [f"### 🎙 补合成完成（{len(wav_paths)}/{len(results)} 成功）"]
    for r in results:
        txt = (r.get("text") or "")[:30]
        if r["status"] == "ok":
            md.append(f"- ✅ 句{r['index'] + 1}: {txt}")
        else:
            md.append(f"- {r['error']}")
    md.append(f"\n> 任务 ID：`{task_id}`｜产物目录：`{task_dir}`")
    return wav_paths, "\n".join(md)


def do_supplement_export(sup_format, sup_bitrate, sup_wavs, sup_role, ss):
    """把已合成的补录 wav 导出为独立音频（不进整本拼接），经白名单后返回下载路径。

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
    out_path = SupplementService.build_output_path(project_dir, role, sup_format)
    meta = ss.script.get("meta", {}) if isinstance(ss.script, dict) else {}
    title = f"{meta.get('title', 'audiobook')} - {role} 补录" if meta else None
    artist = meta.get("author") if meta else None
    try:
        final = _audio_pipeline().export_supplement(
            paths=wavs, out_path=out_path, format=sup_format, bitrate=sup_bitrate,
            title=title, artist=artist,
        )
        return _safe_path_for_file_component(final), f"✅ 导出完成：{os.path.basename(final)}"
    except Exception as e:
        return None, str(e)


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
            gr.update(choices=cats + ["— 新建 —"] if cats else ["未分类", "— 新建 —"], value=category or "未分类"))


def filter_vlib_by_category(category):
    """按分类筛选音色库 → 返回可选音色列表（供绑定区 v_lib 使用）。"""
    return gr.update(choices=voice_lib.voice_names(category or None), value=None)

def open_segments_folder(ss):
    if not ss.project: return "请先打开项目"
    d = ProjectService.get_project_dir(ss.project)
    sd = os.path.join(d, "segments")
    os.makedirs(sd, exist_ok=True)
    os.startfile(sd)
    return ""

# ═══════════ O4/O5/O9/O13 新增 handler（仅追加，不触碰既有红线接线） ═══════════

# ── O4：书架 + 章节树 ──
def refresh_bookshelf():
    """刷新书架 Dataframe（返回着色契约 dict，列：项目|章|段进度|状态）。

    V3 / V4 项目混合展示：V4 项目行带格式标记。
    """
    rows = []
    for item in V4ProjectService.scan_projects():
        marker = "V4 " if item.project_format == "v4" else "V3 "
        rows.append(
            [
                f"{marker}{item.name}",
                item.total_chapters,
                f"{item.completed_segments}/{item.total_segments}",
                item.status,
            ]
        )
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
    if V4ProjectService.detect_format(project) == "v4":
        return _render_chapter_tree_v4(project)
    return _pm.build_chapter_tree(project)


def _render_chapter_tree_v4(project_name):
    """V4 章节树：章节 + 片段 + 状态徽标。"""
    context = V4ProjectService.open_project(project_name)
    if context is None:
        return "<i>未打开项目</i>"
    project_path = context.project_path
    parts = ["<div class='chapter-tree'>"]
    for chapter in context.script.chapters:
        segs = chapter.segments
        done = sum(
            1
            for seg in segs
            if V4QualityService.segment_audio(project_path, seg.segment_id)
        )
        badge = (
            f"<span class='tree-badge done'>{done}/{len(segs)} 段</span>"
            if done
            else "<span class='tree-badge'>待合成</span>"
        )
        parts.append(
            f"<details open><summary>{chapter.title} {badge}</summary>"
        )
        for seg in segs[:8]:
            speaker = seg.speaker_id or "待确认"
            state = "✅" if V4QualityService.segment_audio(
                project_path, seg.segment_id
            ) else "·"
            text = (context.script and _segment_text(project_path, seg)) or ""
            parts.append(
                f"<div class='tree-item'>{state} <b>{speaker}</b> "
                f"<span class='tree-text'>{text[:36]}</span></div>"
            )
        if len(segs) > 8:
            parts.append(f"<div class='tree-item'>… 共 {len(segs)} 段</div>")
        parts.append("</details>")
    parts.append("</div>")
    return "".join(parts)


def _segment_text(project_path, seg):
    """取 V4 segment 原文（text_override 优先，否则从 source 切）。"""
    if getattr(seg, "text_override", None):
        return seg.text_override
    try:
        source = (project_path / "source/source.txt").read_text(encoding="utf-8")
        return source[seg.start:seg.end]
    except (OSError, IndexError):
        return ""


def refresh_projects_full():
    """p_refresh 全量刷新：刷新 p_sel 选项（V3/V4 混合）。"""
    return gr.update(choices=project_choices())


# ── O5：合成前分段预览 / 勾选 ──
def render_preview(ss):
    """渲染合成前预览 Dataframe + 章节勾选（回填已持久化选择）。

    返回 (预览行, gr.update(章节选项+勾选值))。
    """
    if not ss or not ss.project:
        return [], gr.update(choices=[], value=[])
    if getattr(ss, "is_v4", False):
        return _render_preview_v4(ss)
    snap = _snap(ss)
    script = snap.script
    chapters = script.get("chapters", [])
    chapter_options = [str(ch.get("id")) for ch in chapters]
    chapter_labels = {
        str(ch.get("id")): f"第{ch.get('id')}章 {ch.get('title', '')}"
        for ch in chapters
    }
    rows = synth_progress.build_preview_rows_from_script(snap.script)
    # 回填勾选：读 synthesis_selections.json
    sel = _pm.get_synthesis_selections(ss.project)
    saved = sel.get("chapters")
    if saved is not None:
        chosen = [c for c in saved if c in chapter_options]
    else:
        chosen = list(chapter_options)
    return df_style.style_dataframe(rows, synth_progress.PREVIEW_HEADERS, status_col=None), gr.update(
        choices=[(chapter_labels.get(c, c), c) for c in chapter_options],
        value=chosen,
    )


def _render_preview_v4(ss):
    """V4 合成前预览：展示待合成片段概览（真正的计划在④生成）。"""
    rows = []
    for chapter in ss.script.chapters:
        for seg in chapter.segments:
            rows.append(
                [
                    seg.segment_id,
                    chapter.chapter_id,
                    seg.speaker_id or "待确认",
                    seg.kind,
                    seg.status,
                ]
            )
    chapter_options = [ch.chapter_id for ch in ss.script.chapters]
    labels = {ch.chapter_id: ch.title for ch in ss.script.chapters}
    return df_style.style_dataframe(
        rows, ["id", "chapter", "speaker", "kind", "status"], status_col=None
    ), gr.update(
        choices=[(labels.get(c, c), c) for c in chapter_options],
        value=list(chapter_options),
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
    if getattr(ss, "is_v4", False):
        opts = [
            (chapter.title, chapter.chapter_id) for chapter in ss.script.chapters
        ]
        return gr.update(choices=opts, value=opts[0][1] if opts else None)
    script = _snap(ss).script
    opts = [
        (f"第{ch.get('id')}章 {ch.get('title', '')}", str(ch.get("id")))
        for ch in script.get("chapters", [])
    ]
    return gr.update(choices=opts, value=opts[0][1] if opts else None)


def preview_chapter(ss, chapter_id):
    """合并试听单章：调 audio_pipeline.concat_for_preview 返回路径。"""
    if not ss or not ss.project or not chapter_id:
        return None
    if getattr(ss, "is_v4", False):
        return V4QualityService.chapter_audio(
            V4ProjectService.root() / ss.project, chapter_id
        )
    proj_dir = ProjectService.get_project_dir(ss.project)
    out_path = os.path.join(config.get_preview_dir(), f"chapter_{chapter_id}.wav")
    try:
        return _audio_pipeline().concat_for_preview(proj_dir, chapter_id, out_path)
    except Exception:
        return None


# ═══════════ UI ═══════════

# ═══════════ 页面级刷新辅助（打开项目统一链路复用） ═══════════

def refresh_categories():
    """刷新绑定/保存分类下拉（v_bind_category / v_save_category）。"""
    cats = voice_lib.list_categories()
    return (
        gr.update(choices=cats or ["未分类"]),
        gr.update(
            choices=(cats or []) + ["— 新建 —"] if cats else ["未分类", "— 新建 —"],
            value="未分类",
        ),
    )


def refresh_voice_filters():
    """一次扫描结果刷新绑定筛选、资产筛选和新声音分类。"""
    cats = voice_lib.list_categories()
    filter_choices = cats or ["未分类"]
    save_choices = cats + ["— 新建 —"] if cats else ["未分类", "— 新建 —"]
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
    if getattr(ss, "is_v4", False):
        return _production_check_v4(ss)
    try:
        snap = _snap(ss)
        if snap is None:
            return "#### 生产检查\n请先打开项目。"        # ProjectSnapshot stores the raw structured_script dict; validation
        # expects the parsed Script model used by the loader/service layer.
        errors = script_loader.validate_script(script_loader.from_dict(snap.script))
        roles = snap.script.get("voices", {}) or {}
        missing = [role for role in roles if not snap.bindings.get(role)]
        lines = ["#### 生产检查"]
        if errors:
            lines.append(f"⚠ 剧本需要检查（{len(errors)} 项提示），请先回到项目页确认书稿。")
        else:
            lines.append("✅ 剧本有效")
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


def _production_check_v4(ss):
    """V4 项目生产检查：unresolved 片段数 + 未绑定音色角色数。"""
    from repositories.production_repository import ProductionRepository

    lines = ["#### 生产检查"]
    try:
        unresolved = sum(
            seg.status == "unresolved"
            for ch in ss.script.chapters
            for seg in ch.segments
        )
        project_path = V4ProjectService.root() / ss.project
        production = ProductionRepository(project_path)
        voices, _p, _pr, _profile = production.load_inputs()
        speakers = ss.speakers_v4.speakers
        missing = [
            item.display_name
            for item in speakers
            if item.speaker_id not in voices.bindings
        ]
        lines.append("✅ V4 剧本有效（source-first）")
        if unresolved:
            lines.append(
                f"⚠ {unresolved} 个片段待确认角色，请先到「③ 角色与声音」处理。"
            )
        if missing:
            lines.append(
                f"⚠ {len(missing)} 个角色未绑定音色：{', '.join(missing[:8])}"
                + ("…" if len(missing) > 8 else "")
                + "。"
            )
            lines.append("这里不会阻断你查看队列或质检。")
        else:
            lines.append("✅ 所有角色已绑定音色，可以生成计划并合成。")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"⚠ 状态读取失败：{exc}")
    return "\n".join(lines)


def refresh_export_default_dir(ss):
    """显示当前项目的动态默认导出目录，避免用户猜路径。"""
    if not ss or not ss.project:
        return "项目默认目录：打开项目后显示。留空保存位置即可使用该目录。"
    try:
        if getattr(ss, "is_v4", False):
            project_dir = os.path.normpath(
                str(V4ProjectService.root() / ss.project)
            )
        else:
            project_dir = os.path.normpath(
                ProjectService.get_project_dir(ss.project)
            )
        output_dir = os.path.normpath(os.path.join(project_dir, "output"))
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

    if getattr(ss, "is_v4", False):
        return _dashboard_snapshot_v4(ss)

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
        failed_segments = getattr(meta, "failed_count", 0)
        statuses = getattr(meta, "segments_status", {}) or {}
        completed_chapters = sum(
            1 for chapter in chapters
            if chapter.get("segments")
            and all(statuses.get(segment.get("id")) == "done" for segment in chapter["segments"])
        )
        roles = script.get("voices", {}) or {}
        role_total = len(roles)
        roles_bound = sum(1 for role in roles if ss.bindings.get(role))

        issues: list[tuple[str, str]] = []
        unbound = role_total - roles_bound
        if unbound:
            issues.append(("warning", f"还有 {unbound} 个角色未绑定声音"))
        if failed_segments:
            issues.append(("error", f"有 {failed_segments} 个段落需要检查或重新合成"))
        remaining = max(total_segments - completed_segments, 0)
        if not unbound and remaining:
            issues.append(("info", f"还有 {remaining} 个段落等待完成"))

        if unbound:
            next_step = "配置角色声音"
            next_detail = "所有角色完成绑定后，才能开始整本书的生产。"
        elif failed_segments or remaining:
            next_step = "开始或继续生产"
            next_detail = "已有结果会自动保留，可随时进入队列继续合成与质检。"
        else:
            next_step = "交付成品"
            next_detail = "章节已全部完成，可导出有声书和字幕文件。"

        state = getattr(ss, "synthesis", None)
        if state is not None:
            task_label = f"生产任务 · {getattr(state, 'status', 'unknown')}"
            task_detail = f"已完成 {getattr(state, 'completed', 0)}/{getattr(state, 'total', 0)} 段"
        elif completed_segments:
            task_label = "最近一次生产结果"
            task_detail = f"项目已完成 {completed_segments}/{total_segments} 段，可继续质检或交付。"
        else:
            task_label = "尚未开始生产"
            task_detail = "完成角色声音配置后，即可按剧本开始合成。"

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


def _dashboard_snapshot_v4(ss):
    """V4 项目工作台概览（章节 / 片段 / 待确认 / 已合成 / 待绑定）。"""
    try:
        from repositories.production_repository import ProductionRepository
        from services.v4_progress import V4ProgressService

        script = ss.script
        project_path = V4ProjectService.root() / ss.project
        chapters = list(script.chapters)
        unresolved = sum(
            seg.status == "unresolved"
            for ch in chapters
            for seg in ch.segments
        )
        production = ProductionRepository(project_path)
        voices, _p, _pr, _profile = production.load_inputs()
        plan = production.load_plan()
        progress = V4ProgressService.from_project(
            project_path,
            chapters,
            plan_revision=plan.revision if plan else None,
        )
        completed = progress.segments_done
        speakers = ss.speakers_v4.speakers
        bound = sum(1 for s in speakers if s.speaker_id in voices.bindings)
        title = ss.project
        try:
            with (project_path / "project.json").open(
                "r", encoding="utf-8"
            ) as handle:
                title = json.load(handle).get("title") or ss.project
        except (OSError, json.JSONDecodeError):
            pass
        issues = []
        if unresolved:
            issues.append(("warning", f"还有 {unresolved} 个片段待确认角色"))
        if bound < len(speakers):
            issues.append(("warning", f"还有 {len(speakers) - bound} 个角色未绑定音色"))
        remaining = max(progress.segments_total - completed, 0)
        if not unresolved and bound == len(speakers) and remaining:
            issues.append(("info", f"还有 {remaining} 个段落等待合成"))
        if unresolved or bound < len(speakers):
            next_step, next_detail = (
                "确认角色与音色",
                "在「③ 角色与声音」完成角色确认和音色绑定后即可生产。",
            )
        elif remaining:
            next_step, next_detail = (
                "开始或继续生产",
                "在「④ 生产与质检」生成计划并合成，已有结果自动保留。",
            )
        else:
            next_step, next_detail = (
                "交付成品",
                "章节已全部完成，可在「⑤ 交付」导出有声书。",
            )
        return project_dashboard_html(
            title=title,
            project_name=ss.project,
            chapters_done=progress.chapters_done,
            chapters_total=progress.chapters_total,
            segments_done=progress.segments_done,
            segments_total=progress.segments_total,
            roles_bound=bound,
            roles_total=len(speakers),
            task_label=(
                "最近一次生产结果" if completed else "尚未开始生产"
            ),
            task_detail=(
                f"已完成 {completed}/{progress.segments_total} 段，可继续质检或交付。"
                if completed
                else "完成角色确认与音色绑定后即可开始合成。"
            ),
            next_step=next_step,
            next_detail=next_detail,
            issues=issues,
        )
    except Exception as exc:
        logger.warning("刷新 V4 工作台状态失败: %s", exc)
        return empty_dashboard_html()


def refresh_overview(ss):
    """刷新工作台的项目状态、生产摘要、待办和项目书架。"""
    return (*_dashboard_snapshot(ss), refresh_bookshelf())


def refresh_p_sel(name):
    """刷新项目下拉选项（确保选中项在 choices 内，V3/V4 混合）。"""
    return gr.update(choices=project_choices(), value=name)


def project_choices():
    """V3 / V4 混合项目下拉选项（label 带格式标记，value 为项目名）。"""
    return [
        (
            f"{item.name}（{'V4' if item.project_format == 'v4' else 'V3'}）",
            item.name,
        )
        for item in V4ProjectService.scan_projects()
    ]


def _open_chain_rest(event):
    """把打开项目后的统一刷新接到 event 的 .then 链上（3 入口复用）。

    顺序与原 22 元组全量刷新契约一致，覆盖：顶栏 / 章节表 / 章节试听
    选项 / 队列列表 / 章节树 / 合成预览 / 音色库 / 分类下拉 / 生产检查 /
    默认导出目录 / 概览 / 项目下拉。
    """
    e = event
    e = e.then(refresh_top_status, [ss], [top_status])
    e = e.then(project_dir_markup, [p_sel], [p_dir_md, p_open_dir])
    e = e.then(preview_chapters, [ss], [e_chapter_table, e_seg_audio, e_seg_sel])
    e = e.then(preview_chapter_options, [ss], [e_chapter_sel])
    e = e.then(refresh_queue_list, [ss], [s_queue_list])
    e = e.then(render_chapter_tree, [p_sel], [p_chapter_tree])
    e = e.then(render_preview, [ss], [s_preview_df, s_chapters_sel])
    e = e.then(refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category])
    e = e.then(refresh_categories, [], [v_bind_category, v_save_category])
    e = e.then(refresh_production_voice_choices, [], [e_voice, sup_voice])
    e = e.then(refresh_production_check, [ss], [production_check])
    e = e.then(refresh_export_default_dir, [ss], [e_save_dir_hint])
    e = e.then(
        refresh_overview, [ss],
        [ov_status, ov_progress, ov_task, ov_issues, ov_bookshelf],
    )
    e = e.then(refresh_p_sel, [p_sel], [p_sel])
    e = e.then(
        v4_ui.refresh_v4_reanalyze_visibility, [p_sel], [v_reanalyze]
    )
    e = e.then(
        v4_ui.analysis_progress_text, [p_sel], [v_analysis_progress]
    )
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
        nav_v4 = nav["nav_v4"]
        nav_v4_role = nav["nav_v4_role"]
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

            # ───────── 新建项目 ─────────
            cr_page = create_create_project_page()
            grp_create_project = cr_page["group"]
            cp_name = cr_page["cp_name"]
            cp_source = cr_page["cp_source"]
            cp_source_text = cr_page["cp_source_text"]
            cp_title = cr_page["cp_title"]
            cp_author = cr_page["cp_author"]
            cp_slot_status = cr_page["cp_slot_status"]
            cp_cleanup = cr_page["cp_cleanup"]
            cp_config_summary = cr_page["cp_config_summary"]
            cp_create = cr_page["cp_create"]
            cp_status = cr_page["cp_status"]
            cp_result = cr_page["cp_result"]
            cp_json_name = cr_page["cp_json_name"]
            cp_json_file = cr_page["cp_json_file"]
            cp_json_status = cr_page["cp_json_status"]
            cp_json_slot_status = cr_page["cp_json_slot_status"]
            cp_json_cleanup = cr_page["cp_json_cleanup"]
            cp_json_create = cr_page["cp_json_create"]
            cp_json_result = cr_page["cp_json_result"]

            # ───────── v4 Source-first 工作流 ─────────
            v4_page = create_v4_workspace_page()
            grp_v4 = v4_page["group"]
            v4_project = v4_page["project"]
            v4_refresh_projects = v4_page["refresh_projects"]
            v4_open = v4_page["open_project"]
            v4_summary = v4_page["summary"]
            v4_review = v4_page["review"]
            v4_segment_ids = v4_page["segment_ids"]
            v4_voice_speaker = v4_page["voice_speaker"]
            v4_voice_file = v4_page["voice_file"]
            v4_bind_voice = v4_page["bind_voice"]
            v4_voice_status = v4_page["voice_status"]
            v4_profile = v4_page["profile"]
            v4_plan = v4_page["plan"]
            v4_plan_status = v4_page["plan_status"]
            v4_queue = v4_page["queue"]
            v4_chapter = v4_page["chapter"]
            v4_chapter_audio = v4_page["chapter_audio"]
            v4_export_format = v4_page["export_format"]
            v4_bitrate = v4_page["bitrate"]
            v4_export = v4_page["export"]
            v4_export_file = v4_page["export_file"]
            v4_export_status = v4_page["export_status"]
            v4_v3_project = v4_page["v3_project"]
            v4_migrate = v4_page["migrate"]
            v4_migration_status = v4_page["migration_status"]

            # ───────── v4 角色工作台（开发模式调试入口） ─────────
            v4r_page = create_v4_role_page()
            grp_v4_role = v4r_page["group"]
            v4r_project = v4r_page["project"]

            # ───────── 项目 ─────────
            prj_page = create_project_page()
            grp_project = prj_page["group"]
            p_sel = prj_page["p_sel"]
            p_refresh = prj_page["p_refresh"]
            p_open = prj_page["p_open"]
            p_migrate = prj_page["p_migrate"]
            p_del = prj_page["p_del"]
            p_open_msg = prj_page["p_open_msg"]
            p_migrate_msg = prj_page["p_migrate_msg"]
            p_summary = prj_page["p_summary"]
            p_dir_md = prj_page["p_dir_md"]
            p_open_dir = prj_page["p_open_dir"]
            p_open_dir_msg = prj_page["p_open_dir_msg"]
            p_chapter_tree = prj_page["p_chapter_tree"]

            # ───────── 音色资产 ─────────
            vce_page = create_voice_page()
            grp_voices = vce_page["group"]
            v_status = vce_page["v_status"]
            v_continue_analysis = vce_page["v_continue_analysis"]
            v_reanalyze = vce_page["v_reanalyze"]
            v_analysis_msg = vce_page["v_analysis_msg"]
            v_analysis_progress = vce_page["v_analysis_progress"]
            v_analysis_trace = vce_page["v_analysis_trace"]
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
            v_recommend = vce_page["v_recommend"]
            v_recommendations = vce_page["v_recommendations"]
            v_recommend_status = vce_page["v_recommend_status"]
            advanced_role_page = vce_page["advanced_role"]

            # ───────── 生产阶段内部导航 ─────────
            production_nav = create_production_navigation()
            grp_production_nav = production_nav["group"]
            production_stage = production_nav["stage"]
            production_check = production_nav["production_check"]

            # ───────── 合成 ─────────
            syn_page = create_synthesis_page()
            grp_synth = syn_page["group"]
            s_preview_df = syn_page["s_preview_df"]
            s_chapters_sel = syn_page["s_chapters_sel"]
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

            # ───────── 试听与质检 ─────────
            review_page = create_review_page()
            grp_review = review_page["group"]
            e_chapter_table = review_page["e_chapter_table"]
            e_chapter_sel = review_page["e_chapter_sel"]
            e_chapter_audio = review_page["e_chapter_audio"]
            e_seg_sel = review_page["e_seg_sel"]
            e_emo = review_page["e_emo"]
            e_alpha = review_page["e_alpha"]
            e_rate = review_page["e_rate"]
            e_voice = review_page["e_voice"]
            e_regenerate = review_page["e_regenerate"]
            e_seg_audio = review_page["e_seg_audio"]
            e_regenerate_msg = review_page["e_regenerate_msg"]

            # ───────── 导出 ─────────
            export_page = create_export_page()
            grp_export = export_page["group"]
            e_fmt = export_page["e_fmt"]
            e_br = export_page["e_br"]
            e_save_dir = export_page["e_save_dir"]
            e_save_dir_hint = export_page["e_save_dir_hint"]
            e_go = export_page["e_go"]
            e_out = export_page["e_out"]
            e_path = export_page["e_path"]
            e_subtitle = export_page["e_subtitle"]
            e_subtitle_btn = export_page["e_subtitle_btn"]
            e_subtitle_out = export_page["e_subtitle_out"]
            e_subtitle_msg = export_page["e_subtitle_msg"]

            # ───────── 角色单独补录 / 补合成导出 ─────────
            supplement_page = create_supplement_page()
            grp_supplement = supplement_page["group"]
            sup_role = supplement_page["sup_role"]
            sup_refresh = supplement_page["sup_refresh"]
            sup_text = supplement_page["sup_text"]
            sup_split_punct = supplement_page["sup_split_punct"]
            sup_json = supplement_page["sup_json"]
            sup_json_parse = supplement_page["sup_json_parse"]
            sup_json_role = supplement_page["sup_json_role"]
            sup_json_lines = supplement_page["sup_json_lines"]
            sup_emotion = supplement_page["sup_emotion"]
            sup_emo_alpha = supplement_page["sup_emo_alpha"]
            sup_rate = supplement_page["sup_rate"]
            sup_quality = supplement_page["sup_quality"]
            sup_voice = supplement_page["sup_voice"]
            sup_mode = supplement_page["sup_mode"]
            sup_synth = supplement_page["sup_synth"]
            sup_synth_status = supplement_page["sup_synth_status"]
            sup_wavs = supplement_page["sup_wavs"]
            sup_play_all = supplement_page["sup_play_all"]
            sup_play_seg = supplement_page["sup_play_seg"]
            sup_audio = supplement_page["sup_audio"]
            sup_format = supplement_page["sup_format"]
            sup_bitrate = supplement_page["sup_bitrate"]
            sup_export = supplement_page["sup_export"]
            sup_out = supplement_page["sup_out"]
            sup_path = supplement_page["sup_path"]
            # ───────── 设置 ─────────
            set_page = create_settings_page()
            grp_settings = set_page["group"]
            s_provider = set_page["s_provider"]
            s_model = set_page["s_model"]
            s_provider_config = set_page["s_provider_config"]
            s_api_key = set_page["s_api_key"]
            s_base_url = set_page["s_base_url"]
            s_timeout = set_page["s_timeout"]
            s_clear_key = set_page["s_clear_key"]

    # 填充 _GROUPS（运行时装载，供 navigation._goto 使用）
    _GROUPS[:] = [
        grp_overview,
        grp_create_project,
        grp_v4,
        grp_v4_role,
        grp_project,
        grp_voices,
        grp_production_nav,
        grp_synth,
        grp_review,
        grp_export,
        grp_supplement,
        grp_settings,
    ]

    # ═══════════ 侧边栏导航切换 ═══════════

    # 旧的全量刷新契约（22 元组）已移除（阶段三：open_project 首步 + _open_chain_rest 打开链）

    nav_overview.click(
        lambda: _goto("overview"), None, _GROUPS,
        js=activate_js("overview")).then(
        refresh_overview, [ss], [ov_status, ov_progress, ov_task, ov_issues, ov_bookshelf])
    nav_project.click(
        lambda: _goto("project"), None, _GROUPS,
        js=activate_js("project"))
    nav_create_project.click(
        lambda: _goto("create_project"), None, _GROUPS,
        js=activate_js("create_project")).then(
        lambda: (
            "##### v4 创建流程\n导入当前章节并完成结构分析；"
            "AI 未配置时项目仍会先保存，可在角色与声音页面继续分析。"
        ), [], [cp_config_summary])
    nav_v4.click(
        lambda: _goto("v4"), None, _GROUPS,
        js=activate_js("v4")
    ).then(
        lambda: (
            gr.update(choices=v4_ui.scan_v4_projects()),
            gr.update(choices=ProjectService.scan_projects()),
        ),
        None,
        [v4_project, v4_v3_project],
    )
    nav_v4_role.click(
        lambda: _goto("v4_role"), None, _GROUPS,
        js=activate_js("v4_role")
    ).then(
        lambda: gr.update(choices=v4_ui.scan_v4_projects()),
        None,
        [v4r_project],
    )
    nav_settings.click(
        lambda: _goto("settings"), None, _GROUPS,
        js=activate_js("settings")).then(
        settings_ui.load_ai_settings, [], [s_provider, s_model, s_base_url, s_timeout, s_provider_config, s_api_key, s_clear_key]
    ).then(
        settings_ui.load_ai_analysis_settings,
        [s_provider],
        [
            set_page["s_analysis_provider_info"], set_page["s_analysis_depth"],
            set_page["s_analysis_reasoning"], set_page["s_analysis_auto_upgrade"],
            set_page["s_analysis_capability"], set_page["s_analysis_prompt_core"],
            set_page["s_analysis_prompt_supplement"],
            set_page["s_analysis_prompt_preview"], set_page["s_analysis_prompt_version"],
        ],
    )
    nav_voices.click(
        lambda: _goto("voices"), None, _GROUPS,
        js=activate_js("voices")).then(
        refresh_role_list,
        [v_role_search, v_role, ss], [v_table]).then(
        refresh_voice_filters,
        [], [v_bind_category, v_lib_category, v_save_category]).then(
        refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category]).then(
            _refresh_embedded_v4_role_project,
            [p_sel],
            [advanced_role_page["project"]],
        ).then(
        v4_ui.refresh_v4_reanalyze_visibility, [p_sel], [v_reanalyze]).then(
        v4_ui.analysis_progress_text, [p_sel], [v_analysis_progress]).then(
        v4_ui.analysis_trace_text, [p_sel], [v_analysis_trace])
    nav_synth.click(
        lambda: _goto("synth"), None, _GROUPS,
        js=activate_js("synth")).then(
        lambda: gr.update(value="synth"), None, [production_stage]).then(
        refresh_production_voice_choices, [], [e_voice, sup_voice]).then(
        refresh_production_check, [ss], [production_check]).then(
        preview_chapters, [ss], [e_chapter_table, e_seg_audio, e_seg_sel]).then(
        preview_chapter_options, [ss], [e_chapter_sel]).then(
        refresh_queue_list, [ss], [s_queue_list]).then(
        refresh_supplement_roles, [ss], [sup_role])
    nav_export.click(
        lambda: _goto("export"), None, _GROUPS,
        js=activate_js("export")).then(
        refresh_export_default_dir, [ss], [e_save_dir_hint])

    # ── 生产阶段内部导航：合成中心 / 试听质检 / 角色补录 ──
    # 内部切换不移动高亮：nav_active_elem_id 保证 synth/review/supplement
    # 均映射到 nav-synth，故此处不附加 activate_js。
    production_stage.change(_goto, [production_stage], _GROUPS).then(
        refresh_production_check, [ss], [production_check]
    )

    # ── 概览页：书架点选 → 回填 p_sel → open_project 首步 → 打开链刷新 → 切页 ──
    chain = ov_bookshelf.select(
        select_project_from_bookshelf, [ov_bookshelf], [p_sel]
    ).then(open_project, [p_sel, ss], [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status])
    _open_chain_rest(chain).then(
        lambda: _goto("project"), None, _GROUPS,
        js=activate_js("project")
    )

    # ── 概览页快捷操作：「打开项目」切页 → open_project 首步 → 打开链刷新 ──
    chain = ov_open.click(
        lambda: _goto("project"), None, _GROUPS,
        js=activate_js("project")).then(open_project, [p_sel, ss], [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status])
    _open_chain_rest(chain)
    ov_voices.click(
        lambda: _goto("voices"), None, _GROUPS,
        js=activate_js("voices")).then(
        refresh_role_list,
        [v_role_search, v_role, ss], [v_table]).then(
        refresh_voice_filters,
        [], [v_bind_category, v_lib_category, v_save_category]).then(
        refresh_voice_lib, [v_lib_search, v_lib_category], [v_lib_browser, v_lib_category]).then(
            _refresh_embedded_v4_role_project,
            [p_sel],
            [advanced_role_page["project"]],
        ).then(
        v4_ui.analysis_progress_text, [p_sel], [v_analysis_progress]).then(
        v4_ui.analysis_trace_text, [p_sel], [v_analysis_trace])
    ov_synth.click(
        lambda: _goto("synth"), None, _GROUPS,
        js=activate_js("synth")).then(
        lambda: gr.update(value="synth"), None, [production_stage]).then(
        refresh_production_voice_choices, [], [e_voice, sup_voice]).then(
        refresh_production_check, [ss], [production_check]).then(
        preview_chapters, [ss], [e_chapter_table, e_seg_audio, e_seg_sel]).then(
        preview_chapter_options, [ss], [e_chapter_sel]).then(
        refresh_queue_list, [ss], [s_queue_list]).then(
        refresh_supplement_roles, [ss], [sup_role])
    ov_export.click(
        lambda: _goto("export"), None, _GROUPS,
        js=activate_js("export")).then(
        refresh_export_default_dir, [ss], [e_save_dir_hint])

    # ═══════════ events（业务接线，沿用 v2） ═══════════

    # ═══════════ 新建项目页面 ═══════════
    cp_source.change(
        create_ui.derive_project_fields,
        [cp_source, cp_name, cp_title],
        [cp_name, cp_title],
    ).then(
        create_ui.inspect_project_name,
        [cp_name],
        [cp_slot_status, cp_cleanup],
    )
    cp_name.change(
        create_ui.inspect_project_name,
        [cp_name],
        [cp_slot_status, cp_cleanup],
    )
    cp_cleanup.click(
        create_ui.archive_orphan_and_recheck,
        [cp_name],
        [cp_slot_status, cp_cleanup],
    )
    creation_chain = cp_create.click(
        v4_ui.create_v4_from_source,
        [cp_name, cp_source, cp_title, cp_author, cp_source_text],
        [cp_status, cp_result, v4_project, cp_json_result],
        concurrency_limit=1,
        concurrency_id="project-creation",
    ).then(
        lambda project_name: gr.update(
            choices=project_choices(), value=project_name
        ),
        [v4_project],
        [p_sel],
    ).then(
        open_project,
        [p_sel, ss],
        [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status],
    )
    _open_chain_rest(creation_chain).then(
        lambda: _goto("voices"), None, _GROUPS,
        js=activate_js("voices")
    )
    p_migrate.click(
        migrate_v3_to_v4,
        [p_sel],
        [p_migrate_msg, p_sel],
        concurrency_limit=1,
        concurrency_id="project-migration",
    )
    v4_refresh_projects.click(
        lambda: gr.update(choices=v4_ui.scan_v4_projects()),
        None,
        [v4_project],
    )
    v4_open.click(
        v4_ui.open_v4_project,
        [v4_project],
        [
            v4_summary,
            v4_review["table"],
            v4_review["speaker"],
            v4_voice_speaker,
            v4_review["merge_source"],
            v4_review["merge_target"],
            v4_plan["table"],
            v4_queue["table"],
            v4_profile,
            v4_queue["summary"],
            v4_chapter,
            v4_review["candidates_table"],
            v4_review["candidate"],
            v4_review["candidate_target"],
        ],
    )
    v4_review["extract"].click(
        v4_ui.extract_v4_characters,
        [v4_project],
        [v4_review["extract_status"]],
        concurrency_limit=1,
        concurrency_id="v4-character-extraction",
    ).then(
        v4_ui.open_v4_project,
        [v4_project],
        [
            v4_summary,
            v4_review["table"],
            v4_review["speaker"],
            v4_voice_speaker,
            v4_review["merge_source"],
            v4_review["merge_target"],
            v4_plan["table"],
            v4_queue["table"],
            v4_profile,
            v4_queue["summary"],
            v4_chapter,
            v4_review["candidates_table"],
            v4_review["candidate"],
            v4_review["candidate_target"],
        ],
    )
    v4_review["route"].click(
        v4_ui.route_v4_speakers,
        [v4_project],
        [v4_review["status"]],
        concurrency_limit=1,
        concurrency_id="v4-speaker-routing",
    ).then(
        v4_ui.open_v4_project,
        [v4_project],
        [
            v4_summary,
            v4_review["table"],
            v4_review["speaker"],
            v4_voice_speaker,
            v4_review["merge_source"],
            v4_review["merge_target"],
            v4_plan["table"],
            v4_queue["table"],
            v4_profile,
            v4_queue["summary"],
            v4_chapter,
            v4_review["candidates_table"],
            v4_review["candidate"],
            v4_review["candidate_target"],
        ],
    )
    v4_review["stop_route"].click(
        v4_ui.stop_v4_routing,
        [v4_project],
        [v4_review["status"]],
    )
    v4_review["assign"].click(
        v4_ui.assign_v4_speaker,
        [
            v4_project,
            v4_segment_ids,
            v4_review["speaker"],
            v4_review["new_speaker"],
            v4_review["lock_speaker"],
        ],
        [v4_review["status"]],
    ).then(
        v4_ui.open_v4_project,
        [v4_project],
        [
            v4_summary,
            v4_review["table"],
            v4_review["speaker"],
            v4_voice_speaker,
            v4_review["merge_source"],
            v4_review["merge_target"],
            v4_plan["table"],
            v4_queue["table"],
            v4_profile,
            v4_queue["summary"],
            v4_chapter,
            v4_review["candidates_table"],
            v4_review["candidate"],
            v4_review["candidate_target"],
        ],
    )
    v4_review["confirm_candidate"].click(
        v4_ui.confirm_v4_candidate,
        [v4_project, v4_review["candidate"]],
        [v4_review["candidate_status"]],
    ).then(
        v4_ui.open_v4_project,
        [v4_project],
        [
            v4_summary,
            v4_review["table"],
            v4_review["speaker"],
            v4_voice_speaker,
            v4_review["merge_source"],
            v4_review["merge_target"],
            v4_plan["table"],
            v4_queue["table"],
            v4_profile,
            v4_queue["summary"],
            v4_chapter,
            v4_review["candidates_table"],
            v4_review["candidate"],
            v4_review["candidate_target"],
        ],
    )
    v4_review["reject_candidate"].click(
        v4_ui.reject_v4_candidate,
        [v4_project, v4_review["candidate"]],
        [v4_review["candidate_status"]],
    ).then(
        v4_ui.open_v4_project,
        [v4_project],
        [
            v4_summary,
            v4_review["table"],
            v4_review["speaker"],
            v4_voice_speaker,
            v4_review["merge_source"],
            v4_review["merge_target"],
            v4_plan["table"],
            v4_queue["table"],
            v4_profile,
            v4_queue["summary"],
            v4_chapter,
            v4_review["candidates_table"],
            v4_review["candidate"],
            v4_review["candidate_target"],
        ],
    )
    v4_review["merge_candidate"].click(
        v4_ui.merge_v4_candidate,
        [v4_project, v4_review["candidate"], v4_review["candidate_target"]],
        [v4_review["candidate_status"]],
    ).then(
        v4_ui.open_v4_project,
        [v4_project],
        [
            v4_summary,
            v4_review["table"],
            v4_review["speaker"],
            v4_voice_speaker,
            v4_review["merge_source"],
            v4_review["merge_target"],
            v4_plan["table"],
            v4_queue["table"],
            v4_profile,
            v4_queue["summary"],
            v4_chapter,
            v4_review["candidates_table"],
            v4_review["candidate"],
            v4_review["candidate_target"],
        ],
    )
    v4_review["merge"].click(
        v4_ui.merge_v4_speakers,
        [
            v4_project,
            v4_review["merge_source"],
            v4_review["merge_target"],
        ],
        [v4_review["status"]],
    )
    v4_bind_voice.click(
        v4_ui.bind_v4_voice,
        [v4_project, v4_voice_speaker, v4_voice_file],
        [v4_voice_status],
    )
    v4_plan["generate"].click(
        v4_ui.generate_v4_plan,
        [v4_project],
        [
            v4_plan["table"],
            v4_plan_status,
            v4_queue["table"],
            v4_queue["summary"],
        ],
    )
    v4_queue["start"].click(
        v4_ui.run_v4_synthesis,
        [v4_project],
        [
            v4_queue["status"],
            v4_queue["table"],
            v4_queue["summary"],
            v4_chapter,
        ],
        concurrency_limit=1,
        concurrency_id="v4-synthesis",
    )
    v4_queue["cancel"].click(
        v4_ui.cancel_v4_synthesis,
        [v4_project],
        [v4_queue["status"], v4_queue["table"], v4_queue["summary"]],
    )
    v4_queue["refresh"].click(
        v4_ui.refresh_v4_queue,
        [v4_project],
        [v4_queue["table"], v4_queue["summary"]],
    )
    v4_chapter.change(
        v4_ui.chapter_audio,
        [v4_project, v4_chapter],
        [v4_chapter_audio],
    )
    v4_export.click(
        v4_ui.export_v4,
        [v4_project, v4_export_format, v4_bitrate],
        [v4_export_file, v4_export_status],
    )
    v4_migrate.click(
        v4_ui.migrate_v3_project,
        [v4_v3_project],
        [v4_migration_status, v4_project],
    )
    # 独立页仅保留为开发调试入口；正式用户从「③ 角色与声音」折叠区进入。
    _wire_v4_role_controls(v4r_page)
    cp_json_file.change(
        create_ui.derive_json_project_name,
        [cp_json_file, cp_json_name],
        [cp_json_name],
    ).then(
        create_ui.inspect_project_name,
        [cp_json_name],
        [cp_json_slot_status, cp_json_cleanup],
    )
    cp_json_name.change(
        create_ui.inspect_project_name,
        [cp_json_name],
        [cp_json_slot_status, cp_json_cleanup],
    )
    cp_json_cleanup.click(
        create_ui.archive_orphan_and_recheck,
        [cp_json_name],
        [cp_json_slot_status, cp_json_cleanup],
    )
    cp_json_create.click(
        create_ui.create_from_json,
        [cp_json_name, cp_json_file],
        [cp_json_result, p_sel, cp_result],
        concurrency_limit=1,
        concurrency_id="project-creation",
    )

    # ═══════════ 项目管理 ═══════════

    # ═══════════ 设置页面 ═══════════
    wire_settings_page(set_page)

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
                "unbind_voice": unbind_voice,
                "play_lib_voice": play_lib_voice,
                "save_to_lib": save_to_lib,
                "filter_vlib_by_category": filter_vlib_by_category,
                "refresh_voice_lib": refresh_voice_lib,
                "select_voice_from_browser": select_voice_from_browser,
                "preview_bound_voice": preview_bound_voice,
                "confirm_v4_speaker_candidate": v4_ui.confirm_v4_speaker_candidate,
                "reject_v4_speaker_candidate": v4_ui.reject_v4_speaker_candidate,
            },
        },
    )
    analysis_chain = v_continue_analysis.click(
        lambda: "⏳ 开始分析…", None, [v_analysis_msg]
    ).then(
        v4_ui.continue_v4_analysis,
        [p_sel],
        [v_analysis_msg, v_analysis_progress],
        concurrency_limit=1,
        concurrency_id="v4-analysis",
    ).then(
        open_project,
        [p_sel, ss],
        [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status],
    ).then(
        v4_ui.analysis_trace_text, [p_sel], [v_analysis_trace]
    )
    _open_chain_rest(analysis_chain)
    reanalysis_chain = v_reanalyze.click(
        lambda: "⏳ 开始重新分析…", None, [v_analysis_msg]
    ).then(
        v4_ui.reanalyze_v4_project,
        [p_sel],
        [v_analysis_msg, v_analysis_progress],
        concurrency_limit=1,
        concurrency_id="v4-analysis",
    ).then(
        open_project,
        [p_sel, ss],
        [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status],
    ).then(
        v4_ui.analysis_trace_text, [p_sel], [v_analysis_trace]
    )
    _open_chain_rest(reanalysis_chain)
    _wire_v4_role_controls(advanced_role_page)

    p_refresh.click(refresh_projects_full, [], [p_sel])
    chain = p_open.click(open_project, [p_sel, ss], [p_summary, v_table, v_role, v_role_title, v_lib, s_log, v_status])
    _open_chain_rest(chain)
    p_open_dir.click(open_project_dir, [p_sel], [p_open_dir_msg])
    p_del.click(delete_project, p_sel, p_sel)
    s_start.click(do_synthesis, [ss, s_beam, s_emo, s_override, s_alpha, s_rate, s_chapters_sel], outputs=[s_log, s_queue_list]).then(
        refresh_top_status, [ss], [top_status])
    s_cancel.click(cancel, [ss], outputs=s_log).then(refresh_top_status, [ss], [top_status])
    s_pause.click(pause_synthesis, [ss], [s_queue_list, s_pause, s_resume])
    s_resume.click(resume_synthesis, [ss], [s_queue_list, s_pause, s_resume])
    s_open_btn.click(open_segments_folder, [ss], s_open_msg)
    e_chapter_sel.change(preview_chapter, [ss, e_chapter_sel], [e_chapter_audio])
    e_seg_sel.change(play_segment, [e_seg_sel, ss], e_seg_audio)
    e_regenerate.click(regenerate_segment, [e_seg_sel, e_emo, e_alpha, e_rate, e_voice, ss], [e_seg_audio, e_regenerate_msg])
    e_go.click(do_export, [e_fmt, e_br, e_save_dir, ss], [e_out, e_path])
    e_subtitle_btn.click(do_export_subtitles, [ss, e_subtitle], [e_subtitle_out, e_subtitle_msg])

    # ── 角色单独补录 / 补合成导出 ──
    sup_refresh.click(refresh_supplement_roles, [ss], [sup_role])
    sup_json_parse.click(do_supplement_parse_json, [sup_json, ss],
                         [sup_role, sup_json_role, sup_json_lines, sup_synth_status])
    sup_synth.click(do_supplement_synth,
                    [sup_role, sup_mode, sup_text, sup_json_role, sup_json_lines,
                     sup_emotion, sup_emo_alpha, sup_rate, sup_quality,
                     sup_split_punct, sup_voice, ss],
                    [sup_wavs, sup_synth_status])
    sup_export.click(do_supplement_export,
                     [sup_format, sup_bitrate, sup_wavs, sup_role, ss],
                     [sup_out, sup_path])
    sup_play_all.click(lambda wavs, ss: play_supplement_preview("all", wavs, ss),
                       [sup_wavs, ss], [sup_audio])
    sup_play_seg.click(lambda wavs, ss: play_supplement_preview("seg", wavs, ss),
                       [sup_wavs, ss], [sup_audio])

if __name__ == "__main__":
    os.chdir(BASE)
    from lib.logging_setup import setup_logging
    from services.service_lifecycle import ServiceLifecycle

    setup_logging(log_dir=os.path.join(BASE, "logs"))
    # 数据目录外置后，首次启动把程序目录内的旧克隆音色迁移到外置 voice_library（一次性、安全拷贝）。
    config.migrate_legacy_voice_library()
    # Gradio 默认只允许 serve 当前 cwd 与 tempdir 下的文件。数据目录（音色库、预览、
    # 合成产物、导出）已全部外置到 config.get_data_dir()（如 D:\AudiobookStudio），
    # 不在 cwd 内，返回其下音频路径给 Audio/File 组件会在序列化阶段触发 InvalidPathError
    # 导致前端显示「错误」。将其加入 allowed_paths 白名单，递归放行其下所有子目录。
    def _close_runtime_resources():
        from lib import tts_engine
        from tts.runtime_cleanup import release_inference_memory

        tts_engine.close_engine()
        release_inference_memory(clear_cuda_cache=True)

    ServiceLifecycle.configure(
        pid_path=ServiceLifecycle.pid_path_for_data_dir(config.get_data_dir()),
        port=7862,
        # The small delay in ServiceLifecycle lets Gradio return the confirmation
        # message before the owned process exits.
        exit_callback=lambda: os._exit(0),
    )
    # Hooks are released in reverse registration order: stop workers first,
    # then unload the resident TTS/CUDA objects they may still reference.
    ServiceLifecycle.register_cleanup("tts-runtime", _close_runtime_resources)
    ServiceLifecycle.register_cleanup(
        "v3-synthesis", lambda: SynthesisService.shutdown_all(timeout=5.0)
    )
    ServiceLifecycle.register_cleanup(
        "v4-synthesis", lambda: V4SynthesisService.shutdown_all(timeout=5.0)
    )

    demo = app.queue()
    ServiceLifecycle.register_server(demo.close)
    demo.launch(
        server_name="0.0.0.0",
        server_port=7862,
        share=False,
        inbrowser=True,
        allowed_paths=[config.get_data_dir()],
    )
