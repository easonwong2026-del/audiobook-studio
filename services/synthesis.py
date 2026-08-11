"""Runtime-private synthesis worker + segment-boundary control.

设计要点：
- 重活只在独立 ``production_runtime`` 的 worker 线程跑；Web/MCP 轮询 SQLite。
- 后台队列选 ``concurrent.futures.ThreadPoolExecutor``（单 worker，单 GPU 串行安全），
  不引入 asyncio：TTS 引擎是同步阻塞 API，asyncio 仍需 ``run_in_executor``，
  本质等同本方案但更复杂。
- 协作取消：worker 在驱动 ``lib.queue.synthesize_project`` 的**每个 yield 之间**
  检查 ``state.cancel`` 标志；单段内 GPU 调用不可中断（杀线程会泄漏显存），
  故取消粒度细化到「段边界」，这是务实解耦档的明确边界（D2）。

``persist_task=True`` 仅保留给兼容测试；正式生产由 runtime 持久化 owner、
heartbeat、control intent 和进度。
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Optional

from lib import progress as _progress
from lib import project_manager as pm
from lib import project_paths, segment_cache
from lib import queue as synth_queue
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository

logger = logging.getLogger(__name__)

# 末 N 行日志用于显示（D5）
_MAX_LOG_LINES = 50

# 合法状���集合（供 UI / 守卫校验）
SYNTHESIS_STATES = (
    "pending", "running", "pausing", "paused",
    "recovering",
    "cancelling", "cancelled", "done", "error", "interrupted",
    "needs_attention",
)


@dataclass
class SynthesisState:
    """合成任务态：被 worker 线程写、UI 轮询读。

    Attributes:
        task_id: 任务标识（由 ``SynthesisService.start`` 生成）。
        project: 项目名。
        status: pending | running | pausing | paused | cancelling
            | cancelled | done | error。
        progress: 0..1 进度。
        total: 总段数（预读项目元数得到）。
        completed: 已完成段数（以项目真实累计为准，兼容断点续跑）。
        current_segment: 正在合成的段 ID（如已知）。
        log_lines: 滚动日志（末 50 行）。
        cancel: 协作取消标志（由 ``SynthesisService.cancel`` 置位，worker 段边界检查）。
        cancel_requested: 取消请求标志（语义同 cancel，供 UI / 守卫判断「已请求停止」）。
        future: 后台任务的 Future（5.4：存于 state，便于判断任务是否仍在运行）。
        error: 错误信息（status=error 时）。
        paused: 协作暂停标志（由 ``SynthesisService.pause/resume`` 置位，段边界挂起）。
        segment_states: 合成期内存实时段态列表（O3 队列列表数据源），
            由 ``lib.progress.build_segment_states`` 初始化、``cb_seg_state`` 更新，
            **绝不反向写** ``meta.segments_status``。
    """

    task_id: str
    project: str
    status: str = "pending"
    progress: float = 0.0
    total: int = 0
    completed: int = 0
    current_segment: Optional[str] = None
    log_lines: list[str] = field(default_factory=list)
    cancel: bool = False
    cancel_requested: bool = False
    future: Any = None
    error: Optional[str] = None
    paused: bool = False
    segment_states: list[dict] = field(default_factory=list)
    # ProductionJobService uses this in-memory hook to persist a V2 snapshot.
    # It is deliberately not part of TaskRecord and never reaches JSON.
    on_update: Optional[Callable[["SynthesisState"], None]] = field(
        default=None, repr=False, compare=False
    )
    selected_segment_ids: Optional[list[str]] = field(
        default=None, repr=False, compare=False
    )
    selected_chapters: Optional[list[str]] = field(
        default=None, repr=False, compare=False
    )
    failed_segment_ids: list[str] = field(default_factory=list)
    recovery: Optional[dict] = field(default=None, repr=False, compare=False)
    engine_generation: int = 0
    last_failure: Optional[dict] = field(default=None, repr=False, compare=False)

    def append_log(self, line: str) -> None:
        """追加一行日志并保留末 50 行（D5）。"""
        self.log_lines.append(line)
        if len(self.log_lines) > _MAX_LOG_LINES:
            self.log_lines = self.log_lines[-_MAX_LOG_LINES:]

    def snapshot_text(self) -> str:
        """当前日志快照文本（UI 直接展示）。"""
        return "\n".join(self.log_lines[-_MAX_LOG_LINES:])

    def notify(self) -> None:
        """Notify an optional owner without allowing persistence to break TTS."""
        callback = self.on_update
        if callback is None:
            return
        try:
            callback(self)
        except Exception:  # pragma: no cover - defensive boundary for workers
            logger.exception("合成状态回调失败")


class SynthesisService:
    """后台合成服务（模块级单 worker 线程池）。

    executor 为模块级单例（D4：随进程退出由解释器回收，无需 ``atexit`` 显式 shutdown）。
    提供 reset_executor() 用���测试隔离（避免跨测试线程池状态污染）。
    """

    _executor: ClassVar[ThreadPoolExecutor] = ThreadPoolExecutor(max_workers=1)

    @classmethod
    def reset_executor(cls) -> None:
        """关闭旧线程池并创建新线程池（测试隔离用）。"""
        cls._executor.shutdown(wait=False, cancel_futures=True)
        cls._executor = ThreadPoolExecutor(max_workers=1)

    @staticmethod
    def start(state, project, bindings, num_beams: int = 2,
              emotion: str = None, emo_alpha: float = None,
              speech_rate: float = None, cb_seg_state=None,
              selected_chapters: Optional[list] = None,
              selected_segment_ids: Optional[list] = None,
              persist_task: bool = True,
              voice_overrides: Optional[dict[str, str]] = None,
              recovery=None, budget=None) -> str:
        """提交后台合成，立即返回 task_id；重活在 worker 线程执行。

        阶段四：在提交 worker 前写入 TaskRecord（running 态）。

        Args:
            state: 合成任务态（由调用方创建并持有引用，用于后续轮询 / 取消）。
            project: 项目名。
            bindings: 角色 -> 参考音频路径 的绑定表。
            num_beams: GPT beam search 宽度。
            emotion: 全局情感覆盖（None=按剧本）。
            emo_alpha: 全局情绪强度覆盖（None=按剧本）。
            speech_rate: 全局语速覆盖（None=按剧本）。
            cb_seg_state: 可选段状态回调，签名 ``(seg_id, status, progress=0.0, **meta)``；
                ``None`` 时 ``_run`` 内部以 ``state.segment_states`` 为数据源自建回调。
            selected_chapters: 勾选导出的章节 id 列表。
            selected_segment_ids: 可选的精确段范围；用于失败段重试。
            persist_task: 是否由本兼容服务写旧版 TaskRecord。统一生产任务由
                ``ProductionJobService`` 持久化，传入 False 避免覆盖 V2 字段。

        Returns:
            task_id。
        """
        state.status = "pending"
        state.cancel = False
        state.cancel_requested = False
        state.paused = False
        state.error = None
        state.selected_chapters = (
            [str(item) for item in selected_chapters]
            if selected_chapters else None
        )
        state.selected_segment_ids = (
            [str(item) for item in selected_segment_ids]
            if selected_segment_ids else None
        )
        state.future = SynthesisService._executor.submit(
            SynthesisService._run, state, project, bindings,
            num_beams, emotion, emo_alpha, speech_rate, cb_seg_state,
            selected_chapters, selected_segment_ids, persist_task,
            voice_overrides, recovery, budget,
        )
        # 写入任务状态记录（running）
        if persist_task:
            try:
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                TaskRepository.save_task(TaskRecord(
                    task_id=state.task_id,
                    task_type="synthesis",
                    project=project,
                    status="running",
                    source="system",
                    progress={
                        "total": state.total,
                        "completed": state.completed,
                        "failed": len(state.failed_segment_ids),
                    },
                    created_at=now,
                    started_at=now,
                    updated_at=now,
                ))
            except Exception as exc:
                logger.warning("保存合成任务状态失败: %s", exc)
        state.notify()
        return state.task_id

    @staticmethod
    def get_snapshot(state: SynthesisState) -> SynthesisState:
        """UI 轮询用：返回当前态（dataclass 可序列化，原样返回同一对象即可）。"""
        return state

    @staticmethod
    def cancel(state: SynthesisState) -> None:
        """请求取消：置 ``cancel`` / ``cancel_requested=True`` 并标记 ``status='cancelling'``。

        5.4 状态机：点击停止 → 进入 ``cancelling``（中间态），worker 在下一「段边界」
        检查到 ``cancel`` 标志后真正退出并置 ``cancelled``（终态）。UI 在 ``cancelling``
        期间展示「正在停止，当前段完成后结束」并禁止开启新任务。cancel 优先于 pause。
        """
        state.cancel = True
        state.cancel_requested = True
        state.status = "cancelling"
        state.notify()

    @staticmethod
    def pause(state: SynthesisState) -> None:
        """请求协作暂停，并在 worker 段边界确认 ``paused``。

        ``pausing`` 表示当前 GPU 段仍可能执行；只有 worker 到达安全边界后
        才会写 ``paused``。暂停中亦可取消（cancel 优先）。
        """
        state.paused = True
        if state.status in {"pending", "running"}:
            state.status = "pausing"
        state.notify()

    @staticmethod
    def resume(state: SynthesisState) -> None:
        """撤销暂停意图，worker 继续拉取下一段。"""
        state.paused = False
        if state.status in {"paused", "pausing"}:
            state.status = "running"
        state.notify()

    @staticmethod
    def get_segment_states(state: SynthesisState) -> list[dict]:
        """返回合成期内存段态列表（只读快照），供 O3 队列列表渲染。"""
        return state.segment_states

    @staticmethod
    def _persist_legacy_task(
        state: SynthesisState,
        project: str,
        status: str,
        error_summary: str = "",
    ) -> None:
        """Persist direct legacy callers without clobbering an existing V2 row."""
        try:
            record = TaskRepository.load_task(state.task_id)
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            if record is None:
                record = TaskRecord(
                    task_id=state.task_id,
                    task_type="synthesis",
                    project=project,
                    status=status,
                    created_at=now,
                )
            record.status = status
            record.project = project
            record.progress = {
                "total": state.total,
                "completed": state.completed,
                "failed": len(state.failed_segment_ids),
                "current_segment": state.current_segment,
            }
            record.failed_segment_ids = list(state.failed_segment_ids)
            record.error_summary = error_summary
            record.updated_at = now
            if status in {"done", "cancelled", "error"}:
                record.finished_at = now
            TaskRepository.save_task(record)
        except Exception as exc:
            logger.warning("保存合成任务状态失败: %s", exc)

    @staticmethod
    def _run(state: SynthesisState, project: str, bindings: dict, num_beams: int = 2,
             emotion: str = None, emo_alpha: float = None,
             speech_rate: float = None, cb_seg_state=None,
             selected_chapters: Optional[list] = None,
             selected_segment_ids: Optional[list] = None,
             persist_task: bool = True,
             voice_overrides: Optional[dict[str, str]] = None,
             recovery=None, budget=None) -> None:
        """worker 主体：驱动 ``lib.queue.synthesize_project``，逐 yield 写回 ``state``。

        段边界检查 ``state.cancel`` -> 协作取消（置 ``cancelled`` 终态）；检查
        ``state.paused`` -> 协作暂停挂起（O12：不杀进行中进程，仅停止提交新段；
        进行中段仍跑完即停）。单测可直接同步调用本方法（无需线程）。

        阶段四：终态（done / cancelled / error）后更新 TaskRecord。

        Args:
            state: 合成任务态。
            project: 项目名。
            bindings: 角色 -> 参考音频路径 的绑定表。
            num_beams: GPT beam search 宽度。
            emotion: 全局情感覆盖（None=按剧本）。
            emo_alpha: 全局情绪强度覆盖（None=按剧本）。
            speech_rate: 全局语速覆盖（None=按剧本）。
            cb_seg_state: 可选段状态回调；``None`` 时以 ``state.segment_states`` 为
                数据源自建回调（``lib.progress.update_segment_state``）。
            selected_segment_ids: 可选的精确段范围。
            persist_task: 是否写入兼容版 TaskRecord。
        """
        if state.cancel:
            state.status = "cancelling"
        elif state.paused:
            state.status = "pausing"
        else:
            state.status = "running"
        state.append_log("🚀 开始合成…")
        state.notify()
        # 默认回调：直接驱动本任务的 state.segment_states（O3 列表数据源）
        if cb_seg_state is None:
            def _default_cb(seg_id, status, progress=0.0, **meta):
                _progress.update_segment_state(
                    state.segment_states, seg_id, status, progress, **meta
                )
            cb_seg_state = _default_cb
        try:
            # 预读总数与已完成数（用于进度条分母 / 断点续跑初值），仅读一次。
            # P2 提速：进度不再于每个 yield 重新打开 project.json 计算，改为本地累加。
            try:
                meta, script_data, _ = pm.open_project(project)
                chapters = script_data.get("chapters", []) if isinstance(script_data, dict) else []
                selected_chapter_set = (
                    {str(item) for item in selected_chapters}
                    if selected_chapters else None
                )
                selected_segment_set = (
                    {str(item) for item in selected_segment_ids}
                    if selected_segment_ids else None
                )
                scope_ids: list[str] = []
                for chapter in chapters:
                    if not isinstance(chapter, dict):
                        continue
                    chapter_id = str(chapter.get("id", ""))
                    if selected_chapter_set is not None and chapter_id not in selected_chapter_set:
                        continue
                    for segment in chapter.get("segments", []):
                        if not isinstance(segment, dict):
                            continue
                        segment_id = str(segment.get("id", ""))
                        if selected_segment_set is not None and segment_id not in selected_segment_set:
                            continue
                        if segment_id:
                            scope_ids.append(segment_id)
                if state.total <= 0:
                    state.total = len(scope_ids) if (
                        selected_chapter_set is not None or selected_segment_set is not None
                    ) else max(meta.total_segments, 0)
                # ProductionJobService seeds these counts.  The fallback keeps
                # direct legacy callers correct for chapter/segment scopes and
                # treats a stale ``done`` marker without its WAV as pending.
                segments_dir = project_paths.project_dir(
                    ProjectRepository.get_project_dir(project), "segments"
                )
                completed_ids = {
                    segment_id for segment_id in scope_ids
                    if meta.segments_status.get(segment_id) == "done"
                    and segment_cache.has_segment_wav(segments_dir, segment_id)
                }
                if state.completed <= 0:
                    state.completed = len(completed_ids)
                state.failed_segment_ids = [
                    segment_id for segment_id in scope_ids
                    if meta.segments_status.get(segment_id) == "failed"
                ]
            except Exception:  # pylint: disable=broad-except
                if state.total < 0:
                    state.total = 0
                if state.completed < 0:
                    state.completed = 0
            state.progress = min(1.0, state.completed / max(state.total, 1))
            state.notify()

            gen = synth_queue.synthesize_project(
                project, bindings, num_beams=num_beams,
                emotion=emotion, emo_alpha=emo_alpha, speech_rate=speech_rate,
                cb_seg_state=cb_seg_state, selected_chapters=selected_chapters,
                selected_segment_ids=selected_segment_ids,
                voice_overrides=voice_overrides,
                recovery=recovery,
                budget=budget,
            )
            # 手动驱动生成器：在「段边界」检查暂停/取消，并控制是否向下拉取（暂停时
            # 不调 next，从而不提交新段）。等价于原 ``for raw in gen``，但支持协作暂停。
            _paused_logged = False
            while True:
                # 段边界协作暂停：暂停且未取消时不向下拉取（不提交新段），worker 存活挂起
                while state.paused and not state.cancel:
                    if state.status != "paused":
                        state.status = "paused"
                        state.notify()
                    if not _paused_logged:
                        state.append_log("⏸ 已暂停，等待恢复…")
                        _paused_logged = True
                    time.sleep(0.1)
                # 段边界协作取消点：暂停中亦可取消（cancel ��先）。
                # 5.4：cancel() 已置 status='cancelling'，此处真正退出并置 'cancelled' 终态。
                if state.cancel:
                    state.append_log("⏹ 已停止（用户取消）")
                    state.status = "cancelled"
                    state.cancel_requested = True
                    if persist_task:
                        SynthesisService._persist_legacy_task(
                            state, project, "cancelled"
                        )
                    state.notify()
                    return

                try:
                    raw = next(gen)
                except StopIteration:
                    break
                # 成功拉取到一段 yield -> 重置暂停日志标记（下次暂停再提示一次）
                _paused_logged = False

                if raw.startswith("[+]"):
                    parts = raw[3:].split("|")
                    line = f"✅ {parts[0]} {parts[1]}" if len(parts) > 1 else raw
                    state.append_log(line)
                    segment_id = parts[0].strip() if parts else ""
                    if segment_id and segment_id in state.failed_segment_ids:
                        state.failed_segment_ids.remove(segment_id)
                    # P2 提速：本地累加已完成段数，避免每 yield 重读 project.json
                    state.completed += 1
                    state.progress = min(1.0, state.completed / max(state.total, 1))
                elif raw.startswith("[/]"):
                    # 合成中提示（含段 ID），仅更新当前段，不记录到日志
                    marker = raw[3:].strip() if len(raw) > 3 else ""
                    seg = (
                        marker.split(" ")[0]
                        if marker and marker != "vram_clean" else None
                    )
                    if seg:
                        state.current_segment = seg
                    state.notify()
                    continue
                elif raw.startswith("[=]"):
                    parts = raw[3:].split("|")
                    title = parts[1] if len(parts) > 1 else ""
                    state.append_log(f"📖 第{parts[0][2:]}章 {title} 完成")
                elif raw.startswith("[0]"):
                    line = raw.replace("[0] ", "✅ ").replace("all_done", "所有段落已完成")
                    state.append_log(line)
                elif raw.startswith("[X]"):
                    failed_segment = raw[3:].strip().split(" ", 1)[0] if len(raw) > 3 else ""
                    if failed_segment and failed_segment not in state.failed_segment_ids:
                        state.failed_segment_ids.append(failed_segment)
                    state.append_log(raw.replace("[X]", "❌"))
                elif raw.startswith("[re] stop"):
                    # Recovery budget exhausted: the queue stops pulling new
                    # segments.  Persist needs_attention and end the run.
                    state.status = "needs_attention"
                    state.append_log("⛔ 自动恢复失败，停止继续生产")
                    state.notify()
                    break
                elif raw.startswith("[re] cancelled"):
                    # Cancel won during recovery; the top-of-loop cancel
                    # check transitions the task to cancelled without
                    # pulling more segments.
                    state.notify()
                    continue
                else:
                    state.append_log(raw)
                state.notify()

            if state.status != "needs_attention":
                state.status = "error" if state.failed_segment_ids else "done"
                state.progress = min(1.0, state.completed / max(state.total, 1))
                state.append_log(
                    "❌ 合成完成，但存在失败段落"
                    if state.failed_segment_ids else "✅ 合成完成"
                )
            if persist_task:
                SynthesisService._persist_legacy_task(
                    state,
                    project,
                    state.status,
                    "存在失败段落" if state.failed_segment_ids else "",
                )
            state.notify()
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("合成失败: %s", exc)
            state.status = "error"
            state.error = str(exc)
            state.append_log(f"❌ 合成错误: {exc}")
            if persist_task:
                SynthesisService._persist_legacy_task(
                    state, project, "error", str(exc)[:200]
                )
            state.notify()
        finally:
            pass  # empty_cache 移出 finally，避免后台线程中 torch CUDA 访问 segfault
