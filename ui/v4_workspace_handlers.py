"""Thin Gradio adapters for the integrated v4 project shell."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import gradio as gr

from ai.speaker_routing import create_speaker_routing_adapter
from domain.v4 import ScriptDocument, SpeakersDocument
from domain.v4.production import VoiceBinding, VoiceBindings
from lib import config
from repositories.audio_cache_repository import AudioCacheRepository
from repositories.production_repository import ProductionRepository
from repositories.project_v4_repository import ProjectV4Repository
from repositories.routing_checkpoint_repository import RoutingCheckpointRepository
from repositories.runtime_repository import RuntimeRepository
from services.ai_settings import AiSettingsService
from services.chapter_assembler import ChapterAssembler
from services.invalidation_service import InvalidationService
from services.migration_v3_to_v4 import V3ToV4MigrationService
from services.plan_preview import synthesis_plan_rows, synthesis_plan_summary
from services.project import ProjectService
from services.speaker_review_service import SpeakerReviewService
from services.speaker_routing_service import SpeakerRoutingService
from services.synthesis_executor import SynthesisExecutor
from services.synthesis_planner import SynthesisPlanner
from services.v4_export import V4ExportService
from services.v4_project_creation import V4ProjectCreationService
from tts.indextts2_adapter import IndexTTS2Adapter
from tts.text_measurement import CharacterMeasurer, ConservativeTokenMeasurer


def _root() -> Path:
    return Path(config.get_projects_root())


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


def create_v4_from_source(name, source_file, title, author):
    source = getattr(source_file, "name", None) or source_file
    if not name or not source:
        return "⚠ 请输入项目名称并上传书稿", "", gr.update(), gr.update()
    try:
        result = V4ProjectCreationService(
            ProjectV4Repository(_root())
        ).create_from_source(
            source,
            str(name),
            title=str(title or ""),
            author=str(author or ""),
        )
        manifest = ProjectV4Repository(_root()).load_manifest(result.project_path)
        script = _load_script(result.project_path)
        total = sum(len(item.segments) for item in script.chapters)
        message = (
            "### ✅ v4 项目创建成功\n\n"
            f"- 作品：**{manifest.title}**\n"
            f"- 语义片段：{total}\n"
            f"- 待确认角色：{result.unresolved_segments}\n"
            f"- Source SHA：`{script.source_sha256}`\n\n"
            "已进入「② 项目管理」；请到「③ 角色与声音」确认角色并绑定音色，"
            "然后在「④ 生产与质检」生成计划并合成。"
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


def open_v4_project(name: str):
    if not name:
        return (
            "请选择 v4 项目", [], gr.update(choices=[]),
            gr.update(choices=[]), gr.update(choices=[]), gr.update(choices=[]),
            [], [], "", "", gr.update(choices=[]),
        )
    project = _root() / name
    RuntimeRepository(project / "runtime/runtime.db").initialize()
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
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
    plan = production.load_plan()
    plan_rows = synthesis_plan_rows(plan) if plan else []
    queue_rows = _queue_rows(project)
    summary = (
        f"**{name}** · {len(script.chapters)} 章 · "
        f"{sum(len(item.segments) for item in script.chapters)} 片段 · "
        f"{len(unresolved)} 待确认"
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
    if result.script.revision != script.revision:
        ProjectV4Repository(_root()).save_script_and_speakers(
            project, source, result.script, result.speakers
        )
    return (
        f"✅ 完成批次 {result.completed_batches}，失败 "
        f"{result.failed_batches}，待确认 {result.unresolved_segments}"
    )


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
    updated_script, updated_speakers = SpeakerReviewService.merge_speakers(
        script,
        speakers,
        source_speaker_id=source_id,
        target_speaker_id=target_id,
    )
    ProjectV4Repository(_root()).save_script_and_speakers(
        project, source_text, updated_script, updated_speakers
    )
    return "✅ 角色已合并；旧角色名已保留为 alias"


def open_v4_role_project(project_name: str):
    """V4 角色工作台：加载 unresolved 表与全部角色下拉（复用薄服务）。"""
    if not project_name:
        return (
            "请选择 v4 项目", [], gr.update(choices=[]), gr.update(choices=[]),
            gr.update(choices=[]), gr.update(choices=[]), gr.update(choices=[]),
        )
    project = _root() / project_name
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    script = _load_script(project, source)
    speakers = _load_speakers(project)
    unresolved = SpeakerReviewService.unresolved_rows(source, script)
    review_rows = [
        [item["segment_id"], item["chapter_id"], item["text"]]
        for item in unresolved
    ]
    speaker_choices = [
        (item.display_name, item.speaker_id) for item in speakers.speakers
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
    )


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
    project = _root() / project_name
    production = ProductionRepository(project)
    voices, _performance, _pronunciation, _profile = production.load_inputs()
    raw = Path(source).read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    target = project / "assets/voices" / f"{fingerprint[:16]}{Path(source).suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    bindings = dict(voices.bindings)
    bindings[speaker_id] = VoiceBinding(
        target.relative_to(project).as_posix(), fingerprint
    )
    production.save_document(
        "voices.json",
        VoiceBindings(bindings, revision=voices.revision + 1).to_dict(),
    )
    return "✅ 音色已绑定；仅该角色相关任务会失效"


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
