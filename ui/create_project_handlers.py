"""UI adapters for the single-file structured-script import workflow."""
from __future__ import annotations

import html
import logging
import os

from repositories.project_repo import ProjectRepository, sanitize_project_name
from services.structured_script_import import StructuredScriptImportService

logger = logging.getLogger(__name__)


def _update(**kwargs):
    """Keep pure name/preview helpers importable without loading the UI runtime."""
    import gradio as gr

    return gr.update(**kwargs)


def _file_value_path(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("path") or value.get("name")
    return getattr(value, "path", None) or getattr(value, "name", None)


def _original_file_name(value) -> str:
    if isinstance(value, str):
        candidate = value
    elif isinstance(value, dict):
        candidate = value.get("orig_name") or value.get("name") or value.get("path") or ""
    else:
        candidate = (
            getattr(value, "orig_name", None)
            or getattr(value, "name", None)
            or getattr(value, "path", None)
            or ""
        )
    return str(candidate).replace("\\", "/").rsplit("/", 1)[-1]


def derive_json_project_name(file_value, current_name: str = "") -> str:
    """Fill a name only while the user has not entered one manually."""
    if str(current_name or "").strip():
        return str(current_name).strip()
    source = _file_value_path(file_value)
    if source and os.path.isfile(source):
        try:
            return StructuredScriptImportService.inspect(source).suggested_project_name
        except (OSError, ValueError, TypeError):
            return ""
    filename = os.path.splitext(_original_file_name(file_value))[0]
    try:
        return sanitize_project_name(filename) if filename else ""
    except ValueError:
        return ""


def _slot_status_text(inspection) -> str:
    if inspection is None:
        return "⚪ 请输入项目名称后检查项目槽位"
    labels = {
        "available": "✅ 项目槽位可用",
        "valid": "⚠ 已存在合法项目，不会覆盖",
        "legacy": "⚠ 发现 Legacy 项目，不会覆盖",
        "incomplete": "⚠ 发现不完整项目目录，不会自动删除",
        "temporary": "⚠ 发现临时项目目录，不会自动删除",
        "corrupted": "⚠ 发现损坏项目目录，不会自动删除",
    }
    text = labels.get(inspection.status, f"⚠ 项目槽位状态：{inspection.status}")
    details = [*inspection.missing_files, *inspection.invalid_files]
    if details:
        text += "；" + "、".join(html.escape(item) for item in details)
    return text


def format_json_preview(preview) -> str:
    """Render the complete preflight summary, including every blocking error."""
    heading = "✅ JSON 检查通过" if preview.valid else "❌ JSON 检查未通过"
    narrator = "已定义" if preview.narrator_defined else "未定义"
    lines = [
        f"### {heading}",
        f"- **作品**：{html.escape(preview.title)}",
        f"- **作者**：{html.escape(preview.author)}",
        f"- **章节**：{preview.chapter_count}",
        f"- **片段**：{preview.segment_count:,}",
        f"- **角色**：{preview.role_count}",
        f"- **旁白**：{narrator}",
        f"- **警告**：{len(preview.warnings)}",
        f"- **错误**：{len(preview.errors)}",
    ]
    if preview.unknown_roles:
        lines.append(
            "- **未知角色**：" + "、".join(html.escape(role) for role in preview.unknown_roles)
        )
    if preview.warnings:
        lines.extend(["", "#### 可继续创建的警告"])
        lines.extend(f"- {html.escape(item)}" for item in preview.warnings)
    if preview.errors:
        lines.extend(["", "#### 必须修复的错误"])
        lines.extend(f"- {html.escape(item)}" for item in preview.errors)
    return "\n".join(lines)


def inspect_json(json_file, project_name: str = "") -> tuple[str, str, dict, dict]:
    """Inspect JSON and return preview, slot status, cleanup visibility, create state."""
    source = _file_value_path(json_file)
    name = str(project_name or "").strip()
    if not source or not os.path.isfile(source):
        return (
            "### 等待导入\n请上传外部 Agent 生成的 `structured_script.json`。",
            "⚪ 尚未选择 JSON 文件",
            _update(visible=False),
            _update(interactive=False),
        )
    try:
        preview = StructuredScriptImportService.inspect(source, name or None)
    except (OSError, ValueError, TypeError) as exc:
        return (
            f"### ❌ JSON 检查失败\n{html.escape(str(exc))}",
            "⚪ 无法检查项目槽位",
            _update(visible=False),
            _update(interactive=False),
        )
    slot = preview.slot
    can_create = preview.valid and slot is not None and slot.status == "available" and bool(name)
    cleanup_visible = bool(slot and slot.status in {"incomplete", "corrupted", "temporary"})
    if not name:
        slot_text = "⚪ 请确认项目名称后检查槽位"
    else:
        slot_text = _slot_status_text(slot)
    return (
        format_json_preview(preview),
        slot_text,
        _update(visible=cleanup_visible),
        _update(interactive=can_create),
    )


def inspect_project_name(project_name: str) -> tuple[str, dict]:
    """Keep an immediate name-only slot check for keyboard edits."""
    name = str(project_name or "").strip()
    if not name:
        return "⚪ 请输入项目名称", _update(visible=False)
    try:
        inspection = ProjectRepository.inspect_project_slot(name)
    except ValueError as exc:
        return f"❌ 名称不可用：{html.escape(str(exc))}", _update(visible=False)
    return _slot_status_text(inspection), _update(
        visible=inspection.status in {"incomplete", "corrupted", "temporary"}
    )


def archive_orphan_and_recheck(project_name: str) -> tuple[str, dict]:
    """Archive only an explicitly selected orphan; never delete valid projects."""
    try:
        archived = ProjectRepository.archive_orphan_project(project_name)
        return (
            ("✅ 残留目录已移动到回收站，名称现已可用。"
             f"\n\n归档位置：`{html.escape(archived)}`"),
            _update(visible=False),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 无法归档残留：{html.escape(str(exc))}", _update(visible=True)


def format_creation_warnings(warnings: list[str], limit: int = 10) -> str:
    if not warnings:
        return ""
    safe = [html.escape(str(item)) for item in warnings[:limit]]
    lines = [f"\n\n#### 导入警告：共 {len(warnings)} 项 warning", *(f"- {item}" for item in safe)]
    hidden = len(warnings) - len(safe)
    if hidden:
        lines.append(f"另有 {hidden} 条未展示。")
    return "\n".join(lines)


def create_from_json(project_name, json_file, ss=None) -> tuple[str, bool]:
    """Create one V3 project and report ``(message, creation_success)``."""
    source = _file_value_path(json_file)
    name = str(project_name or "").strip()
    if not name:
        return "### ⚠ 请输入项目名称", False
    if not source or not os.path.isfile(source):
        return "### ⚠ 请上传 structured_script.json", False
    try:
        result = StructuredScriptImportService.create(name, source)
        if ss is not None:
            from services import ProjectService

            snapshot = ProjectService.open_project_as_snapshot(result.project_name)
            ss.apply_project_snapshot(snapshot, project=result.project_name)
        message = (
            "### ✅ 项目创建成功\n\n"
            f"- **项目名称**：`{html.escape(result.project_name)}`\n"
            f"- **作品**：{html.escape(result.title)}\n"
            f"- **章节**：{result.chapter_count}\n"
            f"- **片段**：{result.segment_count:,}\n"
            f"- **角色**：{result.role_count}\n"
            "\n**下一步**：前往「角色与声音」完成声音绑定。"
            + format_creation_warnings(result.warnings)
        )
        return message, True
    except ValueError as exc:
        return f"### ❌ 创建失败\n{html.escape(str(exc))}", False
    except Exception as exc:  # pragma: no cover - final UI safety net
        logger.exception("JSON 项目创建失败")
        return f"### ❌ 创建异常\n{html.escape(str(exc)[:800])}", False


def require_creation_success(creation_success: bool) -> None:
    """Stop the dependent success chain when creation did not succeed."""
    if not creation_success:
        import gradio as gr

        raise gr.Error("项目创建未成功；请留在当前页面修正错误后重试。")


__all__ = [
    "archive_orphan_and_recheck",
    "create_from_json",
    "derive_json_project_name",
    "format_json_preview",
    "inspect_json",
    "inspect_project_name",
    "require_creation_success",
]
