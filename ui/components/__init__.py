"""可复用的纯展示组件。

本包不读取项目文件、不调用 Service，只负责把已经计算好的 UI 状态呈现为组件或 HTML。
"""

from .dashboard import empty_dashboard_html, project_dashboard_html
from .production_nav import create_production_navigation
from .voice_binding import format_bound_role_choices, format_role_choices, format_role_label

__all__ = [
    "empty_dashboard_html",
    "project_dashboard_html",
    "create_production_navigation",
    "format_bound_role_choices",
    "format_role_choices",
    "format_role_label",
]
