"""Production review audio orchestration.

This module keeps the review page's file lookup, cache naming and status
messages out of ``app.py``.  It intentionally has no Gradio dependency so the
same behavior can be exercised by Windows/path tests.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

from lib import chapter_identity, config, project_paths, segment_cache
from lib.audio_validation import is_valid_wav_file

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewPageState:
    """All values needed to initialize the review page in one callback."""

    chapter_table: str
    chapter_choices: list[tuple[str, str]] = field(default_factory=list)
    selected_chapter: str | None = None
    chapter_audio: str | None = None
    chapter_status: str = ""
    segment_choices: list[tuple[str, str]] = field(default_factory=list)
    selected_segment: str | None = None
    segment_audio: str | None = None
    segment_status: str = ""


@dataclass(frozen=True)
class ReviewAudioResult:
    path: str | None
    status: str


def _safe_project_name(project_name: str) -> str:
    return chapter_identity.safe_filename(project_name, "project")


def preview_cache_dir(project_name: str, kind: str = "chapters") -> str:
    """Return a project-isolated preview cache directory under data_dir."""
    if kind not in {"chapters", "segments", "supplement"}:
        raise ValueError(f"未知试听缓存类型: {kind}")
    data_root = os.path.realpath(config.get_data_dir())
    path = os.path.realpath(
        os.path.join(config.get_preview_dir(), _safe_project_name(project_name), kind)
    )
    if os.path.commonpath([data_root, path]) != data_root:
        raise ValueError("试听缓存路径超出数据目录")
    os.makedirs(path, exist_ok=True)
    return os.path.normpath(path)


def _nonempty_file(path: str | None) -> bool:
    try:
        return bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)
    except OSError:
        return False


_valid_wav_file = is_valid_wav_file


def _allowed_audio_path(project_name: str, path: str, kind: str = "segments") -> str | None:
    """Return a Gradio-safe copy when a legacy path is outside data_dir."""
    if not _nonempty_file(path):
        return None
    absolute = os.path.normpath(os.path.abspath(path))
    data_root = os.path.realpath(config.get_data_dir())
    try:
        inside_data = os.path.commonpath([data_root, os.path.realpath(absolute)]) == data_root
    except ValueError:
        inside_data = False
    if inside_data:
        return absolute

    cache_dir = preview_cache_dir(project_name, kind)
    destination = os.path.join(cache_dir, chapter_identity.safe_filename(os.path.basename(absolute), "preview.wav"))
    try:
        shutil.copy2(absolute, destination)
    except OSError as exc:
        logger.exception("复制试听音频到允许目录失败: %s", exc)
        return None
    return destination if _nonempty_file(destination) else None


def _segment_audio(project_name: str, project_dir: str, segment: dict[str, Any]) -> str | None:
    """Resolve a segment's WAV through the unified artifact resolver.

    The resolver uses engine provenance (segment revision > latest production
    task > Settings default) so a segment produced yesterday with IndexTTS 2
    stays playable even after Settings switches to IndexTTS 2.5, and a
    dual-engine project can always find the audio it actually produced.
    """
    # The active revision may be archived outside ``segments/`` while a repair
    # is preparing or has failed.  Resolve it first so the old audio remains
    # playable throughout the repair lifecycle.
    try:
        from repositories.quality_repo import QualityRepository

        state_path = QualityRepository.state_path(project_name, create=False)
        revision = (
            QualityRepository.get_active_revision(
                project_name, str(segment.get("id") or "")
            )
            if os.path.isfile(state_path)
            else None
        )
        if revision and revision.get("relative_path"):
            active = project_paths.resolve_relative(
                project_dir, revision.get("relative_path", "")
            )
            if _valid_wav_file(active):
                return active
    except Exception:
        pass
    seg_dir = project_paths.project_dir(project_dir, "segments")
    speaker_fingerprint = None
    if os.path.isfile(project_paths.project_file(project_dir, "voice_cast")):
        try:
            from repositories.project_repo import ProjectRepository

            bindings = ProjectRepository.load_bindings(project_dir)
            role_bindings = bindings.get("role_bindings", {}) if isinstance(bindings, dict) else {}
            role_binding = role_bindings.get(str(segment.get("role_id") or ""))
            if not isinstance(role_binding, dict):
                role_name = str(segment.get("role") or segment.get("speaker") or "")
                role_binding = next(
                    (item for item in role_bindings.values()
                     if isinstance(item, dict) and item.get("role_name") == role_name),
                    None,
                )
            if isinstance(role_binding, dict):
                path = str(role_binding.get("project_voice_path") or "")
                if path and not os.path.isabs(path):
                    try:
                        path = project_paths.resolve_relative(project_dir, path)
                    except ValueError:
                        path = os.path.join(project_dir, path)
                speaker_fingerprint = segment_cache.speaker_fingerprint_for_path(path)
        except Exception:
            speaker_fingerprint = None
    artifact = segment_cache.resolve_segment_artifact(
        segments_dir=seg_dir,
        seg_id=str(segment.get("id") or ""),
        emotion=str(segment.get("emotion") or "neutral"),
        emo_alpha=segment.get("emo_alpha", 1.0),
        speech_rate=segment.get("speech_rate", 1.0),
        pinyin_hints=segment.get("pinyin_hints"),
        director_metadata=segment_cache.director_metadata_for(segment),
        speaker_fingerprint=speaker_fingerprint,
        project_name=project_name,
    )
    return artifact.path if artifact.exists() else None


def _segment_label(segment: dict[str, Any], *, audio_valid: bool = True) -> str:
    text = " ".join(str(segment.get("text") or "").split())
    if len(text) > 36:
        text = text[:36] + "…"
    suffix = "" if audio_valid else " · 未生成"
    icon = "✅" if audio_valid else "⚪"
    return f"{icon} {segment.get('id')} · {segment.get('role') or segment.get('speaker') or ''} · {text}{suffix}"


def build_segment_choices(
    project_name: str,
    project_dir: str,
    script: dict[str, Any],
    *,
    include_missing: bool = False,
) -> list[tuple[str, str]]:
    """Build label/value choices, optionally keeping missing segments visible."""
    choices: list[tuple[str, str]] = []
    for chapter in script.get("chapters", []):
        for segment in chapter.get("segments", []):
            audio = _segment_audio(project_name, project_dir, segment)
            audio_valid = _valid_wav_file(audio)
            if audio_valid or include_missing:
                choices.append(
                    (_segment_label(segment, audio_valid=audio_valid), str(segment.get("id")))
                )
    return choices


def normalize_segment_id(choice: Any, script: dict[str, Any]) -> str:
    """Normalize a Gradio label/value choice without parsing display text."""
    if isinstance(choice, (tuple, list)) and len(choice) >= 2:
        return str(choice[1])
    if isinstance(choice, dict):
        for key in ("value", "id", "label"):
            if key in choice and choice[key] is not None:
                return str(choice[key])
        return ""
    value = "" if choice is None else str(choice)
    for chapter in script.get("chapters", []):
        for segment in chapter.get("segments", []):
            segment_id = str(segment.get("id"))
            if segment_id == value or _segment_label(segment) == value:
                return segment_id
    return value


def play_segment(project_name: str, project_dir: str, script: dict[str, Any], choice: Any) -> ReviewAudioResult:
    selected_id = normalize_segment_id(choice, script)
    if not selected_id:
        return ReviewAudioResult(None, "⚪ 未选择段落。")
    for chapter in script.get("chapters", []):
        for segment in chapter.get("segments", []):
            if str(segment.get("id")) != str(selected_id):
                continue
            audio = _segment_audio(project_name, project_dir, segment)
            if not _valid_wav_file(audio):
                _record_event(project_dir, "segment_preview", "missing", segment_id=str(selected_id))
                return ReviewAudioResult(None, f"ℹ 当前段落 {selected_id} 没有已合成音频。")
            safe_audio = _allowed_audio_path(project_name, audio, "segments")
            if not safe_audio:
                _record_event(project_dir, "segment_preview", "unavailable", segment_id=str(selected_id))
                return ReviewAudioResult(None, f"⚠ 段落 {selected_id} 的音频路径无法访问。")
            _record_event(project_dir, "segment_preview", "done", segment_id=str(selected_id))
            return ReviewAudioResult(safe_audio, f"✅ 段落 {selected_id} 试听音频已准备。")
    return ReviewAudioResult(None, f"⚠ 未找到段落 {selected_id}，请刷新项目后重试。")


def _chapter_fingerprint(project_name: str, project_dir: str, chapter: dict[str, Any]) -> str:
    files: list[dict[str, Any]] = []
    for segment in chapter.get("segments", []):
        audio = _segment_audio(project_name, project_dir, segment)
        stat: dict[str, Any] = {"id": str(segment.get("id")), "path": ""}
        if audio:
            try:
                info = os.stat(audio)
                stat.update({"path": os.path.basename(audio), "size": info.st_size, "mtime_ns": info.st_mtime_ns})
            except OSError:
                stat["path"] = os.path.basename(audio)
        files.append(stat)
    payload = {"chapter": chapter, "files": files}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


def render_chapter_preview(project_name: str, project_dir: str, script: dict[str, Any], chapter_id: Any) -> ReviewAudioResult:
    """Merge one chapter into a project-isolated, content-addressed preview."""
    chapters = script.get("chapters", [])
    target: tuple[int, dict[str, Any]] | None = next(
        ((index, chapter) for index, chapter in enumerate(chapters)
         if str(chapter.get("id")) == str(chapter_id)),
        None,
    )
    if target is None:
        return ReviewAudioResult(None, "⚠ 未找到所选章节，请刷新项目后重试。")
    index, chapter = target
    valid_audio = [
        audio for segment in chapter.get("segments", [])
        for audio in [_segment_audio(project_name, project_dir, segment)]
        if _valid_wav_file(audio)
    ]
    label = chapter_identity.chapter_label(chapter, index, len(chapters))
    if not valid_audio:
        _record_event(project_dir, "chapter_preview", "missing", chapter_id=str(chapter_id))
        return ReviewAudioResult(None, f"ℹ {label} 没有可试听的已合成音频。")

    fingerprint = _chapter_fingerprint(project_name, project_dir, chapter)
    safe_project = _safe_project_name(project_name)
    safe_chapter = chapter_identity.chapter_file_stem(chapter, index, len(chapters))
    output_dir = preview_cache_dir(project_name, "chapters")
    output_path = os.path.normpath(os.path.join(output_dir, f"chapter_{safe_project}_{safe_chapter}_{fingerprint}.wav"))
    try:
        from lib import audio_pipeline

        result = audio_pipeline.concat_for_preview(project_dir, chapter_id, output_path)
        if not result or not _valid_wav_file(result):
            _record_event(project_dir, "chapter_preview", "merge_failed", chapter_id=str(chapter_id))
            return ReviewAudioResult(None, f"⚠ {label} 试听音频拼接失败，未生成有效音频。")
        safe_result = _allowed_audio_path(project_name, result, "chapters")
        if not safe_result:
            _record_event(project_dir, "chapter_preview", "unavailable", chapter_id=str(chapter_id))
            return ReviewAudioResult(None, f"⚠ {label} 的试听音频路径无法访问。")
        _record_event(project_dir, "chapter_preview", "done", chapter_id=str(chapter_id), path=os.path.relpath(safe_result, config.get_data_dir()))
        return ReviewAudioResult(safe_result, f"✅ {label} 章节试听已准备。")
    except Exception as exc:
        logger.exception("章节试听生成失败")
        _record_event(project_dir, "chapter_preview", "error", chapter_id=str(chapter_id), error=str(exc)[:240])
        return ReviewAudioResult(None, f"❌ {label} 试听音频拼接失败，请点击重新加载。")


def build_chapter_table(project_name: str, project_dir: str, script: dict[str, Any], meta: Any) -> tuple[str, list[tuple[str, str]]]:
    chapters = script.get("chapters", [])
    total_done = 0
    total_segments = 0
    rows = ["| 章节 | 完成 | 详情 |", "|---|---:|---|"]
    choices: list[tuple[str, str]] = []
    for index, chapter in enumerate(chapters):
        segments = chapter.get("segments", [])
        done_ids = [str(segment.get("id")) for segment in segments if _valid_wav_file(_segment_audio(project_name, project_dir, segment))]
        missing_ids = [str(segment.get("id")) for segment in segments if str(segment.get("id")) not in done_ids]
        total_done += len(done_ids)
        total_segments += len(segments)
        detail = f"{len(done_ids)}/{len(segments)}"
        if done_ids:
            detail += " ✅ " + ", ".join(done_ids[:4])
        if missing_ids and len(missing_ids) <= 2:
            detail += " ❌ " + ", ".join(missing_ids)
        label = chapter_identity.chapter_label(chapter, index, len(chapters))
        rows.append(f"| {label} | {len(done_ids)}/{len(segments)} | {detail} |")
        choices.append((label, str(chapter.get("id"))))
    summary = f"### 📊 {total_done}/{total_segments} 段已完成\n\n" + "\n".join(rows)
    if not total_done:
        summary += "\n\n⚠ 未检测到合成段落"
    return summary, choices


def initialize(project_name: str | None, project_dir: str | None, script: dict[str, Any] | None, meta: Any = None) -> ReviewPageState:
    if not project_name or not project_dir or not script:
        return ReviewPageState(
            chapter_table="*请先在项目管理中打开项目。*",
            chapter_status="⚪ 尚未打开项目。",
            segment_status="⚪ 尚未加载段落列表。",
        )
    table, chapter_choices = build_chapter_table(project_name, project_dir, script, meta)
    selected_chapter = chapter_choices[0][1] if chapter_choices else None
    # Chapter preview is deliberately generated only after the user asks for
    # it.  Page entry loads the current segment into the single shared player.
    chapter_result = ReviewAudioResult(
        None,
        "选择「试听整章」后在同一个播放器中加载章节音频。"
        if selected_chapter is not None
        else "⚪ 当前项目没有可用章节。",
    )
    segment_choices = build_segment_choices(
        project_name, project_dir, script, include_missing=True
    )
    selected_segment = next(
        (value for label, value in segment_choices if label.startswith("✅ ")),
        None,
    )
    segment_result = (
        play_segment(project_name, project_dir, script, selected_segment)
        if selected_segment is not None
        else ReviewAudioResult(None, "ℹ 当前没有已生成的段落音频可选择。")
    )
    if selected_segment is None:
        chapter_result = ReviewAudioResult(
            None,
            "ℹ 当前没有可试听的已生成音频。"
            if selected_chapter is not None
            else "⚪ 当前项目没有可用章节。",
        )
    return ReviewPageState(
        chapter_table=table,
        chapter_choices=chapter_choices,
        selected_chapter=selected_chapter,
        chapter_audio=chapter_result.path,
        chapter_status=chapter_result.status,
        segment_choices=segment_choices,
        selected_segment=selected_segment,
        segment_audio=segment_result.path,
        segment_status=segment_result.status,
    )


def _record_event(project_dir: str, action: str, status: str, **details) -> None:
    try:
        quality_dir = project_paths.project_dir(project_dir, "quality", create=True)
        event = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, "status": status, **details}
        with open(os.path.join(quality_dir, "review_events.jsonl"), "a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("记录试听事件失败: %s", exc)


class ReviewAudioService:
    """Stable service façade used by Gradio and platform-independent tests."""

    build_segment_choices = staticmethod(build_segment_choices)
    normalize_segment_id = staticmethod(normalize_segment_id)
    play_segment = staticmethod(play_segment)
    render_chapter_preview = staticmethod(render_chapter_preview)
    initialize = staticmethod(initialize)


__all__ = [
    "ReviewAudioResult",
    "ReviewPageState",
    "ReviewAudioService",
    "build_segment_choices",
    "initialize",
    "normalize_segment_id",
    "play_segment",
    "preview_cache_dir",
    "render_chapter_preview",
]
