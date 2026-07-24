"""O3 纯逻辑单测：lib.progress 三个纯函数（无 gradio / 无 torch / 无 GPU）。

验证（设计 §10.3）：
1) build_segment_states(project) 返回全段列表、状态与 pm.open_project 的
   meta.segments_status 对齐（pending→pending、done→done、failed→error、未出现段不遗漏）；
2) update_segment_state(states, seg_id, status, progress=...) 更新既有段或追加；
3) to_queue_rows(states) 行数=段数、列数=6、图标映射正确
   （done→✅、running→⏳、error→❌、paused→⏸、pending→⬜、cancelled→⛔）。

用 tmp_path + monkeypatch 建最小项目，复用 test_project_manager 约定（不依赖 gradio/torch）。
"""
import sys
import os
import json

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.project_manager as pm  # noqa: E402
from lib import progress as prog  # noqa: E402


SCRIPT = {
    "meta": {"title": "进度书"},
    "voices": {"旁白": {"description": "x"}, "小明": {"description": "y"}},
    "chapters": [
        {
            "id": 1, "title": "第一章",
            "segments": [
                {"id": "1-001", "role": "旁白",
                 "text": "第一段较长的内容用于测试文本预览截断功能是否正常工作"},
                {"id": "1-002", "role": "小明", "text": "第二段内容"},
            ],
        },
        {
            "id": 2, "title": "第二章",
            "segments": [
                {"id": "2-001", "role": "旁白", "text": "第三段内容"},
            ],
        },
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """用临时目录作 WORKSPACE_ROOT，建一个 2 章 3 段 2 角色项目。"""
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(tmp_path))
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    pm.create_project("prog", str(sp))
    return "prog"


# ─────────────────────────────────────────────────────────────
# 1) build_segment_states
# ─────────────────────────────────────────────────────────────

def test_build_segment_states_returns_all_segments(project):
    states = prog.build_segment_states(project)
    assert len(states) == 3
    ids = [s["seg_id"] for s in states]
    assert ids == ["1-001", "1-002", "2-001"]


def test_build_segment_states_aligns_with_meta(project):
    pm.update_segment_status(project, "1-001", "done")
    pm.update_segment_status(project, "1-002", "failed")
    # 2-001 未触碰 -> 应保持 pending
    states = {s["seg_id"]: s for s in prog.build_segment_states(project)}
    assert states["1-001"]["status"] == "done"
    assert states["1-001"]["progress"] == 1.0
    assert states["1-002"]["status"] == "error"   # failed -> error（内存态）
    assert states["1-002"]["progress"] == 0.0
    assert states["2-001"]["status"] == "pending"
    assert states["2-001"]["progress"] == 0.0


def test_build_segment_states_explicit_pending_maps_to_pending(project):
    # 显式置 pending 也应为 pending（不能误判为 default 分支）
    pm.update_segment_status(project, "1-001", "pending")
    states = {s["seg_id"]: s for s in prog.build_segment_states(project)}
    assert states["1-001"]["status"] == "pending"


def test_build_segment_states_preserves_chapter_role_text(project):
    states = {s["seg_id"]: s for s in prog.build_segment_states(project)}
    assert states["1-001"]["chapter"] == "第一章"
    assert states["1-001"]["role"] == "旁白"
    assert states["1-001"]["text"].startswith("第一段较长")


# ─────────────────────────────────────────────────────────────
# 2) update_segment_state
# ─────────────────────────────────────────────────────────────

def test_update_segment_state_updates_existing(project):
    states = prog.build_segment_states(project)
    st = prog.update_segment_state(states, "1-001", prog.SEGMENT_STATUS_RUNNING, 0.0)
    assert st["status"] == "running"
    assert st["progress"] == 0.0
    # 原地更新：列表中应反映新状态
    assert any(s["seg_id"] == "1-001" and s["status"] == "running" for s in states)
    # 长度不变（更新而非追加）
    assert len(states) == 3


def test_update_segment_state_appends_when_missing():
    states: list = []
    st = prog.update_segment_state(states, "9-999", prog.SEGMENT_STATUS_DONE, 1.0)
    assert len(states) == 1
    assert states[0]["seg_id"] == "9-999"
    assert states[0]["status"] == "done"
    assert states[0]["progress"] == 1.0


def test_update_segment_state_merges_meta():
    states = [{"seg_id": "x", "status": "pending", "progress": 0.0}]
    prog.update_segment_state(states, "x", "running", 0.0, role="旁白", chapter="一")
    assert states[0]["role"] == "旁白"
    assert states[0]["chapter"] == "一"


def test_update_segment_state_rejects_invalid_status():
    states = [{"seg_id": "x", "status": "pending", "progress": 0.0}]
    with pytest.raises(ValueError):
        prog.update_segment_state(states, "x", "bogus", 0.0)


# ─────────────────────────────────────────────────────────────
# 3) to_queue_rows
# ─────────────────────────────────────────────────────────────

def test_to_queue_rows_row_and_column_counts(project):
    states = prog.build_segment_states(project)
    rows = prog.to_queue_rows(states)
    assert len(rows) == len(states) == 3
    for r in rows:
        assert len(r) == 6, f"每行应 6 列，实际 {len(r)}: {r}"


def test_to_queue_rows_icon_mapping():
    states = [
        {"seg_id": "a", "status": "done", "progress": 1.0, "chapter": "一", "role": "旁白", "text": "x"},
        {"seg_id": "b", "status": "running", "progress": 0.0, "chapter": "一", "role": "旁白", "text": "x"},
        {"seg_id": "c", "status": "error", "progress": 0.0, "chapter": "一", "role": "旁白", "text": "x"},
        {"seg_id": "d", "status": "paused", "progress": 0.0, "chapter": "一", "role": "旁白", "text": "x"},
        {"seg_id": "e", "status": "pending", "progress": 0.0, "chapter": "一", "role": "旁白", "text": "x"},
        {"seg_id": "f", "status": "cancelled", "progress": 0.0, "chapter": "一", "role": "旁白", "text": "x"},
    ]
    rows = prog.to_queue_rows(states)
    icons = [r[0] for r in rows]
    assert icons == ["✅", "⏳", "❌", "⏸", "⬜", "⛔"]


def test_to_queue_rows_progress_percent():
    done = [{"seg_id": "a", "status": "done", "progress": 1.0, "chapter": "一", "role": "旁白", "text": "x"}]
    assert prog.to_queue_rows(done)[0][5] == "100%"
    run = [{"seg_id": "b", "status": "running", "progress": 0.0, "chapter": "一", "role": "旁白", "text": "x"}]
    assert prog.to_queue_rows(run)[0][5] == "0%"


def test_to_queue_rows_text_truncation():
    long_text = "一二三四五六七八九十一二三四五六七八九十" + "x" * 50
    states = [{"seg_id": "a", "status": "pending", "progress": 0.0,
               "chapter": "一", "role": "旁白", "text": long_text}]
    preview = prog.to_queue_rows(states)[0][4]
    # _PREVIEW_LEN=40 -> 截断为 40 字符 + "…"
    assert len(preview) <= 41, f"预览过长: {preview!r}"
    assert preview.endswith("…")


def test_to_queue_rows_headers_and_datatypes_match():
    assert prog.QUEUE_HEADERS == ["状态", "章节", "段落", "角色", "文本预览", "进度%"]
    assert prog.QUEUE_DATATYPES == ["str", "str", "str", "str", "str", "str"]
