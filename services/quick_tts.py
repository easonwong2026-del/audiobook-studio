"""Quick TTS —— 无项目临时配音服务（禁止 import gradio，可单测）。

用户不创建项目 / 不导入书 / 不绑定 Voice Cast，临时 1~2 句台词：
选全局声音库 → 输入台词 → 生成 → 试听 / 导出。

关键约束（PR B 修复 5）：
- **不污染项目书架**：任务使用正式 global utility context
  ``QUICK_TTS_CONTEXT="__quick_tts__"``，持久化在
  ``<data_dir>/runtime/utility_tasks.sqlite3``，绝不创建 project.json /
  structured_script.json，不会出现在 ``ProjectService.scan_projects()``。
- **必须走 singleton runtime**：本服务不直接 TTS()/init model/GPU inference，
  通过 ``RuntimeTTSService``（durable utility task）+ 既有 ProductionRuntime
  claim/GPU owner 完成，不复制 GPU 调度。
- **busy 语义**：正式整书生产（或任何 TTS/export lane）运行中 → 抛
  ``QuickTTSBusyError``，不抢 GPU、不停正式任务、不偷偷切 engine。
- **引擎**：无显式 engine 时按既有 utility 选择规则（pending switch target >
  runtime current > settings default），并在 task 创建时冻结 engine_snapshot。
- **产物目录**：cache ``<data_dir>/quick_tts/cache/<task_id>/``（允许清理）；
  exports ``<data_dir>/quick_tts/exports/``（用户主动导出，不自动删）。
"""
from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from typing import Any

from lib import config
from repositories.task_repo import QUICK_TTS_CONTEXT

logger = logging.getLogger(__name__)

_CACHE_DIRNAME = "cache"
_EXPORTS_DIRNAME = "exports"
_DEFAULT_CACHE_MAX_AGE_DAYS = 7


class QuickTTSBusyError(RuntimeError):
    """Raised when the singleton TTS engine is busy with another lane."""

    code = "QUICK_TTS_BUSY"

    def __init__(self, active: Any) -> None:
        task_type = str(getattr(active, "task_type", "") or "生产")
        status = str(getattr(active, "status", "") or "")
        task_id = str(getattr(active, "task_id", "") or "未知")
        super().__init__(
            "当前正式生产任务正在使用 TTS 引擎，请生产完成或暂停后再进行临时配音"
            f"（任务 {task_id}，类型 {task_type}，状态 {status}）"
        )
        self.task_id = task_id
        self.status = status
        self.task_type = task_type


class QuickTTSService:
    """无项目临时配音编排：路径 / 合成 / 导出（纯业务，无 UI）。"""

    @staticmethod
    def root_dir() -> str:
        return os.path.join(config.get_data_dir(), "quick_tts")

    @staticmethod
    def cache_root() -> str:
        root = os.path.join(QuickTTSService.root_dir(), _CACHE_DIRNAME)
        os.makedirs(root, exist_ok=True)
        return root

    @staticmethod
    def exports_root() -> str:
        root = os.path.join(QuickTTSService.root_dir(), _EXPORTS_DIRNAME)
        os.makedirs(root, exist_ok=True)
        return root

    @staticmethod
    def task_cache_dir(task_id: str | None = None) -> str:
        task_id = str(task_id or "").strip() or uuid.uuid4().hex
        path = os.path.join(QuickTTSService.cache_root(), task_id)
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def synthesize(
        text: str,
        speaker_audio: str,
        *,
        num_beams: int = 2,
        overrides: dict[str, Any] | None = None,
        timeout: float = 3600.0,
        progress_cb: Any = None,
    ) -> str:
        """Synthesize one or more sentences through the singleton runtime.

        Returns the generated WAV absolute path.

        Raises:
            QuickTTSBusyError: 另一 lane（正式生产 / 补录 / 试听 / 导出）运行中。
            RuntimeTTSError: runtime 任务失败 / 超时。
            ValueError: 台词为空或未提供参考声音。
        """
        text = str(text or "").strip()
        speaker = str(speaker_audio or "").strip()
        if not text:
            raise ValueError("请输入台词（至少一句）")
        if not speaker or not os.path.isfile(speaker):
            raise ValueError("请选择全局声音库中的参考声音")

        from .runtime_tts import RuntimeTTSService

        return RuntimeTTSService.quick_tts_synthesize(
            text=text,
            speaker_audio=speaker,
            num_beams=num_beams,
            overrides=overrides,
            timeout=timeout,
            progress_cb=progress_cb,
        )

    @staticmethod
    def export(
        wav_path: str,
        name: str,
        fmt: str,
        bitrate: str = "192k",
        *,
        title: str | None = None,
        artist: str | None = None,
    ) -> str:
        """Export one quick-TTS WAV into the user exports directory.

        命名规则（与项目补录导出一致）：非法字符清洗 / 扩展名归一 / 重名后缀，
        不静默覆盖。产物落 ``<data_dir>/quick_tts/exports/``，不自动删除。

        Args:
            wav_path: 已合成的 WAV 绝对路径。
            name: 用户自定义导出名称（可为空，回退 ``quick_tts``）。
            fmt: ``wav`` / ``mp3`` / ``m4b``。
            bitrate: mp3/m4b 比特率。
            title / artist: 标签元数据（best-effort）。

        Returns:
            最终文件绝对路径。
        """
        from lib import audio_pipeline
        from services.export_naming import build_export_path, unique_path

        source = str(wav_path or "")
        if not source or not os.path.isfile(source):
            raise RuntimeError("导出失败：没有可导出的临时配音音频（请先生成）")
        out_dir = QuickTTSService.exports_root()
        target = unique_path(build_export_path(out_dir, name, fmt, fallback="quick_tts"))
        return audio_pipeline.export_supplement(
            paths=[source],
            out_path=target,
            format=fmt,
            bitrate=bitrate,
            title=title or "Quick TTS",
            artist=artist,
        )

    @staticmethod
    def cleanup_cache(max_age_days: int = _DEFAULT_CACHE_MAX_AGE_DAYS) -> int:
        """清理过期 Quick TTS cache 目录（不触碰 exports）。

        Args:
            max_age_days: 过期天数阈值（默认 7 天）。

        Returns:
            删除的 cache 目录数量。
        """
        root = QuickTTSService.cache_root()
        if not os.path.isdir(root):
            return 0
        cutoff = time.time() - max_age_days * 86400
        cleaned = 0
        for entry in os.listdir(root):
            full = os.path.join(root, entry)
            if not os.path.isdir(full):
                continue
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if mtime < cutoff:
                shutil.rmtree(full, ignore_errors=True)
                cleaned += 1
        return cleaned


__all__ = ["QUICK_TTS_CONTEXT", "QuickTTSBusyError", "QuickTTSService"]
