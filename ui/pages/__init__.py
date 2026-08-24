"""UI 页面 Builder 统一导出。

每个页面模块导出一个 ``create_<page>_page()`` 函数，
返回组件引用字典（dict[str, Any]）。
"""
from __future__ import annotations

from .create_project_page import create_create_project_page
from .export_page import create_export_page
from .overview_page import create_overview_page
from .review_page import create_review_page
from .settings_page import create_settings_page
from .supplement_page import create_supplement_page
from .synthesis_page import create_synthesis_page
from .voice_page import create_voice_page

__all__ = [
    "create_overview_page",
    "create_create_project_page",
    "create_voice_page",
    "create_synthesis_page",
    "create_review_page",
    "create_export_page",
    "create_supplement_page",
    "create_settings_page",
]
