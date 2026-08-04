"""角色与声音页面事件接线。

本模块只注册 Gradio 事件；业务逻辑仍由 ``app.py`` 的既有回调和
项目 / 音色库 service 层负责。
"""
from __future__ import annotations

import os


def wire_voice_page(page: dict, context: dict) -> None:
    """Register role selection, filtering, audition and binding events."""
    cb = context["callbacks"]
    session = context["session"]

    page["v_table"].change(
        cb["select_role_from_list"],
        [page["v_table"], session],
        [
            page["v_role"], page["v_role_title"], page["v_audio"],
            page["v_lib"], page["v_current"], page["v_preview_audio"],
            page["v_bind_msg"],
        ],
    )
    page["v_role_search"].change(
        cb["refresh_role_list"],
        [page["v_role_search"], page["v_role"], session],
        [page["v_table"]],
    )
    page["v_bind"].click(
        cb["bind_voice"],
        [page["v_role"], page["v_audio"], page["v_lib"], session],
        [
            page["v_bind_msg"], page["v_table"], page["v_lib"],
            page["v_role"], page["v_role_title"], page["v_current"],
        ],
    )
    page["v_lib"].change(cb["play_lib_voice"], page["v_lib"], page["v_audio"])
    page["v_lib"].change(
        lambda choice: f"*当前参考音频: 音色库/{choice}*" if choice else "*当前参考音频: 未选择*",
        page["v_lib"],
        page["v_current"],
    )
    page["v_audio"].change(
        lambda path: f"*当前参考音频: {os.path.basename(path) if path else '未选择'}*",
        page["v_audio"],
        page["v_current"],
    )
    page["v_save_btn"].click(
        cb["save_to_lib"],
        [
            page["v_record"], page["v_upload_clone"], page["v_save_name"],
            page["v_save_category"], session,
        ],
        [page["v_save_msg"], page["v_lib"], context["production_voice"], page["v_save_category"]],
    )
    page["v_bind_category"].change(
        cb["filter_vlib_by_category"], [page["v_bind_category"]], page["v_lib"]
    )
    page["v_lib_search"].change(
        cb["refresh_voice_lib"],
        [page["v_lib_search"], page["v_lib_category"]],
        [page["v_lib_browser"], page["v_lib_category"]],
    )
    page["v_lib_category"].change(
        cb["refresh_voice_lib"],
        [page["v_lib_search"], page["v_lib_category"]],
        [page["v_lib_browser"], page["v_lib_category"]],
    )
    page["v_lib_browser"].select(
        cb["select_voice_from_browser"],
        [page["v_lib_browser"]],
        [page["v_lib"], page["v_preview_audio"]],
    )
    page["v_preview_btn"].click(
        cb["preview_bound_voice"],
        [page["v_role"], page["v_audio"], page["v_lib"], session],
        page["v_preview_audio"],
    )
