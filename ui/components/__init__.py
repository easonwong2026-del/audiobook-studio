"""可复用的纯展示组件。

本包不读取项目文件、不调用 Service，只负责把已经计算好的 UI 状态呈现为组件或 HTML。
"""

from .brand_logo import BRAND_MARK_PATH, BRAND_MARK_SIZE, create_brand_logo
from .dashboard import empty_dashboard_html, project_dashboard_html
from .production_nav import create_production_navigation
from .voice_binding import (
    build_role_management_choices,
    build_role_management_rows,
    build_v4_role_management_choices,
    format_bound_role_choices,
    format_role_choices,
    format_role_label,
    format_role_management_summary,
)

__all__ = [
    "BRAND_MARK_PATH",
    "BRAND_MARK_SIZE",
    "create_brand_logo",
    "empty_dashboard_html",
    "project_dashboard_html",
    "create_production_navigation",
    "build_role_management_choices",
    "build_role_management_rows",
    "build_v4_role_management_choices",
    "format_bound_role_choices",
    "format_role_choices",
    "format_role_label",
    "format_role_management_summary",
]
