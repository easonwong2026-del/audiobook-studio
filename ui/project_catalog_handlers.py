"""项目书架 handler：搜索、选择隔离、管理动作与统一刷新（UI 层）。

UI 层模块（允许 import gradio，与服务层隔离纪律一致）：磁盘操作一律走
Service 层（``ProjectStorageService`` / ``ProjectBackupService`` /
``ProjectCatalogService``），UI 层禁止直接 shutil/os 操作项目资产。

核心不变式：
- 书架点选只写 ``ss.selected_project``，**绝不动** ``ss.project``、不加载剧本；
- 各管理动作 handler 收显式 ``project_name`` 参数，不再从 ``ss.project`` 读取；
- ``refresh_project_catalog`` 是目录类组件全量刷新的唯一出口。
"""
from __future__ import annotations

import html
import logging
from typing import Any

import gradio as gr

from lib import dataframe_style as df_style
from services import ProjectBackupService, ProjectStorageService
from services.project_catalog import ProjectCatalogService

logger = logging.getLogger(__name__)

# 由 app.py 注入（避免 handler 模块反向 import app 造成循环依赖）。
_OPEN_PROJECT_CALLBACK = None

_BOOKSHELF_HINT = (
    "从书架选择项目后，可对选中项目执行管理操作；「打开项目」才会进入工作流。"
)


def _update(**kwargs: Any) -> Any:
    """构造 ``gr.update``（保持与 create_project_handlers 同名的轻量出口）。"""
    return gr.update(**kwargs)


def bind_open_project(callback) -> None:
    """供 app.py 注入 ``open_project``（书架「打开项目」唯一打开入口）。"""
    global _OPEN_PROJECT_CALLBACK
    _OPEN_PROJECT_CALLBACK = callback


def _empty_open_outputs() -> tuple:
    """打开项目空态返回（与 app.open_project 空项目分支同契约，7 元组）。"""
    return (
        "📖 等待打开项目",
        _update(choices=[], value=None),
        None,
        "### 当前角色配置\n请从左侧角色列表选择角色。",
        _update(choices=[]),
        "",
        "打开项目后显示角色绑定状态。",
    )


# ── 书架行渲染 ──


def render_bookshelf_rows(search_query: str = "") -> dict:
    """渲染书架 Dataframe（着色契约 dict，列：项目|章|段进度|状态）。"""
    projects = ProjectCatalogService.search_projects(search_query or "")
    rows = [
        [p.project_name, p.chapters, f"{p.completed}/{p.segments}", p.status]
        for p in projects
    ]
    return df_style.style_dataframe(
        rows,
        df_style.BOOKSHELF_HEADERS,
        status_col=3,
        status_color_map=df_style.STATUS_WORD_COLORS,
    )


def apply_project_search(query: str, ss=None) -> tuple[dict, str, dict]:
    """搜索 → 渲染书架行 + 同步选中状态（含清除被过滤掉的 selected）。

    Args:
        query: 搜索关键词。
        ss: 会话状态（可为 None，纯渲染用）。

    Returns:
        ``(书架行 update, 选中信息 Markdown, 选中项目 State 复位 update)`` 三元组。

    选中项目如果被过滤出结果，则同步清空 ``ss.selected_project`` 与 UI 选中
    态，避免「搜索后书架看不到 A，但动作仍作用于 A」的幽灵状态；若选中项目
    仍在结果中则保留（不做无谓清除）。
    """
    styled = render_bookshelf_rows(query)
    if ss is not None:
        # 搜索 query 单一状态来源：导航离开/返回后仍保持过滤
        ss.set_catalog_query(query)
    visible = {row[0] for row in (styled.get("data") or [])}
    selected = ss.selected_project if ss is not None else None
    if selected and selected not in visible:
        if ss is not None:
            ss.clear_selected()
        return styled, _BOOKSHELF_HINT, _update(value="")
    if selected:
        info = _selected_info(selected)
        return styled, info, _update(value=selected)
    return styled, _BOOKSHELF_HINT, _update(value="")


def _selected_info(name: str) -> str:
    """渲染选中项目信息 Markdown（失败时降级为可管理提示）。"""
    summary = ProjectCatalogService.get_summary(name)
    if summary is None:
        return (
            f"已选择项目：**`{html.escape(name)}`**\n"
            "\n（项目摘要读取失败，仍可执行管理操作。）"
        )
    return (
        f"已选择项目：**`{html.escape(name)}`**\n"
        f"- 书名：{html.escape(summary.title)}\n"
        f"- 作者：{html.escape(summary.author)}\n"
        f"- 章：{summary.chapters} · 段：{summary.segments} · "
        f"已完成：{summary.completed} · 失败：{summary.failed}\n"
        f"- 状态：{summary.status}"
    )


def select_bookshelf_row(rows, ss, evt: gr.SelectData) -> tuple[str, str, dict]:
    """书架 select → 只设 ``ss.selected_project``，绝不动 ``ss.project``。

    Args:
        rows: 书架 Dataframe 值（dict 或二维列表）。
        ss: 会话状态（原地 mutate ``selected_project``）。
        evt: gradio SelectData（含 ``index``；由 gradio 自动追加为末参）。

    Returns:
        ``(选中项目名, 选中信息 Markdown, p_sel 下拉同步 update)`` 三元组。
    """
    if evt is None or evt.index is None:
        return "", _BOOKSHELF_HINT, _update()
    try:
        rows = rows["data"] if isinstance(rows, dict) else rows
        name = rows[evt.index[0]][0]
    except Exception:
        return "", _BOOKSHELF_HINT, _update()
    name = str(name or "")
    if not name:
        return "", _BOOKSHELF_HINT, _update()
    ss.set_selected(name)
    return name, _selected_info(name), _update(value=name)


# ── 打开项目 / 目录 ──


def open_selected_project(selected: str, ss) -> tuple:
    """点「打开项目」才打开：校验选中项后委托 app 的 ``open_project``。

    selected 来自书架选中 State；本函数**不**从 ``ss.project`` 读取，
    仅当显式点击「打开项目」才进入打开流程。
    """
    name = str(selected or "")
    if not name:
        return _empty_open_outputs()
    if _OPEN_PROJECT_CALLBACK is None:
        logger.warning("open_project 回调未注入，无法打开项目：%s", name)
        return _empty_open_outputs()
    try:
        return _OPEN_PROJECT_CALLBACK(name, ss)
    except Exception as exc:
        logger.warning("打开项目失败 %s: %s", name, exc)
        return _empty_open_outputs()


def open_selected_directory(project_name: str, key: str = "") -> str:
    """打开选中项目目录或逻辑子目录（不要求项目已打开；走 procutil 无黑框）。

    Args:
        project_name: 选中项目名。
        key: 逻辑目录 key（``""`` = 项目根；``segments`` = 生成音频；
            ``delivery_official`` = 导出成品）。
    """
    if not project_name:
        return "⚪ 请先从书架选择项目。"
    _ok, message = ProjectStorageService.open_directory(project_name, key)
    return ("✅ " if _ok else "❌ ") + message


def open_selected_generated_audio(project_name: str) -> str:
    """打开选中项目的生成音频目录（v3 → 02_生成音频/分段音频）。"""
    return open_selected_directory(project_name, "segments")


def open_selected_deliveries(project_name: str) -> str:
    """打开选中项目的导出成品目录（v3 → 03_导出成品/正式导出）。"""
    return open_selected_directory(project_name, "delivery_official")


# ── 备份 ──


def create_selected_backup(project_name: str, target_dir: str = "") -> str:
    """为选中项目创建备份（收 project_name，不读 ``ss.project``）。"""
    if not project_name:
        return "⚪ 请先从书架选择项目。"
    try:
        path = ProjectBackupService.create_backup(project_name, target_dir or None)
        return f"✅ 项目备份已创建：`{path}`"
    except Exception as exc:
        return f"❌ 创建备份失败：{exc}"


# ── 清理缓存（扫描 → token 确认两步） ──


def scan_selected_cleanup(project_name: str) -> tuple[str, str, dict]:
    """扫描选中项目清理候选（不要求打开），返回 (预览, token, 确认按钮可见性)。"""
    if not project_name:
        return "⚪ 请先从书架选择项目。", "", _update(visible=False)
    try:
        plan = ProjectStorageService.scan_cleanup(project_name)
        if not plan["candidates"]:
            return (
                "✅ 当前没有可安全清理的缓存或临时文件。\n\n"
                "不会删除 structured_script.json、原始文件、有效音频或导出文件。",
                plan["token"],
                _update(visible=False),
            )
        from collections import Counter

        categories = Counter(item["reason"] for item in plan["candidates"])
        lines = [
            f"### 预计可释放 {_format_size(plan['total_bytes'])}",
            f"共 {len(plan['candidates'])} 个文件，确认后才会删除。",
            "",
        ]
        lines.extend(
            f"- **{reason}**：{count} 个" for reason, count in categories.items()
        )
        lines.extend([
            "",
            "**不会删除**：structured_script.json、原始文件、已生成有效音频、"
            "用户手工音频和导出文件。",
        ])
        lines.append("")
        lines.extend(
            f"- `{item['relative_path']}`：{item['reason']}"
            for item in plan["candidates"][:30]
        )
        if len(plan["candidates"]) > 30:
            lines.append(
                f"- … 其余 {len(plan['candidates']) - 30} 项已纳入同一确认令牌"
            )
        return "\n".join(lines), plan["token"], _update(visible=True)
    except Exception as exc:
        return f"❌ 扫描失败：{exc}", "", _update(visible=False)


def execute_selected_cleanup(project_name: str, token: str) -> tuple[str, str, dict]:
    """执行选中项目清理（token 确认后）。"""
    if not project_name:
        return "⚪ 请先从书架选择项目。", "", _update(visible=False)
    try:
        result = ProjectStorageService.execute_cleanup(project_name, token)
        if result.get("stale"):
            plan = result.get("plan", {})
            if plan.get("candidates"):
                return (
                    "⚠ 文件在确认前发生了变化，已重新扫描。请重新确认这次清理。",
                    plan.get("token", ""),
                    _update(visible=True),
                )
            return "✅ 文件已发生变化，当前没有可安全清理的内容。", "", _update(visible=False)
        return (
            f"✅ 已清理 {result['removed_files']} 个安全文件，"
            f"释放 {_format_size(result['removed_bytes'])}。",
            "",
            _update(visible=False),
        )
    except Exception as exc:
        return f"❌ 执行清理失败：{exc}", "", _update(visible=False)


def cancel_selected_cleanup() -> tuple[str, str, dict]:
    """取消清理：项目文件没有改变。"""
    return "已取消清理。项目文件没有改变。", "", _update(visible=False)


# ── 存储布局整理（扫描方案 → token 确认两步；v1/v2 → v3 显式迁移） ──


def _layout_label(version: int) -> str:
    return {1: "v1（旧版英文布局）", 2: "v2（中文 canonical 布局）", 3: "v3（新版布局）"}.get(
        int(version), f"v{version}"
    )


def scan_selected_storage_upgrade(project_name: str) -> tuple[str, str, dict]:
    """扫描选中项目的存储布局整理方案（只读），返回 (预览, token, 确认按钮可见性)。

    仅 v1/v2 项目返回可执行方案；v3 项目显示已是最新版。打开项目不会自动迁移。
    """
    if not project_name:
        return "⚪ 请先从书架选择项目。", "", _update(visible=False)
    try:
        plan = ProjectStorageService.plan_storage_upgrade(project_name)
        if plan.get("code") == "ALREADY_CURRENT":
            return (
                f"✅ 项目已是 **{_layout_label(3)}**，无需整理。\n\n"
                "打开项目不会自动迁移；只有 v1/v2 项目才需要显式整理。",
                "",
                _update(visible=False),
            )
        if plan.get("blockers"):
            lines = [
                f"### 项目存在活动任务，暂不能整理（{_layout_label(plan['from_version'])} → v3）",
            ]
            lines.extend(
                f"- **{item['code']}**：{item['message']}"
                for item in plan["blockers"][:10]
            )
            lines.append("\n请先停止相关任务后再整理。")
            return "\n".join(lines), "", _update(visible=False)
        from services.project_storage import format_size

        lines = [
            f"### 存储布局整理方案（{_layout_label(plan['from_version'])} → **v3**）",
            f"- 将整理 **{plan['file_count']}** 个文件，共 **{format_size(plan['total_bytes'])}**。",
            "- 整理前会自动创建完整备份（备份路径见执行结果，永不自动删除）。",
            "- 项目根目录将只保留 4 个一级目录：`01_原始资料/ 02_生成音频/ 03_导出成品/ 99_系统数据/`。",
        ]
        if plan.get("conflicts"):
            lines.append("")
            lines.append("**⚠ 目标目录已存在内容（将合并保留，不覆盖）：**")
            lines.extend(
                f"- `{item['target']}`（{'非空' if item['non_empty'] else '已存在'}）"
                for item in plan["conflicts"][:10]
            )
        if plan.get("unknown_paths"):
            lines.append("")
            lines.append("**📁 无法识别的根级内容（将原样保留到 99_系统数据/迁移保留/，不删除）：**")
            lines.extend(
                f"- `{item['path']}`（{item['kind']}）"
                for item in plan["unknown_paths"][:15]
            )
            if len(plan["unknown_paths"]) > 15:
                lines.append(f"- … 其余 {len(plan['unknown_paths']) - 15} 项")
        records = plan.get("relative_path_records") or []
        if records:
            lines.append("")
            lines.append(f"**将同步重写 {sum(item['count'] for item in records)} 处历史路径记录**（resolver 兜底双保险）。")
        lines.extend([
            "",
            "整理是不可逆的显式操作，但整理前会先创建完整备份；确认后执行。",
        ])
        return "\n".join(lines), plan.get("token", ""), _update(visible=True)
    except Exception as exc:
        return f"❌ 扫描整理方案失败：{exc}", "", _update(visible=False)


def execute_selected_storage_upgrade(project_name: str, token: str) -> tuple[str, str, dict]:
    """执行选中项目存储布局整理（token 确认后）。"""
    if not project_name:
        return "⚪ 请先从书架选择项目。", "", _update(visible=False)
    if not token:
        return "⚪ 请先扫描整理方案再确认。", "", _update(visible=False)
    try:
        result = ProjectStorageService.upgrade_storage(project_name, token)
        from services.project_storage import format_size

        lines = [
            "✅ 项目已整理为 **v3** 存储布局。",
            f"- 整理文件：{result.get('file_count', 0)} 个（{format_size(result.get('total_bytes', 0))}）。",
            f"- 重写历史路径记录：{sum(item.get('count', 0) for item in result.get('relative_path_records', []) or [])} 处。",
            f"- 备份：`{result.get('backup_path')}`（永不自动删除）。",
        ]
        if result.get("unknown_paths"):
            lines.append(
                f"- 保留无法识别的根级内容：{len(result['unknown_paths'])} 项（99_系统数据/迁移保留/）。"
            )
        return "\n".join(lines), "", _update(visible=False)
    except Exception as exc:
        if getattr(exc, "code", None) == "PROJECT_HAS_ACTIVE_PRODUCTION":
            message = "项目正在生产，请先停止任务后再整理"
        else:
            message = f"❌ 整理失败：{exc}"
        return message, "", _update(visible=False)


def cancel_selected_storage_upgrade() -> tuple[str, str, dict]:
    """取消整理：项目文件没有改变。"""
    return "已取消整理。项目文件没有改变。", "", _update(visible=False)


# ── 诊断与修复 ──


def check_selected_integrity(project_name: str) -> tuple[str, dict]:
    """诊断选中项目（不要求打开）。"""
    if not project_name:
        return "⚪ 请先从书架选择项目。", _update(visible=False)
    try:
        report = ProjectStorageService.check_integrity(project_name)
        if report["ok"]:
            return "✅ 项目正常，未发现需要处理的问题。", _update(visible=False)
        repairable = sum(1 for issue in report["issues"] if issue.get("repairable"))
        manual = report["issue_count"] - repairable
        lines = [
            f"### 项目存在 {report['issue_count']} 项问题",
            f"- 可自动安全修复：{repairable} 项",
            f"- 需要人工处理：{manual} 项",
        ]
        lines.extend(
            f"- **{issue['severity']} / {issue['code']}**：{issue['message']}"
            for issue in report["issues"][:30]
        )
        lines.extend([
            "",
            "安全修复不会修改 structured_script.json、正常音频、用户手工音频、"
            "角色绑定决定或有效导出成品。",
        ])
        return "\n".join(lines), _update(visible=bool(repairable))
    except Exception as exc:
        return f"❌ 完整性检查失败：{exc}", _update(visible=False)


def repair_selected_integrity(project_name: str) -> tuple[str, dict]:
    """修复选中项目（仅安全修复；不要求打开）。"""
    if not project_name:
        return "⚪ 请先从书架选择项目。", _update(visible=False)
    try:
        report = ProjectStorageService.repair_integrity(project_name)
        repaired = report.get("repaired", [])
        if report["ok"]:
            return (
                "✅ 已完成安全修复。"
                + (
                    "\n" + "\n".join(f"- {item}" for item in repaired)
                    if repaired
                    else ""
                ),
                _update(visible=False),
            )
        repairable = any(
            issue.get("repairable") for issue in report.get("issues", [])
        )
        return (
            f"⚠ 已修复 {len(repaired)} 项；仍有 {report['issue_count']} 项问题，"
            "请人工处理剩余项目。",
            _update(visible=repairable),
        )
    except Exception as exc:
        return f"❌ 修复失败：{exc}", _update(visible=False)


# ── 移入回收站（两步确认） ──


def archive_selected(
    project_name: str, confirmed_project: str, ss
) -> tuple[str, dict, dict, dict]:
    """移入回收站（两步确认，确认态**绑定项目名**）。

    第一次点击（``confirmed_project != project_name``）→ 显示「确认将项目移入
    回收站？」，并把确认态记录为**当前项目名**；只有
    ``confirmed_project == project_name`` 才真正调用
    ``ProjectStorageService.archive``。确认态绑定项目名后，即使用户改选其他
    项目，旧确认态只对旧项目名生效，**绝不会误归档新选项目**（QA 缺陷修复）。
    若 ``ss.project == project_name`` → 安全 reset session（复用 delete_project
    既有逻辑）后再归档；active production 拦截由 archive 内部
    ``ensure_project_mutation_allowed`` 保证（P0）。

    选中态清理语义（本窗口状态一致性修复）：
    - 第一次确认点击 / 被 guard 阻止 → **保留**选中态（不清任何 selection）；
    - 归档成功 → 清 ``ss.selected_project`` + ``bookshelf_selected_proj`` State
      + 选中信息 Markdown（若被归档项目正是当前 opened，同时 reset 整个
      session，见 C 场景）。

    Args:
        project_name: 选中项目名（显式传入，不读 ``ss.project``）。
        confirmed_project: 已确认的项目名（State 值，字符串语义；``""`` 表示
            尚未确认，与 ``project_name`` 不一致时要求重新确认）。
        ss: 会话状态（可能为 None）。

    Returns:
        ``(消息, 确认状态 update, 选中项目 State update, 选中信息 Markdown update)``
        四元组。
    """
    noop = _update()
    if not project_name:
        return "⚪ 请先从书架选择项目。", _update(value=""), noop, noop
    if str(confirmed_project or "") != project_name:
        return (
            f"⚠ 确认将「{html.escape(project_name)}」移入回收站？"
            "回收站内可恢复；再次点击「移入回收站」执行。",
            _update(value=project_name),
            noop,
            noop,
        )
    try:
        target = ProjectStorageService.archive(project_name)
        if ss is not None:
            if ss.project == project_name:
                ss.set_project(None, None, {})
                ss.set_snapshot(None)
                ss.synthesis = None
                message = f"✅ 项目已移入回收站，可从 `{_dirname(target)}` 恢复。"
            else:
                message = f"✅ 项目已移入回收站：`{target}`"
            # 归档成功：无论是否 opened，selected 一律清空（A 不再可被动作指向）
            ss.clear_selected()
        else:
            message = f"✅ 项目已移入回收站：`{target}`"
        return (
            message,
            _update(value=""),
            _update(value=""),
            _update(value=_BOOKSHELF_HINT),
        )
    except Exception as exc:
        if getattr(exc, "code", None) == "PROJECT_HAS_ACTIVE_PRODUCTION":
            message = "项目正在生产，请先停止任务后再移入回收站"
        else:
            message = f"❌ 归档项目失败：{exc}"
        # guard 阻止：仅复位确认态，selection 一律保留
        return message, _update(value=""), noop, noop


# ── 全局：从备份恢复 ──


def restore_backup_global(archive_path) -> str:
    """从备份恢复（全局，不要求打开项目；成功后统一刷新书架）。"""
    if not archive_path:
        return "⚪ 请选择项目备份 ZIP。"
    try:
        path = ProjectBackupService.restore_backup(archive_path)
        return f"✅ 项目备份已恢复到：`{path}`；书架已刷新。"
    except Exception as exc:
        return f"❌ 恢复备份失败：{exc}"


# ── 全局：回收站 ──


def render_archived_projects() -> tuple[list, dict, str]:
    """渲染回收站表 + 下拉 + 状态文本（复用既有回收站逻辑）。"""
    from datetime import datetime

    from services.project_storage import format_size

    archived = ProjectStorageService.list_archived()
    rows: list[list] = []
    choices: list[tuple[str, str]] = []
    for item in archived:
        timestamp = item.get("archived_at")
        archived_at = (
            datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            if timestamp
            else "未知"
        )
        rows.append([
            item.get("original_name", ""),
            archived_at,
            format_size(item.get("storage_bytes", 0)),
            item.get("archive_id", ""),
        ])
        choices.append((
            f"{item.get('original_name', '')} · {archived_at}",
            item.get("archive_id", ""),
        ))
    status = "回收站为空。" if not rows else f"回收站共有 {len(rows)} 个项目。"
    return rows, _update(choices=choices, value=None), status


def refresh_archived_projects_global() -> tuple[list, dict, str]:
    """刷新回收站（表 + 下拉 + 状态）。"""
    return render_archived_projects()


def restore_archived_global(archive_id: str) -> str:
    """恢复回收站项目（拒绝重名覆盖由 Service 保证）；成功后统一刷新。"""
    if not archive_id:
        return "⚪ 请先选择回收站项目。"
    try:
        result = ProjectStorageService.restore_archived(archive_id)
        name = result["project_name"]
        return f"✅ 已恢复「{name}」，完整性检查通过；可在书架中打开。"
    except Exception as exc:
        return f"❌ 恢复失败：{exc}"


def permanently_delete_archived_global(archive_id: str, confirmed: bool) -> str:
    """永久删除回收站项目（checkbox 二次确认）。"""
    if not confirmed:
        return "⚠ 永久删除前请勾选二次确认。"
    if not archive_id:
        return "⚪ 请先选择回收站项目。"
    try:
        ProjectStorageService.permanently_delete_archived(archive_id)
        return "✅ 已永久删除回收站项目。"
    except Exception as exc:
        return f"❌ 永久删除失败：{exc}"


# ── 统一刷新出口 ──


def refresh_project_catalog(search_query: str = "", p_sel_value: str = "") -> tuple:
    """目录类组件全量刷新唯一出口。

    Args:
        search_query: 当前书架搜索词（保持过滤状态，不因刷新突然变回全部）。
        p_sel_value: 当前项目下拉选中值；若已不在新 catalog 中（如被归档）
            则同步清空，避免「下拉还指着已归档项目」的幽灵状态。

    Returns:
        固定 5 元组契约：
        ``(书架行 gr.update, p_sel choices gr.update[同一 catalog 生成],
            回收站表 rows, 回收站下拉 gr.update, 回收站状态文本)``
    """
    bookshelf = render_bookshelf_rows(search_query)
    choices = [s.project_name for s in ProjectCatalogService.scan()]
    value = str(p_sel_value or "") if str(p_sel_value or "") in choices else None
    rows, trash_choices, status = render_archived_projects()
    return bookshelf, _update(choices=choices, value=value), rows, trash_choices, status


def _format_size(value: int) -> str:
    """格式化字节数（避免与 handler 内散落的 format_size 重复）。"""
    from services.project_storage import format_size

    return format_size(value)


def _dirname(path: str) -> str:
    """返回目录名（消息展示用，不暴露完整路径细节）。"""
    import os

    return os.path.dirname(os.path.normpath(path))


__all__ = [
    "apply_project_search",
    "archive_selected",
    "bind_open_project",
    "cancel_selected_cleanup",
    "cancel_selected_storage_upgrade",
    "check_selected_integrity",
    "create_selected_backup",
    "execute_selected_cleanup",
    "execute_selected_storage_upgrade",
    "open_selected_deliveries",
    "open_selected_directory",
    "open_selected_generated_audio",
    "open_selected_project",
    "permanently_delete_archived_global",
    "refresh_archived_projects_global",
    "refresh_project_catalog",
    "render_bookshelf_rows",
    "repair_selected_integrity",
    "restore_archived_global",
    "restore_backup_global",
    "scan_selected_cleanup",
    "scan_selected_storage_upgrade",
    "select_bookshelf_row",
]
