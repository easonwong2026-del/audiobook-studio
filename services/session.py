"""会话状态：替代全局可变 ``S`` 的 per-session 真相源（不得依赖 gradio）。

每个 Gradio 浏览器会话通过 ``gr.State(SessionState())`` 持有一个独立实例，
彻底消除原 ``app.py`` 模块级全局 ``S`` 在「多标签页共享同一可变字典」时互相
踩状态的问题。事件处理器对 ``ss`` 做**原地 mutate**（不靠返回值回传状态）。

``synthesis`` 字段引用 ``SynthesisState``；此处仅用字符串前向引用，避免与
``synthesis`` 模块产生循环导入。
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from lib.snapshot import ProjectSnapshot
from repositories.exceptions import ProjectNotFoundError

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期导入，避免与 synthesis 循环导入
    from services.synthesis import SynthesisState


@dataclass
class SessionState:
    """每个 Gradio 会话独立持有的状态对象（经 ``gr.State`` 在 handler 间传递）。

    Attributes:
        project: 当前项目名（``None`` 表示尚未打开项目）。
        script: 当前 Snapshot payload 的深拷贝，仅供 legacy compatibility readers 使用。
        bindings: 当前 Snapshot bindings 的深拷贝，仅供 legacy compatibility readers 使用。
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
        """写入兼容 mirror，原地更新字段（不新建 ``SessionState`` 对象）。

        这是 legacy compatibility setter，不是 opened Snapshot hydrate boundary。
        直接写入 payload 时先使旧 Snapshot 失效，避免调用者留下「新 mirror + 旧
        Snapshot」的混合状态；Open / Create / reload 应使用
        :meth:`apply_project_snapshot`。

        Args:
            name: 项目名。
            script: 结构化剧本（dict）。
            bindings: 角色绑定表（dict）。
        """
        self.project = name
        self.script = copy.deepcopy(script) if script is not None else None
        self.bindings = copy.deepcopy(bindings)
        self.project_snapshot = None

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
        # Keep the stale marker until the next catalog reconciliation clears
        # the browser-side confirmation State as well.
        self.invalidate_archive_confirmation()

    def clear_opened(self) -> None:
        """Clear the opened production/session assets without touching the query."""
        self.set_project(None, None, {})
        self.set_snapshot(None)
        self.synthesis = None

    def set_catalog_query(self, query: Optional[str]) -> None:
        """写入书架搜索 query（导航返回时作为单一过滤来源）。"""
        self.catalog_query = str(query or "")

    def set_snapshot(self, snapshot: Optional[ProjectSnapshot]) -> None:
        """写入 / 清除 cache handle；不会同步 Session compatibility mirrors。

        Open / Create / stale reload 必须使用 ``apply_project_snapshot``；保留这个
        low-level setter 是为了让 ``invalidate_snapshot`` 与旧测试 / integration
        double 保持兼容。
        """
        self.project_snapshot = snapshot

    def apply_project_snapshot(
        self,
        snapshot: ProjectSnapshot,
        *,
        project: Optional[str] = None,
    ) -> ProjectSnapshot:
        """Atomically apply one opened Snapshot and refresh compatibility mirrors.

        ``ProjectSnapshot`` remains the current cache / modern read view.  Session
        ``script`` and ``bindings`` are deliberately deep-copied compatibility
        mirrors; their content follows the Snapshot but their mutable identities do
        not.  Validation happens before mutating any Session field so a mismatched
        Snapshot cannot partially replace an existing opened project.
        """
        snapshot_name = str(getattr(snapshot, "name", "") or "").strip()
        opened_name = str(project if project is not None else snapshot_name).strip()
        if not snapshot_name or opened_name != snapshot_name:
            raise ValueError(
                "Snapshot identity does not match the opened project: "
                f"opened={opened_name!r}, snapshot={snapshot_name!r}"
            )
        mirrored_bindings = copy.deepcopy(snapshot.bindings)
        if not isinstance(mirrored_bindings, dict):
            raise TypeError("ProjectSnapshot.bindings must be a dict")

        # Validate and copy before changing identity/cache fields.  This keeps the
        # transition all-or-nothing for the normal in-process callback path.
        mirrored_script = copy.deepcopy(snapshot.script)
        self.project = opened_name
        self.project_snapshot = snapshot
        self.script = mirrored_script
        self.bindings = mirrored_bindings
        return snapshot

    @staticmethod
    def _same_project_dir(left: Any, right: Any) -> bool:
        if not left or not right:
            return False
        left_path = os.path.normcase(os.path.realpath(os.path.abspath(str(left))))
        right_path = os.path.normcase(os.path.realpath(os.path.abspath(str(right))))
        return left_path == right_path

    def _snapshot_matches_opened(self, snapshot: ProjectSnapshot) -> bool:
        """Reject a cache from another project or another configured data root."""
        if not self.project:
            # A low-level SessionState test / integration may hold a standalone
            # snapshot before an opened identity exists.  Production readers still
            # require ``project`` and therefore cannot consume it as opened state.
            return True
        if str(getattr(snapshot, "name", "") or "") != str(self.project):
            return False
        try:
            from repositories.project_repo import ProjectRepository

            current_dir = ProjectRepository.get_project_dir(str(self.project))
        except (OSError, RuntimeError, ValueError):
            return False
        return self._same_project_dir(current_dir, snapshot.project_dir)

    def ensure_snapshot(self) -> Optional[ProjectSnapshot]:
        """确保返回一个（必要时按脏检测重载的）最新快照；无快照时返回 None。"""
        if self.project_snapshot is None:
            return None
        current = self.project_snapshot
        if not self._snapshot_matches_opened(current):
            # Do not serve an A Snapshot for opened B, or retain a cache tied to a
            # previous data root.  ``app._snap`` / the Voice resolver can rebuild
            # the current opened project through the same apply boundary.
            self.project_snapshot = None
            return None
        try:
            fresh = current.reload_if_stale()
        except ProjectNotFoundError:
            # An externally deleted / archived opened project has no valid
            # payload left to preserve; clear the complete opened context.
            self.clear_opened()
            return None
        except (OSError, RuntimeError, ValueError):
            # A deleted / unavailable project must not continue serving its old
            # payload.  Keep the opened identity for the caller's safe rebuild or
            # error path, but drop the invalid cache.
            self.project_snapshot = None
            return None
        if fresh is current:
            return current
        if self.project:
            try:
                return self.apply_project_snapshot(fresh, project=self.project)
            except (TypeError, ValueError):
                self.project_snapshot = None
                return None
        # Standalone low-level users without an opened identity may still refresh
        # their cache, but cannot claim a Session mirror transition.
        self.project_snapshot = fresh
        return fresh

    def invalidate_snapshot(self) -> None:
        """只使 cache 失效；下次内部读取必须通过 Snapshot resolver rehydrate。"""
        self.project_snapshot = None
