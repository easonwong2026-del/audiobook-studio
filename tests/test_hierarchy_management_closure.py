"""Phase B.5 hierarchy-management closure regression coverage."""
from __future__ import annotations

import json
from pathlib import Path

import gradio as gr
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
from ui.pages.overview_page import create_overview_page
from ui.wiring.project_catalog_wiring import (
    bookshelf_management_outputs,
    hierarchy_outputs,
)


def _script_file(tmp_path: Path, name: str, title: str | None = None) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "meta": {"title": title or name, "author": "作者"},
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
def closure_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True

    def create(name: str, title: str | None = None) -> str:
        return ProjectRepository.create_project(
            name,
            str(_script_file(tmp_path, name, title)),
        )

    return data_root, create


def _meta_path(name: str) -> Path:
    return Path(
        project_paths.project_file(ProjectRepository.get_project_dir(name), "project_meta")
    )


def _ids(*names: str) -> dict[str, str]:
    return {name: ProjectRepository.ensure_project_id(name) for name in names}


def _set_relation(
    child_name: str,
    parent_id: str | None,
    *,
    title: str | None = None,
    order: object = None,
    kind: str = "chapter",
) -> None:
    meta = ProjectRepository._load_meta(ProjectRepository.get_project_dir(child_name))
    meta.project_id = meta.project_id or ProjectRepository.ensure_project_id(child_name)
    meta.project_kind = kind
    meta.parent_project_id = parent_id
    meta.chapter_title = title
    meta.chapter_order = order
    ProjectRepository._save_meta(ProjectRepository.get_project_dir(child_name), meta)


def _refresh(ss) -> tuple:
    return handlers.refresh_bookshelf_management_view_with_hierarchy("", "", ss)


def test_title_and_order_are_explicitly_editable(closure_workspace):
    _root, create = closure_workspace
    create("book")
    create("chapter")
    ss = SessionState()
    ss.set_selected("chapter")

    assert "设置为" in handlers.bind_selected_chapter(
        "chapter", "book", ss, "  初始标题  ", "2"
    )
    assert "更新" in handlers.update_selected_chapter(
        "chapter", "  修订标题  ", "3", ss
    )

    meta = ProjectRepository._load_meta(ProjectRepository.get_project_dir("chapter"))
    assert meta.chapter_title == "修订标题"
    assert meta.chapter_order == 3


def test_invalid_order_is_rejected_without_metadata_change(closure_workspace):
    _root, create = closure_workspace
    create("book")
    create("chapter")
    ProjectCatalogService.bind_chapter("chapter", "book", chapter_order=1)
    before = ProjectRepository._load_meta(ProjectRepository.get_project_dir("chapter"))

    for invalid in ("bad", "0", "-1"):
        with pytest.raises(ValueError, match="正整数"):
            ProjectCatalogService.update_chapter_metadata("chapter", "标题", invalid)

    after = ProjectRepository._load_meta(ProjectRepository.get_project_dir("chapter"))
    assert after.chapter_title == before.chapter_title
    assert after.chapter_order == before.chapter_order == 1


def test_reassign_is_single_operation_and_keeps_physical_dirs_and_session(
    closure_workspace,
):
    root, create = closure_workspace
    create("book-a")
    create("book-b")
    create("chapter")
    ProjectCatalogService.bind_chapter("chapter", "book-a", chapter_order=1)
    project_root = root / "projects"
    before_dirs = {
        name: (project_root / name).resolve()
        for name in ("book-a", "book-b", "chapter")
    }

    ss = SessionState(project="book-b", script={"meta": {}}, bindings={})
    ss.set_selected("chapter")
    ProjectCatalogService.bind_chapter("chapter", "book-b")

    chapter = ProjectCatalogService.get_summary("chapter")
    assert chapter is not None
    assert chapter.parent_project_name == "book-b"
    assert chapter.relation_status == RELATION_VALID
    assert ss.project == "book-b"
    assert ss.selected_project == "chapter"
    assert handlers.reconcile_bookshelf_selection(ss, "chapter")[0].get("value") == "book-b"
    assert {name: (project_root / name).resolve() for name in before_dirs} == before_dirs


def test_unbind_becomes_book_and_preserves_identity(closure_workspace):
    _root, create = closure_workspace
    create("book")
    create("chapter")
    ProjectCatalogService.bind_chapter("chapter", "book")
    before = ProjectRepository._load_meta(ProjectRepository.get_project_dir("chapter"))
    identity = before.project_id

    ProjectCatalogService.clear_chapter_parent("chapter")
    after = ProjectRepository._load_meta(ProjectRepository.get_project_dir("chapter"))
    assert after.project_kind == "book"
    assert after.parent_project_id is None
    assert after.project_id == identity


def test_orphan_repair_is_explicit_and_scan_does_not_write_legacy_metadata(
    closure_workspace,
):
    _root, create = closure_workspace
    create("book")
    create("orphan")
    orphan_path = _meta_path("orphan")
    raw = json.loads(orphan_path.read_text(encoding="utf-8"))
    raw.pop("project_id", None)
    raw["project_kind"] = "chapter"
    raw["parent_project_id"] = "missing-parent"
    raw["chapter_title"] = "孤立章节"
    orphan_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    before = orphan_path.read_bytes()

    orphan = ProjectCatalogService.get_summary("orphan")
    assert orphan is not None
    assert orphan.relation_status == RELATION_ORPHAN
    assert orphan.relation_message == "章节缺少稳定身份，无法解析所属整书"
    assert orphan_path.read_bytes() == before

    ProjectCatalogService.bind_chapter("orphan", "book", chapter_title="已修复")
    repaired = ProjectCatalogService.get_summary("orphan")
    assert repaired is not None
    assert repaired.relation_status == RELATION_VALID
    assert repaired.parent_project_name == "book"


def test_relationship_write_rolls_back_lazy_id_materialization_on_failure(
    closure_workspace, monkeypatch
):
    _root, create = closure_workspace
    create("book")
    create("chapter")
    for name in ("book", "chapter"):
        path = _meta_path(name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("project_id", None)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    original_save = ProjectRepository._save_meta
    parent_dir = ProjectRepository.get_project_dir("book")

    def fail_parent(path, meta):
        if path == parent_dir and meta.project_id:
            raise OSError("simulated parent publish failure")
        return original_save(path, meta)

    monkeypatch.setattr(ProjectRepository, "_save_meta", staticmethod(fail_parent))
    with pytest.raises(RuntimeError, match="关系写入失败"):
        ProjectCatalogService.bind_chapter("chapter", "book")

    chapter = ProjectRepository._load_meta(ProjectRepository.get_project_dir("chapter"))
    parent = ProjectRepository._load_meta(ProjectRepository.get_project_dir("book"))
    assert chapter.project_id is None
    assert chapter.project_kind == "book"
    assert chapter.parent_project_id is None
    assert parent.project_id is None


def test_duplicate_identity_is_diagnosed_and_never_selected_as_parent(
    closure_workspace,
):
    _root, create = closure_workspace
    create("book-a")
    create("book-b")
    create("chapter")
    ids = _ids("book-a", "book-b", "chapter")
    book_b = ProjectRepository._load_meta(ProjectRepository.get_project_dir("book-b"))
    book_b.project_id = ids["book-a"]
    book_b.project_kind = "book"
    book_b.parent_project_id = None
    ProjectRepository._save_meta(ProjectRepository.get_project_dir("book-b"), book_b)
    _set_relation("chapter", ids["book-a"], title="章节")

    hierarchy = ProjectCatalogService.scan_hierarchy()
    assert hierarchy.duplicate_project_ids == (ids["book-a"],)
    assert all(
        item.project_name not in {"book-a", "book-b"}
        for item in ProjectCatalogService.book_choices(hierarchy.projects)
    )
    with pytest.raises(ValueError, match="重复"):
        ProjectCatalogService.bind_chapter("chapter", "book-a")
    chapter = ProjectCatalogService.get_summary("chapter")
    assert chapter is not None
    assert chapter.relation_status == RELATION_INVALID


def test_cycle_can_be_repaired_only_by_explicit_rebind(closure_workspace):
    _root, create = closure_workspace
    create("book")
    create("cycle-a")
    create("cycle-b")
    ids = _ids("book", "cycle-a", "cycle-b")
    _set_relation("cycle-a", ids["cycle-b"], title="A")
    _set_relation("cycle-b", ids["cycle-a"], title="B")

    assert ProjectCatalogService.get_summary("cycle-a").relation_status == RELATION_INVALID
    ProjectCatalogService.bind_chapter("cycle-a", "book")
    repaired = ProjectCatalogService.get_summary("cycle-a")
    assert repaired is not None
    assert repaired.relation_status == RELATION_VALID
    assert ProjectCatalogService.get_summary("cycle-b").relation_status == RELATION_INVALID


def test_hierarchy_controls_reflect_none_book_healthy_and_orphan_states(
    closure_workspace,
):
    _root, create = closure_workspace
    create("book")
    create("chapter")
    create("orphan")
    ProjectCatalogService.bind_chapter("chapter", "book", chapter_title="第一章", chapter_order=2)
    _set_relation("orphan", "missing", title="孤立")

    ss = SessionState()
    none = _refresh(ss)
    assert none[29].get("value") == "未选择"
    assert none[26].get("interactive") is False
    assert none[30].get("interactive") is False
    assert none[32].get("interactive") is False

    ss.set_selected("book")
    book = _refresh(ss)
    assert book[29].get("value") == "整书"
    assert book[30].get("interactive") is False
    assert book[31].get("interactive") is False
    assert book[27].get("interactive") is False

    ss.set_selected("chapter")
    healthy = _refresh(ss)
    assert healthy[29].get("value") == "章节"
    assert healthy[30].get("value") == "第一章"
    assert healthy[31].get("value") == "2"
    assert healthy[32].get("interactive") is True

    ss.set_selected("orphan")
    orphan = _refresh(ss)
    assert orphan[25].get("choices") == ["book"]
    assert orphan[30].get("interactive") is True
    assert "未找到所属整书" in orphan[28].get("value", "")


def test_query_reconciles_selection_after_title_edit_but_keeps_opened(
    closure_workspace,
):
    _root, create = closure_workspace
    create("book")
    create("chapter")
    ProjectCatalogService.bind_chapter("chapter", "book", chapter_title="旧标题")
    ss = SessionState(project="book", script={"meta": {}}, bindings={})
    ss.set_selected("chapter")
    ss.set_catalog_query("旧标题")

    assert "更新" in handlers.update_selected_chapter("chapter", "新标题", "1", ss)
    refreshed = handlers.refresh_bookshelf_management_view_with_hierarchy(
        "旧标题", "book", ss
    )
    assert ss.selected_project is None
    assert ss.project == "book"
    assert refreshed[5].get("value") == ""
    assert ss.catalog_query == "旧标题"


def test_archive_protection_moves_with_last_child(closure_workspace):
    _root, create = closure_workspace
    create("book-a")
    create("book-b")
    create("chapter")
    ProjectCatalogService.bind_chapter("chapter", "book-a")

    with pytest.raises(ValueError, match="仍关联 1 个章节"):
        ProjectStorageService.archive("book-a")
    ProjectCatalogService.bind_chapter("chapter", "book-b")
    ProjectStorageService.archive("book-a")
    with pytest.raises(ValueError, match="仍关联 1 个章节"):
        ProjectStorageService.archive("book-b")


def test_unbound_parent_can_be_archived(closure_workspace):
    _root, create = closure_workspace
    create("book")
    create("chapter")
    ProjectCatalogService.bind_chapter("chapter", "book")
    ProjectCatalogService.clear_chapter_parent("chapter")
    ProjectStorageService.archive("book")
    assert ProjectCatalogService.get_summary("book") is None


def test_hierarchy_output_contract_is_additive_and_wired(closure_workspace):
    block = gr.Blocks()
    block.__enter__()
    try:
        page = create_overview_page()
        assert len(hierarchy_outputs(page)) == 8
        assert len(bookshelf_management_outputs(page, gr.Dropdown())) == 25
        assert len(bookshelf_management_outputs(page, gr.Dropdown(), include_hierarchy=True)) == 33
    finally:
        block.__exit__(None, None, None)
