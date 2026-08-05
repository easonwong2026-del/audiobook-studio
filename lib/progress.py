"""段落级进度状态（纯函数，禁止 import gradio）。

O3 结构化队列进度列表 + O12 段落级暂停/恢复共用的「内存段态」数据源：

- ``build_segment_states(project)``：从项目 ``meta.segments_status`` + 剧本初始化
  全部段态（状态与持久态对齐），仅读盘一次。
- ``update_segment_state(states, seg_id, status, progress, **meta)``：更新/追加单段态。
- ``to_queue_rows(states)``：转为 ``gr.Dataframe`` 行（list[list]）。

数据源纪律（设计 §9.4）：``state.segment_states`` 只由本模块写入，
**绝不反向写 ``meta.segments_status``**（落盘仍归 ``pm.update_segment_status``）。
本模块仅依赖 ``lib.project_manager`` / ``lib.script_loader``，无任何 gradio 依赖，
可被 ``tests/test_progress.py`` 直接单测。
"""
from __future__ import annotations

from typing import Optional

from . import chapter_identity
from . import project_manager as pm

# ── 段状态枚举（集中声明，O3/O12 共用） ──
# pending 待合成 / running 合成中 / done 已完成 / error 失败
# paused 已暂停（全局态） / cancelled 已取消（全局态）
SEGMENT_STATUS_PENDING = "pending"
SEGMENT_STATUS_RUNNING = "running"
SEGMENT_STATUS_DONE = "done"
SEGMENT_STATUS_ERROR = "error"
SEGMENT_STATUS_PAUSED = "paused"
SEGMENT_STATUS_CANCELLED = "cancelled"
# O5：未选中章节的段在内存态标 skipped（⏭）；仅由 queue.synthesize_project 反向写 meta
SEGMENT_STATUS_SKIPPED = "skipped"

VALID_SEGMENT_STATUSES = (
    SEGMENT_STATUS_PENDING,
    SEGMENT_STATUS_RUNNING,
    SEGMENT_STATUS_DONE,
    SEGMENT_STATUS_ERROR,
    SEGMENT_STATUS_PAUSED,
    SEGMENT_STATUS_CANCELLED,
    SEGMENT_STATUS_SKIPPED,
)

# 状态 -> 图标（供 gr.Dataframe 文本列展示）
SEGMENT_STATUS_ICONS = {
    SEGMENT_STATUS_PENDING: "⬜",
    SEGMENT_STATUS_RUNNING: "⏳",
    SEGMENT_STATUS_DONE: "✅",
    SEGMENT_STATUS_ERROR: "❌",
    SEGMENT_STATUS_PAUSED: "⏸",
    SEGMENT_STATUS_CANCELLED: "⛔",
    SEGMENT_STATUS_SKIPPED: "⏭",
}

# gr.Dataframe 列顺序（与设计 §4 一致）：状态图标 | 章节 | 段落 | 角色 | 文本预览 | 进度%
QUEUE_HEADERS = ["状态", "章节", "段落", "角色", "文本预览", "进度%"]
QUEUE_DATATYPES = ["str", "str", "str", "str", "str", "str"]

# O5：合成前分段预览（只读输入预览，无状态图标 / 无进度%）列定义
PREVIEW_HEADERS = ["章节", "段落", "角色", "文本预览"]
PREVIEW_DATATYPES = ["str", "str", "str", "str"]

_PREVIEW_LEN = 40


def build_segment_states(project: str, selected_chapters: Optional[list] = None) -> list[dict]:
    """从项目 ``meta.segments_status`` + 剧本初始化全部段态（按章节/段顺序）。

    状态与持久态对齐：``meta.segments_status`` 仅含 pending/done/failed；
    ``failed`` 映射为内存态 ``error``（O3 列表用 ❌ 展示），``done`` 进度 100%，其余 0%。

    O5：若传入 ``selected_chapters``（章节 id 字符串列表，None/空=全选），则未选中
    章节的段在**内存列表**中标 ``skipped``（⏭）——仅内存展示，绝不反向写
    ``meta.segments_status``（纪律同 O3 §9.4）。

    Args:
        project: 项目名。
        selected_chapters: 选中章节 id 列表（字符串）；None/空表示全选。

    Returns:
        段态字典列表，每项含 ``seg_id`` / ``chapter`` / ``role`` / ``text`` /
        ``status`` / ``progress``。
    """
    meta, script_data, _ = pm.open_project(project)
    selected_set = None
    if selected_chapters:
        selected_set = {str(c) for c in selected_chapters}
    states: list[dict] = []
    chapters = script_data.get("chapters", [])
    for chapter_index, ch in enumerate(chapters):
        ch_id = str(ch.get("id", ""))
        ch_title = ch.get("title", "") or ch_id
        ch_label = f"第{chapter_identity.chapter_number(ch, chapter_index)}章 {ch_title}"
        ch_selected = (selected_set is None) or (ch_id in selected_set)
        for seg in ch.get("segments", []):
            seg_id = seg.get("id")
            # O5：未选中章的段 -> 内存态标 skipped（⏭），不写 meta
            if selected_set is not None and not ch_selected:
                states.append({
                    "seg_id": seg_id,
                    "chapter": ch_title or ch_label,
                    "chapter_label": ch_label,
                    "role": seg.get("role", ""),
                    "text": seg.get("text", ""),
                    "status": SEGMENT_STATUS_SKIPPED,
                    "progress": 0.0,
                })
                continue
            persisted = meta.segments_status.get(seg_id, SEGMENT_STATUS_PENDING)
            if persisted == SEGMENT_STATUS_DONE:
                status = SEGMENT_STATUS_DONE
                progress = 1.0
            elif persisted == "failed":
                status = SEGMENT_STATUS_ERROR
                progress = 0.0
            else:
                status = SEGMENT_STATUS_PENDING
                progress = 0.0
            states.append({
                "seg_id": seg_id,
                "chapter": ch_title or ch_label,
                "chapter_label": ch_label,
                "role": seg.get("role", ""),
                "text": seg.get("text", ""),
                "status": status,
                "progress": progress,
            })
    return states


def update_segment_state(states: list[dict], seg_id: str, status: str,
                         progress: float = 0.0, **meta) -> dict:
    """更新或追加某段状态（原地修改 ``states``）。

    Args:
        states: 段态列表（``build_segment_states`` 产出或已初始化）。
        seg_id: 段 ID。
        status: 状态枚举值（见 ``VALID_SEGMENT_STATUSES``）。
        progress: 0..1 进度。
        **meta: 额外字段（role / chapter / text 等），用于追加新段时补全。

    Returns:
        被更新/追加的段态字典。

    Raises:
        ValueError: ``status`` 非法。
    """
    if status not in VALID_SEGMENT_STATUSES:
        raise ValueError(f"非法段状态: {status!r}（合法值：{VALID_SEGMENT_STATUSES}）")
    for st in states:
        if st.get("seg_id") == seg_id:
            st["status"] = status
            st["progress"] = progress
            if meta:
                st.update(meta)
            return st
    # 未命中：新增（通常不应发生，因 build_segment_states 已预建全段）
    new_state: dict = {"seg_id": seg_id, "status": status, "progress": progress}
    new_state.update(meta)
    states.append(new_state)
    return new_state


def to_queue_rows(states: list[dict]) -> list[list]:
    """将段态列表转为 ``gr.Dataframe`` 行（list[list]）。

    列顺序：状态图标 | 章节 | 段落 | 角色 | 文本预览 | 进度%。

    Args:
        states: 段态列表。

    Returns:
        与 ``QUEUE_HEADERS`` 等宽的二维列表；空列表表示无数据。
    """
    rows: list[list] = []
    for st in states:
        status = st.get("status", SEGMENT_STATUS_PENDING)
        icon = SEGMENT_STATUS_ICONS.get(status, SEGMENT_STATUS_ICONS[SEGMENT_STATUS_PENDING])
        text = st.get("text", "") or ""
        if len(text) > _PREVIEW_LEN:
            preview = text[:_PREVIEW_LEN] + "…"
        else:
            preview = text
        progress = st.get("progress", 0.0) or 0.0
        rows.append([
            icon,
            str(st.get("chapter_label") or st.get("chapter", "")),
            str(st.get("seg_id", "")),
            str(st.get("role", "")),
            preview,
            f"{int(round(progress * 100))}%",
        ])
    return rows


def build_preview_rows_from_script(script: dict) -> list[list]:
    """从已加载的剧本 dict 生成合成前分段预览行（O5，不读盘）。

    列 = 章节 | 段落 | 角色 | 文本预览（与 ``PREVIEW_HEADERS`` 等宽）。

    Args:
        script: 已加载的剧本 dict（含 ``chapters`` 键）。

    Returns:
        二维行列表；空剧本返回空列表。
    """
    rows: list[list] = []
    chapters = script.get("chapters", [])
    for chapter_index, ch in enumerate(chapters):
        ch_id = str(ch.get("id", ""))
        ch_title = ch.get("title", "") or ch_id
        ch_label = f"第{chapter_identity.chapter_number(ch, chapter_index)}章 {ch_title}"
        for seg in ch.get("segments", []):
            text = seg.get("text", "") or ""
            if len(text) > _PREVIEW_LEN:
                preview = text[:_PREVIEW_LEN] + "…"
            else:
                preview = text
            rows.append([
                ch_label,
                str(seg.get("id", "")),
                str(seg.get("role", "")),
                preview,
            ])
    return rows


def build_preview_rows(project: str) -> list[list]:
    """产出合成前分段预览行（O5 输入侧只读预览，无状态图标 / 无进度%）。

    薄包装：读盘取剧本后转调 ``build_preview_rows_from_script``，
    保留原签名与行为以向后兼容其它调用方。

    Args:
        project: 项目名。

    Returns:
        二维行列表；空项目返回空列表。
    """
    _, script_data, _ = pm.open_project(project)
    return build_preview_rows_from_script(script_data)
