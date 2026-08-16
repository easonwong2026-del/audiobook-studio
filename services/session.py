"""会话状态：替代全局可变 ``S`` 的 per-session 真相源（不得依赖 gradio）。

每个 Gradio 浏览器会话通过 ``gr.State(SessionState())`` 持有一个独立实例，
彻底消除原 ``app.py`` 模块级全局 ``S`` 在「多标签页共享同一可变字典」时互相
踩状态的问题。事件处理器对 ``ss`` 做**原地 mutate**（不靠返回值回传状态）。

``synthesis`` 字段引用 ``SynthesisState``；此处仅用字符串前向引用，避免与
``synthesis`` 模块产生循环导入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from lib.snapshot import ProjectSnapshot

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期导入，避免与 synthesis 循环导入
    from services.synthesis import SynthesisState


@dataclass
class SessionState:
    """每个 Gradio 会话独立持有的状态对象（经 ``gr.State`` 在 handler 间传递）。

    Attributes:
        project: 当前项目名（``None`` 表示尚未打开项目）。
        script: 结构化剧本（``lib.project_manager.open_project`` 返回的 raw dict）。
        bindings: 角色 -> 参考音频绝对路径 的绑定表。
        synthesis: 当前合成任务态（``SynthesisState``）；未开始合成时为 ``None``。
    """

    project: Optional[str] = None
    script: Optional[Any] = None
    bindings: dict[str, str] = field(default_factory=dict)
    synthesis: Optional["SynthesisState"] = None
    project_snapshot: Optional[ProjectSnapshot] = None
    # 书架「选中」项目（selected ≠ opened）：点选只改这里，不打开项目、
    # 不加载 structured_script；只有「打开项目」才写 self.project。
    selected_project: Optional[str] = None
    # 书架搜索 query 的单一状态来源（导航离开/返回后仍保持过滤；不依赖
    # Textbox 前端值，避免「搜索框还有词、列表却变回全部」的幽灵状态）。
    catalog_query: str = ""

    def set_project(self, name: str, script: Any, bindings: dict[str, str]) -> None:
        """写入当前项目，原地更新字段（不新建对象，保持 ``gr.State`` 引用稳定）。

        Args:
            name: 项目名。
            script: 结构化剧本（dict）。
            bindings: 角色绑定表（dict）。
        """
        self.project = name
        self.script = script
        self.bindings = bindings

    def set_selected(self, name: Optional[str]) -> None:
        """写入书架选中项目（不打开项目、不加载剧本）。"""
        self.selected_project = str(name) if name else None

    def clear_selected(self) -> None:
        """清空书架选中项目。"""
        self.selected_project = None

    def set_catalog_query(self, query: Optional[str]) -> None:
        """写入书架搜索 query（导航返回时作为单一过滤来源）。"""
        self.catalog_query = str(query or "")

    def set_snapshot(self, snapshot: Optional[ProjectSnapshot]) -> None:
        """写入当前项目快照（``ProjectSnapshot``），原地更新字段。"""
        self.project_snapshot = snapshot

    def ensure_snapshot(self) -> Optional[ProjectSnapshot]:
        """确保返回一个（必要时按脏检测重载的）最新快照；无快照时返回 None。"""
        if self.project_snapshot is None:
            return None
        fresh = self.project_snapshot.reload_if_stale()
        self.project_snapshot = fresh
        return fresh

    def invalidate_snapshot(self) -> None:
        """使当前快照失效（如写盘后状态已变，下次读取触发重载）。"""
        self.project_snapshot = None
