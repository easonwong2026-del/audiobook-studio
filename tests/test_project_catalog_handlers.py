"""项目书架 handler 纯函数测试（无需 gradio UI 运行时）。

覆盖（T02）：
- 搜索渲染：apply_project_search 产出书架着色契约 + 重置选中信息；
- select 只改 selected 不动 project（核心隔离不变式）；
- SelectData 的真实 payload 与异常输入兼容；
- 打开项目回调注入（open_selected_project 委托注入的 open_project）。
"""
from __future__ import annotations

import json

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
    assert bookshelf["headers"] == ["项目", "结构", "段进度", "状态", "最近修改"]
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
        row_value=["alpha", "1 个章节", "0/1", "⚪未开始"],
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


def test_open_selected_project_delegates_to_injected_callback(handler_workspace):
    calls: list[tuple] = []

    def fake_open(name, ss):
        calls.append((name, ss))
        return (f"opened:{name}",) * 6

    handlers.bind_open_project(fake_open)
    try:
        ss = SessionState()
        result = handlers.open_selected_project("alpha", ss)
        assert calls == [("alpha", ss)]
        assert result[0] == "opened:alpha"
        assert len(result) == 6
        # 空选中 → 不打开
        empty = handlers.open_selected_project("", ss)
        assert calls == [("alpha", ss)]
        assert len(empty) == 6
        assert empty[0].get("choices") == []
    finally:
        handlers.bind_open_project(None)


def test_restore_backup_global_and_trash_helpers(handler_workspace):
    assert "请选择项目备份 ZIP" in handlers.restore_backup_global(None)
    assert "请先选择回收站项目" in handlers.restore_archived_global(None)
    assert "勾选二次确认" in handlers.permanently_delete_archived_global("x", False)
    rows, choices, status = handlers.refresh_archived_projects_global()
    assert rows == []
    assert "回收站为空" in status
