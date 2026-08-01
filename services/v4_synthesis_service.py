"""统一合成服务：V4 计划生成 → 后台线程执行 → 暂停 / 继续 / 取消 / 中断恢复。

原「生产与质检」页与临时 V4 工作台共用本服务，禁止复制业务逻辑。

语义约定（与任务要求一一对应）：
- **pause**：协作暂停——当前 TTS 推理**完成后**在任务边界挂起，不杀进行中的进程；
  暂停期间仍响应取消。
- **resume**：继续执行；已完成任务由 ``RuntimeRepository.claim_next_task`` 天然跳过，
  不会重复合成。
- **cancel**：停止——已完成任务的音频与缓存（``synthesis_tasks`` + ``cache_entries``）
  已持久化，**保留**；pending 任务立即标记 cancelled，正在合成的任务完成后退出。
- **interrupted**：进程异常退出后，下次 ``run()`` 开头调用
  ``recover_interrupted_tasks()`` 把 running 任务复位为 pending 继续。

所有运行状态（running / paused / cancelled / done / error）写入 runtime.db
``run_state`` 表，页面刷新或重启后仍可读取。
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lib import config
from repositories.audio_cache_repository import AudioCacheRepository
from repositories.production_repository import ProductionRepository
from repositories.runtime_repository import RuntimeRepository
from services.chapter_assembler import ChapterAssembler
from services.invalidation_service import InvalidationService
from services.plan_preview import synthesis_plan_rows, synthesis_plan_summary
from services.synthesis_executor import ExecutionSummary, SynthesisExecutor
from services.synthesis_planner import SynthesisPlanner
from tts.indextts2_adapter import IndexTTS2Adapter
from tts.text_measurement import CharacterMeasurer, ConservativeTokenMeasurer


@dataclass
class V4SynthesisRun:
    """一次合成运行（进程内注册表项；状态同时持久化到 runtime.db）。"""

    project_name: str
    project_path: Path
    cancel_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)
    status: str = "idle"  # idle|running|paused|cancelling|done|error
    error: str = ""
    summary: Optional[ExecutionSummary] = None
    thread: Optional[threading.Thread] = None


class V4SynthesisService:
    """统一合成服务（进程级注册表 + runtime.db 持久化）。"""

    _runs: dict[str, V4SynthesisRun] = {}
    _lock = threading.Lock()

    # ── 计划 ────────────────────────────────────────────────────────────────

    @classmethod
    def ensure_plan(cls, project_path: str | Path) -> tuple[bool, str]:
        """确保项目已有合成计划；没有则自动生成（返回 (ok, 说明)）。

        若存在 unresolved 片段或未绑定角色，计划仍会生成（这些片段被跳过），
        并在说明中提示用户。
        """
        project = Path(project_path)
        production = ProductionRepository(project)
        if production.load_plan() is not None:
            return True, ""
        _rows, message = cls.generate_plan(project)
        return True, message

    @classmethod
    def generate_plan(cls, project_path: str | Path) -> tuple[list[list], str]:
        """生成合成计划并同步 runtime（局部失效）。返回 (计划行, 消息)。"""
        project = Path(project_path)
        source = (project / "source/source.txt").read_text(encoding="utf-8")
        script = _script_document(project, source)
        speakers = _speakers_document(project)
        production = ProductionRepository(project)
        voices, performance, pronunciation, profile = production.load_inputs()
        previous = production.load_plan()
        measurer = (
            ConservativeTokenMeasurer()
            if profile.limits.metric == "tokens"
            else CharacterMeasurer()
        )
        result = SynthesisPlanner(measurer).plan(
            source,
            script,
            speakers,
            voices,
            performance,
            pronunciation,
            profile,
            previous_plan=previous,
        )
        production.save_plan(result.plan)
        runtime = RuntimeRepository(project / "runtime/runtime.db")
        runtime.initialize()
        diff = InvalidationService.sync_runtime(runtime, previous, result.plan)
        summary = synthesis_plan_summary(result.plan)
        message = (
            f"✅ 计划 revision {result.plan.revision}：{summary['task_count']} tasks；"
            f"复用 {len(diff.reusable_task_ids)}，stale {len(diff.stale_task_ids)}；"
            f"unresolved {len(result.unresolved_segments)}，未绑定角色 "
            f"{len(result.unbound_speakers)}"
        )
        return synthesis_plan_rows(result.plan), message

    # ── 启动 / 控制 ──────────────────────────────────────────────────────────

    @classmethod
    def start(cls, project_name: str) -> tuple[bool, str]:
        """启动后台合成。已存在进行中的运行则拒绝重复启动。"""
        project_path = _root() / project_name
        with cls._lock:
            run = cls._runs.get(project_name)
            if run is not None and run.status in ("running", "paused", "cancelling"):
                return False, f"⚠ 已有合成任务进行中（{run.status}），请先停止。"
            run = V4SynthesisRun(project_name=project_name, project_path=project_path)
            cls._runs[project_name] = run
        production = ProductionRepository(project_path)
        if production.load_plan() is None:
            run.status = "error"
            run.error = "尚未生成合成计划，请先「生成计划」。"
            return False, run.error
        run.status = "running"
        run.thread = threading.Thread(
            target=cls._worker, args=(run,), daemon=True, name=f"v4-synth-{project_name}"
        )
        run.thread.start()
        return True, "🚀 合成已启动（后台任务，可随时暂停 / 取消）"

    @classmethod
    def pause(cls, project_name: str) -> tuple[bool, str]:
        """协作暂停：当前任务完成后在任务边界挂起。"""
        run = cls._runs.get(project_name)
        if run is None or run.status not in ("running", "paused"):
            return False, "当前没有运行中的合成任务。"
        run.pause_event.set()
        run.status = "paused"
        _set_runtime_status(run.project_path, "paused")
        return True, "⏸ 已请求暂停（当前片段完成后挂起）"

    @classmethod
    def resume(cls, project_name: str) -> tuple[bool, str]:
        """继续合成；已完成任务自动跳过。"""
        run = cls._runs.get(project_name)
        if run is None or run.status != "paused":
            return False, "当前没有已暂停的合成任务。"
        run.pause_event.clear()
        run.status = "running"
        _set_runtime_status(run.project_path, "running")
        return True, "▶ 已继续合成"

    @classmethod
    def cancel(cls, project_name: str) -> tuple[bool, str]:
        """取消合成：pending 任务立即取消，已完成缓存保留。"""
        run = cls._runs.get(project_name)
        if run is None or run.status not in ("running", "paused", "cancelling"):
            return False, "当前没有可取消的合成任务。"
        run.cancel_event.set()
        run.status = "cancelling"
        _set_runtime_status(run.project_path, "cancelling")
        count = _cancel_pending(run.project_path)
        return True, f"🛑 正在停止…（已取消 {count} 个尚未开始的任务）"

    @classmethod
    def snapshot(cls, project_name: str) -> dict[str, Any]:
        """汇总运行状态（供页面刷新）。"""
        project_path = _root() / project_name
        run = cls._runs.get(project_name)
        runtime = RuntimeRepository(project_path / "runtime/runtime.db")
        runtime.initialize()
        counts = runtime.task_counts()
        cache_hits = _cache_hits(runtime)
        if run is not None and run.status == "error":
            return {
                "run_status": "error", "error": run.error, "counts": counts,
                "cache_hits": cache_hits,
            }
        if run is not None and run.status in ("done", "cancelled"):
            summary = run.summary
            text = (
                f"完成 {summary.completed}，缓存 {summary.cache_hits}，"
                f"拆分 {summary.split_parents}，失败 {summary.failed}"
                + ("（已取消）" if summary.cancelled else "")
            )
            return {
                "run_status": run.status, "text": text, "counts": counts,
                "cache_hits": cache_hits,
            }
        if run is not None and run.status in ("running", "paused", "cancelling"):
            return {
                "run_status": run.status,
                "text": f"状态：{run.status}",
                "counts": counts, "cache_hits": cache_hits,
            }
        persisted = runtime.run_status()
        if persisted in ("done", "cancelled", "error"):
            return {
                "run_status": persisted, "text": "上次运行：" + persisted,
                "counts": counts, "cache_hits": cache_hits,
            }
        return {
            "run_status": "idle", "text": "尚未开始合成",
            "counts": counts, "cache_hits": cache_hits,
        }

    @classmethod
    def queue_rows(cls, project_name: str) -> list[list]:
        """runtime.db 中的任务行（供队列表格展示）。"""
        project_path = _root() / project_name
        path = project_path / "runtime/runtime.db"
        if not path.is_file():
            return []
        RuntimeRepository(path).initialize()
        with sqlite3.connect(path) as connection:
            return [
                list(item)
                for item in connection.execute(
                    """
                    SELECT task_id, chapter_id, speaker_id, status, text_length,
                           attempts, split_depth, CASE WHEN output_path IS NULL
                           THEN '' ELSE '✓' END
                      FROM synthesis_tasks ORDER BY created_at, task_id
                    """
                )
            ]

    @classmethod
    def _worker(cls, run: V4SynthesisRun) -> None:
        runtime = RuntimeRepository(run.project_path / "runtime/runtime.db")
        runtime.initialize()
        adapter: IndexTTS2Adapter | None = None
        try:
            runtime.set_run_status("running")
            production = ProductionRepository(run.project_path)
            _voices, _performance, _pronunciation, profile = production.load_inputs()
            plan = production.load_plan()
            if plan is None:
                raise RuntimeError("尚未生成合成计划，请先「生成计划」。")
            model_dir = config.get_model_dir()
            adapter = IndexTTS2Adapter(
                model_dir,
                lambda voice_id: run.project_path / voice_id,
            )
            measurer = (
                ConservativeTokenMeasurer()
                if profile.limits.metric == "tokens"
                else CharacterMeasurer()
            )
            summary = SynthesisExecutor(
                runtime,
                AudioCacheRepository(runtime.path, run.project_path),
                adapter,
                measurer,
                run.project_path,
            ).run(
                profile,
                should_cancel=lambda: run.cancel_event.is_set(),
                should_pause=lambda: run.pause_event.is_set(),
            )
            for chapter in {item.chapter_id for item in plan.tasks}:
                tasks = [item for item in plan.tasks if item.chapter_id == chapter]
                try:
                    ChapterAssembler(runtime, run.project_path).assemble(
                        chapter, tasks, plan_revision=plan.revision
                    )
                except RuntimeError:
                    continue
            run.summary = summary
            run.status = "cancelled" if summary.cancelled else "done"
            run.error = ""
            runtime.set_run_status(run.status)
        except Exception as exc:  # noqa: BLE001 - 用户可读错误
            run.status = "error"
            run.error = str(exc)[:500]
            try:
                runtime.set_run_status("error")
            except Exception:  # noqa: BLE001
                pass
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:  # noqa: BLE001
                    pass


def _root() -> Path:
    return Path(config.get_projects_root())


def _set_runtime_status(project_path: Path, status: str) -> None:
    try:
        RuntimeRepository(project_path / "runtime/runtime.db").set_run_status(status)
    except Exception:  # noqa: BLE001
        pass


def _cancel_pending(project_path: Path) -> int:
    runtime = RuntimeRepository(project_path / "runtime/runtime.db")
    runtime.initialize()
    return runtime.cancel_pending_tasks()


def _cache_hits(runtime: RuntimeRepository) -> int:
    if not runtime.path.is_file():
        return 0
    try:
        with sqlite3.connect(runtime.path) as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(cache_hit), 0) FROM synthesis_metrics"
            ).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0


def _script_document(project: Path, source: str):
    import json

    from domain.v4 import ScriptDocument

    return ScriptDocument.from_dict(
        json.loads((project / "script/script.json").read_text(encoding="utf-8")),
        source,
    )


def _speakers_document(project: Path):
    import json

    from domain.v4 import SpeakersDocument

    return SpeakersDocument.from_dict(
        json.loads(
            (project / "script/speakers.json").read_text(encoding="utf-8")
        )
    )
