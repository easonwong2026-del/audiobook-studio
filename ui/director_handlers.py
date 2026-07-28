"""AI 剧本导演 UI 回调。

把导演文件处理、人工编辑、声音推荐、试听和反馈从 ``app.py`` 拆出，避免主应用
继续承担业务控制器职责。此模块只做 Gradio 输入输出适配，领域逻辑仍在 services。
"""
from __future__ import annotations

import html
import json
import logging
import os
import uuid
from pathlib import Path

import gradio as gr

from ai.providers import create_provider
from lib import config, voice_lib
from services import (
    DirectorAuditionService,
    ScriptDirectorService,
    VoiceDirectorService,
)

logger = logging.getLogger(__name__)


def _file_value_path(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("path") or value.get("name")
    return getattr(value, "path", None) or getattr(value, "name", None)


def _preview(script):
    roles = list((script.get("voices") or {}).keys())
    samples = [
        segment
        for chapter in script.get("chapters", [])
        for segment in chapter.get("segments", [])
    ][:6]
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(seg.get('speaker') or seg.get('role') or '旁白'))}</td>"
        f"<td>{html.escape(str(seg.get('emotion') or 'neutral'))}</td>"
        f"<td>{html.escape(str(seg.get('text') or '')[:100])}</td>"
        "</tr>"
        for seg in samples
    )
    return (
        "<div class='stage-card'>"
        f"<p><b>角色：</b>{html.escape('、'.join(roles))}</p>"
        "<table><thead><tr><th>角色</th><th>情绪</th><th>片段预览</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _load_script(script_file):
    path = _file_value_path(script_file)
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("请先完成导演分析")
    with open(path, encoding="utf-8") as file:
        return path, json.load(file)


def analyze_director_file(input_file, provider_name, model, title, author):
    source = _file_value_path(input_file)
    if not source:
        return (
            "### ⚠ 请先上传 TXT、DOCX 或 EPUB 小说",
            "<div class='inline-empty'>尚未分析。</div>",
            None,
            gr.update(),
            gr.update(),
            [],
            "",
            gr.update(choices=[], value=None),
        )
    try:
        provider = create_provider(provider_name, model=(model or "").strip() or None)
        director = ScriptDirectorService(provider)
        output_dir = Path(config.get_preview_dir()) / "script_director"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"structured_script_{uuid.uuid4().hex[:12]}.json"
        script = director.analyze_file(
            source,
            output_path=str(output),
            title=(title or "").strip(),
            author=(author or "").strip(),
        )
        chapter_choices = director.chapter_choices(script)
        first_chapter = chapter_choices[0][1] if chapter_choices else None
        roles = list((script.get("voices") or {}).keys())
        chapters = script.get("chapters") or []
        project_title = str(script.get("meta", {}).get("title") or Path(source).stem)
        status = (
            "### ✅ 导演分析完成\n"
            f"{len(chapters)} 章 · {script['meta']['total_segments']} 段 · "
            f"{len(roles)} 个角色 · Provider：`{provider.name}`"
        )
        return (
            status,
            _preview(script),
            str(output),
            str(output),
            project_title,
            director.editor_rows(script, first_chapter),
            "",
            gr.update(choices=chapter_choices, value=first_chapter),
        )
    except Exception as exc:
        logger.exception("AI 剧本导演分析失败")
        return (
            f"### ❌ 导演分析失败\n{html.escape(str(exc))}",
            "<div class='inline-empty'>未生成可用剧本，请检查 Provider 配置和输入文件。</div>",
            None,
            gr.update(),
            gr.update(),
            [],
            "",
            gr.update(choices=[], value=None),
        )


def refresh_director_editor(script_file, chapter_id):
    try:
        _, script = _load_script(script_file)
        return ScriptDirectorService.editor_rows(script, str(chapter_id))
    except Exception:
        return []


def apply_director_edits(script_file, rows, chapter_id):
    path = _file_value_path(script_file)
    if not path:
        return (
            "### ⚠ 请先完成一次导演分析",
            "<div class='inline-empty'>没有可编辑剧本。</div>",
            gr.update(),
            gr.update(),
            gr.update(),
            "",
        )
    try:
        script, backup, changed = ScriptDirectorService.save_segment_edits(path, rows)
        return (
            f"### ✅ 已保存人工调整\n更新 {changed} 个 segment，可撤销本次保存。",
            _preview(script),
            path,
            path,
            ScriptDirectorService.editor_rows(script, str(chapter_id)),
            backup,
        )
    except Exception as exc:
        logger.exception("保存人工导演调整失败")
        return (
            f"### ❌ 保存失败\n{html.escape(str(exc))}",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )


def undo_director_edits(script_file, backup_path, chapter_id):
    path = _file_value_path(script_file)
    if not path or not backup_path:
        return (
            "### ⚠ 没有可撤销的人工调整",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            "",
        )
    try:
        script = ScriptDirectorService.undo_segment_edits(path, str(backup_path))
        return (
            "### ↩️ 已撤销上次人工调整",
            _preview(script),
            path,
            path,
            ScriptDirectorService.editor_rows(script, str(chapter_id)),
            "",
        )
    except Exception as exc:
        logger.exception("撤销人工导演调整失败")
        return (
            f"### ❌ 撤销失败\n{html.escape(str(exc))}",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )


def refresh_director_voice_controls(script_file):
    try:
        _, script = _load_script(script_file)
        roles = VoiceDirectorService.role_choices(script)
        first_role = roles[0][1] if roles else None
        segments = VoiceDirectorService.segment_choices(script, first_role or "")
        first_segment = segments[0][1] if segments else None
        voices = voice_lib.voice_names()
        return (
            gr.update(choices=roles, value=first_role),
            gr.update(choices=segments, value=first_segment),
            gr.update(choices=voices, value=voices[0] if voices else None),
        )
    except Exception:
        return (
            gr.update(choices=[], value=None),
            gr.update(choices=[], value=None),
            gr.update(choices=[], value=None),
        )


def refresh_director_segments(script_file, role):
    try:
        _, script = _load_script(script_file)
        choices = VoiceDirectorService.segment_choices(script, str(role or ""))
        return gr.update(choices=choices, value=choices[0][1] if choices else None)
    except Exception:
        return gr.update(choices=[], value=None)


def recommend_director_voice(script_file, role):
    if not role:
        return [], gr.update(), "⚠ 请先选择角色"
    try:
        _, script = _load_script(script_file)
        recommendations = VoiceDirectorService.recommend(script, str(role))
        rows = [
            [item["voice_name"], item["category"], item["score"], item["reasons"]]
            for item in recommendations
        ]
        names = voice_lib.voice_names()
        selected = recommendations[0]["voice_name"] if recommendations else None
        if not recommendations:
            return [], gr.update(choices=names, value=None), "⚠ 音色库为空，请先添加声音"
        return (
            rows,
            gr.update(choices=names, value=selected),
            f"✅ 已为「{html.escape(str(role))}」生成 {len(rows)} 个候选；不会自动绑定。",
        )
    except Exception as exc:
        logger.exception("生成声音推荐失败")
        return [], gr.update(), f"❌ 推荐失败：{html.escape(str(exc))}"


def audition_director_segment(script_file, segment_id, voice_name):
    if not segment_id or not voice_name:
        return None, "⚠ 请选择 segment 和试听声音"
    try:
        path, _ = _load_script(script_file)
        output, cached = DirectorAuditionService.synthesize(
            path,
            str(segment_id),
            str(voice_name),
        )
        return output, f"✅ 导演试听已生成{'（命中缓存）' if cached else ''}"
    except Exception as exc:
        logger.exception("生成导演试听失败")
        return None, f"❌ 试听失败：{html.escape(str(exc))}"


def apply_director_audition_feedback(script_file, segment_id, feedback, chapter_id):
    path = _file_value_path(script_file)
    if not path or not segment_id or not feedback:
        return (
            "### ⚠ 请选择试听 Segment 和反馈",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            None,
            "⚠ 反馈尚未应用",
        )
    try:
        script, backup, summary = ScriptDirectorService.apply_audition_feedback(
            path,
            str(segment_id),
            str(feedback),
        )
        return (
            f"### ✅ 已应用试听反馈\nSegment `{segment_id}`：{summary}",
            _preview(script),
            path,
            path,
            ScriptDirectorService.editor_rows(script, str(chapter_id)),
            backup,
            None,
            "参数已变化，请重新生成试听；可使用“撤销上次保存”恢复。",
        )
    except Exception as exc:
        logger.exception("应用导演试听反馈失败")
        return (
            f"### ❌ 反馈应用失败\n{html.escape(str(exc))}",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            f"❌ {html.escape(str(exc))}",
        )
