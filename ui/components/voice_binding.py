"""角色声音绑定区的展示格式化辅助。

这里仅负责把已经由业务层计算好的角色 choices 转成面向用户的标签，
不改变角色值、绑定表或任何持久化协议。
"""
from __future__ import annotations

from lib import project_manager as _pm


def format_role_label(role: str, voice: dict | None = None) -> str:
    """将角色与剧本描述合并为易读标签：``角色（描述）``。"""
    voice = voice or {}
    description = str(voice.get("description") or voice.get("name") or "").strip()
    if not description:
        return role
    return f"{role}（{description}）"


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
