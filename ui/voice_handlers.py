"""角色与声音页面的 AI 推荐回调。"""
from __future__ import annotations

import html
import logging

from services import VoiceDirectorService

logger = logging.getLogger(__name__)


def _load_project_script(project_name: str) -> dict:
    """从项目名加载剧本。"""
    from services import ProjectService
    meta, script, bindings = ProjectService.open_project(project_name)
    return script


def recommend_voice(project_name, role) -> tuple:
    """为角色推荐声音。"""
    if not project_name or not role:
        return [], "⚠ 请先从左侧角色列表选择角色"

    try:
        role = str(role)
        script = _load_project_script(project_name)
        recommendations = VoiceDirectorService.recommend(script, str(role))
        rows = [
            [item["voice_name"], item["category"], item["score"], item["reasons"]]
            for item in recommendations
        ]
        if not recommendations:
            return [], "⚠ 音色库为空，请先添加声音"


        return (
            rows,
            f"✅ 已为「{html.escape(str(role))}」生成 {len(rows)} 个候选；不会自动绑定。",
        )
    except Exception as exc:
        logger.exception("生成声音推荐失败")
        return [], f"❌ 推荐失败：{html.escape(str(exc))}"
