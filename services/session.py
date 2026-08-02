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

if TYPE_CHECKING:
    from services.synthesis import SynthesisState


@dataclass
class SessionState:
    """每个 Gradio 会话独立持有的状态对象（经 ``gr.State`` 在 handler 间传递）。

    Attributes:
        project: 当前项目名（``None`` 表示尚未打开项目）。
        project_format: 当前项目格式（``"v4"`` / ``"v3"`` / ``None``）。
        script: 结构化剧本（V3：``lib.project_manager.open_project`` raw dict；
            V4：``domain.v4.ScriptDocument``）。
        speakers_v4: V4 项目角色文档（``SpeakersDocument``；V3 项目为 None）。
        bindings: 角色 -> 参考音频绝对路径 的绑定表（V3 语义；V4 走 voices.json）。
        synthesis: 当前合成任务态（``SynthesisState``）；未开始合成时为 ``None``。
    """

    project: Optional[str] = None
    project_format: Optional[str] = None
    script: Optional[Any] = None
    speakers_v4: Optional[Any] = None
    bindings: dict[str, str] = field(default_factory=dict)
    synthesis: Optional["SynthesisState"] = None
    project_snapshot: Optional[ProjectSnapshot] = None

    def set_project(self, name: str, script: Any, bindings: dict[str, str]) -> None:
        """写入当前项目，原地更新字段（不新建对象，保持 ``gr.State`` 引用稳定）。

        Args:
            name: 项目名。
            script: 结构化剧本（dict）。
            bindings: 角色绑定表（dict）。
        """
        self.project = name
        self.project_format = "v3"
        self.script = script
        self.speakers_v4 = None
        self.bindings = bindings
        self.synthesis = None
        self.project_snapshot = None

    def set_v4_project(self, name: str, script: Any, speakers: Any) -> None:
        """写入 V4 项目状态（不触碰 V3 快照 / bindings 字段）。"""
        self.project = name
        self.project_format = "v4"
        self.script = script
        self.speakers_v4 = speakers
        self.project_snapshot = None
        self.bindings = {}
        self.synthesis = None

    @property
    def is_v4(self) -> bool:
        return self.project_format == "v4"

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
