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

from .types import Chapter, Script, Segment, VoiceInfo

# ─────────────────────────────────────────────────────────────────────────────
# 顶层 key 别名映射（仅在规范 key 缺失、且存在可明确映射的别名时生效）。
# 明确映射、不做模糊猜测，避免把无关字段误判为角色/章节。
# ─────────────────────────────────────────────────────────────────────────────
_VOICE_ALIASES = ("characters", "roles", "cast", "speakers")
_CHAPTER_ALIASES = ("sections", "episodes", "scenes")

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
        empty = Script(meta={}, voices={}, chapters=[])
        empty._detected_top_keys = None
        empty._not_object = True
        return empty

    # ── 解析 voices（角色表）──
    voices_raw = raw.get("voices")
    if voices_raw is None:
        for alias in _VOICE_ALIASES:
            cand = raw.get(alias)
            if isinstance(cand, dict):
                voices_raw = cand
                break
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
    chapters_raw = raw.get("chapters")
    if chapters_raw is None:
        for alias in _CHAPTER_ALIASES:
            cand = raw.get(alias)
            if isinstance(cand, list):
                chapters_raw = cand
                break
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
    errors: list[str] = []

    if not script.voices:
        errors.append("未定义任何角色（voices 为空或缺失）")
    if not script.chapters:
        errors.append("未定义任何章节（chapters 为空或缺失）")

    voice_names = set(script.voices.keys())
    for ch in script.chapters:
        for seg in ch.segments:
            if seg.role not in voice_names:
                errors.append(
                    f"段落 {seg.id}: 角色 '{seg.role}' 未在 voices 中定义"
                    f"（voices 中仅有：{', '.join(sorted(voice_names)) or '（无）'}；"
                    "请检查 role 拼写，或确认该角色已写入 voices）"
                )

    # 存在错误时，前置可读的诊断信息，帮助用户定位问题。
    if errors:
        errors = _build_diagnostic(script) + errors
    return errors


def count_voiced(script: Script) -> int:
    """所有角色数"""
    return len(script.voices)


def count_chapters(script: Script) -> int:
    return len(script.chapters)


def count_segments(script: Script) -> int:
    return sum(len(ch.segments) for ch in script.chapters)
