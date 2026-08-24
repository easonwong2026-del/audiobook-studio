"""QA 补充测试（PR C 项目书架/项目管理统一）— 独立验证视角。

覆盖工程师 5 个测试文件之外的关键风险点：
1. **archive 两步确认的「确认态未绑定项目」缺陷演示**（期望 FAIL，见
   ``test_qa_archive_two_step_bypass_when_selection_changes``）；
2. **open_directory 非 Windows（Ubuntu CI）安全性**：monkeypatch 平台守卫后
   不崩溃、返回 ``(bool, str)`` 契约；
3. **open_directory 复用 procutil.open_in_folder**（no-window 复用断言）；
4. **搜索过滤在 archive/restore 链尾保持**（用户规格 F：搜「妲己」→ 归档 →
   0 → 恢复 → 1，handler 层端到端）；
5. **从备份恢复全局 handler 消息契约**（restore_backup_global）。

注：本文件只新增/断言，不修改源码。
"""
from __future__ import annotations

import json
import os

import pytest

from repositories.project_repo import ProjectRepository
from services import ProjectBackupService, ProjectStorageService
from services.project_catalog import ProjectCatalogService
from services.session import SessionState
from ui import project_catalog_handlers as handlers


def _script_file(tmp_path, title="QA书", author="作者"):
    path = tmp_path / "book.json"
    path.write_text(json.dumps({
        "meta": {"title": title, "author": author},
        "voices": {"旁白": {"description": "x"}},
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [{"id": "1-001", "role": "旁白", "text": "A"}],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def qa_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project("alpha", str(_script_file(tmp_path, "阿尔法")))
    ProjectRepository.create_project("beta", str(_script_file(tmp_path, "贝塔")))
    return data_root


# ── 1. archive 两步确认缺陷演示（预期 FAIL，报告给工程师） ──


def test_qa_archive_two_step_bypass_when_selection_changes(qa_workspace):
    """修复验证：确认态绑定项目名，改选后第二次点击不会绕过两步确认。

    用户流程（修复后）：
      1) 选中 A → 第一次点「移入回收站」→ 确认态记录为 **alpha**（而非 bool True）；
      2) 改选 B（确认态仍绑定 alpha，与 B 无关，天然免疫改选）；
      3) 第二次点「移入回收站」→ confirmed_project=alpha != beta → 要求重新确认，
         B 不被归档；只有对 B 再确认一次才归档。
    规格 D1 要求两步确认；本用例验证改选项目后无法绕过。
    """
    alpha_dir = os.path.join(qa_workspace, "projects", "alpha")
    beta_dir = os.path.join(qa_workspace, "projects", "beta")
    assert os.path.isdir(alpha_dir) and os.path.isdir(beta_dir)

    # 第一次点击（对 A）：仅提示，确认态记录为项目名 alpha
    msg1, state1, _sel1, _info1 = handlers.archive_selected("alpha", "", None)
    assert "确认将「alpha」移入回收站" in msg1
    assert state1 == "alpha"
    assert os.path.isdir(alpha_dir)

    # 用户改选 B（确认态仍为 alpha，未复位——确认态绑定项目名，免疫改选）
    ss = SessionState()
    handlers.select_bookshelf_row({"data": [["beta", 1, "0/1", "⚪未开始"]]}, ss, _FakeEvent(0))
    assert ss.selected_project == "beta"

    # 第二次点击：confirmed_project=alpha != beta → 必须重新确认，B 不被归档
    msg2, state2, _sel2, _info2 = handlers.archive_selected("beta", state1, ss)
    assert "确认将「beta」移入回收站" in msg2
    assert state2 == "beta"
    assert os.path.isdir(beta_dir)  # 关键：B 未被绕过两步确认归档

    # 对 B 确认后再点击 → 才归档 B
    msg3, state3, _sel3, _info3 = handlers.archive_selected("beta", "beta", ss)
    assert "已移入回收站" in msg3
    assert state3 == ""
    assert not os.path.isdir(beta_dir)


class _FakeEvent:
    def __init__(self, row: int) -> None:
        self.index = (row, 0)


# ── 2. open_directory 非 Windows（Ubuntu CI）安全性 ──


def test_qa_open_directory_linux_no_crash(qa_workspace, monkeypatch):
    """monkeypatch 平台守卫为非 Windows：不崩溃、返回 (bool, str) 契约。

    模拟 Ubuntu CI：``procutil._is_windows()`` 返回 False，且 xdg-open
    缺失（Popen 抛 OSError）→ ``open_in_folder`` 返回 False → service
    返回 (False, str)，绝不抛异常、不产生 console。
    """
    import lib.procutil as procutil_mod
    import services.project_storage as storage_mod

    real_open_in_folder = procutil_mod.open_in_folder

    def _fake_open_in_folder(path):
        # 模拟非 Windows 且 xdg-open 不可用：FileNotFoundError 被内部捕获
        try:
            raise FileNotFoundError("xdg-open not found")
        except OSError:
            return False

    monkeypatch.setattr(storage_mod.procutil, "_is_windows", lambda: False)
    monkeypatch.setattr(storage_mod.procutil, "open_in_folder", _fake_open_in_folder)
    try:
        ok, message = ProjectStorageService.open_directory("alpha")
    finally:
        monkeypatch.setattr(storage_mod.procutil, "open_in_folder", real_open_in_folder)
    assert isinstance(ok, bool)
    assert isinstance(message, str)
    # 非 Windows 且无法打开 → 返回失败消息而非抛异常（CI 不红）
    assert ok is False


def test_qa_open_directory_reuses_procutil(qa_workspace, monkeypatch):
    """open_directory 必须复用 procutil.open_in_folder（no-window 唯一出口）。"""
    import services.project_storage as storage_mod

    calls: list[str] = []

    def _fake_open_in_folder(path):
        calls.append(str(path))
        return True

    monkeypatch.setattr(storage_mod.procutil, "open_in_folder", _fake_open_in_folder)
    ok, message = ProjectStorageService.open_directory("alpha")
    assert ok is True
    assert len(calls) == 1
    assert "alpha" in calls[0]


# ── 3. 搜索过滤在 archive/restore 链尾保持（规格 F，handler 层） ──


def test_qa_search_filter_preserved_across_archive_restore(qa_workspace, tmp_path):
    """搜「阿尔法」命中 1 → 归档后 0 → 恢复后 1（refresh 链尾保持 query）。"""
    # 搜索命中
    assert [s.project_name for s in ProjectCatalogService.search_projects("阿尔法")] == ["alpha"]

    # 归档后按同一 query 刷新 → 0
    handlers.archive_selected("alpha", "alpha", None)
    refreshed = handlers.refresh_bookshelf_management_view("阿尔法", SessionState())
    bookshelf, trash_rows = refreshed[0], refreshed[1]
    assert [row[0] for row in bookshelf["data"]] == []
    assert len(trash_rows) == 1

    # 恢复后按同一 query 刷新 → 1
    archived = ProjectStorageService.list_archived()
    handlers.restore_archived_global(archived[0]["archive_id"])
    refreshed2 = handlers.refresh_bookshelf_management_view("阿尔法", SessionState())
    bookshelf2, trash_rows2 = refreshed2[0], refreshed2[1]
    assert [row[0] for row in bookshelf2["data"]] == ["alpha"]
    assert trash_rows2 == []


# ── 4. 从备份恢复全局 handler 消息契约 ──


def test_qa_restore_backup_global_message(qa_workspace, tmp_path):
    """restore_backup_global：无文件→提示；成功→消息含恢复路径。"""
    assert "请选择项目备份 ZIP" in handlers.restore_backup_global(None)
    assert "请选择项目备份 ZIP" in handlers.restore_backup_global("")

    backup = ProjectBackupService.create_backup("alpha")
    ProjectStorageService.archive("alpha")
    assert not os.path.isdir(os.path.join(qa_workspace, "projects", "alpha"))
    msg = handlers.restore_backup_global(backup)
    assert "已恢复到" in msg
    # 恢复后项目回到书架
    assert os.path.isdir(os.path.join(qa_workspace, "projects", "alpha"))


# ── 5. 打开项目回调未注入时安全降级 ──


def test_qa_open_selected_project_safe_without_callback(qa_workspace):
    """未注入 open_project 回调时返回空态 6 元组，不崩溃。"""
    handlers.bind_open_project(None)
    result = handlers.open_selected_project("alpha", SessionState())
    assert len(result) == 6
    assert result[0].get("choices") == []
