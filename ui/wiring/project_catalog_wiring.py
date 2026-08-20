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


def hierarchy_outputs(page: dict) -> list:
    """Return the dedicated Phase B.5 relationship-control output contract.

    The first four outputs keep the Phase B order.  The four appended outputs
    expose project kind, chapter title, chapter order, and the title/order
    update action without changing the legacy 25-output bookshelf prefix.
    """
    return [
        page["bookshelf_parent_book"],
        page["bookshelf_bind_chapter"],
        page["bookshelf_unbind_chapter"],
        page["bookshelf_relation_status"],
        page["bookshelf_project_kind"],
        page["bookshelf_chapter_title"],
        page["bookshelf_chapter_order"],
        page["bookshelf_update_chapter"],
    ]


def bookshelf_management_outputs(
    page: dict, project_sel, *, include_hierarchy: bool = False
) -> list:
    """Return the 25-output bookshelf contract, optionally plus hierarchy."""
    outputs = [
        page["ov_bookshelf"],
        project_sel,
        page["bookshelf_trash_table"],
        page["bookshelf_trash_sel"],
        page["bookshelf_trash_status"],
        page["bookshelf_selected_proj"],
        page["bookshelf_selected"],
        *selection_ui_outputs(page, project_sel)[1:],
    ]
    if include_hierarchy:
        outputs.extend(hierarchy_outputs(page))
    return outputs


def selection_ui_outputs(page: dict, project_sel) -> list:
    """Return outputs matching ``reconcile_bookshelf_selection``."""
    return [
        project_sel,
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


def wire_project_catalog(page: dict, deps: dict) -> None:
    """注册书架搜索、选择、管理动作、全局恢复/回收站事件。

    Args:
        page: 概览页组件字典（``create_overview_page()`` 的返回）。
        deps: 依赖字典，含：
            - ``session``: ``gr.State(SessionState())``；
            - ``project_sel``: 项目页 ``p_sel`` Dropdown（刷新 choices 用，
              兼容保留；书架 select 同步由 app.py 内联接线完成）；
            - ``catalog_outputs``: legacy five-output catalog components;
            - ``management_outputs``: state-aware 25-output bookshelf refresh;
            - ``callbacks``: app.py 注入的回调，可选键：
              ``open_project`` / ``open_project_outputs`` / ``open_chain_rest`` /
              ``goto_project`` / ``groups``；
            - ``groups``: 导航 ``_GROUPS``（供打开项目后切页）。
    """
    session = deps["session"]
    management_outputs = deps.get("management_outputs") or bookshelf_management_outputs(
        page, deps["project_sel"], include_hierarchy=True
    )
    management_refresh = deps.get(
        "management_refresh",
        catalog_handlers.refresh_bookshelf_management_view_with_hierarchy,
    )
    selection_outputs = selection_ui_outputs(page, deps["project_sel"])
    relationship_outputs = hierarchy_outputs(page)
    cleanup_handler_outputs = cleanup_outputs(page)
    storage_handler_outputs = storage_upgrade_outputs(page)
    cb = deps.get("callbacks", {})
    groups = deps.get("groups", [])
    merge_refresh = deps.get("merge_refresh")
    merge_outputs = deps.get("merge_outputs") or []

    def refresh_merge_after(chain):
        """Refresh the dedicated planner state without growing catalog outputs."""
        if merge_refresh and merge_outputs:
            return chain.then(merge_refresh, [session], merge_outputs)
        return chain

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
            catalog_handlers.reconcile_bookshelf_selection,
            [session, deps["project_sel"]],
            selection_outputs,
        )
        search_chain = search_chain.then(
            catalog_handlers.reconcile_bookshelf_hierarchy_selection,
            [session],
            relationship_outputs,
        )
        search_chain = refresh_merge_after(search_chain)

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
            [page["bookshelf_search"], deps["project_sel"], session],
            management_outputs,
        )
        chain = refresh_merge_after(chain)
        goto = cb.get("goto_project")
        if goto is not None and groups:
            chain.then(goto, None, groups)

    # ── 手动刷新：query 保持，selection 仅在不可见/不存在时清除 ──
    refresh_chain = page["bookshelf_refresh"].click(
        management_refresh,
        [page["bookshelf_search"], deps["project_sel"], session],
        management_outputs,
    )
    refresh_merge_after(refresh_chain)

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

    # ── Phase B/B.5：显式建立、编辑 / 解除 Book ← Chapter 逻辑关系 ──
    bind_chain = page["bookshelf_bind_chapter"].click(
        catalog_handlers.bind_selected_chapter,
        [
            page["bookshelf_selected_proj"],
            page["bookshelf_parent_book"],
            session,
            page["bookshelf_chapter_title"],
            page["bookshelf_chapter_order"],
        ],
        [page["bookshelf_msg"],],
    ).then(
        management_refresh,
        [page["bookshelf_search"], deps["project_sel"], session],
        management_outputs,
    )
    bind_chain = refresh_merge_after(bind_chain)
    update_chain = page["bookshelf_update_chapter"].click(
        catalog_handlers.update_selected_chapter,
        [
            page["bookshelf_selected_proj"],
            page["bookshelf_chapter_title"],
            page["bookshelf_chapter_order"],
            session,
        ],
        [page["bookshelf_msg"],],
    ).then(
        management_refresh,
        [page["bookshelf_search"], deps["project_sel"], session],
        management_outputs,
    )
    update_chain = refresh_merge_after(update_chain)
    unbind_chain = page["bookshelf_unbind_chapter"].click(
        catalog_handlers.unbind_selected_chapter,
        [page["bookshelf_selected_proj"], session],
        [page["bookshelf_msg"],],
    ).then(
        management_refresh,
        [page["bookshelf_search"], deps["project_sel"], session],
        management_outputs,
    )
    unbind_chain = refresh_merge_after(unbind_chain)

    # ── 移入回收站：两步确认（确认态绑定项目名） ──
    # 输出 4 元组（消息 / 确认态 / 选中项目 State / 选中信息 Markdown）：
    # 首次确认与 guard 阻止不清 selection；成功后全清（handler 内完成）。
    # 归档成功后统一刷新：先跑「打开项目」同款全链刷新（opened 被归档时
    # ss 已 reset → 全部页面回空态；只归档 selected 时各页刷新为当前
    # opened 项目状态），再跑目录类刷新（书架 / p_sel / 回收站）。
    archive_chain = page["bookshelf_archive"].click(
        catalog_handlers.archive_selected,
        [
            page["bookshelf_selected_proj"],
            page["bookshelf_archive_confirm"],
            session,
        ],
        [
            page["bookshelf_msg"],
            page["bookshelf_archive_confirm"],
            page["bookshelf_selected_proj"],
            page["bookshelf_selected"],
        ],
    )
    if "open_chain_rest" in cb:
        archive_chain = cb["open_chain_rest"](archive_chain)
    archive_chain = archive_chain.then(
        management_refresh,
        [page["bookshelf_search"], deps["project_sel"], session],
        management_outputs,
    )
    archive_chain = refresh_merge_after(archive_chain)

    # ── 全局：从备份恢复 ──
    restore_chain = page["bookshelf_restore"].click(
        catalog_handlers.restore_backup_global,
        [page["bookshelf_restore_file"]],
        [page["bookshelf_msg"]],
    ).then(
        management_refresh,
        [page["bookshelf_search"], deps["project_sel"], session],
        management_outputs,
    )
    restore_chain = refresh_merge_after(restore_chain)

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
    trash_restore_chain = page["bookshelf_trash_restore"].click(
        catalog_handlers.restore_archived_global,
        [page["bookshelf_trash_sel"]],
        [page["bookshelf_msg"]],
    ).then(
        management_refresh,
        [page["bookshelf_search"], deps["project_sel"], session],
        management_outputs,
    )
    trash_restore_chain = refresh_merge_after(trash_restore_chain)
    trash_delete_chain = page["bookshelf_trash_delete"].click(
        catalog_handlers.permanently_delete_archived_global,
        [page["bookshelf_trash_sel"], page["bookshelf_trash_confirm"]],
        [page["bookshelf_msg"]],
    ).then(
        management_refresh,
        [page["bookshelf_search"], deps["project_sel"], session],
        management_outputs,
    ).then(
        lambda: _update_checkbox_false(),
        [],
        [page["bookshelf_trash_confirm"]],
    )
    trash_delete_chain = refresh_merge_after(trash_delete_chain)

    # ── Phase C.1：独立只读 Chapter → Book planner 状态路径 ──
    merge_source = page.get("merge_source_chapter")
    merge_target = page.get("merge_target_book")
    merge_analyze = page.get("merge_analyze")
    merge_result = page.get("merge_plan_result")
    merge_state = page.get("merge_plan_state")
    merge_resolution = page.get("merge_resolution")
    merge_confirm = page.get("merge_confirm")
    merge_confirmation_state = page.get("merge_confirmation_state")
    merge_execute = page.get("merge_execute")
    merge_execution_result = page.get("merge_execution_result")
    merge_transaction_state = page.get("merge_transaction_state")
    merge_analyze_callback = deps.get("merge_analyze")
    merge_invalidate_callback = deps.get("merge_invalidate")
    merge_prepare_callback = deps.get("merge_prepare_execution")
    merge_clear_callback = deps.get("merge_clear_execution")
    merge_invalidate_execution_callback = deps.get("merge_invalidate_execution")
    merge_confirm_callback = deps.get("merge_confirm")
    merge_execute_callback = deps.get("merge_execute")
    if (
        all(item is not None for item in (merge_source, merge_target, merge_analyze, merge_result, merge_state))
        and merge_analyze_callback is not None
        and merge_invalidate_callback is not None
    ):
        analyze_chain = merge_analyze.click(
            merge_analyze_callback,
            [merge_source, merge_target, session],
            [merge_result, merge_state],
        )
        if merge_prepare_callback and all(
            item is not None
            for item in (
                merge_resolution,
                merge_confirm,
                merge_confirmation_state,
                merge_execute,
                merge_execution_result,
                merge_transaction_state,
            )
        ):
            analyze_chain.then(
                merge_prepare_callback,
                [merge_state],
                [
                    merge_resolution,
                    merge_confirm,
                    merge_confirmation_state,
                    merge_execute,
                    merge_execution_result,
                    merge_transaction_state,
                ],
            )
        for component in (merge_source, merge_target):
            input_chain = component.change(
                merge_invalidate_callback,
                [],
                [merge_result, merge_state],
            )
            if merge_clear_callback and all(
                item is not None
                for item in (
                    merge_resolution,
                    merge_confirm,
                    merge_confirmation_state,
                    merge_execute,
                    merge_execution_result,
                    merge_transaction_state,
                )
            ):
                input_chain.then(
                    merge_clear_callback,
                    [],
                    [
                        merge_resolution,
                        merge_confirm,
                        merge_confirmation_state,
                        merge_execute,
                        merge_execution_result,
                        merge_transaction_state,
                    ],
                )
        if merge_resolution is not None and merge_invalidate_execution_callback and all(
            item is not None
            for item in (
                merge_confirm,
                merge_confirmation_state,
                merge_execute,
                merge_execution_result,
                merge_transaction_state,
            )
        ):
            merge_resolution.change(
                merge_invalidate_execution_callback,
                [],
                [
                    merge_confirm,
                    merge_confirmation_state,
                    merge_execute,
                    merge_execution_result,
                    merge_transaction_state,
                ],
            )
        if merge_confirm is not None and merge_confirm_callback and all(
            item is not None
            for item in (
                merge_confirmation_state,
                merge_execute,
                merge_execution_result,
                merge_transaction_state,
            )
        ):
            merge_confirm.change(
                merge_confirm_callback,
                [merge_confirm, merge_source, merge_target, merge_state, merge_resolution, session],
                [
                    merge_confirmation_state,
                    merge_execute,
                    merge_execution_result,
                    merge_transaction_state,
                ],
            )
        if merge_execute is not None and merge_execute_callback and all(
            item is not None
            for item in (
                merge_execution_result,
                merge_transaction_state,
                merge_confirmation_state,
            )
        ):
            merge_execute.click(
                merge_execute_callback,
                [merge_state, merge_resolution, merge_confirmation_state, session],
                [
                    merge_execution_result,
                    merge_transaction_state,
                    merge_execute,
                    merge_confirm,
                    merge_confirmation_state,
                ],
            )


def _update_checkbox_false() -> Any:
    """永久删除完成后复位二次确认 Checkbox（延迟加载 gradio）。"""
    from ui.project_catalog_handlers import _update

    return _update(value=False)
