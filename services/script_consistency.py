"""structured_script 分析后的只读一致性检查器。"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from typing import Any


def _issue(kind: str, severity: str, message: str, **extra) -> dict[str, Any]:
    return {"type": kind, "severity": severity, "message": message, **extra}


def check_script_consistency(script: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if not isinstance(script, dict):
        return {
            "status": "error",
            "issues": [_issue("top_level_not_object", "error", "JSON 顶层必须是对象")],
            "summary": {"errors": 1, "warnings": 0},
        }
    voices = script.get("voices", {}) if isinstance(script.get("voices"), dict) else {}
    chapters = script.get("chapters", []) if isinstance(script.get("chapters"), list) else []
    chapter_ids = [str(ch.get("id", "")) for ch in chapters if isinstance(ch, dict)]
    for value, count in Counter(chapter_ids).items():
        if value and count > 1:
            issues.append(_issue("duplicate_chapter_id", "error", f"章节 ID {value} 重复"))

    segments: list[dict[str, Any]] = []
    chapter_roles: dict[str, set[str]] = {}
    for chapter_index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            issues.append(_issue(
                "invalid_chapter", "error",
                f"chapters[{chapter_index}] 必须是对象",
                path=f"chapters[{chapter_index}]",
            ))
            continue
        cid = str(chapter.get("id", ""))
        chapter_roles[cid] = set()
        previous_rate = None
        chapter_segments = chapter.get("segments", [])
        if not isinstance(chapter_segments, list):
            issues.append(_issue(
                "invalid_segments", "error",
                f"chapters[{chapter_index}].segments 必须是数组",
                path=f"chapters[{chapter_index}].segments",
            ))
            continue
        if not chapter_segments:
            issues.append(_issue(
                "empty_chapter", "error",
                f"chapters[{chapter_index}].segments 不能为空",
                path=f"chapters[{chapter_index}].segments",
            ))
        for segment_index, seg in enumerate(chapter_segments):
            if not isinstance(seg, dict):
                issues.append(_issue(
                    "invalid_segment", "error",
                    f"chapters[{chapter_index}].segments[{segment_index}] 必须是对象",
                    path=f"chapters[{chapter_index}].segments[{segment_index}]",
                ))
                continue
            segments.append(seg)
            sid = str(seg.get("id", ""))
            path = f"chapters[{chapter_index}].segments[{segment_index}]"
            text = str(seg.get("text", "")).strip()
            role_value = str(seg.get("role") or "").strip()
            speaker_value = str(seg.get("speaker") or "").strip()
            role = role_value or speaker_value
            chapter_roles[cid].add(role)
            if not sid.strip():
                issues.append(_issue("empty_segment_id", "error", "段落 ID 为空", path=f"{path}.id"))
            if not text:
                issues.append(_issue("empty_text", "error", f"段落 {sid or '（无 ID）'} 文本为空", path=f"{path}.text"))
            elif len(text) > 500:
                issues.append(_issue("segment_too_long", "warning", f"段落 {sid} 过长（{len(text)} 字）", path=f"{path}.text"))
            elif len(text) < 2:
                issues.append(_issue("segment_too_short", "warning", f"段落 {sid} 过短", path=f"{path}.text"))
            if not role:
                issues.append(_issue("empty_role", "error", f"段落 {sid or '（无 ID）'} 角色为空", path=f"{path}.speaker"))
            elif role not in voices:
                issues.append(_issue("missing_voice", "error", f"段落 {sid} 使用未定义角色 {role}", role=role, path=f"{path}.speaker"))
            for field, value in (("role", role_value), ("speaker", speaker_value)):
                if value and value not in voices and value != role:
                    issues.append(_issue("missing_voice", "error", f"段落 {sid} 使用未定义角色 {value}", role=value, path=f"{path}.{field}"))
            if role_value and speaker_value and role_value != speaker_value:
                issues.append(_issue("role_speaker_mismatch", "error", f"段落 {sid} 的 role 与 speaker 不一致", path=f"{path}.speaker"))
            delivery = seg.get("delivery") if isinstance(seg.get("delivery"), dict) else {}
            try:
                rate = float(seg.get("speech_rate", delivery.get("speed", 1.0)))
                if previous_rate is not None and abs(rate - previous_rate) > 0.25:
                    issues.append(_issue("speech_rate_jump", "warning", f"段落 {sid} 与相邻段语速跳变", path=f"{path}.speech_rate"))
                previous_rate = rate
            except (TypeError, ValueError, AttributeError):
                issues.append(_issue("invalid_speech_rate", "warning", f"段落 {sid} 语速无效", path=f"{path}.speech_rate"))
            try:
                emotion = float(seg.get("emo_alpha", seg.get("emotion_strength", delivery.get("intensity", 1.0))))
                if not 0 <= emotion <= 1.0:
                    issues.append(_issue("invalid_emotion", "warning", f"段落 {sid} 情绪强度超出 0–1", path=f"{path}.emo_alpha"))
            except (TypeError, ValueError, AttributeError):
                issues.append(_issue("invalid_emotion", "warning", f"段落 {sid} 情绪强度无效", path=f"{path}.emo_alpha"))
            for field in ("pause_before", "pause_after"):
                try:
                    pause = int(seg.get(field, 0))
                    if not 0 <= pause <= 3000:
                        issues.append(_issue("invalid_pause", "warning", f"段落 {sid} 的 {field} 超出 0–3000ms", path=f"{path}.{field}"))
                except (TypeError, ValueError):
                    issues.append(_issue("invalid_pause", "warning", f"段落 {sid} 的 {field} 无效", path=f"{path}.{field}"))
            for pause_index, pause in enumerate(seg.get("pauses", []) or []):
                pause_path = f"{path}.pauses[{pause_index}]"
                if not isinstance(pause, dict):
                    issues.append(_issue("invalid_pause", "warning", f"{pause_path} 必须是对象", path=pause_path))
                    continue
                try:
                    position = int(pause.get("position", 0))
                    if not 0 <= position <= len(text):
                        issues.append(_issue("invalid_pause", "warning", f"{pause_path}.position 超出文本范围", path=f"{pause_path}.position"))
                except (TypeError, ValueError):
                    issues.append(_issue("invalid_pause", "warning", f"{pause_path}.position 无效", path=f"{pause_path}.position"))
                try:
                    duration = int(pause.get("duration", 0))
                    if not 0 <= duration <= 3000:
                        issues.append(_issue("invalid_pause", "warning", f"{pause_path}.duration 超出 0–3000ms", path=f"{pause_path}.duration"))
                except (TypeError, ValueError):
                    issues.append(_issue("invalid_pause", "warning", f"{pause_path}.duration 无效", path=f"{pause_path}.duration"))

    ids = [str(seg.get("id", "")) for seg in segments]
    for value, count in Counter(ids).items():
        if value and count > 1:
            issues.append(_issue("duplicate_segment_id", "error", f"段落 ID {value} 重复", id=value))

    used = Counter(str(seg.get("role") or seg.get("speaker") or "").strip() for seg in segments)
    for role in voices:
        if not used[role]:
            issues.append(_issue("unused_voice", "warning", f"角色 {role} 未被任何段落使用", role=role))
        elif used[role] == 1:
            issues.append(_issue("role_used_once", "warning", f"角色 {role} 仅出现一次", role=role))

    names = [name for name in voices if len(name) >= 2]
    candidate_groups: set[tuple[str, str]] = set()
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            if left in right or right in left or SequenceMatcher(None, left, right).ratio() >= 0.6:
                candidate_groups.add(tuple(sorted((left, right))))
    for left, right in sorted(candidate_groups):
        issues.append(_issue(
            "possible_character_alias", "warning", "发现疑似角色别名（不会自动合并）",
            candidates=[left, right],
        ))
    for roles in chapter_roles.values():
        role_list = sorted(r for r in roles if len(r) >= 2)
        for i, left in enumerate(role_list):
            for right in role_list[i + 1:]:
                if SequenceMatcher(None, left, right).ratio() >= 0.75:
                    pair = tuple(sorted((left, right)))
                    if pair not in candidate_groups:
                        issues.append(_issue(
                            "possible_character_alias", "warning",
                            "同章节出现近似角色名（不会自动合并）", candidates=list(pair),
                        ))

    meta = script.get("meta") if isinstance(script.get("meta"), dict) else {}
    if "total_chapters" in meta and meta.get("total_chapters") != len(chapters):
        issues.append(_issue(
            "chapter_count_mismatch", "error",
            f"meta.total_chapters 声明为 {meta.get('total_chapters')}，实际为 {len(chapters)}",
            path="meta.total_chapters",
        ))
    actual_segments = len(segments)
    if "total_segments" in meta and meta.get("total_segments") != actual_segments:
        issues.append(_issue(
            "segment_count_mismatch", "error",
            f"meta.total_segments 声明为 {meta.get('total_segments')}，实际为 {actual_segments}",
            path="meta.total_segments",
        ))

    counts = Counter(issue["severity"] for issue in issues)
    return {
        "status": "error" if counts["error"] else ("warning" if counts["warning"] else "ok"),
        "issues": issues,
        "summary": {"errors": counts["error"], "warnings": counts["warning"]},
    }
