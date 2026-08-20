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
    # 书架连续 selection context 的版本号。它不是第二个 selection 真相源，
    # 只用于让确认态/短暂 UI 状态知道「用户是否已经离开过这个选择上下文」。
    selection_revision: int = 0
    # ``None`` = 没有可追踪的确认态；``-1`` = 确认态曾存在但已被 selection
    # context 变化失效。保留这个失效标记可防止 A → B → A 复用旧确认。
    _archive_confirmation_revision: Optional[int] = None

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
        selected = str(name) if name else None
        if selected != self.selected_project:
            self.selection_revision += 1
            if self._archive_confirmation_revision is not None:
                self._archive_confirmation_revision = -1
        self.selected_project = selected

    def clear_selected(self) -> None:
        """清空书架选中项目。"""
        self.set_selected(None)

    def begin_archive_confirmation(self) -> None:
        """Bind the next archive click to the current continuous selection."""
        self._archive_confirmation_revision = self.selection_revision

    def clear_archive_confirmation(self) -> None:
        """Forget archive confirmation after success, cancel, or a failed guard."""
        self._archive_confirmation_revision = None

    def invalidate_archive_confirmation(self) -> None:
        """Mark a UI-held confirmation string stale without changing selection."""
        self._archive_confirmation_revision = -1

    def archive_confirmation_is_current(self) -> bool:
        """Return whether an archive confirmation belongs to this selection context."""
        return self._archive_confirmation_revision == self.selection_revision

    def reset_for_data_root(self) -> None:
        """Drop all assets that may belong to the previous data root.

        ``catalog_query`` intentionally survives a data-root switch so the
        bookshelf filter remains the user's query, while selected/opened
        project assets and any production session are discarded.
        """
        self.clear_selected()
        self.clear_opened()
        self.clear_archive_confirmation()

    def clear_opened(self) -> None:
        """Clear the opened production/session assets without touching the query."""
        self.set_project(None, None, {})
        self.set_snapshot(None)
        self.synthesis = None

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
