"""Round IA-1 contracts for the Workbench information architecture."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from lib.types import ProjectSummary
from services.session import SessionState
from ui import project_catalog_handlers as handlers
from ui.navigation import NAV_ITEMS


ROOT = Path(__file__).resolve().parents[1]


def _summary(
    name: str,
    *,
    title: str | None = None,
    chapters: int = 8,
    modified_at: str | None = "2026-08-23T10:20:30",
) -> ProjectSummary:
    return ProjectSummary(
        project_name=name,
        title=title or name,
        author="作者",
        chapters=chapters,
        segments=10,
        completed=4,
        modified_at=modified_at,
    )


def _fake_event(row_value):
    return SimpleNamespace(
        selected=True,
        row_value=row_value,
        value=row_value[0],
        index=(0, 0),
    )


def test_visible_navigation_is_workbench_first_and_project_page_free():
    assert [page_id for page_id, _label, _elem_id in NAV_ITEMS] == [
        "overview",
        "voices",
        "synth",
        "export",
    ]
    labels = [label for _page_id, label, _elem_id in NAV_ITEMS]
    assert "项目管理" not in labels
    assert "新建项目" not in labels


def test_bookshelf_structure_column_uses_internal_script_chapters():
    book = _summary("book", title="完整作品", chapters=8)
    other = _summary("other", title="另一本书", chapters=3, modified_at=None)

    rendered = handlers.render_bookshelf_rows("", [book, other])
    assert rendered["headers"] == ["项目", "结构", "段进度", "状态", "最近修改"]
    rows = {row[0]: row for row in rendered["data"]}
    assert rows["book"][1] == "8 个章节"
    assert rows["book"][4] == "2026-08-23 10:20"
    assert rows["other"][1] == "3 个章节"
    assert book.chapters == 8


def test_search_never_changes_opened_project(monkeypatch):
    book = _summary("book", title="完整作品")
    chapter = _summary("chapter", title="第三章")
    monkeypatch.setattr(
        handlers.ProjectCatalogService,
        "scan",
        classmethod(lambda cls: [book, chapter]),
    )

    ss = SessionState(project="book", script={"sentinel": "opened-book"})
    ss.set_selected("chapter")
    rendered, _hint, selected_state = handlers.apply_project_search("第三章", ss)
    names = {row[0] for row in rendered["data"]}
    assert names == {"chapter"}
    assert selected_state == "chapter"
    assert ss.project == "book"

    _rendered, _hint, selected_state = handlers.apply_project_search("no match", ss)
    assert selected_state == ""
    assert ss.selected_project is None
    assert ss.project == "book"
    assert ss.script == {"sentinel": "opened-book"}


def test_row_select_updates_only_selected_and_inspector_names_both_contexts(monkeypatch):
    book = _summary("book", title="完整作品")
    monkeypatch.setattr(
        handlers.ProjectCatalogService,
        "scan",
        classmethod(lambda cls: [book]),
    )
    ss = SessionState(project="book", script={"sentinel": "opened-book"})

    selected, info = handlers.select_bookshelf_row(
        {"data": [["book", "8 个章节", "4/10", "🟡部分", "—"]]},
        ss,
        _fake_event(["book", "8 个章节", "4/10", "🟡部分", "—"]),
    )

    assert selected == "book"
    assert ss.selected_project == "book"
    assert ss.project == "book"
    assert ss.script == {"sentinel": "opened-book"}
    assert "当前选择：" in info
    assert "当前工作项目" in info


def test_workbench_open_is_not_wired_to_project_page_navigation():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(app_source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "wire_project_catalog"
    ]
    assert len(calls) == 1
    assert "goto_project" not in ast.unparse(calls[0])
    assert 'with gr.Group(visible=True, elem_id="grp-overview")' in (
        ROOT / "ui/pages/overview_page.py"
    ).read_text(encoding="utf-8")
    assert '"workbench_new_project": workbench_new_project' in (
        ROOT / "ui/pages/overview_page.py"
    ).read_text(encoding="utf-8")
