"""TaskRepository 单元测试。

测试内容：
- save_task / load_task / list_tasks / delete_task 全链路
- 按 project / task_type 过滤
- cleanup_old_tasks 过期清理
- JSON 损坏容错
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from repositories.task_repo import TaskRecord, TaskRepository


class TestTaskRecord:
    """TaskRecord 数据类测试。"""

    def test_to_dict_roundtrip(self):
        """to_dict → from_dict 往返不变。"""
        record = TaskRecord(
            task_id="abc123",
            task_type="synthesis",
            project="test_book",
            status="running",
            artifact_dir="/tmp/artifacts",
            error_summary="",
            created_at="2024-06-01T12:00:00",
        )
        d = record.to_dict()
        assert d["task_id"] == "abc123"
        assert d["task_type"] == "synthesis"
        assert d["project"] == "test_book"
        assert d["status"] == "running"

        restored = TaskRecord.from_dict(d)
        assert restored.task_id == record.task_id
        assert restored.status == record.status

    def test_from_dict_partial(self):
        """部分字段 from_dict 使用空值。"""
        d = {"task_id": "xyz", "task_type": "supplement", "project": "p", "status": "done"}
        record = TaskRecord.from_dict(d)
        assert record.task_id == "xyz"
        assert record.artifact_dir == ""  # 默认空
        assert record.created_at == ""  # 默认空


class TestTaskRepository:
    """TaskRepository 持久化测试。"""

    def _make_task_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def test_save_and_load(self, tmp_path, monkeypatch):
        """save → load 往返一致。"""
        # Monkeypatch get_task_dir 以使用临时目录
        def _mock_task_dir():
            d = str(tmp_path / "task_records")
            os.makedirs(d, exist_ok=True)
            return d
        monkeypatch.setattr(TaskRepository, "get_task_dir", _mock_task_dir)

        tid = self._make_task_id()
        record = TaskRecord(
            task_id=tid,
            task_type="synthesis",
            project="my_book",
            status="running",
            artifact_dir=str(tmp_path / "out"),
            created_at="2024-06-01T00:00:00",
        )
        TaskRepository.save_task(record)

        loaded = TaskRepository.load_task(tid)
        assert loaded is not None
        assert loaded.task_id == tid
        assert loaded.status == "running"
        assert loaded.project == "my_book"

    def test_load_not_found(self, tmp_path, monkeypatch):
        """不存在的 task_id 返回 None。"""
        def _mock_task_dir():
            d = str(tmp_path / "task_records_empty")
            os.makedirs(d, exist_ok=True)
            return d
        monkeypatch.setattr(TaskRepository, "get_task_dir", _mock_task_dir)

        result = TaskRepository.load_task("no_such_task")
        assert result is None

    def test_list_tasks(self, tmp_path, monkeypatch):
        """list_tasks 返回所有记录。"""
        def _mock_task_dir():
            d = str(tmp_path / "task_records_list")
            os.makedirs(d, exist_ok=True)
            return d
        monkeypatch.setattr(TaskRepository, "get_task_dir", _mock_task_dir)

        t1 = self._make_task_id()
        t2 = self._make_task_id()
        TaskRepository.save_task(TaskRecord(t1, "synthesis", "book_a", "done"))
        TaskRepository.save_task(TaskRecord(t2, "supplement", "book_a", "running"))

        all_tasks = TaskRepository.list_tasks()
        assert len(all_tasks) == 2

        syn_tasks = TaskRepository.list_tasks(task_type="synthesis")
        assert len(syn_tasks) == 1
        assert syn_tasks[0].task_id == t1

        book_tasks = TaskRepository.list_tasks(project="book_a")
        assert len(book_tasks) == 2

    def test_list_tasks_filter(self, tmp_path, monkeypatch):
        """list_tasks 按 project + task_type 同时过滤。"""
        def _mock_task_dir():
            d = str(tmp_path / "task_records_filter")
            os.makedirs(d, exist_ok=True)
            return d
        monkeypatch.setattr(TaskRepository, "get_task_dir", _mock_task_dir)

        t1 = self._make_task_id()
        t2 = self._make_task_id()
        TaskRepository.save_task(TaskRecord(t1, "synthesis", "book_a", "done"))
        TaskRepository.save_task(TaskRecord(t2, "supplement", "book_b", "running"))

        filtered = TaskRepository.list_tasks(project="book_a", task_type="synthesis")
        assert len(filtered) == 1

        filtered2 = TaskRepository.list_tasks(project="book_b", task_type="synthesis")
        assert len(filtered2) == 0

    def test_delete_task(self, tmp_path, monkeypatch):
        """delete_task 删除文件。"""
        def _mock_task_dir():
            d = str(tmp_path / "task_records_delete")
            os.makedirs(d, exist_ok=True)
            return d
        monkeypatch.setattr(TaskRepository, "get_task_dir", _mock_task_dir)

        tid = self._make_task_id()
        TaskRepository.save_task(TaskRecord(tid, "synthesis", "book", "running"))
        assert TaskRepository.load_task(tid) is not None

        TaskRepository.delete_task(tid)
        assert TaskRepository.load_task(tid) is None

    def test_cleanup_old_tasks(self, tmp_path, monkeypatch):
        """cleanup_old_tasks 删除过期记录。"""
        def _mock_task_dir():
            d = str(tmp_path / "task_records_cleanup")
            os.makedirs(d, exist_ok=True)
            return d
        monkeypatch.setattr(TaskRepository, "get_task_dir", _mock_task_dir)

        # 新任务（不过期）
        new_id = self._make_task_id()
        TaskRepository.save_task(TaskRecord(
            new_id, "synthesis", "book", "done",
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        ))

        # 写一个非常旧的文件模拟过期任务
        old_id = "old_task_001"
        old_path = os.path.join(_mock_task_dir(), f"{old_id}.json")
        with open(old_path, "w", encoding="utf-8") as f:
            json.dump({
                "task_id": old_id,
                "task_type": "supplement",
                "project": "old_book",
                "status": "done",
                "created_at": "2020-01-01T00:00:00",
            }, f)
        # 改 mtime 确保旧
        old_mtime = time.time() - 20 * 86400  # 20 days ago
        os.utime(old_path, (old_mtime, old_mtime))

        cleaned = TaskRepository.cleanup_old_tasks(max_age_days=7)
        assert cleaned >= 1

        # 新任务仍在
        assert TaskRepository.load_task(new_id) is not None
        # 旧任务已删除
        assert TaskRepository.load_task(old_id) is None

    def test_corrupted_json(self, tmp_path, monkeypatch):
        """损坏的 JSON 文件被跳过。"""
        def _mock_task_dir():
            d = str(tmp_path / "task_records_corrupt")
            os.makedirs(d, exist_ok=True)
            return d
        monkeypatch.setattr(TaskRepository, "get_task_dir", _mock_task_dir)

        tid = "corrupted_task"
        path = os.path.join(_mock_task_dir(), f"{tid}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("这不是 JSON{")

        loaded = TaskRepository.load_task(tid)
        assert loaded is None  # 损坏返回 None

        # list_tasks 也应跳过损坏文件
        tasks = TaskRepository.list_tasks()
        assert len(tasks) == 0
