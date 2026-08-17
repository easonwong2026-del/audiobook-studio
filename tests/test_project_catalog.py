"""ProjectCatalogService / ProjectSummary 单测（无 gradio）。

覆盖（T01）：
- scan 字段齐全（title/author/chapters/segments/completed/failed/status/progress/modified_at）；
- search：大小写不敏感、中文 substring（title/author）、空查询=全部、无匹配、按 project_name 排序；
- 坏项目容错（project.json 损坏不拖垮书架，占位字段继续）；
- modified_at 为 ISO 8601 格式；
- get_summary 单项目 / 不存在返回 None。
"""
from __future__ import annotations

import json
import os
import re

import pytest

from repositories.project_repo import ProjectRepository
from services.project_catalog import ProjectCatalogService

BASE_SCRIPT = {
    "meta": {"title": "测试书", "author": "测试作者"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {"id": 1, "title": "第一章", "segments": [
            {"id": "1-001", "role": "旁白", "text": "A"},
            {"id": "1-002", "role": "旁白", "text": "B"},
        ]},
        {"id": 2, "title": "第二章", "segments": [
            {"id": "2-001", "role": "旁白", "text": "C"},
        ]},
    ],
}


def _script_file(tmp_path, title="测试书", author="测试作者"):
    path = tmp_path / "book.json"
    payload = json.loads(json.dumps(BASE_SCRIPT, ensure_ascii=False))
    payload["meta"]["title"] = title
    payload["meta"]["author"] = author
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def catalog_workspace(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project(
        "test_book", str(_script_file(tmp_path))
    )
    ProjectRepository.create_project(
        "Another Book",
        str(_script_file(tmp_path, title="第二本书", author="作者B")),
    )
    return data_root


def test_scan_returns_complete_summaries(catalog_workspace):
    summaries = ProjectCatalogService.scan()
    assert len(summaries) == 2
    by_name = {s.project_name: s for s in summaries}
    first = by_name["test_book"]
    assert first.title == "测试书"
    assert first.author == "测试作者"
    assert first.chapters == 2
    assert first.segments == 3
    assert first.completed == 0
    assert first.failed == 0
    assert first.status == "⚪未开始"
    assert first.progress == 0.0
    assert first.modified_at is not None
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", first.modified_at)


def test_scan_derives_progress_and_status(catalog_workspace):
    ProjectRepository.update_segment_status("test_book", "1-001", "done")
    ProjectRepository.update_segment_status("test_book", "2-001", "failed")
    summary = ProjectCatalogService.get_summary("test_book")
    assert summary is not None
    assert summary.completed == 1
    assert summary.failed == 1
    assert summary.status == "🟡部分"
    assert abs(summary.progress - 1 / 3) < 1e-9


def test_search_case_insensitive(catalog_workspace):
    assert [s.project_name for s in ProjectCatalogService.search_projects("TEST")] == ["test_book"]
    assert [s.project_name for s in ProjectCatalogService.search_projects("ANOTHER")] == ["Another Book"]
    assert [s.project_name for s in ProjectCatalogService.search_projects("book")] == ["Another Book", "test_book"]


def test_search_chinese_substring(catalog_workspace):
    # title 中文 substring
    assert [s.project_name for s in ProjectCatalogService.search_projects("第二本")] == ["Another Book"]
    # author 中文 substring
    assert [s.project_name for s in ProjectCatalogService.search_projects("作者B")] == ["Another Book"]
    # project_name 中文（目录名匹配）
    ProjectRepository.create_project("中文项目", str(_script_file(catalog_workspace)))
    assert [s.project_name for s in ProjectCatalogService.search_projects("中文")] == ["中文项目"]


def test_search_empty_query_returns_all(catalog_workspace):
    assert len(ProjectCatalogService.search_projects("")) == 2
    assert len(ProjectCatalogService.search_projects(None)) == 2
    assert len(ProjectCatalogService.search_projects("   ")) == 2


def test_search_no_match(catalog_workspace):
    assert ProjectCatalogService.search_projects("不存在的项目xyz") == []


def test_search_sorted_by_project_name(catalog_workspace):
    names = [s.project_name for s in ProjectCatalogService.search_projects("")]
    assert names == sorted(names)


def test_bad_project_does_not_break_bookshelf(catalog_workspace):
    ProjectRepository.create_project(
        "bad_book", str(_script_file(catalog_workspace, title="坏书"))
    )
    from lib import project_paths
    meta_path = project_paths.project_file(
        os.path.join(catalog_workspace, "projects", "bad_book"), "project_meta"
    )
    with open(meta_path, "w", encoding="utf-8") as file:
        file.write("{ this is not valid json")
    summaries = ProjectCatalogService.scan()  # 不抛异常
    names = {s.project_name for s in summaries}
    assert "test_book" in names
    assert "Another Book" in names
    bad = next(s for s in summaries if s.project_name == "bad_book")
    # 占位字段继续（title 回退项目名，author 回退未填写）
    assert bad.title == "bad_book"
    assert bad.author == "未填写"
    assert bad.segments == 0


def test_bad_script_meta_falls_back(catalog_workspace):
    ProjectRepository.create_project(
        "scriptless", str(_script_file(catalog_workspace))
    )
    from lib import project_paths
    script_path = project_paths.project_file(
        os.path.join(catalog_workspace, "projects", "scriptless"), "structured_script"
    )
    with open(script_path, "w", encoding="utf-8") as file:
        file.write("{ broken")
    summary = ProjectCatalogService.get_summary("scriptless")
    assert summary is not None
    assert summary.title == "scriptless"
    assert summary.author == "未填写"


def test_get_summary_returns_none_for_missing(catalog_workspace):
    assert ProjectCatalogService.get_summary("no_such_project") is None
    assert ProjectCatalogService.get_summary("") is None
