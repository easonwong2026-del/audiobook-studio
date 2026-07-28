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


# ═══════════════════════════════════════════════════════════════
# v3.3.1：新入口基于项目名而非临时文件路径
# ═══════════════════════════════════════════════════════════════


def _project_script_path(project_name: str) -> str:
    """根据项目名找到 structured_script.json 路径。"""
    from repositories.project_repo import ProjectRepository
    project_dir = ProjectRepository.get_project_dir(project_name)
    return os.path.join(project_dir, "structured_script.json")


def refresh_director_editor_for_project(project_name, chapter_id):
    """基于项目名的章节编辑器刷新。"""
    name = _file_value_path(project_name) or str(project_name or "")
    if not name:
        return []
    try:
        return refresh_director_editor(_project_script_path(name), chapter_id)
    except Exception:
        return []


def apply_director_edits_for_project(project_name, rows, chapter_id):
    """基于项目名保存人工调整。"""
    name = _file_value_path(project_name) or str(project_name or "")
    if not name:
        return (
            "### ⚠ 请先打开项目",
            gr.update(),
            "",
        )
    try:
        path = _project_script_path(name)
        script, backup, changed = ScriptDirectorService.save_segment_edits(path, rows)
        return (
            f"### ✅ 已保存人工调整\n更新 {changed} 个 segment，可撤销。",
            ScriptDirectorService.editor_rows(script, str(chapter_id)),
            backup,
        )
    except Exception as exc:
        logger.exception("保存人工导演调整失败")
        return (
            f"### ❌ 保存失败\n{html.escape(str(exc))}",
            gr.update(),
            gr.update(),
        )


def undo_director_edits_for_project(project_name, backup_path, chapter_id):
    """基于项目名撤销人工调整。"""
    name = _file_value_path(project_name) or str(project_name or "")
    if not name or not backup_path:
        return (
            "### ⚠ 没有可撤销的人工调整",
            gr.update(),
            "",
        )
    try:
        path = _project_script_path(name)
        script = ScriptDirectorService.undo_segment_edits(path, str(backup_path))
        return (
            "### ↩️ 已撤销上次人工调整",
            ScriptDirectorService.editor_rows(script, str(chapter_id)),
            "",
        )
    except Exception as exc:
        logger.exception("撤销人工导演调整失败")
        return (
            f"### ❌ 撤销失败\n{html.escape(str(exc))}",
            gr.update(),
            gr.update(),
        )


# ═══════════════════════════════════════════════════════════════
# v3.3.1：设置页面回调
# ═══════════════════════════════════════════════════════════════


from services.ai_settings import AiSettingsService  # noqa: E402


def update_provider_config_fields(provider: str) -> tuple:
    """切换 Provider 时更新可见字段。"""
    provider = str(provider or "local")
    if provider == "local":
        return (
            "<p>本地离线基线无需配置 API Key。</p>",
            gr.update(visible=False, value=""),
            gr.update(visible=False, value=""),
        )
    is_openai = provider == "openai"
    env_var = "OPENAI_API_KEY" if is_openai else "DEEPSEEK_API_KEY"
    default_base = "https://api.openai.com/v1" if is_openai else "https://api.deepseek.com"
    info = (
        f"<p><b>{provider.title()}</b> 密钥可通过密钥环保存，"
        f"或设置环境变量 <code>{env_var}</code>。</p>"
    )
    config = AiSettingsService.get_provider_config()
    saved_key = AiSettingsService.get_api_key(provider)
    saved_base = config.get(f"{provider}_base_url", "")
    return (
        info,
        gr.update(visible=True, value=saved_key or ""),
        gr.update(visible=True, value=saved_base or default_base),
    )


def save_ai_settings(provider, model, api_key, base_url, timeout) -> str:
    """保存 AI 配置和密钥。"""
    try:
        provider = str(provider or "local")
        config = AiSettingsService.get_provider_config()
        config["default_provider"] = provider
        if model and model.strip():
            config[f"{provider}_model"] = model.strip()
        elif f"{provider}_model" in config:
            del config[f"{provider}_model"]
        if base_url and base_url.strip():
            config[f"{provider}_base_url"] = base_url.strip()
        elif f"{provider}_base_url" in config:
            del config[f"{provider}_base_url"]
        config["timeout"] = int(timeout) if timeout else 180
        AiSettingsService.save_provider_config(config)

        # 保存密钥到 Keyring（非空时）
        if api_key and api_key.strip():
            try:
                AiSettingsService.set_api_key(provider, api_key.strip())
            except Exception as keyring_err:
                return f"⚠ 配置已保存，但密钥保存失败：{keyring_err}"

        return f"✅ **{provider.title()}** 配置已保存。"
    except Exception as exc:
        logger.exception("保存 AI 配置失败")
        return f"❌ 保存失败：{html.escape(str(exc))}"


def test_ai_connection(provider: str) -> str:
    """测试 Provider 连接。"""
    try:
        result = AiSettingsService.check_connection(str(provider or "local"))
        return result
    except Exception as exc:
        logger.exception("测试 AI 连接失败")
        return f"❌ 连接测试异常：{html.escape(str(exc))}"


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
    d = config.get_data_dir()
    os.makedirs(d, exist_ok=True)
    try:
        import subprocess
        subprocess.Popen(["open", d])  # macOS
    except Exception:
        pass
    return ""
