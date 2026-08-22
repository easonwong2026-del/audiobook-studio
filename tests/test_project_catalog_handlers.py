"""项目书架 handler 纯函数测试（无需 gradio UI 运行时）。

覆盖（T02）：
- 搜索渲染：apply_project_search 产出书架着色契约 + 重置选中信息；
- select 只改 selected 不动 project（核心隔离不变式）；
- 动作 handler 收显式 project_name（不读 ss.project）；
- archive 两步确认契约（第一次只提示，第二次才归档）；
- refresh_project_catalog 固定 5 元组契约；
- 打开项目回调注入（open_selected_project 委托注入的 open_project）。
"""
from __future__ import annotations

import json
import os

import gradio as gr
import pytest

from repositories.project_repo import ProjectRepository
from services.session import SessionState
from ui import project_catalog_handlers as handlers


def _script_file(tmp_path, title="书架书", author="作者"):
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
def handler_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project("alpha", str(_script_file(tmp_path, "阿尔法")))
    ProjectRepository.create_project("beta", str(_script_file(tmp_path, "贝塔")))
    return data_root


class _FakeEvent:
    """模拟 gr.SelectData，支持新旧 payload 形态。"""

    def __init__(
        self,
        row: int,
        column: int = 0,
        *,
        row_value=None,
        value=None,
        selected: bool = True,
    ) -> None:
        self.index = (row, column)
        self.row_value = row_value
        self.value = value
        self.selected = selected


def _bookshelf_value(rows):
    """构造与 gr.Dataframe 同形的 {'data': [...]} 值。"""
    return {"data": rows}


def test_apply_project_search_renders_filtered_rows(handler_workspace):
    bookshelf, hint, selected_reset = handlers.apply_project_search("")
    assert bookshelf["headers"] == ["项目", "章", "段进度", "状态"]
    names = {row[0] for row in bookshelf["data"]}
    assert names == {"alpha", "beta"}
    # 搜索后清除选中项目 State，避免管理动作作用于隐藏旧选中
    assert selected_reset == ""

    filtered, _, _ = handlers.apply_project_search("ALPHA")
    assert [row[0] for row in filtered["data"]] == ["alpha"]
    assert "选择" in hint


def test_select_bookshelf_row_only_sets_selected(handler_workspace):
    ss = SessionState(project="opened_proj", script={"meta": {}}, bindings={})
    rows = _bookshelf_value([["alpha", 1, "0/1", "⚪未开始"]])
    name, info = handlers.select_bookshelf_row(
        rows, ss, _FakeEvent(0)
    )
    assert name == "alpha"
    assert ss.selected_project == "alpha"
    # 核心不变式：不打开项目、不加载剧本
    assert ss.project == "opened_proj"
    assert ss.script == {"meta": {}}
    assert "阿尔法" in info
    # p_sel is owned by the following catalog-aware reconciliation callback.


@pytest.mark.parametrize("column", range(4))
def test_select_bookshelf_row_uses_select_data_row_value_for_any_column(
    handler_workspace, column
):
    ss = SessionState(project="opened_proj")
    rows = _bookshelf_value([["stale-cell-value", 99, "99/99", "stale"]])
    event = _FakeEvent(
        0,
        column,
        row_value=["alpha", "1", "0/1", "⚪未开始"],
        value="0/1",
    )

    name, _info = handlers.select_bookshelf_row(rows, ss, event)

    assert name == "alpha"
    assert ss.selected_project == "alpha"
    assert ss.project == "opened_proj"


def test_select_bookshelf_row_accepts_real_gradio_select_data(handler_workspace):
    ss = SessionState(project="beta")
    event = gr.SelectData(
        None,
        {
            "index": [0, 2],
            "value": "0/1",
            "selected": True,
            "row_value": ["alpha", "1", "0/1", "⚪未开始"],
        },
    )

    result = handlers.select_bookshelf_row([], ss, event)

    assert len(result) == 2
    assert result[0] == "alpha"
    assert ss.selected_project == "alpha"
    assert ss.project == "beta"


def test_select_bookshelf_row_maps_display_name_and_preserves_on_deselect(
    handler_workspace,
):
    ss = SessionState(project="beta")
    ss.set_selected("alpha")
    event = _FakeEvent(
        0,
        3,
        row_value=["↳ 第一章 · beta", "1", "0/1", "章节 · ⚪未开始"],
        selected=False,
    )

    name, info = handlers.select_bookshelf_row([], ss, event)

    assert name == "alpha"
    assert "alpha" in info
    assert ss.selected_project == "alpha"
    assert ss.project == "beta"


def test_select_bookshelf_row_ignores_bad_event(handler_workspace):
    ss = SessionState()
    name, info = handlers.select_bookshelf_row(
        _bookshelf_value([["alpha", 1, "0/1", "⚪未开始"]]), ss, None
    )
    assert name == ""
    assert ss.selected_project is None
    assert ss.project is None


def test_action_handlers_take_project_name_not_ss(handler_workspace):
    """动作 handler 收显式 project_name，不依赖 ss.project。"""
    # open_directory 收 project_name
    assert "project_name" in handlers.open_selected_directory.__code__.co_varnames
    # create_backup 收 project_name
    assert "project_name" in handlers.create_selected_backup.__code__.co_varnames
    # cleanup 扫描/执行收 project_name
    assert "project_name" in handlers.scan_selected_cleanup.__code__.co_varnames
    assert "project_name" in handlers.execute_selected_cleanup.__code__.co_varnames
    # integrity 收 project_name
    assert "project_name" in handlers.check_selected_integrity.__code__.co_varnames
    assert "project_name" in handlers.repair_selected_integrity.__code__.co_varnames
    # archive 收 project_name + confirmed_project（确认态绑定项目名，不依赖打开状态）
    assert "project_name" in handlers.archive_selected.__code__.co_varnames
    assert "confirmed_project" in handlers.archive_selected.__code__.co_varnames


def test_archive_two_step_confirmation(handler_workspace, tmp_path):
    """第一次只提示、不归档；确认态绑定项目名后第二次才归档。"""
    project_dir = os.path.join(handler_workspace, "projects", "alpha")
    assert os.path.isdir(project_dir)

    # 第一次点击：confirmed_project 为空（未确认）→ 仅提示，确认态记录 alpha
    msg1, state1, sel1, info1 = handlers.archive_selected("alpha", "", None)
    assert "确认将「alpha」移入回收站" in msg1
    assert state1 == "alpha"
    # 第一次点击不清 selection（noop update）
    assert not sel1.get("value")
    # 第一次点击绝不归档
    assert os.path.isdir(project_dir)

    # 第二次点击：confirmed_project == alpha → 才归档，确认态复位
    msg2, state2, sel2, info2 = handlers.archive_selected("alpha", "alpha", None)
    assert "已移入回收站" in msg2
    assert state2 == ""
    assert not os.path.isdir(project_dir)


def test_archive_confirm_state_bound_to_project_name(handler_workspace):
    """QA 缺陷回归：确认态绑定项目名，改选后不会绕过两步确认。"""
    alpha_dir = os.path.join(handler_workspace, "projects", "alpha")
    beta_dir = os.path.join(handler_workspace, "projects", "beta")
    # 对 A 第一次点击 → 确认态记录 alpha
    _msg1, state1, _sel1, _info1 = handlers.archive_selected("alpha", "", None)
    assert state1 == "alpha"
    # 改选 B（确认态仍为 alpha，未复位）→ 对 B 点击 → 必须要求重新确认
    msg2, state2, _sel2, _info2 = handlers.archive_selected("beta", state1, None)
    assert "确认将「beta」移入回收站" in msg2
    assert state2 == "beta"
    # 关键：beta 未被归档（两步确认未被绕过）
    assert os.path.isdir(alpha_dir)
    assert os.path.isdir(beta_dir)
    # 对 B 确认后再点 → 才归档 B
    msg3, state3, _sel3, _info3 = handlers.archive_selected("beta", "beta", None)
    assert "已移入回收站" in msg3
    assert state3 == ""
    assert not os.path.isdir(beta_dir)
    # alpha 不受影响
    assert os.path.isdir(alpha_dir)


def test_archive_active_production_error_message(handler_workspace, monkeypatch):
    from services.project import ProjectMutationBlockedError

    def _blocked(*_args, **_kwargs):
        raise ProjectMutationBlockedError(
            "archive_project", "task-1", "running", "alpha"
        )

    monkeypatch.setattr(
        "services.project_storage.ensure_project_mutation_allowed", _blocked
    )
    msg, state, sel, info = handlers.archive_selected("alpha", "alpha", None)
    assert "项目正在生产，请先停止任务后再移入回收站" in msg
    assert state == ""
    # guard 阻止时不清 selection（noop update）
    assert not sel.get("value")
    assert not info.get("value")


def test_archive_opened_project_resets_session(handler_workspace):
    """被归档项目 == ss.project 时安全 reset session（selected 同步清空）。"""
    ss = SessionState(project="alpha", script={"meta": {}}, bindings={"旁白": "x"})
    ss.set_selected("alpha")
    ss.set_snapshot(object())  # 占位快照
    ss.synthesis = object()  # 占位合成态
    msg, _state, sel, info = handlers.archive_selected("alpha", "alpha", ss)
    assert "已移入回收站" in msg
    assert ss.project is None
    assert ss.script is None
    assert ss.bindings == {}
    assert ss.project_snapshot is None
    assert ss.synthesis is None
    # archive 成功后 selected 全部清空
    assert ss.selected_project is None
    assert sel == ""
    assert "选择" in info.get("value", "")


def test_refresh_project_catalog_contract(handler_workspace):
    """refresh_project_catalog 返回固定 5 元组契约。"""
    result = handlers.refresh_project_catalog("")
    assert isinstance(result, tuple)
    assert len(result) == 5
    bookshelf, p_sel_update, trash_rows, trash_choices, trash_status = result
    assert bookshelf["headers"] == ["项目", "章", "段进度", "状态"]
    assert {row[0] for row in bookshelf["data"]} == {"alpha", "beta"}
    assert p_sel_update.get("choices") == ["alpha", "beta"]
    assert isinstance(trash_rows, list)
    assert trash_choices.get("choices") == []
    assert "回收站" in trash_status


def test_open_selected_project_delegates_to_injected_callback(handler_workspace):
    calls: list[tuple] = []

    def fake_open(name, ss):
        calls.append((name, ss))
        return (f"opened:{name}",) * 7

    handlers.bind_open_project(fake_open)
    try:
        ss = SessionState()
        result = handlers.open_selected_project("alpha", ss)
        assert calls == [("alpha", ss)]
        assert result[0] == "opened:alpha"
        assert len(result) == 7
        # 空选中 → 不打开
        empty = handlers.open_selected_project("", ss)
        assert calls == [("alpha", ss)]
        assert "等待打开项目" in empty[0]
    finally:
        handlers.bind_open_project(None)


def test_restore_backup_global_and_trash_helpers(handler_workspace):
    assert "请选择项目备份 ZIP" in handlers.restore_backup_global(None)
    assert "请先选择回收站项目" in handlers.restore_archived_global(None)
    assert "勾选二次确认" in handlers.permanently_delete_archived_global("x", False)
    rows, choices, status = handlers.refresh_archived_projects_global()
    assert rows == []
    assert "回收站为空" in status
