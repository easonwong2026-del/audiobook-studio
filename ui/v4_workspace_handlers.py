"""Thin Gradio adapters for the integrated v4 project shell."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr

from ai.character_extraction import create_character_extraction_adapter
from ai.speaker_routing import create_speaker_routing_adapter
from domain.v4 import ScriptDocument, SpeakersDocument
from domain.v4.character_extraction import CharacterCandidatesDocument
from domain.v4.production import VoiceBindings
from lib import config
from repositories.audio_cache_repository import AudioCacheRepository
from repositories.chapter_analysis_repository import ChapterAnalysisStateRepository
from repositories.character_candidates_repository import CharacterCandidatesRepository
from repositories.character_extraction_checkpoint_repository import (
    CharacterExtractionCheckpointRepository,
)
from repositories.production_repository import ProductionRepository
from repositories.project_v4_repository import ProjectV4Repository
from repositories.routing_checkpoint_repository import RoutingCheckpointRepository
from repositories.runtime_repository import RuntimeRepository
from repositories.v4_analysis_repository import V4AnalysisRepository
from services.ai_settings import AiSettingsService
from services.chapter_analysis_service import ChapterAnalysisService
from services.chapter_assembler import ChapterAssembler
from services.character_candidate_service import (
    CharacterCandidateReviewService,
    candidate_rows,
)
from services.character_extraction_service import (
    CharacterExtractionService,
    merge_character_candidates,
)
from services.invalidation_service import InvalidationService
from services.migration_v3_to_v4 import V3ToV4MigrationService
from services.plan_preview import synthesis_plan_rows, synthesis_plan_summary
from services.project import ProjectService
from services.speaker_review_service import SpeakerReviewService
from services.speaker_routing_service import SpeakerRoutingService
from services.synthesis_executor import SynthesisExecutor
from services.synthesis_planner import SynthesisPlanner
from services.v4_analysis_validity import (
    DIALOGUE_COVERAGE_UNKNOWN_LABEL,
    REASON_MESSAGES,
    ReasonCode,
)
from services.v4_export import V4ExportService
from services.v4_project_analysis_pipeline import V4ProjectAnalysisPipeline
from services.v4_project_creation import V4ProjectCreationService
from services.v4_project_service import V4ProjectService
from services.v4_synthesis_service import V4SynthesisService
from services.v4_voice_service import V4VoiceService
from tts.indextts2_adapter import IndexTTS2Adapter
from tts.text_measurement import CharacterMeasurer, ConservativeTokenMeasurer


def _root() -> Path:
    return Path(config.get_projects_root())


def _analysis_summary_text(state: dict | None) -> str:
    if not state:
        return ""
    summary = state.get("summary") or {}
    status = state.get("status")
    if status == "waiting_for_ai":
        return "\n\n⚠ AI 尚未配置，可在设置中配置后点击“快速分析当前章节”。"
    if not summary:
        return ""
    coverage = summary.get("dialogue_coverage")
    coverage_text = (
        f"{coverage * 100:.0f}%"
        if coverage is not None
        else DIALOGUE_COVERAGE_UNKNOWN_LABEL
    )
    text = (
        "\n\n"
        f"AI 分析：{status or '未知'} · "
        f"识别角色 {summary.get('identified_characters', 0)} · "
        f"自动确认 {summary.get('auto_confirmed_characters', 0)} · "
        f"需要检查 {summary.get('needs_review_characters', 0) + summary.get('dialogue_unresolved', 0)} · "
        f"已过滤噪音 {summary.get('filtered_noise', 0)} · "
        f"对白自动归属 {coverage_text}"
    )
    reason_lines = _reason_lines(state)
    if reason_lines:
        text += "\n" + "\n".join(f"⚠ {line}" for line in reason_lines)
    return text


def _reason_lines(state: dict | None) -> list[str]:
    """从 analysis.json 的 validity.reason_codes 生成用户可读原因行。"""
    if not state:
        return []
    reason_codes = ((state.get("validity") or {}).get("reason_codes")) or []
    lines: list[str] = []
    for code_value in reason_codes:
        code = ReasonCode.from_value(code_value)
        if code is None or code == ReasonCode.OK:
            continue
        text = REASON_MESSAGES.get(code, "")
        if text:
            lines.append(text)
    return lines


# ── AI 分析实时进展（读 analysis.json stages 渲染，切换页面不丢失）──

_STAGE_LABELS = {
    "book_understanding": "阅读全书（人物记忆）",
    "script_director": "分析章节剧本",
    "script_review": "复查对白归属",
    "character_extraction": "提取角色",
    "character_consolidation": "统一角色和别名",
    "auto_confirmation": "自动确认高可信角色",
    "speaker_routing": "分析对白归属",
    "consistency_check": "检查分析结果",
    "import": "导入书稿",
}

_STAGE_SLOTS = {
    "book_understanding": 1,
    "script_director": 2,
    "script_review": 3,
    "character_extraction": 1,
    "character_consolidation": 2,
    "auto_confirmation": 3,
    "speaker_routing": 4,
    "consistency_check": 5,
}

# AI-first 管线固定 6 步展示：前 3 步对应 analysis.json 的 stages 键，
# 后 3 步为保存/检查/完成（无独立持久化阶段）。
_AI_FIRST_STAGE_ROWS = (
    ("book_understanding", "阅读全书（人物记忆）"),
    ("script_director", "分析章节剧本"),
    ("script_review", "复查对白归属"),
)
_AI_FIRST_TAIL_ROWS = ("保存分析结果", "一致性检查", "分析完成")


def _stage_label(stage_name: str) -> str:
    return _STAGE_LABELS.get(stage_name, stage_name or "未知")


def _stage_slot(stage_name: str) -> int:
    return _STAGE_SLOTS.get(stage_name, 0)


def _elapsed_text(started_at: str) -> str:
    """把 ISO started_at 转成「N 分钟 / N 秒」已耗时文本（无时区容错）。"""
    if not started_at:
        return ""
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - start).total_seconds()))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    return f"{hours} 小时 {minutes % 60} 分钟"


def _format_duration(duration_ms) -> str:
    """把 stages[*].duration_ms 转成「N 分 N 秒」完成耗时文本。"""
    try:
        seconds = max(0, int(duration_ms) // 1000)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分 {seconds % 60} 秒"
    hours = minutes // 60
    return f"{hours} 小时 {minutes % 60} 分"


def _stage_status_text(stage: dict | None) -> str:
    """单个持久化阶段的用户可读状态（含已耗时/用时）。"""
    stage = stage or {}
    status = stage.get("status") or "unknown"
    if status == "completed":
        duration = _format_duration(stage.get("duration_ms"))
        return f"✅ 完成 · 用时 {duration}" if duration else "✅ 完成"
    if status in {"running", "partial"}:
        elapsed = _elapsed_text(stage.get("started_at", ""))
        return f"🔄 进行中 · 从 {elapsed}前开始" if elapsed else "🔄 进行中"
    if status == "failed":
        return "❌ 失败"
    if status == "skipped":
        return "⏭ 跳过"
    if status == "invalidated":
        return "♻ 已失效"
    return "⏳ 等待"


def _analysis_progress_text(state: dict | None) -> str:
    """从 analysis.json 状态渲染可见进展区：状态 + 当前阶段 x/6 + 阶段明细。"""
    if not state:
        return "打开项目后显示 AI 分析进度。"
    status = state.get("status") or ""
    if status == "waiting_for_ai":
        return "⚠ AI 尚未配置，可在此页配置后点击“继续分析”。"
    stages = state.get("stages") or {}
    current_stage = str(state.get("current_stage") or "")
    if status == "completed":
        header = "### ✅ AI 分析已完成"
    elif status == "running":
        header = "### ⏳ AI 分析进行中"
    elif status == "partial":
        header = "### ⚠ AI 分析未完成（部分章节失败，可继续分析重试）"
    elif status == "needs_attention":
        header = "### ⚠ AI 分析需要人工确认，可继续分析重试"
    else:
        header = f"### AI 分析状态：{status or '未知'}"
    lines = [header]
    if current_stage and current_stage not in {"completed", "needs_attention", "awaiting_ai"}:
        slot = _stage_slot(current_stage)
        slot_text = f"（第 {slot}/6 步）" if slot else ""
        elapsed = _elapsed_text((stages.get(current_stage) or {}).get("started_at", ""))
        elapsed_text = f" · 已进行 **{elapsed}**" if elapsed else ""
        lines.append(
            f"当前阶段：**{_stage_label(current_stage)}**{slot_text}{elapsed_text}"
        )
    for index, (stage_key, label) in enumerate(_AI_FIRST_STAGE_ROWS, start=1):
        lines.append(f"{index}. {label}：{_stage_status_text(stages.get(stage_key))}")
    tail_status = "✅ 完成" if status == "completed" else "⏳ 等待"
    for index, label in enumerate(_AI_FIRST_TAIL_ROWS, start=4):
        lines.append(f"{index}. {label}：{tail_status}")
    covered = {key for key, _label in _AI_FIRST_STAGE_ROWS}
    extra = [
        (key, value)
        for key, value in stages.items()
        if key not in covered and key not in {"completed", "needs_attention"}
    ]
    if extra:
        lines.append("")
        lines.append("其他阶段：")
        for key, stage in extra:
            lines.append(f"- {_stage_label(key)}：{_stage_status_text(stage)}")
    return "\n".join(lines)


def analysis_progress_text(project_name: str) -> str:
    """从磁盘读取 analysis.json 渲染真实阶段与耗时（切换页面时显示真实状态）。"""
    if not project_name:
        return "打开项目后显示 AI 分析进度。"
    try:
        project = _root() / project_name
        source = (project / "source/source.txt").read_text(encoding="utf-8")
        script = _load_script(project, source)
        state = V4AnalysisRepository(project).load(script.source_sha256)
        if state is None:
            chapter_states = [
                ChapterAnalysisStateRepository(project).load(item.chapter_id)
                for item in script.chapters
            ]
            state = next((item for item in reversed(chapter_states) if item), None)
            if state:
                status = state.get("status") or "unknown"
                phase2 = (state.get("phase2") or {}).get("status") or (
                    state.get("stats") or {}
                ).get("phase2_status", "skipped")
                if status == "waiting_for_ai":
                    return "### ⚠ 当前章节等待 AI\n章节原文已保存，配置 Provider 后可继续。"
                return (
                    f"### {'✅' if status in {'analyzed', 'ready_for_synthesis', 'completed'} else '⚠'} 当前章节分析：{status}"
                    f"\n阶段：结构分析已完成 · 演绎导演：{phase2}"
                    f"\n请求 {state.get('ai_requests', 0)} · 修复 {state.get('retries', 0)} · "
                    f"Provider {state.get('provider') or '未配置'} · 模型 {state.get('model') or '默认'}"
                    + (f"\n⚠ {state.get('message')}" if state.get("message") else "")
                )
    except (OSError, ValueError, TypeError):
        state = None
    return _analysis_progress_text(state)


def analysis_trace_text(project_name: str) -> str:
    """Render sanitized analysis trace: stage, model, mode, timing and outcome."""
    if not project_name:
        return "分析追踪会显示在这里。"
    try:
        project = _root() / project_name
        source = (project / "source/source.txt").read_text(encoding="utf-8")
        script = _load_script(project, source)
        states = [
            ChapterAnalysisStateRepository(project).load(item.chapter_id)
            for item in script.chapters
        ]
        state = next((item for item in reversed(states) if item), None)
    except (OSError, ValueError, TypeError):
        state = None
    if not state:
        return "分析追踪会显示在这里。"
    trace = state.get("trace") or []
    lines = [
        "#### 本章分析追踪",
        (
            f"Provider：`{state.get('provider') or '未配置'}` · 模型：`{state.get('model') or '默认'}` · "
            f"推理：`{state.get('reasoning_mode') or '未设置'}`"
        ),
        (
            f"协议：`{state.get('protocol_version') or '未知'}` · 核心提示词：`{state.get('prompt_version') or '未知'}` · "
            f"最终状态：**{state.get('status') or '未知'}**"
        ),
    ]
    if not trace:
        return "\n".join(lines + ["暂无请求记录。"])
    lines.append("请求记录：")
    for item in trace[-8:]:
        event = item.get("event", "event")
        stage = item.get("stage", "phase1_structure")
        duration = item.get("duration_ms")
        suffix = f" · {duration} ms" if duration is not None else ""
        detail = item.get("error") or item.get("final_status") or ""
        lines.append(f"- `{stage}` · {event}{suffix} {detail}".rstrip())
    return "\n".join(lines)


def v4_analysis_buttons_visibility(state: dict | None) -> dict:
    """按分析状态返回「继续 AI 分析 / 重新分析」按钮可见性。

    ``status == "needs_attention"`` 时两者均可见（PRD 待明确事项 6）。
    """
    status = (state or {}).get("status")
    if status == "needs_attention":
        return {
            "v_continue_analysis": gr.update(visible=True),
            "v_reanalyze": gr.update(visible=True),
        }
    return {
        "v_continue_analysis": gr.update(visible=True),
        "v_reanalyze": gr.update(visible=False),
    }


def refresh_v4_reanalyze_visibility(project_name: str):
    """按 analysis.json 状态返回「重新分析」按钮可见性（薄封装，供接线）。"""
    state = None
    if project_name:
        try:
            project = _root() / project_name
            source = (project / "source/source.txt").read_text(encoding="utf-8")
            script = _load_script(project, source)
            state = V4AnalysisRepository(project).load(script.source_sha256)
        except (OSError, ValueError, TypeError):
            state = None
    return v4_analysis_buttons_visibility(state)["v_reanalyze"]


def _chapter_analysis_summary(project: Path, script: ScriptDocument) -> str:
    """Show the latest fast-path state without reading the full-book cache."""
    repository = ChapterAnalysisStateRepository(project)
    states = [repository.load(item.chapter_id) for item in script.chapters]
    states = [item for item in states if item]
    if not states:
        return ""
    state = states[-1]
    status = state.get("status") or "unknown"
    stats = state.get("stats") or {}
    if status == "waiting_for_ai":
        return "\n\n⚠ 当前章节等待 AI 配置；不会自动切换到规则角色识别。"
    return (
        "\n\n"
        f"快速章节分析：{status} · 请求 {state.get('ai_requests', 0)} · "
        f"重试 {state.get('retries', 0)} · 片段 {stats.get('segments', 0)} · "
        f"待确认 {stats.get('unresolved_segments', 0)} · AI 候选 {stats.get('candidate_roles', 0)} · "
        f"阶段 2 {stats.get('phase2_status', 'skipped')} · "
        f"未绑定音色 {len(stats.get('unbound_speakers', []))} · "
        f"Provider {state.get('provider') or '未配置'} · "
        f"模型 {state.get('model') or '默认'} · 推理 {state.get('reasoning_mode') or '未设置'} · "
        f"更新时间 {state.get('updated_at', '')[:19]}"
        + (f"\n\n⚠ {state.get('message')}" if status in {"needs_attention", "failed"} else "")
    )


def scan_v4_projects() -> list[str]:
    root = _root()
    if not root.is_dir():
        return []
    names = []
    for path in root.iterdir():
        manifest = path / "project.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("schema_version") == "audiobook-project-v4":
            names.append(path.name)
    return sorted(names)


def _ensure_progress(progress):
    return progress or gr.Progress()


def _refresh_session_v4(project_name: str, session) -> None:
    if session is None:
        return
    try:
        context = V4ProjectService.open_project(project_name)
        if context is not None:
            session.set_v4_project(project_name, context.script, context.speakers)
    except Exception:  # noqa: BLE001 - disk refresh is supplementary
        return


def confirm_v4_speaker_candidate(
    project_name: str, speaker_id: str, session=None
) -> str:
    """Normal voice page action for promoting an AI candidate."""
    if not project_name or not speaker_id:
        return "⚠ 请先选择一个待确认的 AI 候选角色"
    try:
        _script, _speakers, attached = ChapterAnalysisService.confirm_candidate(
            _root() / project_name, str(speaker_id)
        )
        _refresh_session_v4(project_name, session)
        return f"✅ 已确认候选角色；{attached} 个章节片段已归属该角色。"
    except Exception as exc:  # noqa: BLE001 - visible user action error
        return f"❌ 确认候选角色失败：{str(exc)[:300]}"


def reject_v4_speaker_candidate(
    project_name: str, speaker_id: str, session=None
) -> str:
    """Normal voice page action for denying an AI candidate."""
    if not project_name or not speaker_id:
        return "⚠ 请先选择一个待确认的 AI 候选角色"
    try:
        _script, _speakers, cleared = ChapterAnalysisService.reject_candidate(
            _root() / project_name, str(speaker_id)
        )
        _refresh_session_v4(project_name, session)
        return f"✅ 已拒绝候选角色；{cleared} 个片段回到未知说话人状态，未改成旁白。"
    except Exception as exc:  # noqa: BLE001 - visible user action error
        return f"❌ 拒绝候选角色失败：{str(exc)[:300]}"


def create_v4_from_source(
    name, source_file, title, author, source_text="", progress=None
):
    progress = _ensure_progress(progress)
    source = getattr(source_file, "name", None) or source_file
    pasted = str(source_text or "").strip()
    if not name or (not source and not pasted):
        return "⚠ 请输入项目名称，并上传书稿或粘贴当前章节原文", "", gr.update(), gr.update()
    try:
        result = V4ProjectCreationService(
            ProjectV4Repository(_root())
        ).create_from_source(
            source if source else None,
            str(name),
            title=str(title or ""),
            author=str(author or ""),
            source_text=pasted or None,
            progress_callback=lambda message: _report_analysis_progress(progress, message),
        )
        manifest = ProjectV4Repository(_root()).load_manifest(result.project_path)
        script = _load_script(result.project_path)
        total = sum(len(item.segments) for item in script.chapters)
        if result.analysis is not None:
            analysis_message = result.analysis.message
        elif result.analysis_error:
            analysis_message = f"⚠ 项目已创建，但自动分析未完成：{result.analysis_error}"
        else:
            analysis_message = "项目已创建，可在角色与声音页面继续分析。"
        message = (
            "### ✅ v4 项目创建成功\n\n"
            f"- 作品：**{manifest.title}**\n"
            f"- 语义片段：{total}\n"
            f"- 待确认角色：{result.unresolved_segments}\n"
            f"- Source SHA：`{script.source_sha256}`\n\n"
            f"{analysis_message}\n\n"
            "请到「③ 角色与声音」检查当前章节角色、绑定音色，然后生成本章计划并合成。"
        )
        choices = scan_v4_projects()
        return (
            "✅ 本地 source-first 项目已创建",
            message,
            gr.update(choices=choices, value=result.project_path.name),
            gr.update(),
        )
    except Exception as exc:  # noqa: BLE001 - convert create failure for UI
        return f"❌ 创建失败：{str(exc)[:500]}", "", gr.update(), gr.update()


def _report_analysis_progress(progress, message: str) -> None:
    labels = {
        "正在导入书稿": 1,
        "正在识别章节": 2,
        "按当前章节导入原文": 1,
        "已接收粘贴的当前章节": 1,
        "正在分析当前章节（1/3）": 1,
        "正在校验章节结果（2/3）": 2,
        "正在修复章节结果（重试 1/1）": 2,
        "正在保存章节结果（3/3）": 3,
        "正在阅读全书": 2,
        "正在建立人物关系": 3,
        "正在分析章节剧本": 4,
        "正在复查对白归属": 5,
        "正在保存分析结果": 6,
        "正在提取角色": 3,
        "正在统一角色和别名": 4,
        "正在自动确认高可信角色": 4,
        "正在分析对白归属": 5,
        "正在检查分析结果": 6,
        "分析完成": 6,
        "分析完成（有阶段需要继续）": 6,
    }
    try:
        progress((labels.get(message, 1), 6), desc=message)
    except Exception:  # noqa: BLE001 - progress UI is non-critical
        return


def open_v4_project(name: str):
    if not name:
        return (
            "请选择 v4 项目", [], gr.update(choices=[]),
            gr.update(choices=[]), gr.update(choices=[]), gr.update(choices=[]),
            [], [], "", "", gr.update(choices=[]), [],
            gr.update(choices=[]), gr.update(choices=[]),
        )
    project = _root() / name
    RuntimeRepository(project / "runtime/runtime.db").initialize()
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    candidates = _load_candidates(project, script.source_sha256)
    production = ProductionRepository(project)
    _voices, _performance, _pronunciation, profile = production.load_inputs()
    unresolved = SpeakerReviewService.unresolved_rows(source, script)
    review_rows = [
        [item["segment_id"], item["chapter_id"], item["text"]]
        for item in unresolved
    ]
    speaker_choices = [
        (item.display_name, item.speaker_id) for item in speakers.speakers
    ]
    candidate_choices = [
        (item.display_name, item.candidate_id)
        for item in candidates.candidates
        if item.status == "candidate"
    ]
    merge_target_choices = [
        (item.display_name, item.speaker_id)
        for item in speakers.speakers
        if item.speaker_type == "character" and item.status == "confirmed"
    ]
    plan = production.load_plan()
    plan_rows = synthesis_plan_rows(plan) if plan else []
    queue_rows = _queue_rows(project)
    analysis = V4AnalysisRepository(project).load(script.source_sha256)
    analysis_summary = _analysis_summary_text(analysis) or _chapter_analysis_summary(
        project, script
    )
    summary = (
        f"**{name}** · {len(script.chapters)} 章 · "
        f"{sum(len(item.segments) for item in script.chapters)} 片段 · "
        f"{len(unresolved)} 待确认"
        f"{analysis_summary}"
    )
    profile_text = (
        f"引擎 `{profile.engine}` · 模型 `{profile.model_version or '目标机实测'}` · "
        f"硬件 `{profile.hardware_profile}` · Profile `{profile.profile_id}` · "
        f"{profile.limits.metric} "
        f"推荐/最大 {profile.limits.preferred}/{profile.limits.maximum} · "
        f"并发 {profile.concurrency} · OOM 自动拆分 "
        f"{'开启' if profile.oom_retry else '关闭'} · 情绪 "
        f"{profile.emotion.get('mode', 'text_auto')}"
    )
    chapters = [
        item.chapter_id
        for item in script.chapters
        if (project / "audio/chapters" / f"{item.chapter_id}.wav").is_file()
    ]
    return (
        summary,
        review_rows,
        gr.update(choices=speaker_choices),
        gr.update(choices=speaker_choices),
        gr.update(choices=speaker_choices),
        gr.update(choices=speaker_choices),
        plan_rows,
        queue_rows,
        profile_text,
        _queue_summary(project),
        gr.update(choices=chapters, value=chapters[0] if chapters else None),
        candidate_rows(candidates),
        gr.update(choices=candidate_choices, value=None),
        gr.update(choices=merge_target_choices, value=None),
    )


def extract_v4_characters(project_name: str):
    """Extract chapter candidates; this never mutates the formal speaker table."""
    if not project_name:
        return "⚠ 请先选择 v4 项目"
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    config_values = AiSettingsService.get_effective_provider_config()
    provider = config_values["provider"]
    if provider not in {"deepseek", "openai"}:
        return "⚠ 请在设置中选择并配置 DeepSeek 或 OpenAI"
    adapter = create_character_extraction_adapter(
        provider,
        api_key=config_values["api_key"],
        model=config_values["model"],
        base_url=config_values["base_url"],
        timeout=config_values["timeout"],
    )
    result = CharacterExtractionService(
        adapter,
        CharacterExtractionCheckpointRepository(
            project / "runtime/character_extraction"
        ),
        CharacterCandidatesRepository(project),
    ).extract(source, script)
    return (
        f"✅ 完成章节 {result.completed_chapters}，失败 "
        f"{result.failed_chapters}，候选角色 {len(result.candidates.candidates)}"
    )


def route_v4_speakers(project_name: str):
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    config_values = AiSettingsService.get_effective_provider_config()
    provider = config_values["provider"]
    if provider not in {"deepseek", "openai"}:
        return "⚠ 请在设置中选择并配置 DeepSeek 或 OpenAI"
    adapter = create_speaker_routing_adapter(
        provider,
        api_key=config_values["api_key"],
        model=config_values["model"],
        base_url=config_values["base_url"],
        timeout=config_values["timeout"],
    )
    result = SpeakerRoutingService(
        adapter,
        RoutingCheckpointRepository(project / "runtime/runtime.db"),
    ).route(source, script, speakers)
    candidate_repository = CharacterCandidatesRepository(project)
    current_candidates = candidate_repository.load(script.source_sha256)
    merged_candidates = merge_character_candidates(
        current_candidates.candidates, result.candidates
    )
    if merged_candidates != current_candidates.candidates:
        updated_candidates = CharacterCandidatesDocument(
            source_sha256=script.source_sha256,
            candidates=merged_candidates,
            revision=current_candidates.revision + 1,
        )
        candidate_repository.save(updated_candidates)
    if result.script.revision != script.revision:
        ProjectV4Repository(_root()).save_script_and_speakers(
            project, source, result.script, result.speakers
        )
    return (
        f"✅ 完成批次 {result.completed_batches}，失败 "
        f"{result.failed_batches}，待确认 {result.unresolved_segments}"
        f"，候选 {len(result.candidates)}"
    )


def _analysis_result_text(result) -> str:
    """把 pipeline 结果转成用户可读消息：result.message + 明确 errors（去重）。"""
    parts = [str(getattr(result, "message", "") or "")]
    for error in getattr(result, "errors", None) or []:
        if error and error not in parts[0]:
            parts.append(f"- {error}")
    return "\n".join(parts)


def continue_v4_analysis(project_name: str, progress=None):
    """Default entry point: analyze only the current/pending chapter."""
    progress = _ensure_progress(progress)
    if not project_name:
        return "⚠ 请先选择 v4 项目"
    try:
        result = ChapterAnalysisService.from_ai_settings(
            _root() / project_name
        ).analyze(
            progress_callback=lambda message: _report_analysis_progress(progress, message)
        )
        return _analysis_result_text(result)
    except Exception as exc:  # noqa: BLE001 - project remains openable
        return f"⚠ 项目已保留，但分析未完成：{str(exc)[:500]}"


def analyze_v4_chapter(project_name: str, chapter_id: str = "", progress=None):
    """Analyze one explicitly selected chapter through the fast contract."""
    progress = _ensure_progress(progress)
    if not project_name:
        return "⚠ 请先选择 v4 项目"
    try:
        result = ChapterAnalysisService.from_ai_settings(_root() / project_name).analyze(
            chapter_id=chapter_id or None,
            progress_callback=lambda message: _report_analysis_progress(progress, message),
        )
        return result.message
    except Exception as exc:  # noqa: BLE001 - keep project openable
        return f"⚠ 当前章节分析未完成：{str(exc)[:500]}"


def reanalyze_v4_chapter(project_name: str, chapter_id: str = "", progress=None):
    """Force one new chapter request; repair remains capped at one retry."""
    progress = _ensure_progress(progress)
    if not project_name:
        return "⚠ 请先选择 v4 项目"
    try:
        result = ChapterAnalysisService.from_ai_settings(_root() / project_name).analyze(
            chapter_id=chapter_id or None,
            force=True,
            progress_callback=lambda message: _report_analysis_progress(progress, message),
        )
        return result.message
    except Exception as exc:  # noqa: BLE001 - keep project openable
        return f"⚠ 当前章节重分析未完成：{str(exc)[:500]}"


def view_v4_chapter_script(project_name: str, chapter_id: str = ""):
    if not project_name:
        return "⚠ 请先选择 v4 项目"
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    selected = next(
        (item for item in script.chapters if not chapter_id or item.chapter_id == chapter_id),
        None,
    )
    if selected is None:
        return "⚠ 未找到当前章节"
    lines = [f"### {selected.title or selected.chapter_id}"]
    for index, item in enumerate(selected.segments):
        confidence = (
            f" · 置信度 {item.confidence:.2f}"
            if item.confidence is not None
            else ""
        )
        lines.append(
            f"{index + 1}. **{item.dialogue_type}** · "
            f"`{item.speaker_id or item.candidate_speaker_name or '未知说话人'}`"
            f"{confidence} · {source[item.start:item.end]}"
        )
        if item.candidate_speaker_id and item.speaker_evidence:
            lines.append(f"   - 证据：{'；'.join(item.speaker_evidence[:3])}")
        if item.uncertainty_reason:
            lines.append(f"   - 判断：{item.uncertainty_reason}")
    return "\n".join(lines)


def reanalyze_v4_project(project_name: str, progress=None):
    """Explicitly rerun AI-first analysis with a durable revision snapshot."""
    progress = _ensure_progress(progress)
    if not project_name:
        return "⚠ 请先选择 v4 项目"
    try:
        result = V4ProjectAnalysisPipeline.from_ai_settings(
            _root() / project_name
        ).run(
            progress_callback=lambda message: _report_analysis_progress(progress, message),
            force_reanalysis=True,
        )
        return _analysis_result_text(result)
    except Exception as exc:  # noqa: BLE001 - project remains openable
        return f"⚠ 重分析未完成，原项目已保留：{str(exc)[:500]}"


def confirm_v4_candidate(
    project_name: str,
    candidate_id: str,
    target_speaker_id: str = "",
):
    if not project_name or not candidate_id:
        return "⚠ 请选择候选角色"
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    repository = CharacterCandidatesRepository(project)
    candidates = repository.load(script.source_sha256)
    updated_script, updated_speakers, updated_candidates = CharacterCandidateReviewService.confirm(
        script,
        speakers,
        candidates,
        candidate_id=candidate_id,
        target_speaker_id=target_speaker_id or None,
    )
    ProjectV4Repository(_root()).save_script_and_speakers(
        project, source, updated_script, updated_speakers
    )
    repository.save(updated_candidates)
    return "✅ 候选角色已确认并写入正式角色表"


def merge_v4_candidate(project_name: str, candidate_id: str, target_speaker_id: str):
    if not target_speaker_id:
        return "⚠ 请选择要合并到的已有角色"
    return confirm_v4_candidate(project_name, candidate_id, target_speaker_id)


def reject_v4_candidate(project_name: str, candidate_id: str):
    if not project_name or not candidate_id:
        return "⚠ 请选择候选角色"
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    repository = CharacterCandidatesRepository(project)
    candidates = repository.load(script.source_sha256)
    updated = CharacterCandidateReviewService.reject(
        candidates, candidate_id=candidate_id
    )
    repository.save(updated)
    return "✅ 候选角色已拒绝，不会进入正式角色表"


def stop_v4_routing(project_name: str):
    count = RoutingCheckpointRepository(
        _root() / project_name / "runtime/runtime.db"
    ).cancel_pending()
    return f"已停止 {count} 个未完成批次"


def assign_v4_speaker(
    project_name: str,
    segment_ids_text: str,
    speaker_id: str,
    new_name: str,
    lock: bool,
):
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    segment_ids = [
        item.strip() for item in str(segment_ids_text or "").split(",") if item.strip()
    ]
    updated_script, updated_speakers = SpeakerReviewService.assign(
        script,
        speakers,
        segment_ids=segment_ids,
        speaker_id=speaker_id or None,
        new_speaker_name=new_name or "",
        lock_speaker=bool(lock),
    )
    ProjectV4Repository(_root()).save_script_and_speakers(
        project, source, updated_script, updated_speakers
    )
    return f"✅ 已更新 {len(segment_ids)} 个片段"


def merge_v4_speakers(project_name: str, source_id: str, target_id: str):
    project = _root() / project_name
    source_text = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source_text)
    speakers = _load_speakers(project)
    production = ProductionRepository(project)
    voices, _performance, _pronunciation, _profile = production.load_inputs()
    updated_script, updated_speakers = SpeakerReviewService.merge_speakers(
        script,
        speakers,
        source_speaker_id=source_id,
        target_speaker_id=target_id,
    )
    bindings = dict(voices.bindings)
    source_binding = bindings.pop(source_id, None)
    binding_message = ""
    if source_binding is not None:
        if target_id not in bindings:
            bindings[target_id] = source_binding
            binding_message = "；已将来源角色音色迁移到目标角色"
        else:
            binding_message = "；目标角色已有音色，保留目标绑定"
        production.save_document(
            "voices.json",
            VoiceBindings(bindings, revision=voices.revision + 1).to_dict(),
        )
    ProjectV4Repository(_root()).save_script_and_speakers(
        project, source_text, updated_script, updated_speakers
    )
    refresh_message = ""
    try:
        stale_count = V4VoiceService._refresh_plan(project)
        if stale_count:
            refresh_message = f"；已重建计划并局部失效 {stale_count} 个旧任务"
    except Exception as exc:  # noqa: BLE001 - keep merge durable and explain refresh
        refresh_message = f"；⚠ 计划刷新失败：{exc}"
    return (
        "✅ 角色已合并；旧角色名已保留为 alias"
        f"{binding_message}{refresh_message}"
    )


def open_v4_role_project(project_name: str):
    """V4 角色工作台：加载 unresolved 表与全部角色下拉（复用薄服务）。"""
    if not project_name:
        return (
            "请选择 v4 项目", [], gr.update(choices=[]), gr.update(choices=[]),
            gr.update(choices=[]), gr.update(choices=[]), gr.update(choices=[]),
            [], gr.update(choices=[]), gr.update(choices=[]),
        )
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    candidates = _load_candidates(project, script.source_sha256)
    unresolved = SpeakerReviewService.unresolved_rows(source, script)
    review_rows = [
        [item["segment_id"], item["chapter_id"], item["text"]]
        for item in unresolved
    ]
    speaker_choices = [
        (item.display_name, item.speaker_id) for item in speakers.speakers
    ]
    candidate_choices = [
        (item.display_name, item.candidate_id)
        for item in candidates.candidates
        if item.status == "candidate"
    ]
    merge_target_choices = [
        (item.display_name, item.speaker_id)
        for item in speakers.speakers
        if item.speaker_type == "character" and item.status == "confirmed"
    ]
    summary = (
        f"**{project_name}** · {len(script.chapters)} 章 · "
        f"{sum(len(item.segments) for item in script.chapters)} 片段 · "
        f"{len(unresolved)} 待确认"
    )
    return (
        summary,
        review_rows,
        gr.update(choices=speaker_choices, value=None),
        gr.update(choices=speaker_choices, value=None),
        gr.update(choices=speaker_choices, value=None),
        gr.update(choices=speaker_choices, value=None),
        gr.update(choices=speaker_choices, value=None),
        candidate_rows(candidates),
        gr.update(choices=candidate_choices, value=None),
        gr.update(choices=merge_target_choices, value=None),
    )


def v4_chapter_choices(project_name: str):
    if not project_name:
        return gr.update(choices=[], value=None)
    project = _root() / project_name
    source_path = project / "source/source.txt"
    script_path = project / "script/script.json"
    if not source_path.is_file() or not script_path.is_file():
        return gr.update(choices=[], value=None)
    script = _load_script(project, source_path.read_text(encoding="utf-8"))
    choices = [
        (f"{item.chapter_id} · {item.title or '当前章节'}", item.chapter_id)
        for item in script.chapters
    ]
    return gr.update(choices=choices, value=choices[0][1] if choices else None)


def set_v4_speaker_lock(project_name: str, speaker_id: str):
    """切换角色锁定状态（V4 稳定角色 ID）。"""
    from dataclasses import replace

    if not project_name or not speaker_id:
        return "请先选择要锁定/解锁的角色。"
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    updated = list(speakers.speakers)
    target = None
    for index, item in enumerate(updated):
        if item.speaker_id == speaker_id:
            target = replace(item, locked=not item.locked)
            updated[index] = target
            break
    if target is None:
        return "角色不存在。"
    new_speakers = replace(
        speakers,
        speakers=updated,
        revision=speakers.revision + (updated != speakers.speakers),
    )
    ProjectV4Repository(_root()).save_script_and_speakers(
        project, source, script, new_speakers
    )
    return f"角色「{target.display_name}」已{'锁定' if target.locked else '解锁'}。"


def set_v4_speaker_alias(project_name: str, speaker_id: str, aliases: str):
    """修改角色别名（逗号分隔）。"""
    from dataclasses import replace

    if not project_name or not speaker_id:
        return "请先选择要修改别名的角色。"
    alias_list = [item.strip() for item in (aliases or "").split(",") if item.strip()]
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    updated = list(speakers.speakers)
    target = None
    for index, item in enumerate(updated):
        if item.speaker_id == speaker_id:
            target = replace(item, aliases=alias_list)
            updated[index] = target
            break
    if target is None:
        return "角色不存在。"
    new_speakers = replace(
        speakers,
        speakers=updated,
        revision=speakers.revision + (updated != speakers.speakers),
    )
    ProjectV4Repository(_root()).save_script_and_speakers(
        project, source, script, new_speakers
    )
    return f"已保存别名：{', '.join(alias_list) or '（空）'}。"


def bind_v4_voice(project_name: str, speaker_id: str, audio_file):
    source = getattr(audio_file, "name", None) or audio_file
    if not source:
        return "⚠ 请选择参考音频"
    _ok, message = V4VoiceService.bind_voice(
        _root() / project_name, speaker_id, source
    )
    return message


def generate_v4_plan(project_name: str):
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    production = ProductionRepository(project)
    voices, performance, pronunciation, profile = production.load_inputs()
    previous = production.load_plan()
    measurer = (
        ConservativeTokenMeasurer()
        if profile.limits.metric == "tokens"
        else CharacterMeasurer()
    )
    result = SynthesisPlanner(measurer).plan(
        source,
        script,
        speakers,
        voices,
        performance,
        pronunciation,
        profile,
        previous_plan=previous,
    )
    production.save_plan(result.plan)
    runtime = RuntimeRepository(project / "runtime/runtime.db")
    runtime.initialize()
    diff = InvalidationService.sync_runtime(runtime, previous, result.plan)
    summary = synthesis_plan_summary(result.plan)
    message = (
        f"✅ 计划 revision {result.plan.revision}：{summary['task_count']} tasks；"
        f"复用 {len(diff.reusable_task_ids)}，stale {len(diff.stale_task_ids)}；"
        f"unresolved {len(result.unresolved_segments)}，未绑定角色 "
        f"{len(result.unbound_speakers)}"
    )
    return (
        synthesis_plan_rows(result.plan),
        message,
        _queue_rows(project),
        _queue_summary(project),
    )


def generate_v4_chapter_plan(project_name: str, chapter_id: str = ""):
    """Generate a plan containing only the selected current chapter."""
    if not project_name:
        return [], "⚠ 请先选择 v4 项目", [], "尚未加载项目"
    if not chapter_id:
        return [], "⚠ 请先选择当前章节", [], "尚未选择章节"
    try:
        project = _root() / project_name
        rows, message = V4SynthesisService.generate_plan(
            project, chapter_id=chapter_id
        )
        return rows, f"本章：{message}", _queue_rows(project), _queue_summary(project)
    except Exception as exc:  # noqa: BLE001 - visible UI error
        return [], f"⚠ 本章计划生成失败：{str(exc)[:500]}", [], "计划生成失败"


def generate_v4_chapter_plan_message(project_name: str, chapter_id: str = ""):
    return generate_v4_chapter_plan(project_name, chapter_id)[1]


def synthesize_v4_chapter(project_name: str, chapter_id: str = ""):
    """Build the selected chapter plan and start its existing V4 queue."""
    rows, message, _queue, _summary = generate_v4_chapter_plan(
        project_name, chapter_id
    )
    if not rows:
        return f"{message}\n\n⚠ 本章没有可合成任务，请先绑定音色并确认角色。"
    ok, start_message = V4SynthesisService.start(project_name)
    return f"{message}\n\n{start_message if ok else '⚠ ' + start_message}"


def run_v4_synthesis(project_name: str):
    project = _root() / project_name
    production = ProductionRepository(project)
    _voices, _performance, _pronunciation, profile = production.load_inputs()
    plan = production.load_plan()
    if plan is None:
        return "⚠ 请先生成计划", [], "尚未生成合成任务", gr.update()
    runtime = RuntimeRepository(project / "runtime/runtime.db")
    runtime.initialize()
    model_dir = config.get_model_dir()
    adapter = IndexTTS2Adapter(
        model_dir,
        lambda voice_id: project / voice_id,
    )
    measurer = (
        ConservativeTokenMeasurer()
        if profile.limits.metric == "tokens"
        else CharacterMeasurer()
    )
    summary = SynthesisExecutor(
        runtime,
        AudioCacheRepository(runtime.path, project),
        adapter,
        measurer,
        project,
    ).run(profile)
    for chapter in {item.chapter_id for item in plan.tasks}:
        tasks = [item for item in plan.tasks if item.chapter_id == chapter]
        try:
            ChapterAssembler(runtime, project).assemble(
                chapter, tasks, plan_revision=plan.revision
            )
        except RuntimeError:
            continue
    chapters = [
        item.chapter_id
        for item in _load_script(project).chapters
        if (project / "audio/chapters" / f"{item.chapter_id}.wav").is_file()
    ]
    return (
        (
            f"完成 {summary.completed}，缓存 {summary.cache_hits}，"
            f"拆分 {summary.split_parents}，失败 {summary.failed}"
        ),
        _queue_rows(project),
        _queue_summary(project),
        gr.update(choices=chapters, value=chapters[0] if chapters else None),
    )


def cancel_v4_synthesis(project_name: str):
    runtime = RuntimeRepository(
        _root() / project_name / "runtime/runtime.db"
    )
    runtime.initialize()
    count = runtime.cancel_pending_tasks()
    project = _root() / project_name
    return (
        f"已取消 {count} 个尚未开始的任务",
        _queue_rows(project),
        _queue_summary(project),
    )


def refresh_v4_queue(project_name: str):
    project = _root() / project_name
    return _queue_rows(project), _queue_summary(project)


def chapter_audio(project_name: str, chapter_id: str):
    if not project_name or not chapter_id:
        return None
    path = _root() / project_name / "audio/chapters" / f"{chapter_id}.wav"
    return str(path) if path.is_file() else None


def export_v4(project_name: str, output_format: str, bitrate: str):
    if not project_name:
        return "", "⚠ 请先打开一个 v4 项目"
    try:
        path = V4ExportService.export(
            _root() / project_name,
            output_format=output_format,
            bitrate=bitrate,
        )
        return str(path), f"✅ 已导出：`{path}`"
    except Exception as exc:  # noqa: BLE001 - 导出失败转用户可读消息
        return "", f"❌ 导出失败：{str(exc)[:400]}"


def migrate_v3_project(v3_name: str):
    source = ProjectService.get_project_dir(v3_name)
    result = V3ToV4MigrationService().migrate(source, _root())
    return (
        (
            f"✅ 已复制迁移到 `{result.project_path.name}`；"
            f"v3 备份：`{result.backup_path}`"
        ),
        gr.update(
            choices=scan_v4_projects(), value=result.project_path.name
        ),
    )


def _load_script(project: Path, source: str | None = None) -> ScriptDocument:
    if source is None:
        source = (project / "source/source.txt").read_text(encoding="utf-8")
    return ScriptDocument.from_dict(
        json.loads((project / "script/script.json").read_text(encoding="utf-8")),
        source,
    )


def _load_speakers(project: Path) -> SpeakersDocument:
    return SpeakersDocument.from_dict(
        json.loads((project / "script/speakers.json").read_text(encoding="utf-8"))
    )


def _load_candidates(
    project: Path,
    source_text_or_sha: str,
) -> CharacterCandidatesDocument:
    return CharacterCandidatesRepository(project).load(source_text_or_sha)


def _queue_rows(project: Path) -> list[list]:
    path = project / "runtime/runtime.db"
    if not path.is_file():
        return []
    RuntimeRepository(path).initialize()
    with sqlite3.connect(path) as connection:
        return [
            list(item)
            for item in connection.execute(
                """
                SELECT task_id, chapter_id, speaker_id, status, text_length,
                       attempts, split_depth, CASE WHEN output_path IS NULL
                       THEN '' ELSE '✓' END
                  FROM synthesis_tasks ORDER BY created_at, task_id
                """
            )
        ]


def _queue_summary(project: Path) -> str:
    path = project / "runtime/runtime.db"
    if not path.is_file():
        return "尚未生成合成任务"
    runtime = RuntimeRepository(path)
    runtime.initialize()
    counts = runtime.task_counts()
    with sqlite3.connect(path) as connection:
        cache_hits = connection.execute(
            "SELECT COALESCE(SUM(cache_hit), 0) FROM synthesis_metrics"
        ).fetchone()[0]
        current = connection.execute(
            """
            SELECT chapter_id, speaker_id, text_length, attempts, split_depth
              FROM synthesis_tasks
             WHERE status = 'running'
             ORDER BY started_at, task_id LIMIT 1
            """
        ).fetchone()
        total = connection.execute(
            "SELECT COUNT(*) FROM synthesis_tasks"
        ).fetchone()[0]
        total_chapters = connection.execute(
            "SELECT COUNT(DISTINCT chapter_id) FROM synthesis_tasks"
        ).fetchone()[0]
        completed_chapters = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT chapter_id
                  FROM synthesis_tasks
                 GROUP BY chapter_id
                HAVING SUM(
                    CASE WHEN status IN (
                        'pending', 'running', 'failed', 'stale', 'cancelled'
                    ) THEN 1 ELSE 0 END
                ) = 0
            )
            """
        ).fetchone()[0]
    summary = (
        f"章节 {completed_chapters}/{total_chapters} · Tasks {total} · "
        f"完成 {counts.get('completed', 0)} · 缓存命中 {cache_hits} · "
        f"失败 {counts.get('failed', 0)} · stale {counts.get('stale', 0)}"
    )
    if current:
        summary += (
            f"\n\n当前：章节 `{current[0]}` · 角色 `{current[1] or '未指定'}` · "
            f"长度 {current[2] or 0} · 尝试 {current[3]} · "
            f"拆分深度 {current[4]}"
        )
    return summary
