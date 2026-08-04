"""UI callbacks for storage, project remnants, and environment diagnostics."""
from __future__ import annotations

import html
import os
from datetime import timezone

from lib import config
from repositories.project_repo import ProjectRepository
from services import ProjectService


def refresh_abnormal_projects() -> tuple:
    from datetime import datetime

    inspections = ProjectRepository.list_abnormal_projects()
    rows = []
    for item in inspections:
        details = "、".join([*item.missing_files, *item.invalid_files])
        modified = (
            datetime.fromtimestamp(item.modified_at, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if item.modified_at
            else ""
        )
        rows.append([item.name, item.status, item.path, details, modified])
    choices = [item.name for item in inspections]
    import gradio as gr

    return (
        rows,
        gr.update(choices=choices, value=choices[0] if choices else None),
        f"共发现 {len(choices)} 个异常或残留项目目录。",
    )


def refresh_abnormal_project_data() -> tuple:
    rows, selection, _status = refresh_abnormal_projects()
    return rows, selection


def open_abnormal_project(project_name: str) -> str:
    name = str(project_name or "").strip()
    if not name:
        return "⚠ 请先选择异常项目"
    inspection = ProjectRepository.inspect_project_slot(name)
    if inspection.status not in {"incomplete", "corrupted", "temporary"}:
        return "⚠ 该项目不属于可处理的工作区残留"
    try:
        import subprocess
        import sys

        if sys.platform == "win32":
            os.startfile(inspection.path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", inspection.path])
        else:
            subprocess.Popen(["xdg-open", inspection.path])
        return f"✅ 已打开：`{inspection.path}`"
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 打开目录失败：{html.escape(str(exc))}"


def archive_abnormal_project(project_name: str) -> str:
    name = str(project_name or "").strip()
    if not name:
        return "⚠ 请先选择异常项目"
    try:
        target = ProjectRepository.archive_orphan_project(name)
        return f"✅ 已移动到回收站：`{target}`"
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 归档失败：{html.escape(str(exc))}"


def apply_data_dir(new_dir: str) -> tuple:
    if not new_dir or not str(new_dir).strip():
        return "⚠ 请填写保存位置", ""
    try:
        path = os.path.normpath(ProjectService.set_data_dir(str(new_dir).strip()))
        return f"✅ 数据目录已设置为：{path}（本会话立即生效）", path
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 设置失败：{html.escape(str(exc))}", ""


def open_data_dir() -> str:
    data_dir = config.get_data_dir()
    try:
        import subprocess
        import sys

        if sys.platform == "win32":
            os.startfile(data_dir)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", data_dir])
        else:
            subprocess.Popen(["xdg-open", data_dir])
        return f"✅ 已打开数据目录：`{data_dir}`"
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 打开数据目录失败：{html.escape(str(exc))}"
