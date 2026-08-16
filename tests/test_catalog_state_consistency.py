"""PR #45 收口：selected/opened/archive/search 状态一致性回归（A-L 场景）。

覆盖用户规格 13 的 targeted 场景（handler 层 + SessionState 断言，无 gradio）：
A. 搜索过滤移除 selected → UI 清空 + ss.selected_project=None；
B. 第一次 archive 确认 → selection 保留；
C. archive 被 guard 阻止 → selection 保留；
D. 成功 archive selected 项目 → 全部选中态/UI/session 清空；
E. archive opened 项目 → opened session 完全 reset（project/selected/
   snapshot/synthesis/script/bindings 全清）；
F. archive selected A 而 opened B → B 完整保留（隔离）；
G. archive 确认态绑定项目名 → A 的确认不能归档 B；
H. 搜索 → 导航离开 → 返回 → query/filter 保留（ss.catalog_query 单一来源）；
I. archive → p_sel/catalog 同步（p_sel 不再含 A）；
J. restore → p_sel/catalog 同步（p_sel 恢复 A）；
K. live active production archive 被阻止 → selected/opened 不变；
L. restore duplicate 项目仍被拒绝。
"""
from __future__ import annotations

import json
import os

import pytest

from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord, TaskRepository
from services import ProjectStorageService
from services.project_catalog import ProjectCatalogService
from services.session import SessionState
from ui import project_catalog_handlers as handlers


def _script_file(tmp_path, title="一致性书", author="作者"):
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
def state_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project("alpha", str(_script_file(tmp_path, "阿尔法")))
    ProjectRepository.create_project("beta", str(_script_file(tmp_path, "贝塔")))
    return data_root


class _FakeEvent:
    def __init__(self, row: int) -> None:
        self.index = (row, 0)


def _bookshelf_value(rows):
    return {"data": rows}


def _select(ss, name: str) -> None:
    """模拟书架点选某行（只设 selected，不打开）。"""
    handlers.select_bookshelf_row(
        _bookshelf_value([[name, 1, "0/1", "⚪未开始"]]), ss, _FakeEvent(0)
    )


# ── A. 搜索过滤移除 selected → 全清 ──


def test_a_search_removes_selected_clears_everywhere(state_workspace):
    ss = SessionState()
    _select(ss, "alpha")
    assert ss.selected_project == "alpha"

    bookshelf, info, sel_update = handlers.apply_project_search("贝塔", ss)
    # 过滤结果只剩 beta
    assert [row[0] for row in bookshelf["data"]] == ["beta"]
    # UI selected 组件清空 + 信息回到提示
    assert sel_update.get("value") == ""
    assert "选择" in info
    # SessionState.selected_project 同步清空（无幽灵）
    assert ss.selected_project is None
    # 搜索 query 单一状态来源已落盘
    assert ss.catalog_query == "贝塔"


def test_a_search_keeps_selected_when_still_visible(state_workspace):
    ss = SessionState()
    _select(ss, "alpha")
    bookshelf, info, sel_update = handlers.apply_project_search("阿尔法", ss)
    # alpha 仍在过滤结果 → selected 保留
    assert [row[0] for row in bookshelf["data"]] == ["alpha"]
    assert ss.selected_project == "alpha"
    assert sel_update.get("value") == "alpha"
    assert "阿尔法" in info


# ── B. 第一次 archive 确认 → selection 保留 ──


def test_b_first_archive_click_keeps_selection(state_workspace):
    ss = SessionState()
    _select(ss, "alpha")
    msg, confirm, sel_update, info_update = handlers.archive_selected("alpha", "", ss)
    assert "确认将「alpha」移入回收站" in msg
    assert confirm.get("value") == "alpha"  # 确认态记录项目名
    # selection 不清（noop update）
    assert sel_update.get("value") is None
    assert info_update.get("value") is None
    assert ss.selected_project == "alpha"
    # 项目未被归档
    assert os.path.isdir(os.path.join(state_workspace, "projects", "alpha"))


# ── C. blocked archive → selection 保留 ──


def test_c_blocked_archive_keeps_selection(state_workspace):
    from services.project import ProjectMutationBlockedError

    def _blocked(*_a, **_k):
        raise ProjectMutationBlockedError("archive_project", "task-1", "running", "alpha")

    import services.project_storage as storage_mod
    real = storage_mod.ensure_project_mutation_allowed
    storage_mod.ensure_project_mutation_allowed = _blocked
    try:
        ss = SessionState()
        _select(ss, "alpha")
        msg, confirm, sel_update, info_update = handlers.archive_selected("alpha", "alpha", ss)
        assert "项目正在生产" in msg
        # guard 阻止：selection 保留
        assert ss.selected_project == "alpha"
        assert sel_update.get("value") is None
        assert info_update.get("value") is None
    finally:
        storage_mod.ensure_project_mutation_allowed = real
    assert os.path.isdir(os.path.join(state_workspace, "projects", "alpha"))


# ── D. 成功 archive selected 项目 → 全清 ──


def test_d_successful_archive_selected_clears_everywhere(state_workspace):
    ss = SessionState()
    _select(ss, "alpha")
    msg, confirm, sel_update, info_update = handlers.archive_selected("alpha", "alpha", ss)
    assert "已移入回收站" in msg
    # A 从 catalog 消失
    assert ProjectCatalogService.search_projects("阿尔法") == []
    # SessionState.selected_project 清空
    assert ss.selected_project is None
    # UI selected 组件 + 信息清空
    assert sel_update.get("value") == ""
    assert "选择" in info_update.get("value", "")
    # 确认态复位
    assert confirm.get("value") == ""
    # opened 不受影响（本场景未打开任何项目）
    assert ss.project is None


# ── E. archive opened 项目 → opened session 完全 reset ──


def test_e_archive_opened_project_resets_full_session(state_workspace):
    ss = SessionState(project="alpha", script={"meta": {}}, bindings={"旁白": "x"})
    ss.set_selected("alpha")
    ss.set_snapshot(object())
    ss.synthesis = object()
    handlers.archive_selected("alpha", "alpha", ss)
    # opened/selected/snapshot/synthesis 全部清空
    assert ss.project is None
    assert ss.script is None
    assert ss.bindings == {}
    assert ss.selected_project is None
    assert ss.project_snapshot is None
    assert ss.synthesis is None
    # catalog 中已无 alpha
    assert [s.project_name for s in ProjectCatalogService.scan()] == ["beta"]


# ── F. archive selected A 而 opened B → B 完整保留 ──


def test_f_archive_selected_a_preserves_opened_b(state_workspace):
    ss = SessionState(project="beta", script={"meta": {"title": "贝塔"}}, bindings={"旁白": "b"})
    ss.set_selected("alpha")
    ss.set_snapshot(object())  # B 的快照占位
    ss.synthesis = object()  # B 的合成态占位
    handlers.archive_selected("alpha", "alpha", ss)
    # A 被归档、selected 清空
    assert ProjectCatalogService.search_projects("阿尔法") == []
    assert ss.selected_project is None
    # B 完整保留
    assert ss.project == "beta"
    assert ss.script == {"meta": {"title": "贝塔"}}
    assert ss.bindings == {"旁白": "b"}
    assert ss.project_snapshot is not None
    assert ss.synthesis is not None


# ── G. archive 确认态绑定项目名 ──


def test_g_confirmation_bound_to_project(state_workspace):
    alpha_dir = os.path.join(state_workspace, "projects", "alpha")
    beta_dir = os.path.join(state_workspace, "projects", "beta")
    # 对 A 第一次点击 → 确认态 = alpha
    _msg1, confirm1, _s1, _i1 = handlers.archive_selected("alpha", "", None)
    assert confirm1.get("value") == "alpha"
    # 改选 B（确认态仍为 alpha）→ 第二次点击不能归档 B
    msg2, confirm2, _s2, _i2 = handlers.archive_selected("beta", confirm1.get("value"), None)
    assert "确认将「beta」移入回收站" in msg2
    assert confirm2.get("value") == "beta"
    assert os.path.isdir(beta_dir)  # B 未被绕过两步确认归档
    assert os.path.isdir(alpha_dir)


# ── H. 搜索 → 导航 → 返回 → query/filter 保留 ──


def test_h_search_state_preserved_across_navigation(state_workspace):
    ss = SessionState()
    # 用户输入「乔欣」→ 书架只剩匹配项目
    handlers.apply_project_search("贝塔", ss)
    assert [row[0] for row in handlers.render_bookshelf_rows(ss.catalog_query)["data"]] == ["beta"]
    # 模拟导航离开再返回：刷新走 ss.catalog_query（单一来源），过滤保留
    assert ss.catalog_query == "贝塔"
    bookshelf = handlers.render_bookshelf_rows(ss.catalog_query)
    assert [row[0] for row in bookshelf["data"]] == ["beta"]
    # 搜索框与列表一致（列表不因导航变回全部）
    assert len(bookshelf["data"]) == 1


# ── I. archive → p_sel/catalog 同步 ──


def test_i_archive_syncs_p_sel(state_workspace):
    ss = SessionState()
    _select(ss, "alpha")  # p_sel 同步为 alpha（select 第三输出）
    _msg, _c, _s, _i = handlers.archive_selected("alpha", "alpha", ss)
    # 统一刷新：p_sel choices/value 不再含 A
    bookshelf, p_sel_update, _trash, _tc, _ts = handlers.refresh_project_catalog("", "alpha")
    assert "alpha" not in p_sel_update.get("choices", [])
    assert p_sel_update.get("value") is None
    assert [row[0] for row in bookshelf["data"]] == ["beta"]


# ── J. restore → p_sel/catalog 同步 ──


def test_j_restore_syncs_p_sel(state_workspace):
    handlers.archive_selected("alpha", "alpha", None)
    archived = ProjectStorageService.list_archived()
    assert len(archived) == 1
    handlers.restore_archived_global(archived[0]["archive_id"])
    # 统一刷新：p_sel 恢复 A（choices 含 A）
    bookshelf, p_sel_update, _trash, _tc, _ts = handlers.refresh_project_catalog("", "")
    assert "alpha" in p_sel_update.get("choices", [])
    assert {row[0] for row in bookshelf["data"]} == {"alpha", "beta"}


# ── K. live active production archive 被阻止 → selected/opened 不变 ──


@pytest.mark.parametrize("status", ["pending", "running", "pausing", "recovering", "cancelling"])
def test_k_live_active_archive_blocked(state_workspace, status):
    TaskRepository.save_task(TaskRecord(
        task_id=f"task_{status}",
        task_type="synthesis",
        project="alpha",
        status=status,
        created_at="2026-08-16T00:00:00Z",
        updated_at="2026-08-16T00:01:00Z",
    ))
    ss = SessionState(project="beta", script={"meta": {}}, bindings={})
    ss.set_selected("alpha")
    msg, _confirm, sel_update, info_update = handlers.archive_selected("alpha", "alpha", ss)
    assert "项目正在生产" in msg
    # selected/opened 全部不变
    assert ss.selected_project == "alpha"
    assert ss.project == "beta"
    assert sel_update.get("value") is None
    assert info_update.get("value") is None
    # 项目未被归档
    assert os.path.isdir(os.path.join(state_workspace, "projects", "alpha"))


# ── L. restore duplicate 项目仍被拒绝 ──


def test_l_restore_duplicate_rejected(state_workspace):
    handlers.archive_selected("alpha", "alpha", None)
    archived = ProjectStorageService.list_archived()
    archive_id = archived[0]["archive_id"]
    # 同名项目重新出现（模拟外部创建）
    ProjectRepository.create_project("alpha", str(_script_file(state_workspace)))
    msg = handlers.restore_archived_global(archive_id)
    assert "恢复失败" in msg
    # 回收站条目仍在
    assert len(ProjectStorageService.list_archived()) == 1
