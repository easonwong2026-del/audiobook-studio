"""项目书架事件接线：把 handler 接到概览页组件（仿 ui/wiring/settings_wiring.py）。

只负责注册 Gradio 事件；业务逻辑全部在 ``ui/project_catalog_handlers.py`` /
Service 层。``wire_project_catalog`` 必须在 ``gr.Blocks`` 内调用。
"""
from __future__ import annotations

from typing import Any

from ui import project_catalog_handlers as catalog_handlers


def wire_project_catalog(page: dict, deps: dict) -> None:
    """注册书架搜索、选择、管理动作、全局恢复/回收站事件。

    Args:
        page: 概览页组件字典（``create_overview_page()`` 的返回）。
        deps: 依赖字典，含：
            - ``session``: ``gr.State(SessionState())``；
            - ``project_sel``: 项目页 ``p_sel`` Dropdown（刷新 choices 用，
              兼容保留；书架 select 同步由 app.py 内联接线完成）；
            - ``catalog_outputs``: ``refresh_project_catalog`` 的 5 个输出组件；
            - ``callbacks``: app.py 注入的回调，可选键：
              ``open_project`` / ``open_project_outputs`` / ``open_chain_rest`` /
              ``goto_project`` / ``groups``；
            - ``groups``: 导航 ``_GROUPS``（供打开项目后切页）。
    """
    session = deps["session"]
    catalog_outputs = deps["catalog_outputs"]
    cb = deps.get("callbacks", {})
    groups = deps.get("groups", [])

    # ── 搜索 → 过滤书架行（同时清除选中项目 State，避免动作作用于隐藏旧选中） ──
    for event_name in ("change", "submit"):
        page["bookshelf_search"].__getattribute__(event_name)(
            catalog_handlers.apply_project_search,
            [page["bookshelf_search"]],
            [
                page["ov_bookshelf"],
                page["bookshelf_selected"],
                page["bookshelf_selected_proj"],
            ],
        )

    # 注：书架 select → 只设 ss.selected_project 的接线在 app.py 内联完成
    # （``ov_bookshelf.select(catalog_ui.select_bookshelf_row, ...)``），保持
    # app.py 中可见，避免重复注册同名事件。

    # ── 打开项目（唯一打开入口；需点按钮，选择≠打开） ──
    if "open_project" in cb and "open_project_outputs" in cb:
        chain = page["bookshelf_open"].click(
            catalog_handlers.open_selected_project,
            [page["bookshelf_selected_proj"], session],
            cb["open_project_outputs"],
        )
        if "open_chain_rest" in cb:
            chain = cb["open_chain_rest"](chain)
        goto = cb.get("goto_project")
        if goto is not None and groups:
            chain.then(goto, None, groups)

    # ── 打开目录 / 备份 / 清理 / 诊断 / 移入回收站 ──
    page["bookshelf_open_dir"].click(
        catalog_handlers.open_selected_directory,
        [page["bookshelf_selected_proj"]],
        [page["bookshelf_msg"]],
    )
    page["bookshelf_backup"].click(
        catalog_handlers.create_selected_backup,
        [page["bookshelf_selected_proj"], page["bookshelf_backup_dir"]],
        [page["bookshelf_msg"]],
    )
    page["bookshelf_cleanup"].click(
        catalog_handlers.scan_selected_cleanup,
        [page["bookshelf_selected_proj"]],
        [
            page["bookshelf_msg"],
            page["bookshelf_cleanup_token"],
            page["bookshelf_cleanup_confirm"],
        ],
    )
    page["bookshelf_cleanup_confirm"].click(
        catalog_handlers.execute_selected_cleanup,
        [page["bookshelf_selected_proj"], page["bookshelf_cleanup_token"]],
        [
            page["bookshelf_msg"],
            page["bookshelf_cleanup_token"],
            page["bookshelf_cleanup_confirm"],
        ],
    )
    page["bookshelf_cleanup_cancel"].click(
        catalog_handlers.cancel_selected_cleanup,
        [],
        [
            page["bookshelf_msg"],
            page["bookshelf_cleanup_token"],
            page["bookshelf_cleanup_confirm"],
        ],
    )
    page["bookshelf_integrity"].click(
        catalog_handlers.check_selected_integrity,
        [page["bookshelf_selected_proj"]],
        [page["bookshelf_msg"], page["bookshelf_integrity_repair"]],
    )
    page["bookshelf_integrity_repair"].click(
        catalog_handlers.repair_selected_integrity,
        [page["bookshelf_selected_proj"]],
        [page["bookshelf_msg"], page["bookshelf_integrity_repair"]],
    )
    page["bookshelf_archive"].click(
        catalog_handlers.archive_selected,
        [
            page["bookshelf_selected_proj"],
            page["bookshelf_archive_confirm"],
            session,
        ],
        [page["bookshelf_msg"], page["bookshelf_archive_confirm"]],
    ).then(
        catalog_handlers.refresh_project_catalog,
        [page["bookshelf_search"]],
        catalog_outputs,
    )

    # ── 全局：从备份恢复 ──
    page["bookshelf_restore"].click(
        catalog_handlers.restore_backup_global,
        [page["bookshelf_restore_file"]],
        [page["bookshelf_msg"]],
    ).then(
        catalog_handlers.refresh_project_catalog,
        [page["bookshelf_search"]],
        catalog_outputs,
    )

    # ── 全局：回收站（列表 / 恢复 / 永久删除） ──
    page["bookshelf_trash_refresh"].click(
        catalog_handlers.refresh_archived_projects_global,
        [],
        [
            page["bookshelf_trash_table"],
            page["bookshelf_trash_sel"],
            page["bookshelf_trash_status"],
        ],
    )
    page["bookshelf_trash_restore"].click(
        catalog_handlers.restore_archived_global,
        [page["bookshelf_trash_sel"]],
        [page["bookshelf_msg"]],
    ).then(
        catalog_handlers.refresh_project_catalog,
        [page["bookshelf_search"]],
        catalog_outputs,
    )
    page["bookshelf_trash_delete"].click(
        catalog_handlers.permanently_delete_archived_global,
        [page["bookshelf_trash_sel"], page["bookshelf_trash_confirm"]],
        [page["bookshelf_msg"]],
    ).then(
        catalog_handlers.refresh_project_catalog,
        [page["bookshelf_search"]],
        catalog_outputs,
    ).then(
        lambda: _update_checkbox_false(),
        [],
        [page["bookshelf_trash_confirm"]],
    )


def _update_checkbox_false() -> Any:
    """永久删除完成后复位二次确认 Checkbox（延迟加载 gradio）。"""
    from ui.project_catalog_handlers import _update

    return _update(value=False)
