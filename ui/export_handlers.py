"""Formal Export UI callbacks and durable task observers."""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any

import gradio as gr

from lib import project_paths
from services import ExportService, ProjectService, QualityService, WorkflowService
from ui import file_component_paths

logger = logging.getLogger(__name__)


_EXPORT_ACTIVE_STATUSES = frozenset({
    "pending", "running", "cancelling", "pausing", "paused", "recovering",
})
_EXPORT_TERMINAL_STATUSES = frozenset({
    "done", "error", "cancelled", "interrupted", "needs_attention",
})
_EXPORT_STATUS_LABELS = {
    "pending": "等待导出",
    "running": "正在导出",
    "cancelling": "正在取消",
    "pausing": "正在暂停",
    "paused": "已暂停，等待恢复",
    "recovering": "正在恢复",
}


def _remember_export_ui_state(ss, task_id: str, output_dir: str, project: str = ""):
    """Keep only the UI tracking pointer on the per-session state object.

    The durable task repository remains authoritative; these fields only let
    the existing five-input Export click guard the task it already tracks.
    """
    if ss is None:
        return
    ss._export_ui_task_id = str(task_id or "")
    ss._export_ui_output_dir = str(output_dir or "")
    ss._export_ui_project = str(project or getattr(ss, "project", None) or "")


def _export_ui_reset(message: str, *, task_id: str = "", output_dir: str = ""):
    """Return the complete Export UI state and stop its polling timer."""
    return (
        None,
        message,
        task_id,
        output_dir,
        gr.update(interactive=False),
        gr.Timer(active=False),
        gr.update(interactive=True),
    )


def _export_ui_noop():
    """Do not let a stale callback overwrite a newer project's UI state."""
    return (gr.skip(),) * 7


def _export_ui_callback_is_current(ss, project: str, task_id: str) -> bool:
    """Return whether an in-flight callback still owns the session pointer."""
    current_project = str(getattr(ss, "project", None) or "") if ss else ""
    if current_project != project:
        return False
    if not task_id:
        return True
    current_task_id = str(getattr(ss, "_export_ui_task_id", "") or "") if ss else ""
    return current_task_id == task_id


def _resolve_export_ui_artifact(
    project_name: str,
    task_id: str,
    task: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Resolve the primary ready artifact from the durable delivery manifest.

    The task row tells us which manifest belongs to this export.  The manifest
    is then re-read from persistent history and its relative path is resolved
    through ``project_paths``.  A task status of ``done`` alone is never enough
    to make the UI claim that a file is ready.
    """
    manifest_id = str(task.get("manifest_id") or task_id or "")
    if not manifest_id:
        return None, "任务没有关联的最终 manifest。"
    try:
        manifest = ExportService.get_delivery_manifest(project_name, manifest_id)
    except Exception as exc:  # noqa: BLE001 - UI must render persistent-read errors
        return None, f"最终 manifest 读取失败：{exc}"
    if not manifest or manifest.get("ready") is not True:
        return None, "最终 manifest 尚未 ready。"
    if str(manifest.get("export_id") or task_id) != str(task_id):
        return None, "最终 manifest 与当前导出任务不匹配。"
    outputs = manifest.get("outputs") or []
    primary = next(
        (item for item in outputs if isinstance(item, dict) and item.get("relative_path")),
        None,
    )
    if not primary:
        return None, "最终 manifest 没有可用 artifact。"
    relative_path = str(primary.get("relative_path") or "")
    try:
        project_dir = ProjectService.get_project_dir(project_name)
        artifact_path = project_paths.resolve_relative(project_dir, relative_path)
    except (OSError, KeyError, ValueError) as exc:
        return None, f"最终 artifact 路径无法解析：{exc}"
    try:
        if not os.path.isfile(artifact_path) or os.path.getsize(artifact_path) <= 0:
            return None, "最终 artifact 尚未发布或文件为空。"
    except OSError as exc:
        return None, f"最终 artifact 尚未发布：{exc}"
    return {
        "path": os.path.normpath(artifact_path),
        "filename": os.path.basename(artifact_path),
        "manifest": manifest,
        "output": primary,
    }, ""


def _copy_export_ui_artifact(artifact_path: str, output_dir: str) -> tuple[str, str]:
    """Preserve the existing optional user copy without changing the backend artifact."""
    requested = str(output_dir or "").strip()
    if not requested:
        return artifact_path, ""
    try:
        destination_dir = os.path.abspath(os.path.expanduser(requested))
        os.makedirs(destination_dir, exist_ok=True)
        destination = os.path.join(destination_dir, os.path.basename(artifact_path))
        if os.path.abspath(destination) != os.path.abspath(artifact_path):
            shutil.copy2(artifact_path, destination)
        return destination, ""
    except (OSError, shutil.Error) as exc:
        # The durable official artifact is still valid.  Keep success truthful
        # while making the optional copy failure visible to the user.
        return artifact_path, f"另存到指定位置失败：{exc}"


def _export_ui_values(task_id: str, output_dir: str, ss, *, allow_new_task: bool = False):
    """Read one durable export task and render all Export-only UI outputs."""
    identifier = str(task_id or "").strip()
    requested_dir = str(output_dir or "").strip()
    session_project = str(getattr(ss, "project", None) or "") if ss else ""
    tracked_project = str(getattr(ss, "_export_ui_project", "") or "") if ss else ""
    tracked_task_id = str(getattr(ss, "_export_ui_task_id", "") or "") if ss else ""
    if not allow_new_task and session_project and tracked_project and tracked_project != session_project:
        _remember_export_ui_state(ss, "", "", session_project)
        return _export_ui_reset(
            "当前项目没有已提交的导出任务。",
            output_dir="",
        )
    if (
        not allow_new_task
        and session_project
        and tracked_project == session_project
        and identifier != tracked_task_id
    ):
        # A timer callback may still carry the previous component value after
        # the project-open reconciliation has already cleared the pointer.
        return _export_ui_noop()
    _remember_export_ui_state(ss, identifier, requested_dir, session_project)
    if not identifier:
        return _export_ui_reset("当前没有已提交的导出任务。", output_dir=requested_dir)
    try:
        task = ExportService.get_export_task(
            getattr(ss, "project", None) if ss else "",
            identifier,
        )
    except Exception as exc:  # noqa: BLE001 - UI must surface any read failure
        if not _export_ui_callback_is_current(ss, session_project, identifier):
            return _export_ui_noop()
        return _export_ui_reset(
            f"❌ 导出状态读取失败：{exc}",
            task_id=identifier,
            output_dir=requested_dir,
        )

    if not _export_ui_callback_is_current(ss, session_project, identifier):
        return _export_ui_noop()

    status = str(task.get("status") or "unknown").lower()
    if status in _EXPORT_ACTIVE_STATUSES:
        label = _EXPORT_STATUS_LABELS.get(status, status)
        return (
            None,
            f"⏳ 导出状态：{label}\n任务 ID：{identifier}",
            identifier,
            requested_dir,
            gr.update(interactive=False),
            gr.Timer(active=True),
            gr.update(interactive=False),
        )

    project_name = str(task.get("project") or session_project or "")
    if session_project and project_name and project_name != session_project:
        _remember_export_ui_state(ss, "", "", session_project)
        return _export_ui_reset(
            "当前项目没有已提交的导出任务。",
            output_dir="",
        )
    _remember_export_ui_state(ss, identifier, requested_dir, project_name)
    if status == "done":
        artifact, reason = _resolve_export_ui_artifact(project_name, identifier, task)
        if not _export_ui_callback_is_current(ss, session_project, identifier):
            return _export_ui_noop()
        if artifact is None:
            return _export_ui_reset(
                f"⚠ 导出任务已完成，但最终成品尚未就绪。\n{reason}",
                task_id=identifier,
                output_dir=requested_dir,
            )
        artifact_path, copy_warning = _copy_export_ui_artifact(
            artifact["path"], requested_dir
        )
        if not _export_ui_callback_is_current(ss, session_project, identifier):
            return _export_ui_noop()
        lines = [
            "✅ 导出成功",
            f"文件：{os.path.basename(artifact_path)}",
            f"位置：{artifact_path}",
        ]
        if os.path.abspath(artifact_path) != os.path.abspath(artifact["path"]):
            lines.append(f"正式 artifact：{artifact['path']}")
        if copy_warning:
            lines.append(f"⚠ {copy_warning}")
        return (
            file_component_paths.safe_path_for_file_component(artifact_path),
            "\n".join(lines),
            identifier,
            requested_dir,
            gr.update(interactive=True),
            gr.Timer(active=False),
            gr.update(interactive=True),
        )

    if status == "cancelled":
        return _export_ui_reset(
            "🚫 导出已取消\n未生成最终成品。",
            task_id=identifier,
            output_dir=requested_dir,
        )
    if status == "error":
        error = task.get("error") if isinstance(task.get("error"), dict) else {}
        message = str(error.get("message") or "导出任务失败。")
        return _export_ui_reset(
            f"❌ 导出失败\n{message}",
            task_id=identifier,
            output_dir=requested_dir,
        )
    if status == "interrupted":
        return _export_ui_reset(
            "⚠ 导出已中断\n未生成最终成品，请重新发起导出。",
            task_id=identifier,
            output_dir=requested_dir,
        )
    if status == "needs_attention":
        return _export_ui_reset(
            "⚠ 导出需要处理\n未生成最终成品，请检查运行时状态后重试。",
            task_id=identifier,
            output_dir=requested_dir,
        )
    if status in _EXPORT_TERMINAL_STATUSES:
        return _export_ui_reset(
            f"⚠ 导出任务已结束（{status}），未生成最终成品。",
            task_id=identifier,
            output_dir=requested_dir,
        )

    # Unknown non-terminal states remain observable and are polled, but never
    # become a false success.
    return (
        None,
        f"⏳ 正在同步导出状态：{status}\n任务 ID：{identifier}",
        identifier,
        requested_dir,
        gr.update(interactive=False),
        gr.Timer(active=True),
        gr.update(interactive=False),
    )


def refresh_export_status(task_id: str, output_dir: str, ss):
    """Export-only timer callback backed by the durable task repository."""
    return _export_ui_values(task_id, output_dir, ss)


def reconcile_export_state(task_id: str, output_dir: str, ss):
    """Reconcile the complete Export UI boundary with the opened project.

    This callback is used by project-open and Export navigation chains.  It
    never adopts an arbitrary hidden task id: the hidden component state must
    agree with the session's tracked project/task pointer, otherwise the UI is
    explicitly cleared without touching durable export history.
    """
    session_project = str(getattr(ss, "project", None) or "") if ss else ""
    tracked_project = str(getattr(ss, "_export_ui_project", "") or "") if ss else ""
    tracked_task_id = str(getattr(ss, "_export_ui_task_id", "") or "") if ss else ""
    identifier = str(task_id or "").strip()
    if not session_project:
        _remember_export_ui_state(ss, "", "", "")
        return _export_ui_reset("当前项目没有已提交的导出任务。", output_dir="")
    if tracked_project != session_project or identifier != tracked_task_id:
        _remember_export_ui_state(ss, "", "", session_project)
        return _export_ui_reset(
            "当前项目没有已提交的导出任务。",
            output_dir="",
        )
    return _export_ui_values(task_id, output_dir, ss)


def open_export_location(task_id: str, output_dir: str, ss):
    """Open the directory containing the ready primary export artifact."""
    identifier = str(task_id or "").strip()
    if not identifier:
        return "⚪ 尚未完成任何导出，暂无可打开的位置。"
    try:
        task = ExportService.get_export_task(
            getattr(ss, "project", None) if ss else "",
            identifier,
        )
        if str(task.get("status") or "").lower() != "done":
            return "⚪ 导出尚未完成，暂时不能打开导出位置。"
        project_name = str(task.get("project") or getattr(ss, "project", None) or "")
        artifact, reason = _resolve_export_ui_artifact(project_name, identifier, task)
        if artifact is None:
            return f"⚠ 最终成品尚未就绪，不能打开位置：{reason}"
        target = artifact["path"]
        requested = str(output_dir or "").strip()
        if requested:
            candidate = os.path.join(
                os.path.abspath(os.path.expanduser(requested)),
                os.path.basename(target),
            )
            if os.path.isfile(candidate):
                target = candidate
        from lib.procutil import open_in_folder

        directory = os.path.dirname(target)
        if not open_in_folder(directory):
            return f"❌ 打开导出位置失败：{directory}"
        return f"✅ 已打开导出位置：{directory}"
    except Exception as exc:  # noqa: BLE001 - opening is a best-effort UI action
        return f"❌ 打开导出位置失败：{exc}"


def do_export(fmt, bitrate, output_dir, *args):
    """Start a durable export and immediately render its real task status."""
    qa_policy = "require_passed"
    ss = None
    active_task_id = ""
    active_output_dir = ""
    if len(args) >= 2:
        qa_policy, ss = args[0], args[1]
        if len(args) >= 3:
            active_task_id = str(args[2] or "").strip()
        if len(args) >= 4:
            active_output_dir = str(args[3] or "").strip()
    elif args:
        ss = args[0]
    requested_dir = str(output_dir or "").strip()
    if not ss or not ss.project:
        return _export_ui_reset("请先打开项目", output_dir=requested_dir)
    if not active_task_id:
        tracked_project = str(getattr(ss, "_export_ui_project", "") or "")
        if tracked_project == ss.project:
            active_task_id = str(getattr(ss, "_export_ui_task_id", "") or "").strip()
            active_output_dir = str(
                getattr(ss, "_export_ui_output_dir", "") or ""
            ).strip()
    try:
        # A second click while the current durable export is active must not
        # clear the UI's only tracking id after the backend rejects a second
        # export for the same project.
        if active_task_id:
            current = ExportService.get_export_task(ss.project, active_task_id)
            if str(current.get("status") or "").lower() in _EXPORT_ACTIVE_STATUSES:
                return _export_ui_values(
                    active_task_id,
                    active_output_dir or requested_dir,
                    ss,
                )
        result = ExportService.start_export(
            ss.project,
            fmt,
            bitrate=bitrate,
            qa_policy=str(qa_policy or "require_passed"),
        )
        export_id = str(result.get("task_id") or result.get("export_id") or "")
        if not export_id:
            return _export_ui_reset(
                "❌ 导出启动失败：服务没有返回 durable task_id。",
                output_dir=requested_dir,
            )
        # Re-read the durable task immediately.  This also handles an
        # idempotent replay that is already done without trusting a stale
        # local/UI result payload.
        return _export_ui_values(
            export_id,
            requested_dir,
            ss,
            allow_new_task=True,
        )
    except Exception as e:  # noqa: BLE001 - start errors must become UI state
        # The durable task may have become active between the guard read and
        # start_export().  Preserve that task instead of stopping its polling.
        candidate_task_id = active_task_id
        if not candidate_task_id:
            plan = getattr(e, "plan", None)
            blockers = plan.get("blockers", []) if isinstance(plan, dict) else []
            for blocker in blockers:
                if not isinstance(blocker, dict):
                    continue
                if (
                    str(blocker.get("code") or "") == "EXPORT_ACTIVE"
                    and str(blocker.get("status") or "").lower()
                    in _EXPORT_ACTIVE_STATUSES
                ):
                    candidate_task_id = str(blocker.get("task_id") or "").strip()
                    break
        if candidate_task_id:
            try:
                current = ExportService.get_export_task(ss.project, candidate_task_id)
                if str(current.get("status") or "").lower() in _EXPORT_ACTIVE_STATUSES:
                    return _export_ui_values(
                        candidate_task_id,
                        active_output_dir or requested_dir,
                        ss,
                        allow_new_task=True,
                    )
            except Exception as lookup_error:  # noqa: BLE001 - retain the original start error
                logger.debug("active export lookup after start failure failed: %s", lookup_error)
        return _export_ui_reset(
            f"❌ 导出启动失败：{e}",
            output_dir=requested_dir,
        )


def refresh_export_readiness(fmt, qa_policy, ss):
    """Render the formal delivery gate used by Web and MCP."""
    if not ss or not ss.project:
        return "#### 交付准备度\n请先打开项目。"
    try:
        plan = ExportService.plan_export(
            ss.project,
            fmt or "wav",
            qa_policy=qa_policy or "require_passed",
        )
        summary = plan.get("summary", {})
        metadata = summary.get("metadata", {})
        lines = [
            "#### 交付准备度",
            f"- 合成音频：{summary.get('active_revisions', 0)}/{summary.get('segments', 0)}",
            f"- 生产失败：{summary.get('failed_segments', 0)}",
            f"- 章节：{summary.get('chapters', 0)}",
            f"- FFmpeg：{'正常' if summary.get('ffmpeg_ready') else '不可用'}",
            f"- Metadata：{'正常' if metadata.get('title') else '缺少书名'}",
        ]
        exports = ExportService.list_exports(ss.project)
        if exports:
            latest_export = exports[0]
            lines.append(
                f"- Export：{latest_export.get('status', 'unknown')} · "
                f"{latest_export.get('export_id', '')}"
            )
        try:
            workflow = WorkflowService.get_state(ss.project)
            lines.append(
                "- Delivery："
                + ("current" if workflow["summary"].get("delivered") else "stale/missing")
            )
        except Exception:
            lines.append("- Delivery：状态暂不可用")
        if plan.get("ready"):
            lines.append("\n✅ 已满足当前 QA 策略，可以导出成品。")
        else:
            lines.append("\n**尚未就绪：**")
            lines.extend(
                f"- {item.get('message', item.get('code', '未知问题'))}"
                for item in plan.get("blockers", [])[:12]
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"#### 交付准备度\n❌ 检查失败：{exc}"


def do_export_subtitles(ss, sub_choice):
    """O1：生成字幕（srt / lrc），保持既有 Formal Export 行为。"""
    if not ss or not ss.project:
        return None, "请先打开项目"
    if not sub_choice or sub_choice == "none":
        return None, "未选择字幕格式"
    fmts = ("srt", "lrc") if sub_choice == "both" else (sub_choice,)
    try:
        report = QualityService.get_quality_report(ss.project)
        segment_paths = {}
        for item in report.get("segments", []):
            revision = item.get("audio_revision") or {}
            relative_path = str(revision.get("relative_path") or "")
            if relative_path:
                segment_paths[str(item.get("segment_id") or "")] = os.path.join(
                    ProjectService.get_project_dir(ss.project),
                    *relative_path.split("/"),
                )
        paths = ExportService.export_subtitles(
            ProjectService.get_project_dir(ss.project),
            formats=fmts,
            segment_paths=segment_paths,
            require_complete=True,
        )
        if not paths:
            return None, "未找到已合成段落，无法生成字幕（请先合成）"
        return paths, "字幕已生成"
    except Exception as e:
        return None, str(e)


def refresh_export_default_dir(ss):
    """显示当前项目的动态默认导出目录，避免用户猜路径。"""
    if not ss or not ss.project:
        return "项目默认目录：打开项目后显示。留空保存位置即可使用该目录。"
    try:
        project_dir = os.path.normpath(ProjectService.get_project_dir(ss.project))
        output_dir = os.path.normpath(project_paths.project_dir(project_dir, "exports"))
        return f"项目默认目录：`{output_dir}`\n留空保存位置即可导出到该目录。"
    except Exception as exc:
        logger.warning("读取默认导出目录失败: %s", exc)
        return "项目默认目录：暂时无法读取，请打开项目后重试。"
