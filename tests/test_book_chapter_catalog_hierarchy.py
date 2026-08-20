"""Phase B Book → Chapter Catalog hierarchy regression coverage."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import project_paths
from repositories.project_repo import ProjectRepository
from services import ProjectStorageService
from services.project_catalog import (
    RELATION_INVALID,
    RELATION_ORPHAN,
    RELATION_VALID,
    ProjectCatalogService,
)
from services.session import SessionState
from ui import project_catalog_handlers as handlers
from ui.settings_handlers import apply_data_dir


def _script_file(tmp_path: Path, title: str, author: str = "作者") -> Path:
    path = tmp_path / f"{title}.json"
    path.write_text(
        json.dumps(
            {
                "meta": {"title": title, "author": author},
                "voices": {"旁白": {"description": "x"}},
                "chapters": [
                    {
                        "id": 1,
                        "title": "第一章",
                        "segments": [
                            {"id": "1-001", "role": "旁白", "text": "A"}
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def hierarchy_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True

    def create(name: str, title: str | None = None) -> str:
        script = _script_file(tmp_path, title or name)
        return ProjectRepository.create_project(name, str(script))

    return data_root, create


def _meta_path(name: str) -> Path:
    return Path(
        project_paths.project_file(ProjectRepository.get_project_dir(name), "project_meta")
    )


def _set_relation(
    child_name: str,
    parent_id: str | None,
    *,
    title: str | None = None,
    order=None,
    kind: str = "chapter",
) -> None:
    meta = ProjectRepository._load_meta(ProjectRepository.get_project_dir(child_name))
    meta.project_id = meta.project_id or ProjectRepository.ensure_project_id(child_name)
    meta.project_kind = kind
    meta.parent_project_id = parent_id
    meta.chapter_title = title
    meta.chapter_order = order
    ProjectRepository._save_meta(ProjectRepository.get_project_dir(child_name), meta)


def _project_ids(*names: str) -> dict[str, str]:
    return {name: ProjectRepository.ensure_project_id(name) for name in names}


def test_new_projects_write_book_kind_and_stable_id(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book")

    meta = ProjectRepository._load_meta(ProjectRepository.get_project_dir("book"))
    assert meta.project_kind == "book"
    assert meta.parent_project_id is None
    assert meta.project_id


def test_legacy_metadata_is_read_as_book_without_scan_writeback(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("legacy-book", "Legacy Book")
    path = _meta_path("legacy-book")
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "project_id",
        "project_kind",
        "parent_project_id",
        "chapter_title",
        "chapter_order",
    ):
        raw.pop(key, None)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    summaries = ProjectCatalogService.scan()

    after = path.read_bytes()
    summary = next(item for item in summaries if item.project_name == "legacy-book")
    assert summary.project_kind == "book"
    assert summary.project_id is None
    assert summary.parent_project_id is None
    assert summary.relation_status == "standalone"
    assert after == before


def test_hierarchy_binds_flat_projects_and_sorts_chapters(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book-a", "整书 A")
    create("chapter-z", "章节 Z")
    create("chapter-a", "章节 A")
    create("chapter-missing", "章节 Missing")
    ids = _project_ids("book-a", "chapter-z", "chapter-a", "chapter-missing")

    _set_relation("chapter-z", ids["book-a"], title="章节 Z", order=2)
    _set_relation("chapter-a", ids["book-a"], title="章节 A", order=1)
    _set_relation("chapter-missing", ids["book-a"], title="章节 Missing")

    summaries = ProjectCatalogService.scan()
    assert [item.project_name for item in summaries] == [
        "book-a",
        "chapter-a",
        "chapter-z",
        "chapter-missing",
    ]
    chapter_a = next(item for item in summaries if item.project_name == "chapter-a")
    assert chapter_a.project_kind == "chapter"
    assert chapter_a.parent_project_name == "book-a"
    assert chapter_a.relation_status == RELATION_VALID
    assert Path(hierarchy_workspace[0] / "projects" / "chapter-a").parent.name == "projects"


def test_duplicate_malformed_and_missing_orders_are_safe(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book")
    create("chapter-b", "B")
    create("chapter-a", "A")
    create("chapter-invalid-order", "Invalid")
    ids = _project_ids("book", "chapter-b", "chapter-a", "chapter-invalid-order")
    _set_relation("chapter-b", ids["book"], title="B", order=1)
    _set_relation("chapter-a", ids["book"], title="A", order=1)
    _set_relation("chapter-invalid-order", ids["book"], title="Invalid", order="bad")

    summaries = ProjectCatalogService.scan()
    names = [item.project_name for item in summaries]
    assert names[:3] == ["book", "chapter-a", "chapter-b"]
    assert names[-1] == "chapter-invalid-order"


def test_orphan_parent_chapter_self_parent_and_cycle_are_visible(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book")
    create("orphan")
    create("parent-chapter")
    create("child-of-chapter")
    create("self-parent")
    create("cycle-a")
    create("cycle-b")
    ids = _project_ids(
        "book",
        "orphan",
        "parent-chapter",
        "child-of-chapter",
        "self-parent",
        "cycle-a",
        "cycle-b",
    )
    _set_relation("orphan", "missing-parent", title="孤立")
    _set_relation("parent-chapter", ids["book"], title="章节父级")
    _set_relation("child-of-chapter", ids["parent-chapter"], title="错误子级")
    _set_relation("self-parent", ids["self-parent"], title="自指")
    _set_relation("cycle-a", ids["cycle-b"], title="循环 A")
    _set_relation("cycle-b", ids["cycle-a"], title="循环 B")

    hierarchy = ProjectCatalogService.scan_hierarchy()
    by_name = {item.project_name: item for item in hierarchy.projects}
    assert by_name["orphan"].relation_status == RELATION_ORPHAN
    assert by_name["child-of-chapter"].relation_status == RELATION_INVALID
    assert by_name["self-parent"].relation_status == RELATION_INVALID
    assert by_name["cycle-a"].relation_status == RELATION_INVALID
    assert by_name["cycle-b"].relation_status == RELATION_INVALID
    assert {item.project_name for item in hierarchy.orphan_chapters} >= {
        "orphan",
        "child-of-chapter",
        "self-parent",
        "cycle-a",
        "cycle-b",
    }
    rendered = handlers.render_bookshelf_rows("")
    orphan_row = next(row for row in rendered["data"] if "orphan" in row[0])
    assert orphan_row[0].startswith("↳ ⚠")
    assert "孤立章节" in orphan_row[3]


def test_search_child_and_book_keep_parent_context(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book", "整书标题")
    create("chapter", "普通文件名")
    ids = _project_ids("book", "chapter")
    _set_relation("chapter", ids["book"], title="第三章特别篇", order=3)

    child_results = ProjectCatalogService.search_projects("第三章特别")
    assert [item.project_name for item in child_results] == ["book", "chapter"]
    book_results = ProjectCatalogService.search_projects("整书标题")
    assert [item.project_name for item in book_results] == ["book", "chapter"]


def test_explicit_bind_and_unbind_materialize_only_participants(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book")
    create("chapter")
    create("untouched")
    for name in ("book", "chapter", "untouched"):
        path = _meta_path(name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("project_id", None)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    ProjectCatalogService.bind_chapter("chapter", "book")
    chapter_meta = ProjectRepository._load_meta(ProjectRepository.get_project_dir("chapter"))
    book_meta = ProjectRepository._load_meta(ProjectRepository.get_project_dir("book"))
    untouched_meta = ProjectRepository._load_meta(
        ProjectRepository.get_project_dir("untouched")
    )
    assert chapter_meta.project_id
    assert book_meta.project_id
    assert chapter_meta.parent_project_id == book_meta.project_id
    assert untouched_meta.project_id is None

    ProjectCatalogService.clear_chapter_parent("chapter")
    cleared = ProjectRepository._load_meta(ProjectRepository.get_project_dir("chapter"))
    assert cleared.project_kind == "book"
    assert cleared.parent_project_id is None


def test_relationship_controls_refresh_immediately(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book")
    create("chapter")
    ss = SessionState()
    ss.set_selected("chapter")

    initial = handlers.refresh_bookshelf_management_view_with_hierarchy("", "", ss)
    assert len(initial) == 29
    assert initial[25].get("choices") == ["book"]
    assert initial[26].get("interactive") is True
    assert initial[27].get("interactive") is False

    message = handlers.bind_selected_chapter("chapter", "book", ss)
    assert "设置为" in message
    bound = handlers.refresh_bookshelf_management_view_with_hierarchy("", "", ss)
    assert bound[25].get("value") == "book"
    assert bound[27].get("interactive") is True
    assert "当前所属整书" in bound[28].get("value", "")

    message = handlers.unbind_selected_chapter("chapter", ss)
    assert "解除" in message
    unbound = handlers.refresh_bookshelf_management_view_with_hierarchy("", "", ss)
    assert unbound[25].get("value") is None
    assert unbound[27].get("interactive") is False


def test_selected_opened_contract_survives_hierarchy_rows(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book-a")
    create("book-b")
    create("chapter-a1")
    create("chapter-b2")
    ids = _project_ids("book-a", "book-b", "chapter-a1", "chapter-b2")
    _set_relation("chapter-a1", ids["book-a"], title="A1", order=1)
    _set_relation("chapter-b2", ids["book-b"], title="B2", order=2)

    ss = SessionState(project="book-b", script={"meta": {}}, bindings={})
    ss.set_selected("chapter-a1")
    controls = handlers.reconcile_bookshelf_selection(ss, "chapter-a1")
    assert ss.selected_project == "chapter-a1"
    assert ss.project == "book-b"
    assert controls[0].get("value") == "book-b"

    ss.set_project("chapter-b2", {"meta": {}}, {})
    ss.set_selected("book-a")
    controls = handlers.reconcile_bookshelf_selection(ss, "book-a")
    assert controls[0].get("value") == "chapter-b2"
    assert ss.selected_project == "book-a"


def test_child_row_selects_without_opening(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book")
    create("chapter")
    ids = _project_ids("book", "chapter")
    _set_relation("chapter", ids["book"], title="第一章", order=1)
    rows = handlers.render_bookshelf_rows("")
    child_index = next(
        index for index, row in enumerate(rows["data"]) if "chapter" in row[0]
    )
    ss = SessionState(project="book", script={"meta": {}}, bindings={})
    name, info, _p_sel = handlers.select_bookshelf_row(
        rows,
        ss,
        type("Event", (), {"index": (child_index, 0)})(),
    )
    assert name == "chapter"
    assert ss.selected_project == "chapter"
    assert ss.project == "book"
    assert "book" in info


def test_hierarchy_search_clears_hidden_selection_but_keeps_opened(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book", "整书")
    create("chapter", "章节")
    ids = _project_ids("book", "chapter")
    _set_relation("chapter", ids["book"], title="第一章")
    ss = SessionState(project="book", script={"meta": {}}, bindings={})
    ss.set_selected("chapter")

    _rows, _info, selected_update = handlers.apply_project_search("不存在", ss)
    assert selected_update.get("value") == ""
    assert ss.selected_project is None
    assert ss.project == "book"

    ss.set_selected("chapter")
    refreshed = handlers.refresh_bookshelf_management_view("第一章", "book", ss)
    assert ss.selected_project == "chapter"
    assert ss.project == "book"
    assert refreshed[5].get("value") == "chapter"


def test_archive_chapter_preserves_parent_and_restore_relinks(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book")
    create("chapter")
    ids = _project_ids("book", "chapter")
    _set_relation("chapter", ids["book"], title="第一章")

    ss = SessionState(project="book", script={"meta": {}}, bindings={})
    ss.set_selected("chapter")
    message, *_ = handlers.archive_selected("chapter", "chapter", ss)
    assert "已移入回收站" in message
    assert Path(hierarchy_workspace[0] / "projects" / "book").is_dir()
    archive_id = ProjectStorageService.list_archived()[0]["archive_id"]
    assert "已恢复" in handlers.restore_archived_global(archive_id)
    restored = next(
        item for item in ProjectCatalogService.scan() if item.project_name == "chapter"
    )
    assert restored.relation_status == RELATION_VALID
    assert restored.parent_project_name == "book"


def test_restore_missing_parent_becomes_orphan(hierarchy_workspace):
    _root, create = hierarchy_workspace
    create("book")
    create("chapter")
    ids = _project_ids("book", "chapter")
    _set_relation("chapter", ids["book"], title="第一章")
    handlers.archive_selected("chapter", "chapter", None)
    ProjectRepository.delete_project("book")

    archive_id = ProjectStorageService.list_archived()[0]["archive_id"]
    assert "已恢复" in handlers.restore_archived_global(archive_id)
    restored = next(
        item for item in ProjectCatalogService.scan() if item.project_name == "chapter"
    )
    assert restored.relation_status == RELATION_ORPHAN
    assert restored.parent_project_name is None


def test_archive_book_with_children_is_blocked_and_unrelated_book_is_safe(
    hierarchy_workspace,
):
    _root, create = hierarchy_workspace
    create("book-a")
    create("chapter-a")
    create("book-b")
    ids = _project_ids("book-a", "chapter-a", "book-b")
    _set_relation("chapter-a", ids["book-a"], title="A1")

    with pytest.raises(ValueError, match="仍关联 1 个章节"):
        ProjectCatalogService.assert_archive_allowed("book-a")
    message, *_ = handlers.archive_selected("book-a", "book-a", None)
    assert "仍关联 1 个章节" in message
    assert Path(hierarchy_workspace[0] / "projects" / "book-a").is_dir()

    message, *_ = handlers.archive_selected("book-b", "book-b", None)
    assert "已移入回收站" in message
    remaining = {item.project_name for item in ProjectCatalogService.scan()}
    assert remaining == {"book-a", "chapter-a"}


def test_data_dir_switch_drops_old_hierarchy_and_keeps_query(monkeypatch, tmp_path):
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    ProjectRepository.WORKSPACE_ROOT = str(root_a / "projects")
    ProjectRepository.LEGACY_ROOT = str(root_a / "legacy")
    ProjectRepository._INITIALIZED = True
    script = _script_file(tmp_path, "old")
    ProjectRepository.create_project("old-book", str(script))
    ProjectRepository.create_project("old-chapter", str(script))
    ids = _project_ids("old-book", "old-chapter")
    _set_relation("old-chapter", ids["old-book"], title="旧章节")

    ss = SessionState(project="old-book", script={"meta": {}}, bindings={})
    ss.set_selected("old-chapter")
    ss.set_catalog_query("旧")

    def switch_data_dir(path: str) -> str:
        ProjectRepository.WORKSPACE_ROOT = str(root_b / "projects")
        ProjectRepository.LEGACY_ROOT = str(root_b / "legacy")
        ProjectRepository._INITIALIZED = True
        return str(root_b)

    monkeypatch.setattr("ui.settings_handlers.ProjectService.set_data_dir", switch_data_dir)
    message, returned = apply_data_dir(str(root_b), ss)
    assert "数据目录已设置" in message
    assert returned == str(root_b)
    assert ProjectCatalogService.scan() == []
    assert ss.project is None
    assert ss.selected_project is None
    assert ss.catalog_query == "旧"


def test_physical_project_directories_remain_siblings(hierarchy_workspace):
    root, create = hierarchy_workspace
    create("book")
    create("chapter")
    ids = _project_ids("book", "chapter")
    _set_relation("chapter", ids["book"], title="第一章")

    projects_root = root / "projects"
    assert (projects_root / "book").is_dir()
    assert (projects_root / "chapter").is_dir()
    assert not (projects_root / "book" / "chapter").exists()
