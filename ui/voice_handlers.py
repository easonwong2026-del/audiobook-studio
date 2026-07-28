"""角色与声音页面的 AI 推荐与导演试听回调。"""
from __future__ import annotations

import html
import logging
import os

from lib import voice_lib
from services import (
    ScriptDirectorService,
    VoiceDirectorService,
)

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

        # 自动选中第一个推荐到声音下拉（但不绑定）
        names = voice_lib.voice_names()
        first = recommendations[0]["voice_name"] if recommendations else None

        return (
            rows,
            f"✅ 已为「{html.escape(str(role))}」生成 {len(rows)} 个候选；不会自动绑定。",
        )
    except Exception as exc:
        logger.exception("生成声音推荐失败")
        return [], f"❌ 推荐失败：{html.escape(str(exc))}"


def audition_director_segment(project_name, role, voice_name) -> tuple:
    """使用当前角色的一个代表 Segment 试听。"""
    if not project_name or not role or not voice_name:
        return None, "⚠ 请选择角色和试听声音"

    try:
        script = _load_project_script(project_name)

        # 找到角色的第一个 segment
        for chapter in script.get("chapters", []):
            for segment in chapter.get("segments", []):
                speaker = str(segment.get("speaker") or segment.get("role") or "旁白")
                if speaker == role:
                    # 使用服务的合成
                    from repositories.project_repo import ProjectRepository
                    from services import DirectorAuditionService

                    proj_dir = ProjectRepository.get_project_dir(project_name)
                    script_path = os.path.join(proj_dir, "structured_script.json")

                    output, cached = DirectorAuditionService.synthesize(
                        script_path,
                        str(segment.get("id")),
                        str(voice_name),
                    )
                    return output, f"✅ 导演试听已生成{'（命中缓存）' if cached else ''}"

        return None, "⚠ 未找到角色的代表 Segment"
    except Exception as exc:
        logger.exception("生成导演试听失败")
        return None, f"❌ 试听失败：{html.escape(str(exc))}"


def apply_feedback(project_name, feedback, role) -> tuple:
    """应用试听反馈到角色。"""
    if not project_name or not feedback:
        return None, "⚠ 请先试听并选择反馈"

    try:
        from repositories.project_repo import ProjectRepository
        proj_dir = ProjectRepository.get_project_dir(project_name)
        script_path = os.path.join(proj_dir, "structured_script.json")

        # 找角色的第一个 segment
        script = _load_project_script(project_name)
        target_seg = None
        for chapter in script.get("chapters", []):
            for segment in chapter.get("segments", []):
                speaker = str(segment.get("speaker") or segment.get("role") or "旁白")
                if speaker == (role or "") and target_seg is None:
                    target_seg = segment

        if not target_seg:
            return None, "⚠ 未找到角色的代表 Segment"

        result, backup, summary = ScriptDirectorService.apply_audition_feedback(
            script_path,
            str(target_seg.get("id")),
            str(feedback),
        )
        return None, f"✅ 已应用反馈：{summary}"
    except Exception as exc:
        logger.exception("应用试听反馈失败")
        return None, f"❌ 反馈失败：{html.escape(str(exc))}"
