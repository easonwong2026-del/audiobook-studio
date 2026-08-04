"""角色声音绑定区的展示格式化辅助。

这里仅负责把已经由业务层计算好的角色 choices 转成面向用户的标签，
不改变角色值、绑定表或任何持久化协议。
"""
from __future__ import annotations

from pathlib import Path

from lib import project_manager as _pm


def format_role_label(role: str, voice: dict | None = None) -> str:
    """将角色与剧本描述合并为易读标签：``角色（描述）``。"""
    voice = voice or {}
    description = str(voice.get("description") or voice.get("name") or "").strip()
    if not description:
        return role
    return f"{role}（{description}）"


def build_role_management_rows(
    script: dict | None,
    bindings: dict | None,
    search: str = "",
) -> list[list[str]]:
    """构建角色管理列表行，不改变剧本或绑定数据。

    每行固定包含角色名、剧本中的声线描述和绑定状态，供角色管理列表转换为
    可滚动的单列选择器。搜索只过滤展示行，不参与任何持久化操作。
    """
    voices = (script or {}).get("voices", {}) or {}
    current_bindings = bindings or {}
    query = str(search or "").strip().casefold()
    rows: list[list[str]] = []
    for role, voice in voices.items():
        description = str((voice or {}).get("description") or (voice or {}).get("name") or "").strip()
        if query and query not in f"{role} {description}".casefold():
            continue
        rows.append([
            str(role),
            description or "未填写角色描述",
            "✅ 已绑定" if current_bindings.get(role) else "⚠ 待绑定",
        ])
    return rows


def format_role_management_summary(script: dict | None, bindings: dict | None) -> str:
    """返回角色列表顶部的简洁统计，不暴露文件路径。"""
    roles = (script or {}).get("voices", {}) or {}
    current_bindings = bindings or {}
    total = len(roles)
    bound = sum(1 for role in roles if current_bindings.get(role))
    return f"共 **{total}** 个角色 · **{bound}** 已绑定 · **{total - bound}** 待绑定"


def build_role_management_choices(
    script: dict | None,
    bindings: dict | None,
    search: str = "",
) -> list[tuple[str, str]]:
    """把角色管理行转换为单列 Radio 选项（显示值，实际值）。"""
    current_bindings = bindings or {}
    choices = []
    for role, description, status in build_role_management_rows(
        script, current_bindings, search
    ):
        label = f"{role}\n{description}\n{status}"
        if current_bindings.get(role):
            label += f"\n音色：{Path(str(current_bindings[role])).name}"
        choices.append((label, role))
    return choices


def build_v4_role_management_choices(
    speakers, bindings, speaker_stats: dict[str, dict] | None = None
) -> list[tuple[str, str]]:
    """Build the shared card labels for V4 speakers using stable speaker IDs."""
    current_bindings = bindings or {}
    choices = []
    for speaker in speakers or []:
        label = speaker.display_name
        if speaker.locked:
            label += " 🔒"
        review_status = getattr(speaker, "review_status", "confirmed")
        if speaker.status != "confirmed" and review_status == "confirmed":
            review_status = "unknown"
        status_label = {
            "confirmed": "✅ 已确认",
            "candidate": "🟡 AI 候选 · 需确认",
            "rejected": "⛔ 已拒绝",
            "unknown": "⚪ 未知说话人 · 待确认",
        }.get(review_status, "⚠ 待确认")
        label += f"\n{status_label}"
        if speaker.aliases:
            label += f"（{'/'.join(speaker.aliases[:3])}）"
        stat = (speaker_stats or {}).get(speaker.speaker_id, {})
        card_status = stat.get("status", review_status)
        if card_status != review_status:
            status_label = {
                "confirmed": "✅ 已确认",
                "candidate": "🟡 AI 候选 · 需确认",
                "rejected": "⛔ 已拒绝",
            }.get(card_status, status_label)
        if stat:
            importance = stat.get("importance", "次要角色")
            count = int(stat.get("dialogue_count", 0) or 0)
            confidence = stat.get("confidence")
            confidence_text = (
                f" · 置信度 {float(confidence):.2f}"
                if confidence is not None
                else ""
            )
            label += f"\n{importance} · 对白 {count} 段{confidence_text}"
        binding = current_bindings.get(speaker.speaker_id)
        if binding:
            voice_id = getattr(binding, "voice_id", binding)
            label += f"\n✅ 已绑定\n音色：{Path(str(voice_id)).name}"
        else:
            label += "\n⚠ 待绑定"
        choices.append((label, speaker.speaker_id))
    return choices


def format_role_choices(
    script: dict,
    bindings: dict,
    role_categories: dict | None = None,
) -> list[tuple[str, str]]:
    """保留业务层分组和值，仅替换下拉显示标签。"""
    voices = script.get("voices", {}) or {}
    choices = _pm.build_role_choices(script, bindings, role_categories)
    formatted: list[tuple[str, str]] = []
    for category_label, role in choices:
        role_label = format_role_label(role, voices.get(role))
        if category_label.startswith("【") and "】" in category_label:
            category = category_label.split("】", 1)[0] + "】"
            formatted.append((f"{category}{role_label}", role))
        else:
            formatted.append((role_label, role))
    return formatted


def format_bound_role_choices(script: dict, bindings: dict) -> list[tuple[str, str]]:
    """将补录页的已绑定角色 choices 同样显示为角色描述格式。"""
    voices = script.get("voices", {}) or {}
    choices = _pm.build_bound_role_choices(script, bindings)
    return [
        (f"【已绑定】{format_role_label(role, voices.get(role))}", role)
        for _, role in choices
    ]
