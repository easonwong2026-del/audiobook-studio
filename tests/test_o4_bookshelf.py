"""O4 书架 + 章节树：纯函数单测（无 gradio / 无 torch / 无 GPU）。

验证（设计 §6 O4 / §12.3）：
- ProjectCatalogService.scan() 通过统一目录权威链返回多书摘要，状态色块推导正确；
- pm.build_chapter_tree(project) 产出 HTML 含 <details> 与各章标题 / 段 ID。

用 tmp_path + monkeypatch(WORKSPACE_ROOT) 建最少 4 个状态各异的假项目
（仿 test_progress 约定，不依赖 GPU / 真实模型）。
"""
import sys
import os
import json

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.project_manager as pm  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402
from services.project_catalog import ProjectCatalogService  # noqa: E402


SCRIPT = {
    "meta": {"title": "书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {"id": 1, "title": "第一章",
         "segments": [
             {"id": "1-001", "role": "旁白", "text": "A"},
             {"id": "1-002", "role": "旁白", "text": "B"},
         ]},
        {"id": 2, "title": "第二章",
         "segments": [
             {"id": "2-001", "role": "旁白", "text": "C"},
         ]},
    ],
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """WORKSPACE_ROOT 重定向到 tmp_path，建 4 个状态各异的假项目。"""
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(ProjectRepository, "_INITIALIZED", True)
    # 状态各异：全 pending / 进行中(1 done) / 完成(全 done) / 有失败(1 failed)
    done_map = {
        "p_pending": [],
        "p_progress": ["done"],
        "p_done": ["done", "done", "done"],
        "p_failed": ["failed"],
    }
    seg_ids = ["1-001", "1-002", "2-001"]
    for name, dones in done_map.items():
        sp = tmp_path / f"{name}.json"
        sp.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
        pm.create_project(name, str(sp))
        for i, status in enumerate(dones):
            pm.update_segment_status(name, seg_ids[i], status)
    return str(tmp_path)


def test_catalog_returns_summaries(workspace):
    summaries = ProjectCatalogService.scan()
    by_name = {summary.project_name: summary for summary in summaries}
    assert set(by_name) == {"p_pending", "p_progress", "p_done", "p_failed"}, \
        f"书架应含 4 个假项目，实际: {sorted(by_name)}"
    for summary in summaries:
        assert summary.chapters == 2, summary
        assert summary.segments == 3, summary


def test_catalog_status_derivation(workspace):
    summaries = {
        summary.project_name: summary
        for summary in ProjectCatalogService.scan()
    }
    # 全 pending -> 未开始
    assert summaries["p_pending"].status == "⚪未开始"
    assert summaries["p_pending"].completed == 0
    assert summaries["p_pending"].progress == 0.0
    # 1 done / 0 failed -> 进行中
    assert summaries["p_progress"].status == "🟢进行中"
    assert summaries["p_progress"].completed == 1
    assert abs(summaries["p_progress"].progress - 1 / 3) < 1e-9
    # 全 done -> 完成
    assert summaries["p_done"].status == "✅完成"
    assert summaries["p_done"].completed == 3
    assert summaries["p_done"].progress == 1.0
    # 1 failed / 0 done -> 有失败
    assert summaries["p_failed"].status == "🔴有失败"
    assert summaries["p_failed"].failed == 1


def test_build_chapter_tree_html(workspace):
    html = pm.build_chapter_tree("p_pending")
    assert "<details>" in html, "章节树应含 <details> 折叠结构"
    assert "第一章" in html, "应出现第一章标题"
    assert "第二章" in html, "应出现第二章标题"
    # 各段 ID 出现
    assert "1-001" in html and "1-002" in html and "2-001" in html
    # 折叠摘要应含完成进度（0/2）
    assert "0/2" in html, "章节摘要应显示完成进度"


def test_build_chapter_tree_missing_project_returns_placeholder(workspace):
    # 不存在项目 -> 返回提示文本（不抛异常）
    html = pm.build_chapter_tree("no_such_project")
    assert "未打开项目" in html
