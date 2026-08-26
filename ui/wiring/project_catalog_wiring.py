"""项目书架事件接线：把 handler 接到概览页组件（仿 ui/wiring/settings_wiring.py）。

只负责注册 Gradio 事件；业务逻辑全部在 ``ui/project_catalog_handlers.py`` /
Service 层。``wire_project_catalog`` 必须在 ``gr.Blocks`` 内调用。
"""
from __future__ import annotations

from typing import Any

from ui import project_catalog_handlers as catalog_handlers


def cleanup_outputs(page: dict) -> list:
    """Return the four-output cleanup handler contract."""
    return [
        page["bookshelf_msg"],
        page["bookshelf_cleanup_token"],
        page["bookshelf_cleanup_confirm"],
        page["bookshelf_cleanup_cancel"],
    ]


def storage_upgrade_outputs(page: dict) -> list:
    """Return the four-output Storage Layout v3 handler contract."""
    return [
        page["bookshelf_msg"],
        page["bookshelf_storage_token"],
        page["bookshelf_storage_confirm"],
        page["bookshelf_storage_cancel"],
    ]


def bookshelf_management_outputs(page: dict) -> list:
    """Return the stable bookshelf management output contract."""
    return [
        page["ov_bookshelf"],
        page["bookshelf_trash_table"],
        page["bookshelf_trash_sel"],
        page["bookshelf_trash_status"],
        page["bookshelf_selected_proj"],
        page["bookshelf_selected"],
        *bookshelf_selection_context_outputs(page),
    ]


def bookshelf_selection_context_outputs(page: dict) -> list:
    """Return the output contract for bookshelf selection events."""
    return [
        *(page[key] for key in catalog_handlers.BOOKSHELF_ACTION_KEYS),
        page["bookshelf_archive_confirm"],
        page["bookshelf_cleanup_token"],
        page["bookshelf_cleanup_confirm"],
        page["bookshelf_cleanup_cancel"],
        page["bookshelf_storage_token"],
        page["bookshelf_storage_confirm"],
        page["bookshelf_storage_cancel"],
        page["bookshelf_integrity_repair"],
        page["bookshelf_msg"],
    ]


def selection_ui_outputs(page: dict) -> list:
    """Return the bookshelf selection-context output contract."""
    return bookshelf_selection_context_outputs(page)


def wire_project_catalog(page: dict, deps: dict) -> None:
    """注册书架搜索、选择、管理动作、全局恢复/回收站事件。

    Args:
        page: 概览页组件字典（``create_overview_page()`` 的返回）。
        deps: 依赖字典，含：
            - ``session``: ``gr.State(SessionState())``；
            - ``management_outputs``: state-aware 24-output bookshelf refresh;
            - ``callbacks``: app.py 注入的回调，可选键：
              ``open_project`` / ``open_project_outputs`` / ``open_chain_rest`` /
              ``post_archive_reconcile`` / ``goto_project`` / ``groups``；
            - ``groups``: 导航 ``_GROUPS``（供打开项目后切页）。
    """
    session = deps["session"]
    management_outputs = deps.get("management_outputs") or bookshelf_management_outputs(page)
    management_refresh = deps.get(
        "management_refresh",
        catalog_handlers.refresh_bookshelf_management_view,
    )
    selection_outputs = bookshelf_selection_context_outputs(page)
    cleanup_handler_outputs = cleanup_outputs(page)
    storage_handler_outputs = storage_upgrade_outputs(page)
    cb = deps.get("callbacks", {})
    groups = deps.get("groups", [])

    # ── 搜索 → 过滤书架行（ss.catalog_query 单一状态来源；选中项被过滤出
    #    结果时同步清空 ss.selected_project + UI 选中态，杜绝幽灵状态） ──
    for event_name in ("change", "submit"):
        search_chain = page["bookshelf_search"].__getattribute__(event_name)(
            catalog_handlers.apply_project_search,
            [page["bookshelf_search"], session],
            [
                page["ov_bookshelf"],
                page["bookshelf_selected"],
                page["bookshelf_selected_proj"],
            ],
        )
        search_chain = search_chain.then(
            catalog_handlers.reconcile_bookshelf_selection_context,
            [session],
            selection_outputs,
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
        chain = chain.then(
            management_refresh,
            [page["bookshelf_search"], session],
            management_outputs,
        )
        goto = cb.get("goto_project")
        if goto is not None and groups:
            chain.then(goto, None, groups)

    # ── 手动刷新：query 保持，selection 仅在不可见/不存在时清除 ──
    page["bookshelf_refresh"].click(
        management_refresh,
        [page["bookshelf_search"], session],
        management_outputs,
    )

    # ── 打开目录 / 备份 / 清理 / 整理存储布局 / 诊断 / 移入回收站 ──
    page["bookshelf_open_dir"].click(
        catalog_handlers.open_selected_directory,
        [page["bookshelf_selected_proj"]],
        [page["bookshelf_msg"]],
    )
    page["bookshelf_open_audio"].click(
        catalog_handlers.open_selected_generated_audio,
        [page["bookshelf_selected_proj"]],
        [page["bookshelf_msg"]],
    )
    page["bookshelf_open_delivery"].click(
        catalog_handlers.open_selected_deliveries,
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
        cleanup_handler_outputs,
    )
    page["bookshelf_cleanup_confirm"].click(
        catalog_handlers.execute_selected_cleanup,
        [page["bookshelf_selected_proj"], page["bookshelf_cleanup_token"]],
        cleanup_handler_outputs,
    )
    page["bookshelf_cleanup_cancel"].click(
        catalog_handlers.cancel_selected_cleanup,
        [],
        cleanup_handler_outputs,
    )

    # ── 存储布局整理：扫描方案 → token 确认（v1/v2 → v3 显式迁移） ──
    page["bookshelf_storage"].click(
        catalog_handlers.scan_selected_storage_upgrade,
        [page["bookshelf_selected_proj"]],
        storage_handler_outputs,
    )
    page["bookshelf_storage_confirm"].click(
        catalog_handlers.execute_selected_storage_upgrade,
        [page["bookshelf_selected_proj"], page["bookshelf_storage_token"]],
        storage_handler_outputs,
    )
    page["bookshelf_storage_cancel"].click(
        catalog_handlers.cancel_selected_storage_upgrade,
        [],
        storage_handler_outputs,
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

    # ── 移入回收站：两步确认（确认态绑定项目名） ──
    # 前四个输出保持既有 archive handler 契约；第五个是只在真正归档成功
    # 时递增的 State revision。首次确认 / guard 阻止不会触发后续刷新链。
    page["bookshelf_archive"].click(
        catalog_handlers.archive_selected_with_event,
        [
            page["bookshelf_selected_proj"],
            page["bookshelf_archive_confirm"],
            session,
            page["bookshelf_archive_event"],
        ],
        [
            page["bookshelf_msg"],
            page["bookshelf_archive_confirm"],
            page["bookshelf_selected_proj"],
            page["bookshelf_selected"],
            page["bookshelf_archive_event"],
        ],
    )

    # Only a changed success revision reaches this event.  Reconcile catalog
    # Reconcile Catalog/selection state before any workflow callback runs.
    archive_success_chain = page["bookshelf_archive_event"].change(
        management_refresh,
        [page["bookshelf_search"], session],
        management_outputs,
    )
    if "post_archive_reconcile" in cb:
        archive_success_chain = cb["post_archive_reconcile"](archive_success_chain)

    # ── 全局：从备份恢复 ──
    page["bookshelf_restore"].click(
        catalog_handlers.restore_backup_global,
        [page["bookshelf_restore_file"]],
        [page["bookshelf_msg"]],
    ).then(
        management_refresh,
        [page["bookshelf_search"], session],
        management_outputs,
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
        management_refresh,
        [page["bookshelf_search"], session],
        management_outputs,
    )
    page["bookshelf_trash_delete"].click(
        catalog_handlers.permanently_delete_archived_global,
        [page["bookshelf_trash_sel"], page["bookshelf_trash_confirm"]],
        [page["bookshelf_msg"]],
    ).then(
        management_refresh,
        [page["bookshelf_search"], session],
        management_outputs,
    ).then(
        lambda: _update_checkbox_false(),
        [],
        [page["bookshelf_trash_confirm"]],
    )
def _update_checkbox_false() -> Any:
    """永久删除完成后复位二次确认 Checkbox（延迟加载 gradio）。"""
    from ui.project_catalog_handlers import _update

    return _update(value=False)
