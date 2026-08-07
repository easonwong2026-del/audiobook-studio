"""读取并校验 structured_script.json。

设计要点：
- ``from_dict`` 负责把已加载的 dict 解析为 ``Script``，并对常见顶层 key 别名
  （如 ``characters``→``voices``、``sections``→``chapters``）做**明确映射**兼容，
  以便用户上传格式轻微偏差的文件时也能正常解析。
- ``validate_script`` 在校验失败时返回**带诊断信息**的错误列表，指出顶层 key
  列表、voices/chapters 实际数量与最小合法格式，便于用户快速定位问题。
- 为保持对既有成功项目（version 2.0/2.1，含巨大 meta、字段齐全）的兼容，
  所有字段解析逻辑与旧版完全一致；别名仅在规范 key 缺失时才生效。
"""
from __future__ import annotations

import json
import math

from .types import Chapter, Script, Segment, VoiceInfo

# ─────────────────────────────────────────────────────────────────────────────
# 顶层 key 别名映射（仅在规范 key 缺失、且存在可明确映射的别名时生效）。
# 明确映射、不做模糊猜测，避免把无关字段误判为角色/章节。
# ─────────────────────────────────────────────────────────────────────────────
_VOICE_ALIASES = ("characters", "roles", "cast", "speakers")
_CHAPTER_ALIASES = ("sections", "episodes", "scenes")

# These are the values already exposed by the V3 synthesis/review controls or
# consumed by the existing voice-director compatibility code.  The importer
# rejects unknown values early, before a TTS job can fail halfway through.
VALID_EMOTIONS = frozenset({
    "neutral", "angry", "happy", "sad", "excited", "whisper",
    "cold", "confident", "fearful", "hesitant", "tense",
})
SPEECH_RATE_RANGE = (0.7, 1.5)
PITCH_RANGE = (-12.0, 12.0)
INTENSITY_RANGE = (0.0, 1.0)
PAUSE_RANGE_MS = (0, 3000)
VALID_PAUSE_TYPES = frozenset({"pause_short", "pause_long", "pause_think"})

# 期望的最小合法剧本示例（用于校验失败时的友好提示）。
_MIN_EXAMPLE = (
    '{\n'
    '  "meta": {"title": "书名", "author": "作者"},\n'
    '  "voices": {"旁白": {"description": "沉稳男中音，纪录片风格"}},\n'
    '  "chapters": [\n'
    '    {\n'
    '      "id": 1,\n'
    '      "title": "第一章",\n'
    '      "segments": [\n'
    '        {"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "..."}\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    '}'
)


def resolve_collections(raw: dict) -> tuple[dict, list]:
    """Return the canonical voices/chapters collections using explicit aliases."""
    if not isinstance(raw, dict):
        return {}, []
    voices = raw.get("voices")
    if not isinstance(voices, dict):
        voices = next(
            (raw.get(alias) for alias in _VOICE_ALIASES if isinstance(raw.get(alias), dict)),
            {},
        )
    chapters = raw.get("chapters")
    if not isinstance(chapters, list):
        chapters = next(
            (raw.get(alias) for alias in _CHAPTER_ALIASES if isinstance(raw.get(alias), list)),
            [],
        )
    return voices, chapters


def canonicalize_collections(raw: dict) -> dict:
    """Return a shallow raw-payload copy with compatible aliases materialized."""
    if not isinstance(raw, dict):
        return raw
    voices, chapters = resolve_collections(raw)
    normalized = dict(raw)
    if not isinstance(raw.get("voices"), dict):
        normalized["voices"] = voices
    if not isinstance(raw.get("chapters"), list):
        normalized["chapters"] = chapters
    return normalized


def from_dict(raw: dict) -> Script:
    """从已加载的剧本 dict 构造 Script 对象（不读磁盘，供内存对象复用）。

    与 ``load_script`` 共用同一套字段解析，保证批量合成链路（queue）与
    校验/UI 链路对剧本结构的理解一致。

    解析策略：
    - 若 ``raw`` 不是 dict（顶层是数组/字符串等非对象结构），返回一个空的
      ``Script`` 并标记 ``_not_object=True``，交由校验阶段给出可读提示。
    - voices/chapters 优先使用规范 key（``voices``/``chapters``）；仅当规范 key
      **缺失**时，才回退到别名（``characters``→``voices`` 等），避免覆盖合法字段。
    """
    # 顶层不是对象：无法读取 voices/chapters，直接返回空 Script 并标记。
    if not isinstance(raw, dict):
        empty = Script(meta={}, voices={}, chapters=[], raw=raw)
        empty._detected_top_keys = None
        empty._not_object = True
        return empty

    # ── 解析 voices（角色表）──
    voices_raw, chapters_raw = resolve_collections(raw)
    voices: dict[str, VoiceInfo] = {}
    if isinstance(voices_raw, dict):
        for name, info in voices_raw.items():
            # info 可能为非 dict（容错），统一按 dict 处理
            info = info if isinstance(info, dict) else {}
            voices[name] = VoiceInfo(
                name=name,
                description=info.get("description", ""),
                suggested_audio=info.get("suggested_audio"),
            )

    # ── 解析 chapters（章节列表）──
    chapters: list[Chapter] = []
    if isinstance(chapters_raw, list):
        for ch in chapters_raw:
            ch = ch if isinstance(ch, dict) else {}
            segments: list[Segment] = []
            for i, seg in enumerate(ch.get("segments", [])):
                seg = seg if isinstance(seg, dict) else {}
                seg_id = seg.get("id") or f"{ch.get('id', '?')}-{str(i + 1).zfill(3)}"
                delivery = seg.get("delivery") if isinstance(seg.get("delivery"), dict) else {}
                segments.append(Segment(
                    id=seg_id,
                    role=seg.get("role") or seg.get("speaker", ""),
                    emotion=seg.get("emotion", "neutral"),
                    text=seg.get("text", ""),
                    emo_alpha=seg.get(
                        "emo_alpha",
                        seg.get("emotion_strength", delivery.get("intensity", 1.0)),
                    ),
                    speech_rate=seg.get("speech_rate", delivery.get("speed", 1.0)),
                    pinyin_hints=seg.get("pinyin_hints", {}),
                    pitch=delivery.get("pitch", seg.get("pitch", 0.0)),
                    breath=delivery.get("breath", seg.get("breath", "none")),
                    pause_before=seg.get("pause_before", 0),
                    pause_after=seg.get("pause_after", 0),
                    pauses=seg.get("pauses", []) if isinstance(seg.get("pauses"), list) else [],
                    role_id=seg.get("role_id"),
                ))
            chapters.append(Chapter(
                id=ch.get("id"),
                title=ch.get("title", ""),
                segments=segments,
            ))

    script = Script(
        meta=raw.get("meta", {}) if isinstance(raw.get("meta"), dict) else (raw.get("meta") or {}),
        voices=voices,
        chapters=chapters,
        raw=raw,
    )
    # 记录真实检测到的顶层 key，供校验阶段输出诊断信息（不修改 Script 数据类定义）。
    script._detected_top_keys = list(raw.keys())
    script._not_object = False
    return script


def load_script(path: str) -> Script:
    """加载 JSON，返回 Script 对象。

    若文件不是合法 JSON，会抛出 ``json.JSONDecodeError``（由调用方决定如何提示）。
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return from_dict(raw)


def _build_diagnostic(script: Script) -> list[str]:
    """构造可读的诊断信息（仅在存在校验错误时附加）。"""
    top_keys = getattr(script, "_detected_top_keys", None)
    not_object = getattr(script, "_not_object", False)

    lines: list[str] = ["🔍 诊断信息："]
    if not_object:
        lines.append(
            "· 文件虽是合法 JSON，但顶层结构不是对象（应为 {...}），"
            "而检测到了数组 / 字符串等非对象结构，导致无法读取 voices 与 chapters。"
        )
    else:
        if top_keys is None:
            key_desc = "未知"
        elif not top_keys:
            key_desc = "（空，无任何顶层 key）"
        else:
            key_desc = "、".join(f"'{k}'" for k in top_keys)
        lines.append(f"· JSON 已成功解析；检测到的顶层 key：{key_desc}")
        lines.append(
            f"· voices（角色）数量：{len(script.voices)}，"
            f"chapters（章节）数量：{len(script.chapters)}"
        )
        # 提示缺了哪些必需 key（仅当 voices/chapters 为空时）
        missing = [k for k in ("voices", "chapters") if not getattr(script, k)]
        if missing:
            lines.append(
                f"· 缺少必需的顶层字段：{', '.join(missing)}。"
                "请确认上传的是由 WorkBuddy 生成的 structured_script.json，"
                "且包含 voices（角色表）与 chapters（章节列表）。"
            )
    lines.append(
        "· 期望的最小合法格式（voices 至少 1 个角色，chapters 至少 1 章）："
    )
    lines.append("```json\n" + _MIN_EXAMPLE + "\n```")
    return lines


def validate_script(script: Script) -> list[str]:
    """校验剧本完整性，返回错误列表（含可读的诊断信息）。

    返回空列表表示校验通过。
    """
    issues = validate_script_issues(getattr(script, "raw", None))
    if not issues:
        return []
    return _build_diagnostic(script) + [issue["message"] for issue in issues]


_VALIDATION_HINTS = {
    "top_level_not_object": "将 JSON 顶层改为对象 {...}。",
    "missing_meta": "补充 meta 对象。",
    "invalid_meta": "将 meta 改为 JSON 对象。",
    "missing_voices": "在 voices 中定义角色。",
    "invalid_voices": "将 voices 改为角色名到对象的映射。",
    "empty_voices": "至少定义一个角色。",
    "missing_chapters": "在 chapters 中提供章节数组。",
    "invalid_chapters": "将 chapters 改为数组。",
    "empty_chapters": "至少提供一个章节。",
    "missing_voice": "在 voices 中定义该角色，或修正片段的 role/speaker。",
    "duplicate_segment_id": "为每个片段分配全书唯一的 id。",
    "duplicate_chapter_id": "为每个章节分配全书唯一的 id。",
    "empty_text": "补充片段原文 text。",
    "count_mismatch": "更新 meta 中的统计数字，或修正实际章节/片段数量。",
}


def validate_script_issues(raw) -> list[dict]:
    """Return machine-readable contract errors for an in-memory raw payload.

    This is the structured counterpart of :func:`validate_script`.  Keeping the
    rule implementation here means file imports, MCP calls, and project
    integrity checks can share the exact same validation behavior without
    parsing human-facing Chinese messages.
    """
    issues: list[dict] = []

    def add(code: str, path: str, message: str, **metadata) -> None:
        issue = {
            "code": code,
            "severity": "error",
            "path": path,
            "message": message,
            "fix_hint": _VALIDATION_HINTS.get(code, "按该 JSON 路径修正字段后重新校验。"),
        }
        issue.update({key: value for key, value in metadata.items() if value is not None})
        issues.append(issue)

    if not isinstance(raw, dict):
        add("top_level_not_object", "$", "$: 顶层结构必须是 JSON 对象")
        add("missing_voices", "voices", "voices: 未定义任何角色（voices 为空或缺失）")
        add("missing_chapters", "chapters", "chapters: 未定义任何章节（chapters 为空或缺失）")
        return issues

    if "meta" not in raw:
        add("missing_meta", "meta", "meta: 缺少必填对象")
    elif not isinstance(raw.get("meta"), dict):
        add("invalid_meta", "meta", "meta: 必须是对象")

    voices_key = "voices" if "voices" in raw else next(
        (key for key in _VOICE_ALIASES if key in raw), None
    )
    chapters_key = "chapters" if "chapters" in raw else next(
        (key for key in _CHAPTER_ALIASES if key in raw), None
    )
    if voices_key is None:
        add("missing_voices", "voices", "voices: 缺少必填对象")
        add("empty_voices", "voices", "voices: 未定义任何角色（voices 为空或缺失）")
    elif not isinstance(raw.get(voices_key), dict):
        add("invalid_voices", voices_key, f"{voices_key}: 必须是对象")
    if chapters_key is None:
        add("missing_chapters", "chapters", "chapters: 缺少必填数组")
        add("empty_chapters", "chapters", "chapters: 未定义任何章节（chapters 为空或缺失）")
    elif not isinstance(raw.get(chapters_key), list):
        add("invalid_chapters", chapters_key, f"{chapters_key}: 必须是数组")

    raw_voices = raw.get(voices_key or "voices")
    voice_names: set[str] = set(raw_voices) if isinstance(raw_voices, dict) else set()
    if isinstance(raw_voices, dict):
        if not raw_voices:
            add("empty_voices", voices_key or "voices", "voices: 未定义任何角色（voices 为空或缺失）")
        for role, info in raw_voices.items():
            role_path = f"{voices_key or 'voices'}.{role}"
            if not isinstance(role, str) or not role.strip():
                add("empty_voice_name", voices_key or "voices", "voices: 角色名不能为空")
            if not isinstance(info, dict):
                add("invalid_voice", role_path, f"{voices_key or 'voices'}[{role!r}]: 必须是对象")

    raw_chapters = raw.get(chapters_key or "chapters")
    if isinstance(raw_chapters, list):
        if not raw_chapters:
            add("empty_chapters", chapters_key or "chapters", "chapters: 未定义任何章节（chapters 为空或缺失）")
        seen_chapter_ids: set[str] = set()
        seen_segment_ids: set[str] = set()
        for chapter_index, chapter in enumerate(raw_chapters):
            chapter_path = f"{chapters_key or 'chapters'}[{chapter_index}]"
            if not isinstance(chapter, dict):
                add("invalid_chapter", chapter_path, f"{chapter_path}: 必须是对象")
                continue
            chapter_id = chapter.get("id")
            if chapter_id is None or not str(chapter_id).strip():
                add("empty_chapter_id", f"{chapter_path}.id", f"{chapter_path}.id: 缺少非空章节 ID")
            else:
                normalized_chapter_id = str(chapter_id)
                if normalized_chapter_id in seen_chapter_ids:
                    add(
                        "duplicate_chapter_id",
                        f"{chapter_path}.id",
                        f"{chapter_path}.id: 章节 ID 重复（{normalized_chapter_id}）",
                        id=normalized_chapter_id,
                    )
                seen_chapter_ids.add(normalized_chapter_id)
            title = chapter.get("title")
            if not isinstance(title, str) or not title.strip():
                add("empty_chapter_title", f"{chapter_path}.title", f"{chapter_path}.title: 必须是非空字符串")
            segments = chapter.get("segments")
            if not isinstance(segments, list):
                add("invalid_segments", f"{chapter_path}.segments", f"{chapter_path}.segments: 必须是数组")
                continue
            if not segments:
                add("empty_chapter", f"{chapter_path}.segments", f"{chapter_path}.segments: 不能为空")
            for segment_index, segment in enumerate(segments):
                segment_path = f"{chapter_path}.segments[{segment_index}]"
                if not isinstance(segment, dict):
                    add("invalid_segment", segment_path, f"{segment_path}: 必须是对象")
                    continue
                segment_id = segment.get("id")
                if segment_id is None or not str(segment_id).strip():
                    add("empty_segment_id", f"{segment_path}.id", f"{segment_path}.id: 缺少非空片段 ID")
                else:
                    normalized_segment_id = str(segment_id)
                    if normalized_segment_id in seen_segment_ids:
                        add(
                            "duplicate_segment_id",
                            f"{segment_path}.id",
                            f"{segment_path}.id: 片段 ID 重复（{normalized_segment_id}）",
                            id=normalized_segment_id,
                        )
                    seen_segment_ids.add(normalized_segment_id)

                role_value = segment.get("role")
                speaker_value = segment.get("speaker")
                speaker = role_value if role_value is not None else speaker_value
                if not isinstance(speaker, str) or not speaker.strip():
                    add("empty_role", f"{segment_path}.speaker", f"{segment_path}.speaker: 必须是非空字符串")
                for field, value in (("role", role_value), ("speaker", speaker_value)):
                    field_path = f"{segment_path}.{field}"
                    if value is None:
                        continue
                    if not isinstance(value, str) or not value.strip():
                        add("empty_role", field_path, f"{field_path}: 必须是非空字符串")
                    elif value not in voice_names:
                        add(
                            "missing_voice",
                            field_path,
                            f"{field_path}: 角色“{value}”未在 voices 中定义",
                            role=value,
                        )
                if (
                    isinstance(role_value, str)
                    and isinstance(speaker_value, str)
                    and role_value.strip()
                    and speaker_value.strip()
                    and role_value != speaker_value
                ):
                    add("role_speaker_mismatch", f"{segment_path}.speaker", f"{segment_path}.speaker: role 与 speaker 不一致")
                text = segment.get("text")
                if not isinstance(text, str) or not text.strip():
                    add("empty_text", f"{segment_path}.text", f"{segment_path}.text: 必须是非空字符串")
                _validate_segment_number_issues(segment, segment_path, add)

    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    actual_chapters = len(raw_chapters) if isinstance(raw_chapters, list) else 0
    actual_segments = sum(
        len(ch.get("segments", []))
        for ch in raw_chapters
        if isinstance(ch, dict) and isinstance(ch.get("segments"), list)
    ) if isinstance(raw_chapters, list) else 0
    for field, actual in (("total_chapters", actual_chapters), ("total_segments", actual_segments)):
        if field not in meta:
            continue
        path = f"meta.{field}"
        declared = meta.get(field)
        if not isinstance(declared, int) or isinstance(declared, bool):
            add("invalid_count", path, f"{path}: 必须是整数")
        elif declared != actual:
            add(
                "count_mismatch",
                path,
                f"{path}: 声明为 {declared}，实际为 {actual}",
                expected=actual,
                actual=declared,
            )
    return issues


def _validate_segment_numbers(segment: dict, path: str, errors: list[str]) -> None:
    """Validate the numeric delivery fields already used by the V3 TTS path."""
    delivery = segment.get("delivery") if isinstance(segment.get("delivery"), dict) else {}

    def number(
        field: str,
        value,
        bounds: tuple[float, float],
        *,
        integer: bool = False,
        field_path: str | None = None,
    ):
        field_path = field_path or f"{path}.{field}"
        if value is None:
            return
        if integer and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{field_path}: 必须是整数")
            return
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            errors.append(f"{field_path}: 必须是数字")
            return
        if not math.isfinite(parsed) or not bounds[0] <= parsed <= bounds[1]:
            errors.append(
                f"{field_path}: 数值 {value!r} 超出 {bounds[0]}–{bounds[1]} 范围"
            )

    emotion = segment.get("emotion")
    if emotion is not None and emotion not in VALID_EMOTIONS:
        errors.append(f"{path}.emotion: 不支持的情绪“{emotion}”")
    number(
        "speech_rate",
        segment.get("speech_rate", delivery.get("speed")),
        SPEECH_RATE_RANGE,
    )
    number("pitch", segment.get("pitch", delivery.get("pitch")), PITCH_RANGE)
    number(
        "emo_alpha",
        segment.get("emo_alpha", segment.get("emotion_strength", delivery.get("intensity"))),
        INTENSITY_RANGE,
    )
    for field in ("pause_before", "pause_after"):
        number(field, segment.get(field), PAUSE_RANGE_MS, integer=True)
    if "pauses" not in segment:
        return
    pauses = segment.get("pauses")
    if not isinstance(pauses, list):
        errors.append(f"{path}.pauses: 必须是数组")
        return
    text_length = len(str(segment.get("text") or ""))
    for index, pause in enumerate(pauses):
        pause_path = f"{path}.pauses[{index}]"
        if not isinstance(pause, dict):
            errors.append(f"{pause_path}: 必须是对象")
            continue
        position = pause.get("position")
        number(
            "position",
            position,
            (0, float(text_length)),
            integer=True,
            field_path=f"{pause_path}.position",
        )
        number(
            "duration",
            pause.get("duration"),
            PAUSE_RANGE_MS,
            integer=True,
            field_path=f"{pause_path}.duration",
        )
        pause_type = pause.get("type")
        if pause_type is not None and pause_type not in VALID_PAUSE_TYPES:
            allowed = "、".join(sorted(VALID_PAUSE_TYPES))
            errors.append(
                f"{pause_path}.type: 不支持的停顿类型“{pause_type}”（可选：{allowed}）"
            )


def _validate_segment_number_issues(segment: dict, path: str, add) -> None:
    """Add structured errors for the same delivery fields as the text validator."""

    def number(field: str, value, bounds: tuple[float, float], *, integer: bool = False, field_path: str | None = None):
        target = field_path or f"{path}.{field}"
        if value is None:
            return
        if integer and (not isinstance(value, int) or isinstance(value, bool)):
            add("invalid_number_type", target, f"{target}: 必须是整数")
            return
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            add("invalid_number", target, f"{target}: 必须是数字")
            return
        if not math.isfinite(parsed) or not bounds[0] <= parsed <= bounds[1]:
            add("number_out_of_range", target, f"{target}: 数值 {value!r} 超出 {bounds[0]}–{bounds[1]} 范围")

    delivery = segment.get("delivery") if isinstance(segment.get("delivery"), dict) else {}
    emotion = segment.get("emotion")
    if emotion is not None and emotion not in VALID_EMOTIONS:
        add("invalid_emotion", f"{path}.emotion", f"{path}.emotion: 不支持的情绪“{emotion}”")
    number("speech_rate", segment.get("speech_rate", delivery.get("speed")), SPEECH_RATE_RANGE)
    number("pitch", segment.get("pitch", delivery.get("pitch")), PITCH_RANGE)
    number("emo_alpha", segment.get("emo_alpha", segment.get("emotion_strength", delivery.get("intensity"))), INTENSITY_RANGE)
    for field in ("pause_before", "pause_after"):
        number(field, segment.get(field), PAUSE_RANGE_MS, integer=True)
    if "pauses" not in segment:
        return
    pauses = segment.get("pauses")
    if not isinstance(pauses, list):
        add("invalid_pauses", f"{path}.pauses", f"{path}.pauses: 必须是数组")
        return
    text_length = len(str(segment.get("text") or ""))
    for index, pause in enumerate(pauses):
        pause_path = f"{path}.pauses[{index}]"
        if not isinstance(pause, dict):
            add("invalid_pause", pause_path, f"{pause_path}: 必须是对象")
            continue
        number(
            "position",
            pause.get("position"),
            (0, float(text_length)),
            integer=True,
            field_path=f"{pause_path}.position",
        )
        number(
            "duration",
            pause.get("duration"),
            PAUSE_RANGE_MS,
            integer=True,
            field_path=f"{pause_path}.duration",
        )
        pause_type = pause.get("type")
        if pause_type is not None and pause_type not in VALID_PAUSE_TYPES:
            allowed = "、".join(sorted(VALID_PAUSE_TYPES))
            add(
                "invalid_pause_type",
                f"{pause_path}.type",
                f"{pause_path}.type: 不支持的停顿类型“{pause_type}”（可选：{allowed}）",
            )


def count_voiced(script: Script) -> int:
    """所有角色数"""
    return len(script.voices)


def count_chapters(script: Script) -> int:
    return len(script.chapters)


def count_segments(script: Script) -> int:
    return sum(len(ch.segments) for ch in script.chapters)
