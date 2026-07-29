"""structured_script 分析后的只读一致性检查器。"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from typing import Any


def _issue(kind: str, severity: str, message: str, **extra) -> dict[str, Any]:
    return {"type": kind, "severity": severity, "message": message, **extra}


def check_script_consistency(script: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    voices = script.get("voices", {}) if isinstance(script.get("voices"), dict) else {}
    chapters = script.get("chapters", []) if isinstance(script.get("chapters"), list) else []
    chapter_ids = [str(ch.get("id", "")) for ch in chapters if isinstance(ch, dict)]
    for value, count in Counter(chapter_ids).items():
        if value and count > 1:
            issues.append(_issue("duplicate_chapter_id", "error", f"章节 ID {value} 重复"))

    segments: list[dict[str, Any]] = []
    chapter_roles: dict[str, set[str]] = {}
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        cid = str(chapter.get("id", ""))
        chapter_roles[cid] = set()
        previous_rate = None
        for seg in chapter.get("segments", []):
            if not isinstance(seg, dict):
                continue
            segments.append(seg)
            sid = str(seg.get("id", ""))
            text = str(seg.get("text", "")).strip()
            role = str(seg.get("role") or seg.get("speaker") or "").strip()
            chapter_roles[cid].add(role)
            if not sid.strip():
                issues.append(_issue("empty_segment_id", "error", "段落 ID 为空"))
            if not text:
                issues.append(_issue("empty_text", "error", f"段落 {sid or '（无 ID）'} 文本为空"))
            elif len(text) > 500:
                issues.append(_issue("segment_too_long", "warning", f"段落 {sid} 过长（{len(text)} 字）"))
            elif len(text) < 2:
                issues.append(_issue("segment_too_short", "warning", f"段落 {sid} 过短"))
            if not role:
                issues.append(_issue("empty_role", "error", f"段落 {sid or '（无 ID）'} 角色为空"))
            elif role not in voices:
                issues.append(_issue("missing_voice", "error", f"段落 {sid} 使用未定义角色 {role}", role=role))
            try:
                rate = float(seg.get("speech_rate", seg.get("delivery", {}).get("speed", 1.0)))
                if previous_rate is not None and abs(rate - previous_rate) > 0.25:
                    issues.append(_issue("speech_rate_jump", "warning", f"段落 {sid} 与相邻段语速跳变"))
                previous_rate = rate
            except (TypeError, ValueError, AttributeError):
                issues.append(_issue("invalid_speech_rate", "warning", f"段落 {sid} 语速无效"))
            try:
                emotion = float(seg.get("emo_alpha", seg.get("emotion_strength", seg.get("delivery", {}).get("intensity", 1.0))))
                if not 0 <= emotion <= 1.0:
                    issues.append(_issue("invalid_emotion", "warning", f"段落 {sid} 情绪强度超出 0–1"))
            except (TypeError, ValueError, AttributeError):
                issues.append(_issue("invalid_emotion", "warning", f"段落 {sid} 情绪强度无效"))
            for field in ("pause_before", "pause_after"):
                try:
                    pause = int(seg.get(field, 0))
                    if not 0 <= pause <= 3000:
                        issues.append(_issue("invalid_pause", "warning", f"段落 {sid} 的 {field} 超出 0–3000ms"))
                except (TypeError, ValueError):
                    issues.append(_issue("invalid_pause", "warning", f"段落 {sid} 的 {field} 无效"))

    ids = [str(seg.get("id", "")) for seg in segments]
    for value, count in Counter(ids).items():
        if value and count > 1:
            issues.append(_issue("duplicate_segment_id", "error", f"段落 ID {value} 重复"))

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

    counts = Counter(issue["severity"] for issue in issues)
    return {
        "status": "error" if counts["error"] else ("warning" if counts["warning"] else "ok"),
        "issues": issues,
        "summary": {"errors": counts["error"], "warnings": counts["warning"]},
    }
