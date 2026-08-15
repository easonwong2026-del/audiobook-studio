"""P1-A 回归：保存分类下拉 value='未分类' 必须始终在 choices 中。

Windows 实机出现 Gradio 告警：
    gr.Dropdown(): The value passed into gr.Dropdown() is not in the list of
    choices. Please update the list of choices to include: 未分类 ...

根因：当音色库只有带 ``_`` 前缀的文件时，``voice_lib.list_categories()``
不含“未分类”，但 ``v_save_category`` 的 value 恒为“未分类”（合法业务默认值：
无前缀文件的默认分类 + 保存时的默认分类），导致 value-not-in-choices 告警。

修复：``app._save_category_choices`` 保证“未分类”始终在保存分类下拉的
choices 中（去重，不改变已有分类语义，也不全局开启 allow_custom_value）。
"""
from __future__ import annotations

import app


def test_save_category_choices_empty_keeps_default():
    choices = app._save_category_choices([])
    assert choices == ["未分类", "— 新建 —"]


def test_save_category_choices_none_keeps_default():
    choices = app._save_category_choices(None)
    assert choices == ["未分类", "— 新建 —"]


def test_save_category_choices_always_contains_uncategorized():
    # 音色库只有带前缀文件 → list_categories() 不含“未分类”，但 value 是
    # “未分类” —— choices 必须补上它，否则 Gradio 告警 value-not-in-choices。
    choices = app._save_category_choices(["温柔", "低沉"])
    assert choices == ["未分类", "温柔", "低沉", "— 新建 —"]


def test_save_category_choices_dedupes_uncategorized():
    # 音色库里确实有无前缀文件 → “未分类”已在 cats 中，不能重复添加。
    choices = app._save_category_choices(["未分类", "温柔"])
    assert choices == ["未分类", "温柔", "— 新建 —"]


def test_save_category_choices_preserves_existing_category_semantics():
    # 不改变已有分类的顺序/语义；只保证“未分类”在场。
    choices = app._save_category_choices(["童声", "温柔", "低沉"])
    assert choices == ["未分类", "童声", "温柔", "低沉", "— 新建 —"]


def test_refresh_categories_value_is_in_choices(monkeypatch):
    # 模拟音色库只有带前缀文件：list_categories() 不含“未分类”。
    monkeypatch.setattr(app.voice_lib, "list_categories", lambda: ["温柔"])
    bind_update, save_update = app.refresh_categories()
    assert "未分类" in save_update["choices"]
    assert save_update["value"] == "未分类"
    # 绑定筛选下拉不强制 value（初始为 None），即使 choices 不含“未分类”也不告警
    assert "value" not in bind_update or bind_update["value"] is None


def test_refresh_voice_filters_value_is_in_choices(monkeypatch):
    monkeypatch.setattr(app.voice_lib, "list_categories", lambda: ["低沉", "温柔"])
    bind_update, lib_update, save_update = app.refresh_voice_filters()
    assert save_update["value"] == "未分类"
    assert "未分类" in save_update["choices"]
    # 筛选下拉不设值 → 即使 choices 不含“未分类”也不会告警
    assert bind_update["value"] is None
    assert lib_update["value"] is None


def test_refresh_voice_filters_empty_library_keeps_uncategorized(monkeypatch):
    monkeypatch.setattr(app.voice_lib, "list_categories", lambda: [])
    bind_update, lib_update, save_update = app.refresh_voice_filters()
    assert save_update["choices"] == ["未分类", "— 新建 —"]
    assert save_update["value"] == "未分类"
    assert bind_update["choices"] == ["未分类"]


def test_save_to_lib_refresh_keeps_value_in_choices(monkeypatch):
    """保存后刷新下拉：value=category 必须在 choices 中（含“未分类”）。"""
    monkeypatch.setattr(app.voice_lib, "list_categories", lambda: ["温柔"])
    monkeypatch.setattr(
        app.ProjectService,
        "save_to_lib",
        staticmethod(lambda recorded, uploaded, name, category="": "/tmp/lib/温柔_x.wav"),
    )
    msg, _lib_update, _browser_update, save_update = app.save_to_lib(
        "/tmp/rec.wav", None, "x", "温柔", None
    )
    assert "已保存至音色库" in msg
    assert save_update["value"] == "温柔"
    assert "温柔" in save_update["choices"]
    assert "未分类" in save_update["choices"]
