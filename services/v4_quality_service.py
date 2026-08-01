"""质检服务：V4 项目的 segment / 章节音频、试听、重新生成指定 segment。

原「试听质检」页与 V4 工作台共用本服务。
"""
from __future__ import annotations

import json
from pathlib import Path

from repositories.audio_cache_repository import AudioCacheRepository
from repositories.production_repository import ProductionRepository
from repositories.runtime_repository import RuntimeRepository


class V4QualityService:
    @staticmethod
    def chapter_audio(project_path: str | Path, chapter_id: str) -> str | None:
        """返回章节音频绝对路径；不存在返回 None（页面显示空态，不抛异常）。"""
        if not chapter_id:
            return None
        path = Path(project_path) / "audio/chapters" / f"{chapter_id}.wav"
        return str(path) if path.is_file() else None

    @staticmethod
    def available_chapters(project_path: str | Path) -> list[str]:
        """已合成章节 ID 列表（按脚本顺序）。"""
        project = Path(project_path)
        script = _load_script_dict(project)
        chapters = [
            item["chapter_id"]
            for item in script.get("chapters", [])
            if (project / "audio/chapters" / f"{item['chapter_id']}.wav").is_file()
        ]
        return chapters

    @staticmethod
    def segment_audio(project_path: str | Path, segment_id: str) -> str | None:
        """按 segment 反查已合成音频（runtime 缓存 / 输出路径）。

        未找到任务或音频时返回 None（页面显示空态，不抛异常）。
        """
        project = Path(project_path)
        if not segment_id:
            return None
        runtime = RuntimeRepository(project / "runtime/runtime.db")
        if not runtime.path.is_file():
            return None
        try:
            paths = runtime.resolved_audio_paths(segment_id)
        except KeyError:
            return None
        for rel in paths:
            candidate = project / rel
            if candidate.is_file():
                return str(candidate)
        return None

    @staticmethod
    def segment_task_ids(project_path: str | Path, segment_id: str) -> list[str]:
        """按 segment 定位合成任务（chapter 内文本匹配）。"""
        project = Path(project_path)
        script = _load_script_dict(project)
        for chapter in script.get("chapters", []):
            for segment in chapter.get("segments", []):
                if segment.get("id") != segment_id:
                    continue
                text = segment.get("text_override") or _source_text(
                    project, segment
                )
                runtime = RuntimeRepository(project / "runtime/runtime.db")
                if not runtime.path.is_file():
                    return []
                return [
                    item["task_id"]
                    for item in runtime.tasks_for_text(
                        chapter["chapter_id"], text
                    )
                ]
        return []

    @staticmethod
    def regenerate_segment(
        project_path: str | Path, segment_id: str
    ) -> tuple[bool, str]:
        """重新生成指定 segment：失效其缓存并置回 pending，不影响其他缓存。

        Returns:
            ``(ok, message)``。
        """
        project = Path(project_path)
        task_ids = V4QualityService.segment_task_ids(project, segment_id)
        if not task_ids:
            return False, "未找到该片段的合成任务（可能尚未生成计划）。"
        runtime = RuntimeRepository(project / "runtime/runtime.db")
        cache = AudioCacheRepository(runtime.path, project)
        # 查 cache_key（按任务定位缓存条目）
        import sqlite3

        with sqlite3.connect(runtime.path) as connection:
            rows = connection.execute(
                "SELECT task_id, cache_key FROM synthesis_tasks "
                "WHERE task_id IN (%s)"
                % ",".join("?" * len(task_ids)),
                task_ids,
            ).fetchall()
        invalidated = 0
        requeued = 0
        for task_id, cache_key in rows:
            if cache_key:
                cache.invalidate(cache_key)
                invalidated += 1
            if runtime.requeue_task(task_id):
                requeued += 1
        message = (
            f"✅ 已重新生成片段：{requeued} 个任务置回队列"
            + (f"，{invalidated} 条缓存失效" if invalidated else "")
        )
        return True, message


def _load_script_dict(project: Path) -> dict:
    with (project / "script/script.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _source_text(project: Path, segment: dict) -> str:
    source = (project / "source/source.txt").read_text(encoding="utf-8")
    start = int(segment.get("source_start") or 0)
    end = int(segment.get("source_end") or 0)
    return source[start:end]
