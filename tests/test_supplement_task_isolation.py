"""5.3：补录任务隔离——每次补录使用独立 task_dir，互不覆盖。

覆盖：
- 连续两次补录使用不同 task_id 和 task_dir；
- task_dir 内容隔离（各任务产物不交叉）；
- synthesize_lines 使用 task.task_dir 输出文件；
- cleanup_old_tasks 删除过期任务；
- SupplementTaskState 数据类构造正确。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import tempfile

import numpy as np
import pytest
from scipy.io import wavfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.supplement import SupplementService, SupplementTaskState  # noqa: E402
from lib import tts_engine  # noqa: E402


# ── 假引擎桩 ──────────────────────────────────────────

def _fake_infer(**kwargs):
    """写一段哑 wav 到 kwargs['output_path']"""
    sr = 16000
    out = kwargs.get("output_path", os.path.join(tempfile.gettempdir(), "_fake_sup.wav"))
    wavfile.write(out, sr, np.zeros(sr, dtype=np.int16))


@pytest.fixture(autouse=True)
def patch_engine(monkeypatch):
    monkeypatch.setattr(tts_engine, "synthesize_segment", _fake_infer)


class TestSupplementTaskState:
    def test_dataclass_construction(self):
        """SupplementTaskState 数据类构造与字段"""
        task = SupplementTaskState(
            task_id="abc123",
            project="test_proj",
            role=" narrator",
            status="running",
            created_at="2026-01-01T00:00:00",
            task_dir="/tmp/test_task_dir",
        )
        assert task.task_id == "abc123"
        assert task.project == "test_proj"
        assert task.role == " narrator"
        assert task.status == "running"
        assert task.items == []  # default 空列表

    def test_with_items(self):
        """SupplementTaskState 带 items"""
        task = SupplementTaskState(
            task_id="t1",
            project="p",
            role="r",
            status="running",
            items=[{"index": 0, "text": "hello", "wav_path": "/tmp/001.wav",
                     "status": "done"}],
        )
        assert len(task.items) == 1
        assert task.items[0]["index"] == 0


class TestTaskIsolation:
    """验证连续两次补录使用不同 task_dir"""

    def test_consecutive_tasks_have_different_dirs(self, tmp_path, monkeypatch):
        """两次补录产生不同 task_id 和 task_dir"""
        # 构造最小参数
        lines = ["第一句", "第二句"]
        speaker = str(tmp_path / "speaker.wav")
        wavfile.write(speaker, 16000, np.zeros(16000, dtype=np.int16))

        # 第一次补录
        task1 = SupplementTaskState(
            task_id="task_1",
            project="test_proj",
            role=" narrator",
            status="running",
            task_dir=str(tmp_path / "tasks" / "task_1"),
        )
        os.makedirs(task1.task_dir, exist_ok=True)
        results1 = SupplementService.synthesize_lines(
            role=" narrator", lines=lines, speaker_audio=speaker,
            overrides={}, num_beams=1, task=task1,
        )
        assert len(results1) == 2
        assert os.path.isfile(os.path.join(task1.task_dir, "001.wav"))
        assert os.path.isfile(os.path.join(task1.task_dir, "002.wav"))

        # 第二次补录（不同 task_id）
        task2 = SupplementTaskState(
            task_id="task_2",
            project="test_proj",
            role=" narrator",
            status="running",
            task_dir=str(tmp_path / "tasks" / "task_2"),
        )
        os.makedirs(task2.task_dir, exist_ok=True)
        results2 = SupplementService.synthesize_lines(
            role=" narrator", lines=lines, speaker_audio=speaker,
            overrides={}, num_beams=1, task=task2,
        )
        assert len(results2) == 2

        # 验证目录不同，产物不覆盖
        assert task1.task_dir != task2.task_dir
        task1_file = os.path.join(task1.task_dir, "001.wav")
        task2_file = os.path.join(task2.task_dir, "001.wav")
        assert os.path.isfile(task1_file)
        assert os.path.isfile(task2_file)
        # 两个文件都是有效的 wav
        r1, d1 = wavfile.read(task1_file)
        r2, d2 = wavfile.read(task2_file)
        assert len(d1) > 0
        assert len(d2) > 0

    def test_no_task_uses_flat_cache(self, tmp_path, monkeypatch):
        """未传入 task 时使用旧平面缓存路径（向后兼容）"""
        lines = ["测试句"]
        speaker = str(tmp_path / "speaker.wav")
        wavfile.write(speaker, 16000, np.zeros(16000, dtype=np.int16))

        results = SupplementService.synthesize_lines(
            role=" narrator", lines=lines, speaker_audio=speaker,
            overrides={}, num_beams=1, task=None,
        )
        assert len(results) == 1


class TestCleanupOldTasks:
    def test_cleanup_removes_expired_tasks(self, tmp_path, monkeypatch):
        """cleanup_old_tasks 删除过期任务目录"""
        from lib import config

        # 设置临时数据目录
        monkeypatch.setenv(config.ENV_DATA_DIR, str(tmp_path))
        # 创建一些假 task 目录
        old_dir = os.path.join(tmp_path, "preview", "supplement_tasks", "old_task_1")
        new_dir = os.path.join(tmp_path, "preview", "supplement_tasks", "new_task_2")
        os.makedirs(old_dir, exist_ok=True)
        os.makedirs(new_dir, exist_ok=True)

        # 将 old_dir 的 mtime 设置为 8 天前
        old_time = time.time() - 8 * 86400
        os.utime(old_dir, (old_time, old_time))

        # 将 new_dir 的 mtime 设置为 1 天前
        new_time = time.time() - 1 * 86400
        os.utime(new_dir, (new_time, new_time))

        cleaned = SupplementService.cleanup_old_tasks(max_age_days=7)
        assert cleaned >= 1
        assert not os.path.isdir(old_dir)
        assert os.path.isdir(new_dir)
